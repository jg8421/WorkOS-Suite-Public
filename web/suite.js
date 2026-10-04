/* WorkOS Suite: fixed capabilities only. Navigation never starts a worker. */
(() => {
  'use strict';
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const store = {get(key, fallback = '') { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } }, set(key, value) { try { localStorage.setItem(key, value); } catch {} }};
  const groups = [
    {id:'work',name:'工作',en:'WORK',icon:'◈',views:[['workos','WorkOS 工作台'],['ideas','Idea 灵感']]},
    {id:'files',name:'文件',en:'FILES',icon:'▤',views:[['files','文件管理']]},
    {id:'memory',name:'记录与记忆',en:'RECORD & MEMORY',icon:'◷',views:[['memory','Memory 记忆'],['qwen','千问录音整理']]},
    {id:'devices',name:'设备与运行',en:'DEVICES & RUNTIME',icon:'▣',views:[['phone','手机互联'],['processes','进程管理'],['runtime','工具目录']]}
  ];
  const titles = {workos:'工作台',ideas:'灵感收件箱',files:'文件管理',memory:'记录与记忆',qwen:'千问录音整理',phone:'手机互联',processes:'进程管理',runtime:'工具与运行状态'};
  const state = {view:'workos',csrf:'',bootstrap:{components:[],roots:[],repositories:[]},projects:[],ideas:[],rootId:store.get('suite.root'),path:'',selected:null,files:[],processes:[],renderId:0,trashReceipt:null};
  let toastTimer;
  function toast(message, error = false) {
    const el = $('#toast'); el.textContent = message; el.classList.toggle('error', error); el.classList.remove('hidden');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.add('hidden'), 4300);
  }
  function friendly(error) { return error.message || '暂时无法连接，请刷新后重试。'; }
  async function api(path, options = {}) {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 30000);
    const headers = {'Accept':'application/json', ...options.headers};
    if (options.method && options.method !== 'GET') { headers['Content-Type'] = 'application/json'; headers['X-CSRF-Token'] = state.csrf; }
    try {
      const result = await fetch(path, {...options, headers, credentials:'same-origin', signal:controller.signal});
      const contentType = result.headers.get('content-type') || '';
      const data = contentType.includes('json') ? await result.json() : {};
      if (!result.ok) { const detail = data.error?.message || data.error || data.detail || data.message; const error = new Error(typeof detail === 'string' ? detail : `请求未完成（${result.status}），请稍后重试。`); error.status = result.status; throw error; }
      return data;
    } catch (error) { if (error.name === 'AbortError') throw new Error('这一步需要更长时间。请刷新状态后重试，草稿已保留。'); throw error; }
    finally { clearTimeout(timer); }
  }
  const post = (path, body = {}) => api(path, {method:'POST',body:JSON.stringify(body)});
  const array = (data, key) => Array.isArray(data) ? data : (Array.isArray(data?.[key]) ? data[key] : []);
  const size = bytes => !Number.isFinite(Number(bytes)) ? '—' : Number(bytes) < 1024 ? `${Number(bytes)} B` : Number(bytes) < 1048576 ? `${(Number(bytes)/1024).toFixed(1)} KB` : `${(Number(bytes)/1048576).toFixed(1)} MB`;
  const date = value => { if (!value) return ''; const result = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value); return Number.isNaN(result.valueOf()) ? '' : result.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); };
  const numeric = (value, unit = '', digits = 1) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? `${Number(value).toFixed(digits)}${unit}` : '—';
  function empty(title, detail, action = '') { return `<div class="empty-state"><span class="status-symbol">◇</span><strong>${escape(title)}</strong><span>${escape(detail)}</span>${action}</div>`; }
  function loading() { return '<div class="loading-state"><span class="spinner"></span>正在读取…</div>'; }
  function statusName(value) { return ({running:'运行中',owned:'由工作台启动',borrowed:'外部运行中',stopped:'未启动',needs_setup:'需要配置',unavailable:'需要配置',missing:'需要配置',failed:'暂不可用',starting:'启动中',stopping:'停止中',ready:'已就绪'})[value] || '待确认'; }
  function running(data) { return data?.running === true || ['running','owned','borrowed','ready'].includes(data?.status || data?.state); }
  function statusMarkup(data, title, fallback) {
    const status = data.status || data.state || (data.running === true ? 'running' : 'stopped');
    const detail = data.dependency_reason || data.reason || data.detail || fallback;
    return `<div class="status-card"><span class="status-symbol">${running(data)?'●':'◇'}</span><div><h3>${escape(title)} <span class="pill ${running(data)?'':'warn'}">${escape(statusName(status))}</span></h3><p>${escape(detail)}</p>${data.control_note?`<p>${escape(data.control_note)}</p>`:''}</div></div>`;
  }
  function projectOptions(selected = '', optional = true) { return `${optional?'<option value="">稍后关联项目</option>':''}${state.projects.map(project => `<option value="${escape(project.id)}" ${project.id===selected?'selected':''}>${escape(project.name || project.title || '未命名项目')}</option>`).join('')}`; }
  async function refreshProjects() { const data = await api('/api/state'); state.projects = array(data,'projects'); }
  async function busy(button, operation) {
    if (button?.disabled) return; const original = button?.textContent;
    if (button) { button.disabled = true; button.textContent = '处理中…'; }
    try { return await operation(); } catch (error) { toast(friendly(error), true); }
    finally { if (button?.isConnected) { button.disabled = false; button.textContent = original; } }
  }
  function modal(title, html, confirmText = '确定') {
    return new Promise(resolve => {
      const dialog = $('#action-dialog'); $('#dialog-title').textContent = title; $('#dialog-body').innerHTML = html; $('#dialog-confirm').textContent = confirmText;
      dialog.returnValue = ''; dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm' ? dialog : null), {once:true});
      dialog.showModal(); const first = dialog.querySelector('input,select,textarea'); if (first) setTimeout(() => first.focus(), 50);
    });
  }
  function navigate(view) {
    if (groups.some(group => group.id === view)) view = groups.find(group => group.id === view).views[0][0];
    if (!titles[view]) view = 'workos';
    state.view = view; if (location.hash !== `#${view}`) history.replaceState(null,'',`#${view}`);
    const group = groups.find(item => item.views.some(([id]) => id === view));
    $('#primary-nav').innerHTML = groups.map(item => `<button class="nav-item ${group.id===item.id?'active':''}" data-action="navigate" data-view="${item.id}" ${group.id===item.id?'aria-current="page"':''}><span class="nav-icon" aria-hidden="true">${item.icon}</span><span>${item.name}<small>${item.en}</small></span></button>`).join('');
    $('#category-label').textContent = group.en; $('#page-title').textContent = titles[view]; document.title = `${titles[view]} · WorkOS Suite`;
    $('#module-nav').innerHTML = group.views.map(([id,label]) => `<button class="module-tab ${id===view?'active':''}" data-action="navigate" data-view="${id}" ${id===view?'aria-current="page"':''}>${label}</button>`).join('');
    render();
  }
  async function render() {
    const generation = ++state.renderId; const content = $('#content'); content.innerHTML = loading();
    try { await ({workos:renderWorkOS,ideas:renderIdeas,files:renderFiles,memory:renderMemory,qwen:renderQwen,phone:renderPhone,processes:renderProcesses,runtime:renderRuntime})[state.view](generation); }
    catch (error) { if (generation === state.renderId) content.innerHTML = `<div class="panel">${empty('暂时无法读取',friendly(error),'<button class="button secondary" data-action="refresh">重试</button>')}</div>`; }
  }
  function current(generation) { return generation === state.renderId; }
  function renderWorkOS() {
    $('#content').innerHTML = `<div class="frame-toolbar"><div class="frame-nav">${[['overview','总览'],['research','研究'],['meetings','会议'],['finance','财务'],['deliverables','交付']].map(([id,label])=>`<button class="button secondary small" data-action="workos-section" data-section="${id}">${label}</button>`).join('')}<button class="link-button" data-action="workos-navigation">完整导航</button></div><a href="/workos/#overview" target="_blank" rel="noopener">在独立窗口打开 ↗</a></div><iframe class="workspace-frame" id="workos-frame" title="WorkOS 项目工作台" src="/workos/#overview"></iframe>`;
    $('#workos-frame').addEventListener('load',event=>{try{const doc=event.target.contentDocument;if(!doc||doc.getElementById('workos-suite-embed-style'))return;const style=doc.createElement('style');style.id='workos-suite-embed-style';style.textContent='#sidebar,.sidebar-scrim,#mobile-menu{display:none!important}.main-shell{width:100%!important;margin-left:0!important}';doc.head.appendChild(style);}catch{}});
  }
  async function renderIdeas(generation) {
    const [data] = await Promise.all([api('/api/suite/ideas'),refreshProjects()]); if (!current(generation)) return;
    state.ideas = array(data,'ideas');
    $('#content').innerHTML = `<div class="hero"><div><h2>先记下，再开始。</h2><p>想法先进入收件箱，准备好后送到 WorkOS 项目笔记。</p></div><div class="hero-mark">✦</div></div><div class="panel"><div class="panel-header"><div><h2>快速记下</h2><p>Enter 保存 · Shift + Enter 换行</p></div><button class="button secondary small" data-action="idea-original">打开 Idea 原版 ↗</button></div><form id="idea-form"><textarea id="idea-text" class="capture-text" aria-label="灵感内容" placeholder="一个想法、一个待研究的问题，或下一步要做的事…" maxlength="20000"></textarea><div class="capture-footer"><select id="idea-project" aria-label="关联项目">${projectOptions()}</select><button class="button" type="submit" id="idea-save">保存灵感 <span>↗</span></button></div><div class="draft-hint" id="idea-draft-hint">草稿自动保留在这台设备。</div></form></div><div class="panel"><div class="panel-header"><div><h2>灵感收件箱 <span class="pill">${state.ideas.length}</span></h2><p>关联项目会创建项目笔记，刷新后仍可查看。</p></div><button class="link-button" data-action="refresh-projects">刷新项目</button></div><div id="idea-list">${state.ideas.length?state.ideas.map(idea=>`<article class="idea-card" data-idea-id="${escape(idea.id)}"><div class="idea-text">${escape(idea.text || idea.content)}</div><div class="idea-meta"><span>${escape(date(idea.created_at || idea.created))}${idea.project_id?' · 已关联项目':''}</span><div class="small-actions"><button class="link-button" data-action="idea-share" data-id="${escape(idea.id)}">送到项目</button><button class="link-button danger" data-action="idea-delete" data-id="${escape(idea.id)}">删除</button></div></div></article>`).join(''):empty('这里等着你的第一个想法','写下几句话，就可以开始。')}</div></div><div id="idea-original-panel" class="hidden panel"><div class="frame-toolbar"><span>Idea 原版 · 完整 PWA</span><a href="/idea/" target="_blank" rel="noopener">独立窗口 ↗</a></div><iframe class="workspace-frame" title="Idea 原版" data-src="/idea/"></iframe></div>`;
    $('#idea-text').value = store.get('suite.idea-draft'); $('#idea-project').value = store.get('suite.idea-project');
    $('#idea-text').addEventListener('input', () => { store.set('suite.idea-draft',$('#idea-text').value); $('#idea-draft-hint').textContent = '草稿已保留。'; });
    $('#idea-project').addEventListener('change', () => store.set('suite.idea-project',$('#idea-project').value));
    $('#idea-text').addEventListener('keydown', event => { if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('#idea-form').requestSubmit();} });
    $('#idea-form').addEventListener('submit', event => { event.preventDefault(); busy($('#idea-save'),async()=>{
      const text=$('#idea-text').value.trim();if(!text){$('#idea-text').focus();return;}const project_id=$('#idea-project').value;
      const signature=JSON.stringify([text,project_id]);let request_id=store.get('suite.idea-signature')===signature?store.get('suite.idea-request-id'):'';
      if(!request_id)request_id=crypto.randomUUID?crypto.randomUUID():`idea-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      store.set('suite.idea-signature',signature);store.set('suite.idea-request-id',request_id);
      const idea=await post('/api/suite/ideas',{text,project_id:project_id||null,request_id});
      if(project_id&&idea.id)await post(`/api/suite/ideas/${encodeURIComponent(idea.id)}/share`,{project_id});
      store.set('suite.idea-draft','');store.set('suite.idea-request-id','');store.set('suite.idea-signature','');toast(project_id?'灵感已保存，并送到项目笔记。':'灵感已保存。');if(state.view==='ideas')await render();
    }); });
  }
  function roots() { return array(state.bootstrap,'roots'); }
  function rootOptions() { return roots().map(root=>`<option value="${escape(root.id || root.root_id)}" ${(root.id||root.root_id)===state.rootId?'selected':''}>${escape(root.label || root.name || '已选目录')}</option>`).join(''); }
  function pathJoin(base, leaf) { return [base,leaf].filter(Boolean).join('/').replace(/\\/g,'/'); }
  async function renderFiles(generation) {
    if (!roots().some(root=>(root.id||root.root_id)===state.rootId)) { state.rootId=roots()[0]?.id||roots()[0]?.root_id||'';state.path=''; }
    let data={entries:[]}; if(state.rootId) data=await api(`/api/suite/files?root_id=${encodeURIComponent(state.rootId)}&path=${encodeURIComponent(state.path)}`);if(!current(generation))return;
    state.files=array(data,'entries');state.path=data.path??state.path;state.selected=null;
    $('#content').innerHTML = `<div class="panel"><div class="panel-header"><div><h2>选定目录，安心整理</h2><p>搜索、预览与操作只发生在你添加的目录中。</p></div><button class="button secondary small" data-action="root-add">＋ 添加目录</button></div><div class="toolbar"><div class="toolbar-left"><select id="file-root" aria-label="文件目录">${roots().length?rootOptions():'<option>请先添加目录</option>'}</select><button class="button secondary small" data-action="file-up" ${state.path?'':'disabled'}>↑ 上一级</button></div><div class="toolbar-right"><input id="file-search" type="search" placeholder="搜索当前目录" aria-label="搜索当前目录"><button class="button secondary small" data-action="file-search">搜索</button><button class="button small" data-action="file-mkdir" ${state.rootId?'':'disabled'}>＋ 新建文件夹</button></div></div><div class="breadcrumb"><button data-action="file-root">根目录</button><span>/</span><span id="file-current-path">${escape(state.path||'')}</span></div><div class="file-layout"><div><div class="table-scroll"><table><thead><tr><th>名称</th><th>大小</th><th>修改时间</th><th></th></tr></thead><tbody id="file-rows">${fileRows()}</tbody></table></div>${!state.files.length?empty(state.rootId?'当前目录为空':'从一个目录开始',state.rootId?'可以新建文件夹，或切换目录。':'添加你准备使用的目录。'):''}</div><div class="preview" id="file-preview">${empty('点选文件，查看内容','支持文本、表格与压缩包预览；点 ··· 管理文件夹。')}</div></div></div>${state.trashReceipt?'<div class="banner info">文件已移至回收区。<button class="link-button" data-action="file-restore">撤销上次删除</button></div>':''}`;
    $('#file-root').addEventListener('change',event=>{state.rootId=event.target.value;store.set('suite.root',state.rootId);state.path='';render();});
    $('#file-search').addEventListener('keydown',event=>{if(event.key==='Enter')searchFiles();});
  }
  function fileRows() { return state.files.map((file,index)=>`<tr class="file-row" data-action="file-select" data-index="${index}" tabindex="0" role="button" aria-label="${escape(file.name)}"><td><span class="row-button"><span class="file-symbol">${file.is_dir?'▰':'▤'}</span>${escape(file.name)}</span></td><td>${file.is_dir?'—':size(file.size)}</td><td>${escape(date(file.modified))}</td><td><button class="link-button" data-action="file-manage" data-index="${index}" aria-label="管理 ${escape(file.name)}">···</button></td></tr>`).join(''); }
  async function searchFiles() { if(!state.rootId)return;const query=$('#file-search')?.value||'';const data=await api(`/api/suite/files?root_id=${encodeURIComponent(state.rootId)}&path=${encodeURIComponent(state.path)}&q=${encodeURIComponent(query)}`);if(state.view!=='files')return;state.files=array(data,'entries');$('#file-rows').innerHTML=fileRows(); }
  function fileActions(file) { return `<div class="button-row"><button class="button secondary small" data-action="file-rename">重命名</button><button class="button secondary small" data-action="file-copy">复制</button><button class="button secondary small" data-action="file-move">移动</button>${file.is_dir?'':'<button class="button secondary small" data-action="file-import">送到项目</button>'}<button class="button danger small" data-action="file-trash">移至回收区</button></div>`; }
  async function selectFile(index, manage = false) {
    const file=state.files[index];if(!file)return;state.selected=file;
    if(file.is_dir&&!manage){state.path=file.path;await render();return;}
    document.querySelectorAll('.file-row').forEach(row=>row.classList.toggle('selected',Number(row.dataset.index)===index));
    const preview=$('#file-preview');
    if(file.is_dir){preview.innerHTML=`<div class="panel-header"><h3>${escape(file.name)}</h3><button class="button secondary small" data-action="file-open">打开文件夹</button></div>${empty('管理这个文件夹','整理操作均限于所选根目录。')}${fileActions(file)}`;return;}
    preview.innerHTML=loading();
    const data=await api(`/api/suite/file-preview?root_id=${encodeURIComponent(state.rootId)}&path=${encodeURIComponent(file.path)}`);if(state.view!=='files'||state.selected!==file)return;
    let body='';
    if(data.type==='text')body=`<pre>${escape(data.content)}</pre>`;
    else if(data.type==='table'||(data.type==='archive'&&data.columns?.length))body=`${data.content?`<p class="caption">${escape(data.content)}</p>`:''}<div class="table-scroll"><table><thead><tr>${(data.columns||[]).map(column=>`<th>${escape(column)}</th>`).join('')}</tr></thead><tbody>${(data.rows||[]).map(row=>`<tr>${(Array.isArray(row)?row:Object.values(row)).map(cell=>`<td>${escape(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    else if(data.type==='archive')body=`<pre>${escape(typeof data.content==='string'?data.content:(data.entries||data.rows||[]).map(entry=>typeof entry==='string'?entry:(entry.name||entry.path||'')).join('\n'))}</pre>`;
    else body=empty('下载查看此文件','此格式可通过原应用打开。');
    preview.innerHTML=`<div class="panel-header"><h3>${escape(file.name)}</h3><a class="button secondary small" href="/api/suite/file?root_id=${encodeURIComponent(state.rootId)}&path=${encodeURIComponent(file.path)}" download>下载</a></div>${body}${fileActions(file)}`;
  }
  async function renderMemory(generation) {
    const [status,recent,settings]=await Promise.all([api('/api/suite/memory/status'),api('/api/suite/memory/recent?limit=20'),api('/api/suite/memory/settings')]);if(!current(generation))return;
    const prefs=settings.settings||settings;
    $('#content').innerHTML=`<div class="hero"><div><h2>有需要时，再留下记录。</h2><p>手动添加随时可用。自动导入与采集默认关闭，设置由你决定。</p></div><div class="hero-mark">◷</div></div><div class="grid-two"><div class="panel"><div class="panel-header"><h2>记忆库</h2><div class="button-row"><button class="button secondary small" data-action="memory-start">启动</button><button class="button secondary small" data-action="memory-stop">停止</button></div></div>${statusMarkup(status,'Memory','可手动保存记录；启动服务后使用完整检索。')}<form id="memory-add-form"><div class="form-field"><label for="memory-title">标题</label><input id="memory-title" placeholder="给这条记录一个标题" maxlength="200"></div><div class="form-field"><label for="memory-text">内容</label><textarea id="memory-text" placeholder="希望以后找回的内容…" required maxlength="20000"></textarea></div><div class="button-row"><select id="memory-type" aria-label="记忆类型"><option value="note">笔记</option><option value="idea">想法</option><option value="meeting">会议</option></select><button class="button" type="submit">保存记录</button></div></form></div><div class="panel"><div class="panel-header"><div><h2>记录来源</h2><p>修改后点击保存；浏览页面不会启动采集。</p></div></div><form id="memory-settings-form">${[['codex_import','Codex 记录导入','允许从已配置的 Codex 来源导入。'],['dsh_import','DSH 记录导入','允许从已配置的 DSH 来源导入。'],['desktop_capture','桌面采集','开启后才允许采集桌面；先确认需要保留的范围。'],['cloud_inbox','云收件箱','使用前需要单独配置连接。']].map(([id,title,note])=>`<div class="check-row"><input type="checkbox" id="setting-${id}" name="${id}" ${prefs[id]===true?'checked':''}><label for="setting-${id}">${title}<small>${note}</small></label></div>`).join('')}<div class="button-row"><button class="button secondary" type="submit">保存来源设置</button></div></form></div></div><div class="panel"><div class="panel-header"><div><h2>最近记录</h2><p>查询只读取记忆库，删除会标记为遗忘。</p></div><div class="toolbar-right"><input id="memory-search" type="search" placeholder="查找记录" aria-label="查找记录"><button class="button secondary small" data-action="memory-search">搜索</button></div></div><div id="memory-events">${memoryEvents(array(recent,'events'))}</div></div>`;
    $('#memory-add-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{const text=$('#memory-text').value.trim();if(!text)return;await post('/api/suite/memory/add',{title:$('#memory-title').value.trim(),text,type:$('#memory-type').value,tags:[]});toast('记录已保存。');await render();});});
    $('#memory-settings-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{const prefs={};for(const input of event.target.querySelectorAll('input'))prefs[input.name]=input.checked;await post('/api/suite/memory/settings',prefs);toast('来源设置已保存。');});});
    $('#memory-search').addEventListener('keydown',event=>{if(event.key==='Enter')memorySearch();});
  }
  function memoryEvents(events) { return events.length?events.map(event=>`<article class="idea-card"><h3>${escape(event.title||'记录')}</h3><div class="idea-text">${escape(event.text||event.content)}</div><div class="idea-meta"><span>${escape(date(event.timestamp||event.created_at))} · ${escape(event.type||'note')}</span><button class="link-button danger" data-action="memory-forget" data-id="${escape(event.id)}">遗忘</button></div></article>`).join(''):empty('还没有记录','添加第一条记录，或主动开启你需要的来源。'); }
  async function memorySearch() { const query=$('#memory-search')?.value||'';const data=await api(`/api/suite/memory/search?q=${encodeURIComponent(query)}&limit=30`);if(state.view==='memory')$('#memory-events').innerHTML=memoryEvents(array(data,'events')); }
  async function renderQwen(generation) {
    const [status,recordings]=await Promise.all([api('/api/suite/qwen/status'),api('/api/suite/recordings')]);if(!current(generation))return;
    const items=array(recordings,'recordings');
    $('#content').innerHTML=`<div class="hero"><div><h2>录音完成后，整理下一步。</h2><p>连接千问桌面端。启动自动化后会监测通话并触发录音；也可以只手动触发。</p></div><div class="hero-mark">≋</div></div><div class="panel"><div class="panel-header"><h2>千问自动化</h2><div class="button-row"><button class="button secondary" data-action="qwen-start">启动自动化</button><button class="button secondary" data-action="qwen-stop">停止自动化</button></div></div>${statusMarkup(status,'千问','请先安装并打开千问桌面端，再启动连接。')}<div class="button-row"><button class="button" data-action="qwen-trigger">手动触发一次</button><span class="caption">触发前，请确认千问中的当前录音与页面。</span></div><p class="caption">打开此页不会启动监测或发起模型请求。</p></div><div class="panel"><div class="panel-header"><div><h2>录音归档 <span class="pill">${items.length}</span></h2><p>这里只读取已有归档，项目资料可在文件模块中关联。</p></div><button class="button secondary small" data-action="refresh">刷新归档</button></div>${items.length?`<div class="table-scroll"><table><thead><tr><th>录音</th><th>文件</th><th>大小</th><th>修改时间</th></tr></thead><tbody>${items.map(item=>`<tr><td>${escape(item.name)}</td><td>${escape(Array.isArray(item.files)?item.files.length:(item.files??'—'))}</td><td>${size(item.size_bytes)}</td><td>${escape(date(item.modified_at))}</td></tr>`).join('')}</tbody></table></div>`:empty('暂无录音归档','完成录音并配置归档来源后，可在这里查看。')}</div>`;
  }
  async function renderPhone(generation) {
    const [status,result]=await Promise.all([api('/api/suite/phone/status'),api('/api/suite/phone/devices')]);if(!current(generation))return;
    const devices=array(result,'devices');
    $('#content').innerHTML=`<div class="hero"><div><h2>手机与工作台，保持连接。</h2><p>先授权 USB 或无线调试，再选择设备；每次镜像和截屏都由你主动操作。</p></div><div class="hero-mark">▯</div></div><div class="grid-two"><div class="panel"><div class="panel-header"><h2>连接设备</h2><button class="button secondary small" data-action="refresh">刷新设备</button></div>${statusMarkup(status,'手机互联','需要 Android 调试工具与已授权设备。')}<div class="form-field"><label for="phone-device">已授权设备</label><select id="phone-device"><option value="">${devices.length?'选择设备':'暂无设备，请连接并授权'}</option>${devices.map(device=>`<option value="${escape(device.serial)}" ${device.state==='device'?'':'disabled'}>${escape(device.model||'Android 设备')} · ${escape(device.state==='device'?'已授权':device.state||'待授权')}</option>`).join('')}</select></div><div class="button-row"><button class="button" data-action="phone-mirror" ${devices.some(device=>device.state==='device')?'':'disabled'}>开始镜像</button><button class="button secondary" data-action="phone-screenshot" ${devices.some(device=>device.state==='device')?'':'disabled'}>截取屏幕</button><button class="button secondary" data-action="phone-stop">停止镜像</button></div><p class="caption">停止只关闭由此工作台启动的镜像窗口。</p><details><summary class="caption">无线调试：手动配对</summary><form id="phone-pair-form"><p class="caption">在手机「无线调试」中打开配对码页面，填写该页面显示的地址与六位码。配对码不会保存。</p><div class="form-field"><label for="phone-address">配对地址（IP:端口）</label><input id="phone-address" placeholder="手机显示的配对地址" autocomplete="off" required></div><div class="form-field"><label for="phone-code">六位配对码</label><input id="phone-code" type="password" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" autocomplete="off" required></div><button class="button secondary" type="submit">配对设备</button></form></details></div><div class="panel"><div class="panel-header"><h2>屏幕预览</h2><span class="pill off">按需截屏</span></div><div class="phone-screen" id="phone-screen"><div class="phone-placeholder">▯</div><strong>设备准备好后，点击截取屏幕</strong><span>镜像会在独立窗口打开。<br>工作台不会持续采集手机画面。</span></div></div></div>`;
    $('details',$('#content')).insertAdjacentHTML('beforeend','<form id="phone-connect-form"><p class="caption">已完成配对？使用无线调试主页上的连接地址；它与配对页面的端口不同。</p><div class="form-field"><label for="phone-connect-address">连接地址（IP:端口）</label><input id="phone-connect-address" placeholder="无线调试主页显示的连接地址" autocomplete="off" required></div><button class="button secondary" type="submit">连接已配对设备</button></form>');
    $('#phone-pair-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{const body={address:$('#phone-address').value.trim(),code:$('#phone-code').value};try{await post('/api/suite/phone/pair',body);toast('配对请求已完成，请继续填写连接地址。');}finally{if($('#phone-code'))$('#phone-code').value='';}});});
    $('#phone-connect-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{await post('/api/suite/phone/connect',{address:$('#phone-connect-address').value.trim()});toast('连接请求已完成，请刷新设备。');});});
  }
  async function renderProcesses(generation) {
    const data=await api('/api/suite/processes');if(!current(generation))return;state.processes=array(data,'processes');const system=data.system||{};
    $('#content').innerHTML=`<div class="stats-grid"><div class="stat"><div class="stat-label">CPU</div><div class="stat-value">${numeric(system.cpu_percent??system.cpu,'%')}</div><div class="stat-note">系统实时读数</div></div><div class="stat"><div class="stat-label">内存使用</div><div class="stat-value">${numeric(system.memory_percent,'%')}</div><div class="stat-note">${numeric(system.memory_used_mb,' MB',0)}</div></div><div class="stat"><div class="stat-label">进程数</div><div class="stat-value">${state.processes.length}</div><div class="stat-note">本次可读取的进程</div></div><div class="stat"><div class="stat-label">GPU / 温度</div><div class="stat-value">—</div><div class="stat-note">未接入真实硬件传感器</div></div></div><div class="panel"><div class="panel-header"><div><h2>进程管理</h2><p>每次操作验证 PID 与创建时间；系统及工作台进程受到保护。</p></div><button class="button secondary small" data-action="refresh">刷新进程</button></div><div class="toolbar"><input id="process-search" type="search" placeholder="按进程名称搜索" aria-label="搜索进程"><span class="refresh-time">CPU 未采样时显示 —；不自动刷新或结束进程。</span></div><div class="table-scroll"><table><thead><tr><th>进程</th><th>PID</th><th>CPU</th><th>内存</th><th>操作</th></tr></thead><tbody id="process-rows">${processRows(state.processes)}</tbody></table></div></div>`;
    $('#process-search').addEventListener('input',event=>{$('#process-rows').innerHTML=processRows(state.processes.filter(process=>String(process.name).toLowerCase().includes(event.target.value.toLowerCase())));});
  }
  function processRows(processes) { return processes.map(process=>`<tr><td class="process-name">${escape(process.name)} ${process.protected?'<span class="pill off">受保护</span>':''}</td><td>${escape(process.pid)}</td><td>${numeric(process.cpu,'%')}</td><td>${numeric(process.memory_mb,' MB')}</td><td><div class="small-actions">${process.protected?'<span class="muted">—</span>':`<button class="link-button" data-action="process-control" data-pid="${process.pid}" data-control="suspend">暂停</button><button class="link-button" data-action="process-control" data-pid="${process.pid}" data-control="resume">恢复</button><button class="link-button" data-action="process-control" data-pid="${process.pid}" data-control="priority">优先级</button><button class="link-button danger" data-action="process-control" data-pid="${process.pid}" data-control="terminate">结束</button>`}</div></td></tr>`).join(''); }
  function repositoryCard(repo) {
    const primaryViews={'Local-WorkOS':'workos','jg-file-system':'files','phone-mirror-toolkit':'phone','qwen-auto-record':'qwen','idea-collector-web':'ideas','Fast-Process-Manager':'processes','personal-memory-component':'memory'};
    const view=primaryViews[repo.name];let source='';
    try{const url=new URL(repo.html_url||repo.url);if(url.protocol==='https:'&&url.hostname==='github.com'&&!url.username&&!url.password)source=url.href;}catch{}
    return `<article class="repo-card"><h3>${escape(repo.label||repo.name||repo.id)}</h3><p>${escape(repo.description||repo.purpose||'已收录的本地工具仓库。')}</p><div class="card-bottom"><span class="pill off">${escape(repo.category||repo.role||repo.language||'工具目录')}</span>${view?'<span class="pill">主要模块</span>':''}${repo.fork?'<span class="pill off">参考分支</span>':''}${repo.archived?'<span class="pill off">已归档</span>':''}${repo.available===false?'<span class="pill warn">待配置</span>':''}</div><div class="button-row">${view?`<button class="button soft small" data-action="navigate" data-view="${view}">进入模块</button>`:''}${source?`<a class="link-button" href="${escape(source)}" target="_blank" rel="noopener">查看源码 ↗</a>`:''}</div></article>`;
  }
  function componentCard(component) {
    const status=component.status||component.state||(component.running?'running':'stopped');
    const views={workos:'workos',files:'files',processes:'processes',ideas:'ideas',memory:'memory',qwen:'qwen',phone:'phone'};
    const inprocess=component.ownership==='inprocess';const borrowed=component.ownership==='borrowed'||component.borrowed===true;
    let controls=`<button class="button soft small" data-action="navigate" data-view="${views[component.id]||'runtime'}">${component.id==='phone'?'连接设备':'进入模块'}</button>`;
    if(!inprocess&&component.id!=='phone')controls+=`<button class="button secondary small" data-action="component-control" data-id="${escape(component.id)}" data-control="start" ${running(component)||component.can_start===false?'disabled':''}>启动</button><button class="button secondary small" data-action="component-control" data-id="${escape(component.id)}" data-control="stop" ${borrowed||!running(component)||component.can_stop===false?'disabled':''}>停止</button><button class="button secondary small" data-action="component-control" data-id="${escape(component.id)}" data-control="restart" ${borrowed||component.can_start===false&&component.can_stop===false?'disabled':''}>重启</button>`;
    return `<article class="repo-card primary" data-component-id="${escape(component.id)}"><h3>${escape(component.label||component.name||component.id)}</h3><p>${escape(component.dependency_reason||component.reason||component.description||component.detail||'通过工作台固定入口调用。')}</p><div class="card-bottom"><span class="pill ${running(component)?'':'warn'}">${escape(statusName(status))}</span>${borrowed?'<span class="pill off">外部启动 · 仅查看</span>':''}${inprocess?'<span class="pill off">随工作台运行</span>':''}</div><div class="button-row">${controls}</div></article>`;
  }
  async function renderRuntime(generation) {
    const result=await api('/api/suite/components');if(!current(generation))return;const components=array(result,'components');state.bootstrap.components=components;const repositories=array(state.bootstrap,'repositories');
    $('#content').innerHTML=`<div class="hero"><div><h2>一个入口，每个工具都有自己的位置。</h2><p>七个主要模块连接实际功能；其余仓库保留在目录，按需使用。</p></div><div class="hero-mark">▦</div></div><div class="section-label"><h2>主要模块 <span class="pill">${components.length}</span></h2><span>启动、停止与依赖状态</span></div><div class="catalogue-grid">${components.map(componentCard).join('')||empty('模块目录准备中','刷新查看已注册模块。')}</div><div class="section-label"><h2>仓库目录 <span class="pill">${repositories.length}</span></h2><span>完整保留 · 清楚分工</span></div><div class="catalogue-grid" id="repository-catalogue">${repositories.map(repositoryCard).join('')||empty('仓库目录准备中','目录由已配置的本地仓库生成。')}</div>`;
  }
  async function fileOperation(action) {
    const file=state.selected;const operation=action.replace('file-','');if(operation!=='mkdir'&&!file){toast('请先选择一个文件。',true);return;}
    if(operation==='trash'){const answer=await modal('移至回收区',`<p class="caption">${escape(file.name)} 将移至所选目录的回收区，可撤销此次操作。</p>`,'移至回收区');if(!answer)return;const result=await post('/api/suite/files/operation',{operation:'trash',root_id:state.rootId,path:file.path});state.trashReceipt=result.receipt_id||result.id||result.receipt?.id||result.receipt?.receipt_id;toast('已移至回收区。');await render();return;}
    if(operation==='import'){await refreshProjects();if(!state.projects.length){toast('请先在 WorkOS 创建项目，再刷新项目列表。',true);return;}const answer=await modal('送到项目',`<div class="form-field"><label for="dialog-project">选择项目</label><select id="dialog-project">${projectOptions('',false)}</select></div>`,'导入项目');if(!answer)return;await post('/api/suite/files/import',{root_id:state.rootId,paths:[file.path],project_id:$('#dialog-project').value});toast('已关联到项目资料。');return;}
    const labels={mkdir:'新建文件夹',rename:'重命名',copy:'复制文件',move:'移动文件'};
    const initial=operation==='rename'?file.name:'';
    const isTransfer=operation==='copy'||operation==='move';
    const answer=await modal(labels[operation],`${isTransfer?`<div class="form-field"><label for="dialog-destination-root">目标目录</label><select id="dialog-destination-root">${rootOptions()}</select></div>`:''}<div class="form-field"><label for="dialog-path">${operation==='mkdir'?'文件夹名称':operation==='rename'?'新名称':'目标路径（相对于目标根目录）'}</label><input id="dialog-path" value="${escape(initial)}" required placeholder="${isTransfer?'例如：项目资料/文件名':''}"></div><p class="caption">路径范围限于你添加的目录。</p>`);if(!answer)return;
    const target=$('#dialog-path').value.trim();if(!target)return;
    const body={operation,root_id:state.rootId,path:operation==='mkdir'?pathJoin(state.path,target):file.path};if(operation!=='mkdir')body.target=operation==='rename'?pathJoin(file.path.split('/').slice(0,-1).join('/'),target):target;
    if(isTransfer)body.destination_root_id=$('#dialog-destination-root').value;
    await post('/api/suite/files/operation',body);toast('文件操作已完成。');await render();
  }
  async function processControl(button) {
    const process=state.processes.find(item=>String(item.pid)===button.dataset.pid);if(!process||process.protected)return;
    const action=button.dataset.control;const labels={terminate:'结束进程',suspend:'暂停进程',resume:'恢复进程',priority:'设置优先级'};
    const answer=await modal(labels[action],`<p class="caption">${escape(process.name)} · PID ${escape(process.pid)}</p>${action==='terminate'?'<p class="warning-note">请先保存该应用中的工作。此操作会结束选定进程。</p>':''}${action==='priority'?'<div class="form-field"><label for="dialog-priority">优先级</label><select id="dialog-priority"><option value="normal">正常</option><option value="below_normal">低于正常</option><option value="above_normal">高于正常</option><option value="idle">低</option></select></div>':''}`,labels[action]);if(!answer)return;
    const body={pid:process.pid,creation_time:process.creation_time,action,confirm:true};if(action==='priority')body.priority=$('#dialog-priority').value;
    await post('/api/suite/processes/control',body);toast('进程操作已完成。');await render();
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-action]');if(!button||button.disabled)return;const action=button.dataset.action;
    if(action==='navigate'){navigate(button.dataset.view);return;}
    if(action==='refresh'){render();return;}
    if(action==='workos-section'){const iframe=$('#workos-frame');if(iframe)iframe.src=`/workos/#${button.dataset.section}`;return;}
    if(action==='workos-navigation'){try{const style=$('#workos-frame')?.contentDocument?.getElementById('workos-suite-embed-style');if(style){style.disabled=!style.disabled;button.textContent=style.disabled?'收起原版导航':'完整导航';}}catch{toast('请在独立窗口使用完整导航。');}return;}
    if(action==='idea-original'){const panel=$('#idea-original-panel');panel.classList.toggle('hidden');const iframe=$('iframe',panel);if(!panel.classList.contains('hidden')&&!iframe.src)iframe.src=iframe.dataset.src;return;}
    busy(button.tagName==='BUTTON'?button:null,async()=>{
      if(action==='refresh-projects'){await refreshProjects();$('#idea-project').innerHTML=projectOptions();toast('项目列表已刷新。');}
      else if(action==='idea-share'){await refreshProjects();if(!state.projects.length){toast('请先在 WorkOS 创建项目。',true);return;}const answer=await modal('送到项目笔记',`<div class="form-field"><label for="dialog-project">选择项目</label><select id="dialog-project">${projectOptions('',false)}</select></div>`,'创建项目笔记');if(answer){await post(`/api/suite/ideas/${encodeURIComponent(button.dataset.id)}/share`,{project_id:$('#dialog-project').value});toast('已创建项目笔记。');await render();}}
      else if(action==='idea-delete'){const answer=await modal('删除灵感','<p class="caption">删除这条收件箱灵感？已创建的项目笔记会保留。</p>','删除');if(answer){await api(`/api/suite/ideas/${encodeURIComponent(button.dataset.id)}`,{method:'DELETE'});toast('灵感已删除。');await render();}}
      else if(action==='root-add'){const answer=await modal('添加文件目录','<div class="form-field"><label for="dialog-root">目录完整路径</label><input id="dialog-root" placeholder="选择你希望在工作台使用的目录" required></div><div class="form-field"><label for="dialog-root-label">显示名称</label><input id="dialog-root-label" placeholder="例如：项目资料"></div>','添加目录');if(answer){await post('/api/suite/roots',{path:$('#dialog-root').value.trim(),label:$('#dialog-root-label').value.trim()});state.bootstrap=await api('/api/suite/bootstrap');await render();toast('目录已添加。');}}
      else if(action==='file-up'){state.path=state.path.split('/').filter(Boolean).slice(0,-1).join('/');await render();}
      else if(action==='file-root'){state.path='';await render();}
      else if(action==='file-search')await searchFiles();
      else if(action==='file-select')await selectFile(Number(button.dataset.index));
      else if(action==='file-manage')await selectFile(Number(button.dataset.index),true);
      else if(action==='file-open'){if(state.selected?.is_dir){state.path=state.selected.path;await render();}}
      else if(action==='file-restore'){await post('/api/suite/files/operation',{operation:'restore',root_id:state.rootId,receipt_id:state.trashReceipt});state.trashReceipt=null;toast('文件已恢复。');await render();}
      else if(action.startsWith('file-'))await fileOperation(action);
      else if(action==='memory-search')await memorySearch();
      else if(action==='memory-forget'){const answer=await modal('遗忘这条记录','<p class="caption">这条记录将不再出现在正常搜索和最近记录中。</p>','遗忘');if(answer){await post('/api/suite/memory/forget',{id:button.dataset.id});toast('已标记为遗忘。');await render();}}
      else if(['memory-start','memory-stop','qwen-start','qwen-stop','qwen-trigger'].includes(action)){const [component,verb]=action.split('-');const result=await post(`/api/suite/${component}/${verb}`);toast(result.detail||result.dependency_reason||'操作完成，已更新状态。',result.status==='needs_setup'||result.status==='failed');await render();}
      else if(action==='phone-mirror'||action==='phone-screenshot'){const serial=$('#phone-device').value;if(!serial){toast('请先选择已授权设备。',true);return;}if(action==='phone-mirror'){await post('/api/suite/phone/mirror',{serial,max_size:1280,max_fps:60,no_audio:true});toast('镜像启动请求已完成。');}else{const image=await post('/api/suite/phone/screenshot',{serial});if(!image.base64||!['image/png','image/jpeg'].includes(image.mime))throw new Error('设备没有返回可显示的截图。');$('#phone-screen').innerHTML=`<img alt="你主动截取的手机屏幕" src="data:${image.mime};base64,${escape(image.base64)}">`;}}
      else if(action==='phone-stop'){await post('/api/suite/phone/stop');toast('已停止工作台启动的镜像。');await render();}
      else if(action==='process-control')await processControl(button);
      else if(action==='component-control'){const result=await post(`/api/suite/components/${encodeURIComponent(button.dataset.id)}/${button.dataset.control}`);toast(result.reason||result.dependency_reason||'运行状态已更新。',result.state==='needs_setup'||result.state==='failed');await render();}
    });
  });
  document.addEventListener('keydown',event=>{const row=event.target.closest('.file-row');if(row&&(event.key==='Enter'||event.key===' ')){event.preventDefault();busy(null,()=>selectFile(Number(row.dataset.index)));}});
  $('#refresh-button').addEventListener('click',()=>render());
  window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
  async function init() {
    const bootstrap=await api('/api/suite/bootstrap');
    state.bootstrap=bootstrap;state.csrf=bootstrap.csrf||bootstrap.csrf_token||bootstrap.csrfToken||'';
    $('#version').textContent=String(state.bootstrap.version||'LOCAL');
    navigate(location.hash.slice(1)||'workos');
  }
  init().catch(error=>{ $('#content').innerHTML=`<div class="panel">${empty('连接未完成',friendly(error),'<button class="button secondary" data-action="refresh">重试</button>')}</div>`; });
})();
