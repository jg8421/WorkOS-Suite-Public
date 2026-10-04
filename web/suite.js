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
  const state = {view:'workos',csrf:'',bootstrap:{components:[],roots:[],repositories:[]},projects:[],ideas:[],rootId:store.get('suite.root'),path:'',selected:null,files:[],processes:[],renderId:0,trashReceipt:null,
    fileSelection:new Set(),fileFocus:-1,fileAnchor:-1,fileLocation:'',fileHistory:[],fileHistoryIndex:-1,fileQuery:'',fileSearchMode:'folder',fileSearchToken:0,filePreviewToken:0,fileClipboard:null,fileBusy:false,fileNavigating:null,fileNavigationToken:0,trashReceipts:[]};
  let toastTimer,qwenStatusTimer;
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
    clearTimeout(qwenStatusTimer);
    // A late directory response cannot retain a lock or change another page.
    state.fileNavigating=null;++state.fileNavigationToken;++state.filePreviewToken;
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
  async function nativeTools() {
    if(state.nativeTools&&Date.now()-state.nativeToolsRead<15000)return;
    try{const data=await api('/api/suite/native-tools');state.nativeTools=array(data,'tools');}
    catch(error){state.nativeTools=[];state.nativeToolsError=friendly(error);}
    state.nativeToolsRead=Date.now();
  }
  function nativeToolButton(id) {
    const tool=state.nativeTools?.find(tool=>tool.id===id),title=id==='files'?'打开完整文件工作台':'打开完整进程管理器';
    return `<div class="native-tool-entry"><button class="button secondary small" data-action="native-tool-launch" data-tool-id="${id}" ${tool?.can_launch?'':'disabled'} title="${escape(tool?.reason||'完整窗口使用原工具设置；不自动关闭已有应用。')}">${title} ↗</button>${!tool?.can_launch?`<span class="caption">${escape(tool?.reason||state.nativeToolsError||'完整窗口尚未就绪，请刷新状态。')}</span>`:id==='files'?'<span class="caption">完整窗口使用原工具的目录设置，可在窗口内选择目录。</span>':''}</div>`;
  }
  function fileLocation() { return `${state.rootId}:${state.path}`; }
  function fileLocked() { return state.fileBusy||!!state.fileNavigating; }
  function fileHistorySnapshot() { return {rootId:state.rootId,path:state.path,selection:[...state.fileSelection],focus:state.fileFocus}; }
  function rememberFileLocation() {
    if(state.fileHistoryIndex>=0)state.fileHistory[state.fileHistoryIndex]=fileHistorySnapshot();
  }
  async function loadFileFolder(path,rootId=state.rootId,historyIndex=null) {
    if(fileLocked()||!rootId)return;
    if(historyIndex===null&&rootId===state.rootId&&path===state.path&&!state.fileQuery){focusFileList();return;}
    const token=++state.fileSearchToken,generation=state.renderId,navigationToken=++state.fileNavigationToken;
    state.fileNavigating={token:navigationToken};++state.filePreviewToken;updateFileSelection();
    try {
      const data=await api(`/api/suite/files?root_id=${encodeURIComponent(rootId)}&path=${encodeURIComponent(path)}`);
      if(state.view!=='files'||generation!==state.renderId||token!==state.fileSearchToken||state.fileNavigating?.token!==navigationToken)return;
      rememberFileLocation();state.rootId=rootId;state.path=data.path??path;store.set('suite.root',rootId);
      state.fileQuery='';state.fileSearchMode='folder';state.fileLocation=fileLocation();
      const previous=historyIndex===null?null:state.fileHistory[historyIndex];
      state.fileSelection=new Set(previous?.selection||[]);state.fileFocus=previous?.focus??-1;state.fileAnchor=state.fileFocus;
      if(historyIndex===null){state.fileHistory=state.fileHistory.slice(0,state.fileHistoryIndex+1);state.fileHistory.push(fileHistorySnapshot());state.fileHistoryIndex=state.fileHistory.length-1;}
      else state.fileHistoryIndex=historyIndex;
      await renderFiles(++state.renderId,data);focusFileList();
    } finally {
      if(state.fileNavigating?.token===navigationToken){state.fileNavigating=null;if(state.view==='files'){if($('#file-root'))$('#file-root').value=state.rootId;if($('#file-path'))$('#file-path').value=state.path;updateFileSelection();}}
    }
  }
  async function fileNavigate(direction) {
    if(direction==='up')return loadFileFolder(state.path.split('/').filter(Boolean).slice(0,-1).join('/'));
    const index=state.fileHistoryIndex+(direction==='back'?-1:1),location=state.fileHistory[index];
    if(location)return loadFileFolder(location.path,location.rootId,index);
  }
  async function renderFiles(generation,provided=null) {
    await nativeTools();if(!current(generation))return;
    if (!roots().some(root=>(root.id||root.root_id)===state.rootId)) { state.rootId=roots()[0]?.id||roots()[0]?.root_id||'';state.path=''; }
    let data=provided||{entries:[]};
    if(state.rootId&&!provided)data=await api(`/api/suite/files${state.fileSearchMode==='tree'&&state.fileQuery?'/search':''}?root_id=${encodeURIComponent(state.rootId)}&path=${encodeURIComponent(state.path)}&q=${encodeURIComponent(state.fileQuery)}`);
    if(!current(generation))return;
    if(state.fileLocation!==fileLocation()){
      state.fileSelection.clear();state.fileFocus=-1;state.fileAnchor=-1;state.fileQuery='';state.fileSearchMode='folder';state.fileLocation=fileLocation();
      if(!state.fileHistory.length){state.fileHistory.push(fileHistorySnapshot());state.fileHistoryIndex=0;}
    }
    state.files=array(data,'entries');state.path=data.path??state.path;
    state.fileSelection=new Set([...state.fileSelection].filter(path=>state.files.some(file=>file.path===path)));
    state.fileFocus=Math.min(state.fileFocus,state.files.length-1);
    const root=roots().find(root=>(root.id||root.root_id)===state.rootId);
    $('#content').innerHTML = `<div class="panel file-workbench" id="file-workbench"><div class="panel-header"><div><h2>文件工作台</h2><p>单击选择与预览 · 双击 / Enter 打开 · Ctrl / Shift 多选</p></div><div class="button-row">${nativeToolButton('files')}<button class="button secondary small" data-action="file-shortcuts">快捷键</button><button class="button secondary small" data-action="root-add">＋ 添加目录</button></div></div><div class="toolbar"><div class="toolbar-left file-navigation"><button class="button secondary small" data-action="file-back" aria-label="后退" title="Alt + ←" ${state.fileHistoryIndex>0?'':'disabled'}>←</button><button class="button secondary small" data-action="file-forward" aria-label="前进" title="Alt + →" ${state.fileHistoryIndex<state.fileHistory.length-1?'':'disabled'}>→</button><button class="button secondary small" data-action="file-up" title="Alt + ↑" ${state.path?'':'disabled'}>↑ 上一级</button><select id="file-root" aria-label="文件目录">${roots().length?rootOptions():'<option>请先添加目录</option>'}</select></div><div class="toolbar-right file-search-tools"><input id="file-search" type="search" value="${escape(state.fileQuery)}" placeholder="输入筛选 · Enter 搜索子目录与内容" aria-label="搜索文件"><button class="button secondary small" data-action="file-search">深入搜索</button><button class="button small" data-action="file-mkdir" ${state.rootId?'':'disabled'}>＋ 新建文件夹</button></div></div><div class="file-address"><label for="file-path">位置</label><input id="file-path" value="${escape(state.path)}" placeholder="所选根目录内的路径" aria-label="目录相对路径"><button class="button secondary small" data-action="file-go">转到</button></div><div class="breadcrumb"><button data-action="file-root">${escape(root?.label||'根目录')}</button>${state.path.split('/').filter(Boolean).map((part,index,parts)=>`<span>/</span><button data-action="file-folder" data-path="${escape(parts.slice(0,index+1).join('/'))}">${escape(part)}</button>`).join('')}<span id="file-current-path" class="hidden">${escape(state.path)}</span></div><div class="file-selection-bar"><span id="file-selection-status" role="status"></span><div class="button-row"><button class="button secondary small" data-action="file-copy" title="Ctrl + C">复制</button><button class="button secondary small" data-action="file-cut" title="Ctrl + X">剪切</button><button class="button secondary small" data-action="file-paste" title="Ctrl + V">粘贴</button><button class="button secondary small" data-action="file-rename" title="F2">重命名</button><button class="button danger small" data-action="file-trash" title="Delete · 可撤销">回收</button></div></div><div id="file-search-status" class="caption" role="status">${fileSearchStatus(data)}</div><div class="file-layout"><div class="file-list-panel"><div class="table-scroll file-table-scroll"><table id="file-table" role="grid" aria-label="文件列表" aria-multiselectable="true" tabindex="0"><thead><tr><th><input type="checkbox" id="file-select-all" aria-label="全选当前列表"></th><th>名称</th><th>大小</th><th>修改时间</th><th></th></tr></thead><tbody id="file-rows">${fileRows()}</tbody></table></div><div id="file-empty" ${state.files.length?'class="hidden"':''}>${empty(state.rootId?'没有匹配文件':'从一个目录开始',state.rootId?'清空搜索或切换目录；也可以新建文件夹。':'添加你准备使用的目录。')}</div></div><div class="preview" id="file-preview" tabindex="0" aria-label="文件预览">${empty('选择文件，立即预览','↑ / ↓ 切换预览；Enter 或双击在原应用打开。')}</div></div></div><div id="file-undo" class="banner info ${state.trashReceipts.length?'':'hidden'}">文件已移至回收区。<button class="link-button" data-action="file-restore">撤销上次删除 · Ctrl + Z</button></div>`;
    $('#file-root').addEventListener('change',event=>{const rootId=event.target.value;event.target.value=state.rootId;busy(null,()=>loadFileFolder('',rootId));});
    $('#file-search').addEventListener('input',()=>{if(!fileComposing)busy(null,()=>searchFiles(false));});
    $('#file-search').addEventListener('compositionend',()=>busy(null,()=>searchFiles(false)));
    $('#file-search').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing&&!fileComposing&&event.keyCode!==229&&Date.now()-fileCompositionEnd>100){event.preventDefault();busy(null,()=>searchFiles(true));}});
    $('#file-path').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing&&!fileComposing&&event.keyCode!==229&&Date.now()-fileCompositionEnd>100){event.preventDefault();busy(null,()=>loadFileFolder(event.target.value.trim().replace(/\\/g,'/')));}});
    $('#file-select-all').addEventListener('change',event=>selectAllFiles(event.target.checked));
    updateFileSelection();if(state.fileSelection.size)busy(null,previewFileSelection);
  }
  function fileSearchStatus(data={}) {
    const scope=state.fileSearchMode==='tree'?'子目录名称与文档内容':'当前目录名称';
    return `${escape(scope)} · ${state.files.length} 项${data.truncated?' · 已达到搜索范围上限，请缩小目录或关键词':''}${Number.isFinite(data.scanned)?` · 检查 ${data.scanned} 项`:''}`;
  }
  function fileRows() { return state.files.map((file,index)=>`<tr class="file-row${state.fileSelection.has(file.path)?' selected':''}${state.fileClipboard?.mode==='cut'&&state.fileClipboard.rootId===state.rootId&&state.fileClipboard.items.some(item=>item.path===file.path)?' cut':''}" data-action="file-select" data-index="${index}" tabindex="${index===state.fileFocus?0:-1}" aria-selected="${state.fileSelection.has(file.path)}" aria-label="${escape(file.name)}"><td><input type="checkbox" data-file-checkbox="${index}" aria-label="选择 ${escape(file.name)}" ${state.fileSelection.has(file.path)?'checked':''} tabindex="-1"></td><td><span class="row-button"><span class="file-symbol">${file.is_dir?'▰':'▤'}</span><span>${escape(file.name)}${state.fileSearchMode==='tree'?`<small>${escape(file.path)}${file.match?` · ${escape(({name:'名称匹配',content:'内容匹配'})[file.match]||file.match)}`:''}</small>`:''}</span></span></td><td>${file.is_dir?'—':size(file.size)}</td><td>${escape(date(file.modified))}</td><td><button class="link-button" data-action="file-manage" data-index="${index}" aria-label="管理 ${escape(file.name)}">···</button></td></tr>`).join(''); }
  async function searchFiles(deep=true) {
    if(!state.rootId||fileLocked())return;
    const query=$('#file-search')?.value||'',rootId=state.rootId,path=state.path,generation=state.renderId,token=++state.fileSearchToken;
    state.fileQuery=query;state.fileSearchMode=deep&&query.trim()?'tree':'folder';
    const status=$('#file-search-status');if(status)status.textContent=deep&&query.trim()?'正在搜索子目录名称与文档内容…':'正在筛选…';
    const data=await api(`/api/suite/files${state.fileSearchMode==='tree'?'/search':''}?root_id=${encodeURIComponent(rootId)}&path=${encodeURIComponent(path)}&q=${encodeURIComponent(query)}`);
    if(state.view!=='files'||generation!==state.renderId||token!==state.fileSearchToken||rootId!==state.rootId||path!==state.path)return;
    state.files=array(data,'entries');state.fileSelection=new Set([...state.fileSelection].filter(path=>state.files.some(file=>file.path===path)));state.fileFocus=-1;state.fileAnchor=-1;
    $('#file-rows').innerHTML=fileRows();$('#file-empty').classList.toggle('hidden',!!state.files.length);$('#file-search-status').innerHTML=fileSearchStatus(data);updateFileSelection();await previewFileSelection();
  }
  function selectedFiles() { return state.files.filter(file=>state.fileSelection.has(file.path)); }
  function updateFileSelection() {
    const files=selectedFiles(),locked=fileLocked();state.selected=files[0]||null;
    document.querySelectorAll('.file-row').forEach(row=>{const index=Number(row.dataset.index),selected=state.fileSelection.has(state.files[index]?.path);row.classList.toggle('selected',selected);row.setAttribute('aria-selected',String(selected));row.tabIndex=index===state.fileFocus?0:-1;const checkbox=$('input',row);if(checkbox)checkbox.checked=selected;});
    const checkbox=$('#file-select-all');if(checkbox){checkbox.checked=!!state.files.length&&files.length===state.files.length;checkbox.indeterminate=files.length>0&&files.length<state.files.length;checkbox.disabled=locked;}
    for(const input of document.querySelectorAll('#file-root,#file-search,#file-path,[data-file-checkbox]'))input.disabled=locked;
    $('#file-workbench')?.setAttribute('aria-busy',String(locked));
    const status=$('#file-selection-status');if(status)status.textContent=`${files.length?`已选 ${files.length} 项`:`${state.files.length} 项`}${state.fileClipboard?` · ${state.fileClipboard.mode==='cut'?'待移动':'已复制'} ${state.fileClipboard.items.length} 项`:''}${state.fileNavigating?' · 正在切换目录…':state.fileBusy?' · 操作中…':''}`;
    for(const button of document.querySelectorAll('#file-workbench [data-action]')){
      const action=button.dataset.action;
      if(['file-copy','file-cut','file-trash','file-copy-to','file-move','file-import'].includes(action))button.disabled=locked||!files.length;
      else if(action==='file-rename'||action==='file-open')button.disabled=locked||files.length!==1;
      else if(action==='file-paste')button.disabled=locked||!state.fileClipboard?.items.length||!state.rootId;
      else if(action==='file-back')button.disabled=locked||state.fileHistoryIndex<=0;
      else if(action==='file-forward')button.disabled=locked||state.fileHistoryIndex>=state.fileHistory.length-1;
      else if(action==='file-up')button.disabled=locked||!state.path;
      else if(['file-root','file-folder','file-go','file-mkdir','file-search','file-select','file-manage'].includes(action))button.disabled=locked||!state.rootId;
      else if(action==='file-restore')button.disabled=locked||!state.trashReceipts.length;
    }
  }
  function focusFileList() { const row=document.querySelector(`.file-row[data-index="${state.fileFocus}"]`);(row||$('#file-table'))?.focus({preventScroll:true});row?.scrollIntoView({block:'nearest'}); }
  function selectAllFiles(selected=true) { if(fileLocked())return;state.fileSelection=new Set(selected?state.files.map(file=>file.path):[]);if(selected&&state.fileFocus<0)state.fileFocus=0;state.fileAnchor=state.fileFocus;updateFileSelection();busy(null,previewFileSelection); }
  async function selectFile(index,options={}) {
    if(fileLocked())return;const file=state.files[index];if(!file)return;
    const previous=state.fileFocus;state.fileFocus=index;
    if(options.range){const anchor=state.fileAnchor>=0?state.fileAnchor:(previous>=0?previous:index);if(!options.toggle)state.fileSelection.clear();for(let i=Math.min(anchor,index);i<=Math.max(anchor,index);i++)state.fileSelection.add(state.files[i].path);}
    else if(options.toggle){if(state.fileSelection.has(file.path))state.fileSelection.delete(file.path);else state.fileSelection.add(file.path);state.fileAnchor=index;}
    else {state.fileSelection=new Set([file.path]);state.fileAnchor=index;}
    updateFileSelection();if(options.focus!==false)focusFileList();await previewFileSelection();
  }
  function fileActions() { return `<div class="button-row"><button class="button secondary small" data-action="file-rename">重命名 · F2</button><button class="button secondary small" data-action="file-copy">复制 · Ctrl C</button><button class="button secondary small" data-action="file-cut">剪切 · Ctrl X</button><button class="button secondary small" data-action="file-copy-to">复制到…</button><button class="button secondary small" data-action="file-move">移动到…</button><button class="button secondary small" data-action="file-import">送到项目</button><button class="button danger small" data-action="file-trash">移至回收区</button></div>`; }
  async function previewFileSelection() {
    const preview=$('#file-preview');if(!preview)return;const files=selectedFiles(),token=++state.filePreviewToken,rootId=state.rootId,generation=state.renderId;
    if(!files.length){preview.innerHTML=empty('选择文件，立即预览','↑ / ↓ 切换预览；Enter 或双击在原应用打开。');return;}
    if(files.length>1){preview.innerHTML=`<div class="panel-header"><h3>已选择 ${files.length} 项</h3></div><p class="caption">${files.filter(file=>file.is_dir).length} 个文件夹 · ${files.filter(file=>!file.is_dir).length} 个文件</p><ul class="file-selection-summary">${files.slice(0,25).map(file=>`<li>${escape(file.name)}</li>`).join('')}${files.length>25?`<li>另外 ${files.length-25} 项</li>`:''}</ul>${fileActions()}`;updateFileSelection();return;}
    const file=files[0];
    if(file.is_dir){preview.innerHTML=`<div class="panel-header"><h3>${escape(file.name)}</h3><button class="button secondary small" data-action="file-open">打开文件夹 · Enter</button></div>${empty('文件夹已选中','可以复制、剪切或进入；单击不会改变位置。')}${fileActions()}`;updateFileSelection();return;}
    preview.innerHTML=loading();
    try {
      const data=await api(`/api/suite/file-preview?root_id=${encodeURIComponent(rootId)}&path=${encodeURIComponent(file.path)}`);
      if(state.view!=='files'||generation!==state.renderId||token!==state.filePreviewToken||rootId!==state.rootId)return;
      let body='';
      if(data.type==='text')body=`<pre tabindex="0">${escape(data.content)}</pre>`;
      else if(data.type==='table'||(data.type==='archive'&&data.columns?.length))body=`${data.content?`<p class="caption">${escape(data.content)}</p>`:''}<div class="table-scroll"><table><thead><tr>${(data.columns||[]).map(column=>`<th>${escape(column)}</th>`).join('')}</tr></thead><tbody>${(data.rows||[]).map(row=>`<tr>${(Array.isArray(row)?row:Object.values(row)).map(cell=>`<td>${escape(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
      else if(data.type==='archive')body=`<pre>${escape(typeof data.content==='string'?data.content:(data.entries||data.rows||[]).map(entry=>typeof entry==='string'?entry:(entry.name||entry.path||'')).join('\n'))}</pre>`;
      else body=empty('使用原应用查看','可以在原应用打开，或下载原文件。');
      preview.innerHTML=`<div class="panel-header"><h3>${escape(file.name)}</h3><div class="button-row"><button class="button secondary small" data-action="file-open">原应用打开</button><a class="button secondary small" href="/api/suite/file?root_id=${encodeURIComponent(rootId)}&path=${encodeURIComponent(file.path)}" download>下载</a></div></div>${body}${fileActions()}`;updateFileSelection();
    } catch(error) {if(token===state.filePreviewToken&&generation===state.renderId){preview.innerHTML=`<h3>${escape(file.name)}</h3>${empty('预览暂不可用',friendly(error))}<button class="button secondary small" data-action="file-open">原应用打开</button>${fileActions()}`;updateFileSelection();}}
  }
  async function openSelectedFile() {
    if(fileLocked())return;
    const files=selectedFiles();if(files.length!==1){toast('请选择一个文件或文件夹打开。',true);return;}
    return openFileTarget(files[0],state.rootId);
  }
  async function openFileTarget(file,rootId) {
    if(fileLocked()||!file||state.view!=='files')return;
    if(file.is_dir)return loadFileFolder(file.path,rootId);
    state.fileBusy=true;updateFileSelection();
    try{const result=await post('/api/suite/files/open',{root_id:rootId,path:file.path});toast(result.detail||'已交给原应用打开。');}
    finally{state.fileBusy=false;if(state.view==='files')updateFileSelection();}
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
  function qwenActivity(status) {
    const listening=status.listening===true,paused=status.paused===true||status.status==='paused',starting=status.status==='starting';
    return {label:starting?'连接准备中':paused?'监听已暂停':listening?'正在监听通话':status.status==='needs_setup'?'需要配置':'监听未启动',active:listening,detail:status.detail||status.dependency_reason||'请检查千问客户端与监听服务。'};
  }
  function qwenStatusMarkup(status) {
    const activity=qwenActivity(status),apps=status.apps||[],name=key=>apps.find(app=>app.key===key)?.label||key;
    const calls=(status.active_calls||[]).map(name),pending=(status.pending||[]).map(name);
    const recording=status.recording_verified===true?(status.recording?'已确认：正在录音':'当前未录音'):'录音状态尚未核实';
    const panelURL=qwenPanelURL(status);
    return `<div class="status-card"><span class="status-symbol">${activity.active?'●':'◇'}</span><div><h3><span>${escape(activity.label)}</span> <span class="pill ${activity.active?'':'warn'}">${status.source==='original'?'已连接原监听服务':'Suite 监听服务'}</span></h3><p>${escape(activity.detail)}</p><p><strong>${escape(recording)}</strong>${calls.length?` · 通话检测：${escape(calls.join('、'))}`:''}${pending.length?` · 等待触发：${escape(pending.join('、'))}`:''}</p>${status.hotkey?`<p>千问录音快捷键：${escape(status.hotkey)}</p>`:''}${status.error?`<p class="qwen-error">${escape(status.error)}</p>`:''}${status.control_note?`<p>${escape(status.control_note)}</p>`:''}<p class="caption">${status.source==='original'?'使用这台机器已有的监听与设置，不会再启动第二套。暂停监听不会结束千问中的录音。':'启动监听会监测你启用的通话应用；停止监听不会结束千问内的录音。'}</p>${panelURL?`<a class="button secondary small" id="qwen-original-panel" href="${escape(panelURL)}" target="_blank" rel="noopener noreferrer">打开原千问完整面板 ↗</a>`:''}</div></div>`;
  }
  function qwenPanelURL(status) {
    if(status.source!=='original'||typeof status.panel_url!=='string')return '';
    try{const url=new URL(status.panel_url);return url.protocol==='http:'&&url.hostname==='127.0.0.1'&&Number(url.port)>0&&Number(url.port)<=65535&&url.pathname==='/'&&!url.search&&!url.hash&&!url.username&&!url.password?url.href:'';}catch{return '';}
  }
  function paintQwenStatus(status) {
    const panel=$('#qwen-live-status');if(!panel)return;panel.innerHTML=qwenStatusMarkup(status);
    const start=$('[data-action="qwen-start"]'),stop=$('[data-action="qwen-stop"]'),trigger=$('[data-action="qwen-trigger"]');
    if(start){start.textContent=status.paused?'恢复监听':'启动监听';start.disabled=status.can_start===false||status.listening===true;}
    if(stop){stop.textContent=status.source==='original'?'暂停监听':'停止监听';stop.disabled=status.can_stop===false||!status.running||status.paused===true;}
    if(trigger)trigger.disabled=status.can_trigger===false||status.status==='starting';
    // A listener may become discoverable after the first paint. Refresh its
    // nonsecret controls only while no user draft exists; polling never edits a draft.
    const config=status.configuration;
    if(config&&!state.qwenConfigDirty&&JSON.stringify(config)!==state.qwenShownConfig){
      state.qwenShownConfig=JSON.stringify(config);
      const apps=status.apps?.length?status.apps:Array.isArray(config.apps)?config.apps:[];
      const appPanel=$('#qwen-config-form .qwen-apps');if(appPanel)appPanel.innerHTML=apps.map(app=>`<label><input type="checkbox" data-qwen-app="${escape(app.key)}" ${app.enabled?'checked':''}>${escape(app.label)}</label>`).join('');
      for(const [id,value] of [['qwen-hotkey',config.hotkey],['qwen-poll',config.poll_interval_sec],['qwen-debounce',config.start_debounce_sec],['qwen-cooldown',config.retrigger_cooldown_sec]])if($('#'+id)&&value!==undefined)$('#'+id).value=value;
      if($('#qwen-config-origin'))$('#qwen-config-origin').textContent=config.source==='original'?'当前使用原服务的配置。保存后直接更新原监听，不创建第二套配置。':'设置只修改监听规则，不会启动录音。';
    }
    $('#qwen-status-time').textContent=`状态更新于 ${new Date().toLocaleTimeString('zh-CN')} · 每 3 秒读取一次`;
  }
  function scheduleQwenStatus(generation) {
    clearTimeout(qwenStatusTimer);if(state.view!=='qwen'||!current(generation))return;
    qwenStatusTimer=setTimeout(async()=>{
      if(state.view!=='qwen'||!current(generation))return;
      try{const status=await api('/api/suite/qwen/status');if(state.view==='qwen'&&current(generation))paintQwenStatus(status);}
      catch(error){if(state.view==='qwen'&&current(generation))$('#qwen-status-time').textContent=`状态暂未更新：${friendly(error)}。将继续尝试连接。`;}
      finally{scheduleQwenStatus(generation);}
    },3000);
  }
  function qwenFormConfiguration() {
    const apps={};for(const input of document.querySelectorAll('[data-qwen-app]'))apps[input.dataset.qwenApp]={enabled:input.checked};
    return {apps,trigger:{hotkey:$('#qwen-hotkey').value.trim()},poll_interval_sec:Number($('#qwen-poll').value),start_debounce_sec:Number($('#qwen-debounce').value),retrigger_cooldown_sec:Number($('#qwen-cooldown').value)};
  }
  async function renderQwen(generation) {
    const [status,recordings]=await Promise.all([api('/api/suite/qwen/status'),api('/api/suite/recordings')]);if(!current(generation))return;
    const items=array(recordings,'recordings'),saved=status.configuration||{},configuration={...saved,...state.qwenConfigDraft},apps=status.apps?.length?status.apps:Array.isArray(saved.apps)?saved.apps:Object.keys(saved.apps||{}).map(key=>({key,label:key,enabled:saved.apps[key]?.enabled}));
    $('#content').innerHTML=`<div class="hero"><div><h2>通话监听，录音与整理。</h2><p>显示这台机器实际监听状态；原监听服务可以继续使用，录音仍由千问客户端完成。</p></div><div class="hero-mark">≋</div></div><div class="panel"><div class="panel-header"><h2>千问自动录音</h2><div class="button-row"><button class="button secondary" data-action="qwen-start">启动监听</button><button class="button secondary" data-action="qwen-stop">暂停监听</button></div></div><div id="qwen-live-status" role="status">${qwenStatusMarkup(status)}</div><div class="button-row"><button class="button" data-action="qwen-trigger">手动触发一次</button><span class="caption">发送触发请求后，以实际录音状态为准。</span></div><p class="caption" id="qwen-status-time"></p><details class="qwen-settings" ${state.qwenConfigDirty?'open':''}><summary>监听应用、快捷键与诊断</summary><form id="qwen-config-form"><p class="caption" id="qwen-config-origin">${saved.source==='original'?'当前使用原服务的配置。保存后直接更新原监听，不创建第二套配置。':'设置只修改监听规则，不会启动录音。'}${state.qwenConfigDirty?' · 补充设置尚未保存。':''}</p><div class="qwen-apps">${apps.map(app=>`<label><input type="checkbox" data-qwen-app="${escape(app.key)}" ${configuration.apps?.[app.key]?.enabled??app.enabled?'checked':''}>${escape(app.label)}</label>`).join('')||'<p class="caption">尚未读取到应用设置，请重试连接。</p>'}</div><div class="form-field"><label for="qwen-hotkey">千问客户端的实际录音快捷键</label><input id="qwen-hotkey" value="${escape(configuration.trigger?.hotkey||configuration.hotkey||status.hotkey||'')}" placeholder="填写与你的千问设置一致的组合键" maxlength="80" required><span class="caption">例如 rightctrl+/；必须与你在千问客户端设置的组合键一致。</span></div><details><summary class="caption">检测与防重复间隔</summary><div class="qwen-timing"><div class="form-field"><label for="qwen-poll">检测间隔（秒）</label><input id="qwen-poll" type="number" min="0.5" max="30" step="0.1" value="${escape(configuration.poll_interval_sec??2)}" required></div><div class="form-field"><label for="qwen-debounce">持续通话多久后触发（秒）</label><input id="qwen-debounce" type="number" min="0" max="120" step="1" value="${escape(configuration.start_debounce_sec??3)}" required></div><div class="form-field"><label for="qwen-cooldown">再次触发间隔（秒）</label><input id="qwen-cooldown" type="number" min="0" max="3600" step="1" value="${escape(configuration.retrigger_cooldown_sec??30)}" required></div></div><p class="caption">${configuration.require_playback?'通话检测还要求播放音频；仅启动应用不代表进入通话。':'按启用的应用通话检测规则判断；没有检测到通话时不自动触发。'}</p></details><div class="button-row"><button class="button secondary" type="submit">保存监听设置</button><span id="qwen-config-status" class="caption">${state.qwenConfigDirty?'设置尚未保存':''}</span></div></form></details><p class="caption">打开此页只读取状态。原服务会按既有设置继续监听；离开 Suite 不会关闭原监听。</p></div><div class="panel"><div class="panel-header"><div><h2>录音归档 <span class="pill">${items.length}</span></h2><p>读取已有归档，资料可在文件模块中关联项目。</p></div><button class="button secondary small" data-action="refresh">刷新归档</button></div>${items.length?`<div class="table-scroll"><table><thead><tr><th>录音</th><th>文件</th><th>大小</th><th>修改时间</th></tr></thead><tbody>${items.map(item=>`<tr><td>${escape(item.name)}</td><td>${escape(Array.isArray(item.files)?item.files.length:(item.files??'—'))}</td><td>${size(item.size_bytes)}</td><td>${escape(date(item.modified_at))}</td></tr>`).join('')}</tbody></table></div>`:empty('暂无录音归档','这里只显示实际归档；触发请求成功不等于录音或归档成功。')}</div>`;
    state.qwenShownConfig=null;paintQwenStatus(status);scheduleQwenStatus(generation);
    $('#qwen-config-form').addEventListener('input',()=>{state.qwenConfigDraft=qwenFormConfiguration();state.qwenConfigDirty=true;$('#qwen-config-status').textContent='设置尚未保存';});
    $('#qwen-config-form').addEventListener('submit',event=>{event.preventDefault();busy(event.submitter,async()=>{const draft=qwenFormConfiguration();state.qwenConfigDraft=draft;state.qwenConfigDirty=true;await post('/api/suite/qwen/config',draft);state.qwenConfigDraft=null;state.qwenConfigDirty=false;toast('监听设置已保存。');if(state.view==='qwen')await render();});});
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
    const [data]=await Promise.all([api('/api/suite/processes'),nativeTools()]);if(!current(generation))return;state.processes=array(data,'processes');const system=data.system||{};
    $('#content').innerHTML=`<div class="stats-grid"><div class="stat"><div class="stat-label">CPU</div><div class="stat-value">${numeric(system.cpu_percent??system.cpu,'%')}</div><div class="stat-note">系统实时读数</div></div><div class="stat"><div class="stat-label">内存使用</div><div class="stat-value">${numeric(system.memory_percent,'%')}</div><div class="stat-note">${numeric(system.memory_used_mb,' MB',0)}</div></div><div class="stat"><div class="stat-label">进程数</div><div class="stat-value">${state.processes.length}</div><div class="stat-note">本次可读取的进程</div></div><div class="stat"><div class="stat-label">GPU / 温度</div><div class="stat-value">—</div><div class="stat-note">未接入真实硬件传感器</div></div></div><div class="panel"><div class="panel-header"><div><h2>进程管理</h2><p>每次操作验证 PID 与创建时间；系统及工作台进程受到保护。</p></div><div class="button-row">${nativeToolButton('processes')}<button class="button secondary small" data-action="refresh">刷新进程</button></div></div><div class="toolbar"><input id="process-search" type="search" placeholder="按进程名称搜索" aria-label="搜索进程"><span class="refresh-time">CPU 未采样时显示 —；不自动刷新或结束进程。</span></div><div class="table-scroll"><table><thead><tr><th>进程</th><th>PID</th><th>CPU</th><th>内存</th><th>操作</th></tr></thead><tbody id="process-rows">${processRows(state.processes)}</tbody></table></div></div>`;
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
  const newFileRequest = () => crypto.randomUUID?crypto.randomUUID():`file-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  function minimalFileSelection(files) { return files.filter(file=>!files.some(parent=>parent!==file&&parent.is_dir&&file.path.toLowerCase().startsWith(parent.path.toLowerCase()+'/'))); }
  async function fileOperation(action) {
    if(fileLocked())return;state.fileBusy=true;updateFileSelection();
    try { return await performFileOperation(action); }
    finally {state.fileBusy=false;if(state.view==='files')updateFileSelection();}
  }
  async function copySelectedFiles(mode) {
    const files=minimalFileSelection(selectedFiles());if(!files.length){toast('请先选择文件或文件夹。',true);return;}
    const clipboard={rootId:state.rootId,mode,items:files.map(file=>({...file,requests:new Map()}))};state.fileClipboard=clipboard;
    document.querySelectorAll('.file-row').forEach(row=>row.classList.toggle('cut',mode==='cut'&&state.fileSelection.has(state.files[Number(row.dataset.index)]?.path)));
    updateFileSelection();
    try {const result=await post('/api/suite/files/clipboard',{root_id:clipboard.rootId,paths:clipboard.items.map(file=>file.path),mode});toast(result.system_clipboard?`${mode==='cut'?'已剪切':'已复制'} ${files.length} 项；可在这里或资源管理器粘贴。`:`${mode==='cut'?'已剪切':'已复制'} ${files.length} 项；在目标目录按 Ctrl + V。${result.detail||''}`);}
    catch(error){toast(`已保留 ${files.length} 项，可在工作台内粘贴。系统剪贴板暂不可用：${friendly(error)}`,true);}
  }
  function availableCopyName(file,names) {
    if(!names.has(file.name.toLowerCase()))return file.name;
    const dot=file.is_dir?-1:file.name.lastIndexOf('.'),suffix=dot>0?file.name.slice(dot):'',stem=dot>0?file.name.slice(0,dot):file.name;
    let name=`${stem} - 副本${suffix}`,number=2;while(names.has(name.toLowerCase()))name=`${stem} - 副本 (${number++})${suffix}`;return name;
  }
  async function transferFiles(clipboard,destinationRoot=state.rootId,destinationPath=state.path) {
    if(!clipboard?.items.length){toast('先选择文件，再按 Ctrl + C 或 Ctrl + X。',true);return;}
    const data=await api(`/api/suite/files?root_id=${encodeURIComponent(destinationRoot)}&path=${encodeURIComponent(destinationPath)}`),names=new Set(array(data,'entries').map(file=>file.name.toLowerCase())),remaining=[],done=[];
    const signature=JSON.stringify([clipboard.mode,destinationRoot,destinationPath]);let errorMessage='';
    for(const file of clipboard.items){
      const initial=pathJoin(destinationPath,file.name);
      if(clipboard.rootId===destinationRoot&&(initial.toLowerCase()===file.path.toLowerCase()&&clipboard.mode==='cut'||file.is_dir&&`${destinationPath}/`.toLowerCase().startsWith(`${file.path}/`.toLowerCase()))){remaining.push(file);errorMessage='目标与原地址相同或位于原文件夹内，原文件已保留。';continue;}
      if(clipboard.mode==='cut'&&names.has(file.name.toLowerCase())){remaining.push(file);errorMessage='目标已有同名文件，原文件已保留。';continue;}
      let request=file.requests?.get(signature);
      if(!request){const name=clipboard.mode==='copy'?availableCopyName(file,names):file.name;request={operation:clipboard.mode==='cut'?'move':'copy',root_id:clipboard.rootId,path:file.path,destination_root_id:destinationRoot,target:pathJoin(destinationPath,name),request_id:newFileRequest()};file.requests?.set(signature,request);}
      try {await post('/api/suite/files/operation',request);file.requests?.delete(signature);names.add(request.target.split('/').pop().toLowerCase());done.push(request.target);}
      catch(error){remaining.push(file);errorMessage=friendly(error);}
    }
    if(state.fileClipboard===clipboard){if(clipboard.mode==='cut'||remaining.length)state.fileClipboard=remaining.length?{...clipboard,items:remaining}:null;}
    if(state.rootId===destinationRoot&&state.path===destinationPath&&state.view==='files'){state.fileSelection=new Set(done);await render();focusFileList();}
    toast(`${done.length?`已${clipboard.mode==='cut'?'移动':'复制'} ${done.length} 项。`:''}${remaining.length?`${remaining.length} 项未完成，保留原文件和待粘贴项。${errorMessage}`:''}`,!!remaining.length);
  }
  async function restoreFiles() {
    const batch=state.trashReceipts.at(-1);if(!batch)return;
    const remaining=[];let restored=0,errorMessage='';
    for(const item of batch){try{await post('/api/suite/files/operation',{operation:'restore',root_id:item.rootId,receipt_id:item.id,request_id:item.restoreRequest||(item.restoreRequest=newFileRequest())});restored++;}catch(error){remaining.push(item);errorMessage=friendly(error);}}
    if(remaining.length)state.trashReceipts[state.trashReceipts.length-1]=remaining;else state.trashReceipts.pop();
    if(state.view==='files')await render();toast(`已恢复 ${restored} 项。${remaining.length?`${remaining.length} 项未恢复；原地址未覆盖。${errorMessage}`:''}`,!!remaining.length);
  }
  async function performFileOperation(action) {
    const operation=action.replace('file-',''),files=minimalFileSelection(selectedFiles()),rootId=state.rootId,folder=state.path;
    if(operation==='restore')return restoreFiles();
    if(operation==='paste')return transferFiles(state.fileClipboard);
    if(operation==='copy'||operation==='cut')return copySelectedFiles(operation);
    if(operation!=='mkdir'&&!files.length){toast('请先选择文件或文件夹。',true);return;}
    if(operation==='trash'){
      const answer=await modal(`将 ${files.length} 项移至回收区？`,`<p class="caption">文件与文件夹内容将保留在本机回收区。可以用 Ctrl + Z 撤销；不会永久删除。</p><ul class="file-selection-summary">${files.slice(0,12).map(file=>`<li>${escape(file.name)}</li>`).join('')}${files.length>12?`<li>另外 ${files.length-12} 项</li>`:''}</ul>`,'移至回收区');if(!answer)return;
      const receipts=[];let errorMessage='';
      for(const file of files){try{const result=await post('/api/suite/files/operation',{operation:'trash',root_id:rootId,path:file.path,request_id:newFileRequest()});receipts.push({id:result.receipt_id||result.id||result.receipt?.id,rootId});state.fileSelection.delete(file.path);}catch(error){errorMessage=friendly(error);break;}}
      if(receipts.length)state.trashReceipts.push(receipts);if(state.view==='files')await render();focusFileList();toast(`已移至回收区 ${receipts.length} 项。${errorMessage?`其他项目保留。${errorMessage}`:'Ctrl + Z 可撤销。'}`,!!errorMessage);return;
    }
    if(operation==='import'){
      if(files.some(file=>file.is_dir)){toast('请只选择需要导入的文件；文件夹可以进入后多选。',true);return;}
      await refreshProjects();if(!state.projects.length){toast('请先在 WorkOS 创建项目，再刷新项目列表。',true);return;}
      const answer=await modal('送到项目',`<p class="caption">已选 ${files.length} 个文件；原文件保留。</p><div class="form-field"><label for="dialog-project">选择项目</label><select id="dialog-project">${projectOptions('',false)}</select></div>`,'导入项目');if(!answer)return;
      await post('/api/suite/files/import',{root_id:rootId,paths:files.map(file=>file.path),project_id:$('#dialog-project').value});toast('已关联到项目资料。');return;
    }
    if(operation==='copy-to'||operation==='move'){
      const answer=await modal(operation==='move'?'移动到文件夹':'复制到文件夹',`<p class="caption">已选 ${files.length} 项。复制遇到重名会生成副本；移动不会覆盖已有文件。</p><div class="form-field"><label for="dialog-destination-root">目标目录</label><select id="dialog-destination-root">${rootOptions()}</select></div><div class="form-field"><label for="dialog-path">目标文件夹相对路径</label><input id="dialog-path" placeholder="留空表示所选根目录"></div>`);if(!answer)return;
      return transferFiles({rootId,mode:operation==='move'?'cut':'copy',items:files.map(file=>({...file,requests:new Map()}))},$('#dialog-destination-root').value,$('#dialog-path').value.trim().replace(/\\/g,'/'));
    }
    if(operation==='rename'&&files.length!==1){toast('F2 一次重命名一项；请先选择一个文件。',true);return;}
    const file=files[0],initial=operation==='rename'?file.name:'';
    const answerPromise=modal(operation==='mkdir'?'新建文件夹':'重命名',`<div class="form-field"><label for="dialog-path">${operation==='mkdir'?'文件夹名称':'新名称'}</label><input id="dialog-path" value="${escape(initial)}" required maxlength="255"></div><p class="caption">保留扩展名；名称不能包含路径分隔符。</p>`);
    const input=$('#dialog-path');setTimeout(()=>{input.focus();const dot=initial.lastIndexOf('.');input.setSelectionRange(0,file&&!file.is_dir&&dot>0?dot:initial.length);},60);
    const answer=await answerPromise;if(!answer)return;const target=$('#dialog-path').value.trim();
    if(!target||/[\\/:*?"<>|]/.test(target)||target==='.'||target==='..'){toast('请输入有效名称，不要包含路径或特殊字符。',true);return;}
    if(operation==='rename'&&target===file.name)return;
    const body={operation,root_id:rootId,path:operation==='mkdir'?pathJoin(folder,target):file.path,request_id:newFileRequest()};
    if(operation==='rename')body.target=pathJoin(file.path.split('/').slice(0,-1).join('/'),target);
    await post('/api/suite/files/operation',body);state.fileSelection=new Set([body.target||body.path]);toast(operation==='rename'?'已重命名。':'文件夹已创建。');if(state.view==='files'){await render();state.fileFocus=state.files.findIndex(item=>item.path===(body.target||body.path));updateFileSelection();focusFileList();}
  }
  function showFileShortcuts() {
    return modal('文件工作台快捷键',`<dl class="shortcut-list">${[['↑ / ↓ · Home / End','移动选择并预览'],['Ctrl / Shift + 点击','逐项多选 / 连续多选'],['Shift + ↑ / ↓','扩展连续选择'],['Ctrl + A','全选当前列表'],['Enter / 双击','进入文件夹 / 原应用打开'],['Alt + ← / → / ↑','后退 / 前进 / 上一级'],['Backspace','上一级'],['Ctrl + C / X / V','复制 / 剪切 / 粘贴真实文件'],['F2','重命名所选一项'],['Delete','移至回收区，确认后执行'],['Ctrl + Z','撤销上次回收'],['Ctrl + F / L','搜索 / 目录地址'],['输入搜索 · Enter','本层实时筛选 / 递归名称与内容搜索'],['F5','刷新并保留选中项目'],['Esc','清除选择']].map(([keys,detail])=>`<dt>${escape(keys)}</dt><dd>${escape(detail)}</dd>`).join('')}</dl><p class="caption">文本输入、中文组词和弹窗中保留原有键盘行为。选中文字时 Ctrl + C 复制文字。网页缩放仍使用浏览器 Ctrl + / −。</p>`,'知道了');
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
    if(action==='dialog-cancel'){$('#action-dialog').close('cancel');return;}
    if(action==='navigate'){navigate(button.dataset.view);return;}
    if(action==='refresh'){render();return;}
    if(action==='workos-section'){const iframe=$('#workos-frame');if(iframe)iframe.src=`/workos/#${button.dataset.section}`;return;}
    if(action==='workos-navigation'){try{const style=$('#workos-frame')?.contentDocument?.getElementById('workos-suite-embed-style');if(style){style.disabled=!style.disabled;button.textContent=style.disabled?'收起原版导航':'完整导航';}}catch{toast('请在独立窗口使用完整导航。');}return;}
    if(action==='idea-original'){const panel=$('#idea-original-panel');panel.classList.toggle('hidden');const iframe=$('iframe',panel);if(!panel.classList.contains('hidden')&&!iframe.src)iframe.src=iframe.dataset.src;return;}
    busy(button.tagName==='BUTTON'?button:null,async()=>{
      if(action==='native-tool-launch'){const id=button.dataset.toolId;if(!['files','processes'].includes(id))return;const result=await post(`/api/suite/native-tools/${id}/launch`,{});toast(result.detail||'完整原版窗口已启动；使用原工具的设置与功能。');}
      else if(action==='refresh-projects'){await refreshProjects();$('#idea-project').innerHTML=projectOptions();toast('项目列表已刷新。');}
      else if(action==='idea-share'){await refreshProjects();if(!state.projects.length){toast('请先在 WorkOS 创建项目。',true);return;}const answer=await modal('送到项目笔记',`<div class="form-field"><label for="dialog-project">选择项目</label><select id="dialog-project">${projectOptions('',false)}</select></div>`,'创建项目笔记');if(answer){await post(`/api/suite/ideas/${encodeURIComponent(button.dataset.id)}/share`,{project_id:$('#dialog-project').value});toast('已创建项目笔记。');await render();}}
      else if(action==='idea-delete'){const answer=await modal('删除灵感','<p class="caption">删除这条收件箱灵感？已创建的项目笔记会保留。</p>','删除');if(answer){await api(`/api/suite/ideas/${encodeURIComponent(button.dataset.id)}`,{method:'DELETE'});toast('灵感已删除。');await render();}}
      else if(action==='root-add'){const answer=await modal('添加文件目录','<div class="form-field"><label for="dialog-root">目录完整路径</label><input id="dialog-root" placeholder="选择你希望在工作台使用的目录" required></div><div class="form-field"><label for="dialog-root-label">显示名称</label><input id="dialog-root-label" placeholder="例如：项目资料"></div>','添加目录');if(answer){await post('/api/suite/roots',{path:$('#dialog-root').value.trim(),label:$('#dialog-root-label').value.trim()});state.bootstrap=await api('/api/suite/bootstrap');await render();toast('目录已添加。');}}
      else if(action==='file-up'||action==='file-back'||action==='file-forward')await fileNavigate(action.replace('file-',''));
      else if(action==='file-root')await loadFileFolder('');
      else if(action==='file-folder')await loadFileFolder(button.dataset.path);
      else if(action==='file-go')await loadFileFolder($('#file-path').value.trim().replace(/\\/g,'/'));
      else if(action==='file-shortcuts')await showFileShortcuts();
      else if(action==='file-search')await searchFiles();
      else if(action==='file-select')await selectFile(Number(button.dataset.index),{toggle:event.ctrlKey||event.metaKey||!!event.target.closest('[data-file-checkbox]'),range:event.shiftKey});
      else if(action==='file-manage')await selectFile(Number(button.dataset.index));
      else if(action==='file-open')await openSelectedFile();
      else if(action.startsWith('file-'))await fileOperation(action);
      else if(action==='memory-search')await memorySearch();
      else if(action==='memory-forget'){const answer=await modal('遗忘这条记录','<p class="caption">这条记录将不再出现在正常搜索和最近记录中。</p>','遗忘');if(answer){await post('/api/suite/memory/forget',{id:button.dataset.id});toast('已标记为遗忘。');await render();}}
      else if(['memory-start','memory-stop','qwen-start','qwen-stop','qwen-trigger'].includes(action)){const [component,verb]=action.split('-');const result=await post(`/api/suite/${component}/${verb}`);toast(component==='qwen'&&verb==='trigger'?(result.recording_verified&&result.recording?'已确认千问正在录音。':'触发请求已发送；请查看实际录音状态。'):(result.detail||result.dependency_reason||'操作完成，已更新状态。'),result.status==='needs_setup'||result.status==='failed');await render();}
      else if(action==='phone-mirror'||action==='phone-screenshot'){const serial=$('#phone-device').value;if(!serial){toast('请先选择已授权设备。',true);return;}if(action==='phone-mirror'){await post('/api/suite/phone/mirror',{serial,max_size:1280,max_fps:60,no_audio:true});toast('镜像启动请求已完成。');}else{const image=await post('/api/suite/phone/screenshot',{serial});if(!image.base64||!['image/png','image/jpeg'].includes(image.mime))throw new Error('设备没有返回可显示的截图。');$('#phone-screen').innerHTML=`<img alt="你主动截取的手机屏幕" src="data:${image.mime};base64,${escape(image.base64)}">`;}}
      else if(action==='phone-stop'){await post('/api/suite/phone/stop');toast('已停止工作台启动的镜像。');await render();}
      else if(action==='process-control')await processControl(button);
      else if(action==='component-control'){const result=await post(`/api/suite/components/${encodeURIComponent(button.dataset.id)}/${button.dataset.control}`);toast(result.reason||result.dependency_reason||'运行状态已更新。',result.state==='needs_setup'||result.state==='failed');await render();}
    });
  });
  let fileComposing=false,fileCompositionEnd=0;
  document.addEventListener('compositionstart',()=>{fileComposing=true;});
  document.addEventListener('compositionend',()=>{fileComposing=false;fileCompositionEnd=Date.now();});
  $('#action-dialog form').addEventListener('submit',event=>{if(fileComposing||Date.now()-fileCompositionEnd<100)event.preventDefault();});
  document.addEventListener('dblclick',event=>{const row=event.target.closest('.file-row');if(state.view==='files'&&row&&!event.target.closest('button,input')&&!fileComposing&&!fileLocked()){event.preventDefault();const index=Number(row.dataset.index),file=state.files[index],rootId=state.rootId;busy(null,()=>selectFile(index));busy(null,()=>openFileTarget(file,rootId));}});
  document.addEventListener('keydown',event=>{
    if(state.view!=='files'||fileComposing||event.isComposing||event.keyCode===229||event.defaultPrevented||$('#action-dialog').open)return;
    const target=event.target;if(!(target instanceof Element)||target.closest('input,textarea,select,[contenteditable=""],[contenteditable="true"],[role="textbox"]'))return;
    if(!target.closest('#file-workbench,#file-undo')&&target!==document.body)return;
    const key=event.key,ctrl=event.ctrlKey||event.metaKey;let operation=null;
    if(fileLocked()){
      const command=ctrl&&!event.altKey&&['c','x','v','a','f','l','z','n'].includes(key.toLowerCase())||event.altKey&&!ctrl&&['ArrowLeft','ArrowRight','ArrowUp'].includes(key)||!ctrl&&!event.altKey&&['ArrowUp','ArrowDown','Home','End','F2','Delete','Backspace','F5','Escape',' ','Enter'].includes(key);
      if(command&&!(ctrl&&key.toLowerCase()==='c'&&window.getSelection()?.toString()))event.preventDefault();
      if(state.fileNavigating&&ctrl&&key.toLowerCase()==='v')toast('正在切换目录，请完成后再粘贴。');return;
    }
    if(event.altKey&&!ctrl){if(key==='ArrowLeft')operation=()=>fileNavigate('back');else if(key==='ArrowRight')operation=()=>fileNavigate('forward');else if(key==='ArrowUp')operation=()=>fileNavigate('up');}
    else if(ctrl&&!event.altKey){
      if(key.toLowerCase()==='c'&&!window.getSelection()?.toString())operation=()=>fileOperation('file-copy');
      else if(key.toLowerCase()==='x'&&!window.getSelection()?.toString())operation=()=>fileOperation('file-cut');
      else if(key.toLowerCase()==='v')operation=()=>fileOperation('file-paste');
      else if(key.toLowerCase()==='a')operation=()=>selectAllFiles();
      else if(key.toLowerCase()==='f')operation=()=>{$('#file-search')?.focus();$('#file-search')?.select();};
      else if(key.toLowerCase()==='l')operation=()=>{$('#file-path')?.focus();$('#file-path')?.select();};
      else if(key.toLowerCase()==='z'&&state.trashReceipts.length)operation=()=>fileOperation('file-restore');
      else if(key.toLowerCase()==='n'&&event.shiftKey)operation=()=>fileOperation('file-mkdir');
    } else if(!event.altKey){
      if(['ArrowUp','ArrowDown','Home','End'].includes(key)){const index=key==='Home'?0:key==='End'?state.files.length-1:state.fileFocus<0?(key==='ArrowUp'?state.files.length-1:0):Math.max(0,Math.min(state.files.length-1,state.fileFocus+(key==='ArrowDown'?1:-1)));operation=()=>selectFile(index,{range:event.shiftKey});}
      else if(key==='F2')operation=()=>fileOperation('file-rename');
      else if(key==='Delete')operation=()=>fileOperation('file-trash');
      else if(key==='Backspace')operation=()=>fileNavigate('up');
      else if(key==='F5')operation=()=>render();
      else if(key==='Escape')operation=()=>{selectAllFiles(false);};
      else if(key===' '&&target.closest('.file-row'))operation=()=>selectFile(Number(target.closest('.file-row').dataset.index),{toggle:true});
      else if(key==='Enter'&&Date.now()-fileCompositionEnd>100&&!target.closest('button,a'))operation=openSelectedFile;
    }
    if(operation){event.preventDefault();if(event.repeat&&['Delete','F2','Enter'].includes(key))return;busy(null,operation);}
  });
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
