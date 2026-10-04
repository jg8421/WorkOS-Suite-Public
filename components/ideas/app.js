'use strict';
/* ============================================================
 * 想法收集器 · 安卓版（PWA）
 * 通过 File System Access API 直接读写 Syncthing 同步文件夹：
 *   data/ideas.json + data/images/（与桌面版完全兼容）
 * 数据格式：ideas.json = [ {type,title,content,id,created,updated,images:[rel...]} ]
 *   content 可为字符串，或 [{type:'text',text},{type:'image',rel}] 片段数组
 *   rel 相对 data 目录，如 images\abc.png（含反斜杠）
 * ============================================================ */

const DB_NAME = 'idea-collector-db';
const DB_VER = 1;
const HANDLE_KEY = 'dirHandle';

const $ = (sel) => document.querySelector(sel);
const views = { welcome: $('#welcome'), home: $('#home'), edit: $('#edit') };

const state = {
  dir: null,          // 用户选择的根文件夹
  dataDir: null,      // 实际存放 ideas.json 的目录（优先 data/ 子目录）
  useSub: true,       // 是否使用了 data/ 子目录
  ideas: [],          // 全部想法（按 updated 倒序）
  filter: '',
  edit: null,         // 正在编辑的想法副本
  originalImages: [],
  pendingNewImages: [],   // 本次编辑新增、尚未保存的图片文件 rel
  inlineImageRels: [],    // content 里内嵌图片 rel（编辑时保留）
  loadedEmpty: false,     // 加载到的想法数为 0（防误覆盖警告）
};

let savedHandle = null; // IndexedDB 里记住的文件夹句柄
let savedGranted = false; // 已保存句柄是否确认过读写权限
const thumbCache = new Map(); // rel -> objectURL

/* ================= IndexedDB：记住文件夹句柄 ================= */
function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VER);
    req.onupgradeneeded = () => { req.result.createObjectStore('meta'); };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function idbGet(key) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('meta', 'readonly');
    const req = tx.objectStore('meta').get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function idbSet(key, value) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('meta', 'readwrite');
    tx.objectStore('meta').put(value, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}
async function idbDel(key) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('meta', 'readwrite');
    tx.objectStore('meta').delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/* ================= 文件夹 ================= */
function supportFS() { return 'showDirectoryPicker' in window; }

async function pickFolder() {
  const btn = document.getElementById ? document.getElementById('btn-pick') : null;
  const oldText = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '正在打开文件夹选择…'; }
  try {
    // 同步调用（前面不加 await），避免“用户激活被消耗”导致选择器不弹出
    const dir = await window.showDirectoryPicker({ mode: 'readwrite' });
    await idbSet(HANDLE_KEY, dir);
    await connect(dir);
  } catch (e) {
    const name = e && e.name ? e.name : '';
    const msg = e && e.message ? e.message : String(e);
    if (name === 'AbortError') {
      // 部分 Chrome/安卓系统会静默中止，自动重试一次
      try {
        await new Promise((r) => setTimeout(r, 400));
        const dir2 = await window.showDirectoryPicker({ mode: 'readwrite' });
        await idbSet(HANDLE_KEY, dir2);
        await connect(dir2);
      } catch (e2) {
        const n2 = e2 && e2.name ? e2.name : '';
        const m2 = e2 && e2.message ? e2.message : String(e2);
        if (n2 === 'AbortError') {
          alert('文件夹选择窗口没有打开。\n\n可能原因：\n1) Chrome 版本过旧或过新（142+ 曾有选择器故障，请升级到最新版）\n2) 系统文件管理器异常（可试试 Edge 浏览器）\n\n页面底部会显示你的浏览器版本，发给我即可排查。');
        } else {
          alert('选择文件夹失败：' + n2 + ' ' + m2);
        }
      }
    } else if (name === 'NotAllowedError') {
      alert('选择文件夹失败：浏览器要求必须由点击按钮直接触发选择窗口。\n请把 Chrome/Edge 升级到最新版，再点一次「选择同步文件夹」。');
    } else {
      alert('选择文件夹失败：' + name + ' ' + msg);
    }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = oldText; }
  }
}

async function ensurePermission(dir) {
  const opts = { mode: 'readwrite' };
  let p = await dir.queryPermission(opts);
  if (p !== 'granted') p = await dir.requestPermission(opts);
  return p === 'granted';
}

async function resolveDataFiles(dir) {
  let dataDir = dir, useSub = false, ideasFile = null;
  try { dataDir = await dir.getDirectoryHandle('data'); useSub = true; } catch (e) { dataDir = dir; }
  try { ideasFile = await dataDir.getFileHandle('ideas.json'); }
  catch (e) {
    if (useSub) {
      dataDir = dir; useSub = false;
      try { ideasFile = await dataDir.getFileHandle('ideas.json'); } catch (e2) { ideasFile = null; }
    }
  }
  return { dataDir, useSub, ideasFile };
}

async function connect(dir) {
  const ok = await ensurePermission(dir);
  if (!ok) { show('welcome'); toast('未获得文件夹读写权限'); return; }
  const res = await resolveDataFiles(dir);
  if (!res.ideasFile) {
    show('welcome');
    toast('该文件夹里没有 ideas.json，请选择已同步的「想法收集器」文件夹');
    return;
  }
  state.dir = dir;
  state.dataDir = res.dataDir;
  state.useSub = res.useSub;
  await refresh();
  show('home');
}

async function switchFolder() {
  const msg = state.dir ? '切换到另一个文件夹？（当前已连接：' + state.dir.name + '）' : '选择同步文件夹';
  if (!confirm(msg)) return;
  try {
    const dir = await window.showDirectoryPicker({ mode: 'readwrite' });
    await idbSet(HANDLE_KEY, dir);
    await connect(dir);
  } catch (e) {
    const name = e && e.name ? e.name : '';
    const msg = e && e.message ? e.message : String(e);
    if (name === 'AbortError') {
      alert('文件夹选择窗口没有打开。请升级 Chrome 到最新版，或换用 Edge 重试。');
      return;
    }
    alert('切换失败：' + name + ' ' + msg);
  }
}

/* ================= 数据读写 ================= */
function genId() {
  const u = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : '';
  if (u) return u.replace(/-/g, '');
  return Date.now().toString(16) + Math.random().toString(16).slice(2);
}
function nowStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
         'T' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}
function relToPath(rel) { return String(rel || '').replace(/\\/g, '/'); }
function pathToRel(path) { return String(path || '').split('/').join('\\'); }

async function loadIdeas() {
  const f = await state.dataDir.getFileHandle('ideas.json');
  const file = await f.getFile();
  const text = await file.text();
  let arr = [];
  if (text.trim()) {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) arr = parsed;
    else if (parsed && Array.isArray(parsed.ideas)) arr = parsed.ideas;
  }
  arr = arr.filter((it) => it && typeof it === 'object');
  arr.forEach((it) => {
    if (!it.id) it.id = genId();
    if (!it.created) it.created = '';
    if (!it.updated) it.updated = it.created;
    if (!Array.isArray(it.images)) it.images = [];
    if (it.image_rel) { it.images.push(it.image_rel); delete it.image_rel; }
    it.images = it.images.filter(Boolean);
  });
  arr.sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')));
  return arr;
}

async function saveIdeas() {
  const f = await state.dataDir.getFileHandle('ideas.json', { create: true });
  const w = await f.createWritable();
  await w.write(JSON.stringify(state.ideas, null, 2));
  await w.close();
}

async function getFileByRel(rel) {
  const parts = relToPath(rel).split('/').filter(Boolean);
  if (!parts.length) throw new Error('empty rel: ' + rel);
  let d = state.dataDir;
  for (let i = 0; i < parts.length - 1; i++) d = await d.getDirectoryHandle(parts[i]);
  const fh = await d.getFileHandle(parts[parts.length - 1]);
  return fh.getFile();
}

async function deleteFileByRel(rel) {
  try {
    const parts = relToPath(rel).split('/').filter(Boolean);
    if (!parts.length) return;
    let d = state.dataDir;
    for (let i = 0; i < parts.length - 1; i++) d = await d.getDirectoryHandle(parts[i]);
    await d.removeEntry(parts[parts.length - 1], { recursive: false });
    thumbCache.delete(rel);
  } catch (e) { /* 文件不存在或删除失败则忽略 */ }
}

async function writeImageFile(name, blob) {
  const imagesDir = await state.dataDir.getDirectoryHandle('images', { create: true });
  const fh = await imagesDir.getFileHandle(name, { create: true });
  const w = await fh.createWritable();
  await w.write(blob);
  await w.close();
  return pathToRel('images/' + name);
}

async function thumbUrl(rel) {
  if (thumbCache.has(rel)) return thumbCache.get(rel);
  try {
    const file = await getFileByRel(rel);
    const url = URL.createObjectURL(file);
    thumbCache.set(rel, url);
    return url;
  } catch (e) { return null; }
}

/* ================= 内容处理（兼容字符串 / 片段数组） ================= */
function contentText(content) {
  if (content == null) return '';
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((s) => {
      if (!s) return '';
      if (s.type === 'image') return ' [图片] ';
      return s.text || '';
    }).join('');
  }
  return String(content);
}
function editableText(content) {
  if (content == null) return '';
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.filter((s) => s && s.type !== 'image').map((s) => (s && s.text) || '').join('');
  }
  return String(content);
}
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function fmtTime(t) {
  if (!t) return '';
  return String(t).replace('T', ' ').slice(0, 16);
}

/* ================= UI ================= */
function show(name) {
  Object.keys(views).forEach((k) => views[k].classList.toggle('hidden', k !== name));
}
let toastTimer = null;
function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2200);
}

function renderList() {
  const q = state.filter.trim().toLowerCase();
  const items = state.ideas.filter((it) => {
    if (!q) return true;
    return (it.title || '').toLowerCase().includes(q) ||
           contentText(it.content).toLowerCase().includes(q);
  });
  const listEl = $('#list');
  listEl.innerHTML = '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = q ? '没有匹配的想法' : '还没有想法，点右下角 ＋ 新建';
    listEl.appendChild(empty);
    return;
  }
  items.forEach((it) => {
    const card = document.createElement('button');
    card.className = 'card';
    const imgCount = (it.images || []).length;
    const snippet = contentText(it.content).replace(/\s+/g, ' ').trim().slice(0, 90);
    card.innerHTML =
      '<div class="card-title">' + escapeHtml(it.title || '(无标题)') + '</div>' +
      (snippet ? '<div class="card-snippet">' + escapeHtml(snippet) + '</div>' : '') +
      '<div class="card-meta">' + escapeHtml(fmtTime(it.updated)) +
        (imgCount ? ' · 📷 ' + imgCount : '') + '</div>';
    card.addEventListener('click', () => openEdit(it.id));
    listEl.appendChild(card);
  });
}

/* ---------- 编辑 ---------- */
function openEdit(id) {
  const it = state.ideas.find((x) => x.id === id);
  if (!it) return;
  state.edit = JSON.parse(JSON.stringify(it));
  state.originalImages = (state.edit.images || []).slice();
  state.pendingNewImages = [];
  state.inlineImageRels = [];
  if (Array.isArray(state.edit.content)) {
    state.inlineImageRels = state.edit.content
      .filter((s) => s && s.type === 'image' && s.rel)
      .map((s) => s.rel);
  }
  $('#edit-title').value = state.edit.title || '';
  $('#edit-content').value = editableText(state.edit.content);
  renderEditImages();
  show('edit');
  setTimeout(() => $('#edit-title').focus(), 60);
}

function newIdea() {
  const id = genId();
  const now = nowStamp();
  state.ideas.unshift({ type: 'text', title: '', content: '', id, created: now, updated: now, images: [] });
  openEdit(id);
}

function hasUnsavedChanges() {
  if (!state.edit) return false;
  const imgs = state.edit.images || [];
  return $('#edit-title').value.trim() !== (state.edit.title || '') ||
         $('#edit-content').value !== editableText(state.edit.content) ||
         imgs.length !== state.originalImages.length ||
         imgs.some((r, i) => r !== state.originalImages[i]);
}

function goBack() {
  if (state.edit && hasUnsavedChanges()) {
    if (!confirm('有未保存的修改，确定放弃吗？')) return;
    // 清理本次新增但未保存的图片
    const keep = new Set((state.edit.images || []).concat(state.inlineImageRels));
    state.pendingNewImages.forEach((rel) => { if (!keep.has(rel)) deleteFileByRel(rel); });
  }
  backToList();
}

function backToList() {
  state.edit = null;
  state.pendingNewImages = [];
  thumbCache.clear();
  $('#search').value = '';
  state.filter = '';
  renderList();
  show('home');
}

async function renderEditImages() {
  const box = $('#edit-images');
  box.innerHTML = '';
  const list = state.edit ? (state.edit.images || []) : [];
  if (!list.length) {
    const h = document.createElement('div');
    h.className = 'hint small';
    h.textContent = '还没有图片附件，点下方「添加图片」可把截图/照片加进来';
    box.appendChild(h);
    return;
  }
  for (const rel of list) {
    const wrap = document.createElement('div');
    wrap.className = 'edit-img';
    const url = await thumbUrl(rel);
    const img = document.createElement('img');
    if (url) {
      img.src = url;
      img.alt = '图片';
      img.addEventListener('click', () => window.open(url, '_blank'));
    } else {
      wrap.textContent = '图片读取失败';
      wrap.style.display = 'flex';
      wrap.style.alignItems = 'center';
      wrap.style.justifyContent = 'center';
      wrap.style.fontSize = '11px';
      wrap.style.color = '#b0b6c4';
    }
    const del = document.createElement('button');
    del.className = 'img-del';
    del.textContent = '✕';
    del.addEventListener('click', () => {
      state.edit.images = (state.edit.images || []).filter((r) => r !== rel);
      renderEditImages();
    });
    wrap.appendChild(img);
    wrap.appendChild(del);
    box.appendChild(wrap);
  }
}

async function addImageBlob(blob) {
  try {
    const t = (blob.type || '').toLowerCase();
    const ext = t.includes('jpeg') || t.includes('jpg') ? '.jpg'
              : t.includes('webp') ? '.webp'
              : t.includes('gif') ? '.gif'
              : '.png';
    const name = genId().slice(0, 12) + ext;
    const rel = await writeImageFile(name, blob);
    if (!Array.isArray(state.edit.images)) state.edit.images = [];
    state.edit.images.push(rel);
    state.pendingNewImages.push(rel);
    renderEditImages();
    toast('图片已添加，保存后生效');
  } catch (e) {
    toast('图片添加失败：' + (e && e.message ? e.message : e));
  }
}

async function saveEdit() {
  const it = state.edit;
  if (!it) return;
  if (state.loadedEmpty && !confirm('警告：当前文件夹里没有任何想法（可能是选错了文件夹，或 Syncthing 还没同步完成）。继续保存会覆盖这个空文件，确定吗？')) return;
  it.title = $('#edit-title').value.trim();
  const text = $('#edit-content').value;
  const segs = [];
  if (text) segs.push({ type: 'text', text: text });
  state.inlineImageRels.forEach((rel) => segs.push({ type: 'image', rel: rel }));
  it.content = segs.length ? segs : '';
  it.images = (it.images || []).filter(Boolean);
  // 清理本次新增但被移除的图片文件
  const keep = new Set(it.images.concat(state.inlineImageRels));
  for (const rel of state.pendingNewImages) {
    if (!keep.has(rel)) await deleteFileByRel(rel);
  }
  const now = nowStamp();
  if (!it.created) it.created = now;
  it.updated = now;
  if (it.type !== 'image' && !it.content && !it.images.length) it.type = 'text';
  const idx = state.ideas.findIndex((x) => x.id === it.id);
  if (idx >= 0) state.ideas[idx] = it;
  else state.ideas.unshift(it);
  state.ideas.sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')));
  await saveIdeas();
  thumbCache.clear();
  toast('已保存 ✓');
  backToList();
}

async function deleteCurrent() {
  const it = state.edit;
  if (!it) return;
  if (!confirm('确定删除「' + (it.title || '无标题') + '」吗？')) return;
  // 与桌面版一致：删除 images 数组对应的图片文件（内嵌图保留）
  const rels = (it.images || []).slice();
  if (it.image_rel) rels.push(it.image_rel);
  state.ideas = state.ideas.filter((x) => x.id !== it.id);
  for (const rel of rels) await deleteFileByRel(rel);
  await saveIdeas();
  thumbCache.clear();
  toast('已删除');
  backToList();
}

/* ================= 事件 ================= */
function bindEvents() {
  $('#btn-pick').addEventListener('click', async () => {
    const saved = savedHandle;
    if (saved && savedGranted) {
      try { await connect(saved); return; } catch (e) { /* 句柄失效，走重新选择 */ }
    }
    // 直接同步弹出选择器：先不 await 权限检查，避免“用户激活被消耗”导致选择器不弹出
    await pickFolder();
  });
  $('#btn-folder').addEventListener('click', switchFolder);
  $('#btn-refresh').addEventListener('click', refresh);
  $('#btn-add').addEventListener('click', newIdea);
  $('#btn-back').addEventListener('click', goBack);
  $('#btn-save').addEventListener('click', saveEdit);
  $('#btn-del').addEventListener('click', deleteCurrent);
  $('#btn-addimg').addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    for (const f of files) await addImageBlob(f);
  });
  $('#search').addEventListener('input', (e) => {
    state.filter = e.target.value;
    renderList();
  });
  // 粘贴图片（电脑上截图直接 Ctrl+V；手机键盘粘贴同样触发）
  document.addEventListener('paste', (e) => {
    if (views.edit.classList.contains('hidden')) return;
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (const item of items) {
      if (item.type && item.type.indexOf('image/') === 0) {
        e.preventDefault();
        const blob = item.getAsFile();
        if (blob) addImageBlob(blob);
        return;
      }
    }
  });
}

/* ================= 初始化 ================= */
function updateEmptyWarn() {
  const el = document.getElementById ? document.getElementById('empty-warn') : null;
  if (!el) return;
  if (state.loadedEmpty) {
    el.classList.remove('hidden');
  } else {
    el.classList.add('hidden');
  }
}

async function refresh() {
  try {
    state.ideas = await loadIdeas();
    state.loadedEmpty = state.ideas.length === 0;
    updateEmptyWarn();
    renderList();
    const where = state.useSub ? 'data\\ideas.json' : 'ideas.json';
    $('#folder-info').textContent = '📁 ' + where + '（' + (state.dir ? state.dir.name : '') + '）· Syncthing 自动同步';
  } catch (e) {
    toast('读取失败：' + (e && e.message ? e.message : e));
  }
}

function fillDiagnostics() {
  const el = document.getElementById ? document.getElementById('diag') : null;
  if (!el) return;
  const ua = navigator.userAgent || '';
  const chrome = ua.match(/Chrome\/([\d.]+)/);
  const isEdge = ua.indexOf('Edg/') >= 0;
  const isWeChat = ua.indexOf('MicroMessenger') >= 0;
  const name = isWeChat ? '微信内置浏览器' : isEdge ? 'Edge' : (ua.indexOf('SamsungBrowser') >= 0 ? '三星浏览器' : 'Chromium/Chrome');
  const ver = chrome ? chrome[1] : '未知';
  const proto = (typeof location !== 'undefined' && location && location.protocol) ? location.protocol : 'n/a';
  el.textContent = '浏览器：' + name + ' ' + ver + '｜文件夹API：' + (supportFS() ? '支持' : '不支持') + '｜协议：' + proto;
}

async function init() {
  fillDiagnostics();
  if (!supportFS()) {
    $('#welcome-desc').innerHTML = '当前浏览器不支持文件夹访问。<br>请用手机 <b>Chrome</b> 或 <b>Edge</b>（132 以上）打开本页面。';
    $('#btn-pick').style.display = 'none';
    show('welcome');
    return;
  }
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
  bindEvents();
  savedHandle = await idbGet(HANDLE_KEY);
  const saved = savedHandle;
  if (saved) {
    try {
      const ok = await ensurePermission(saved);
      savedGranted = ok;
      if (ok) { await connect(saved); return; }
    } catch (e) { savedGranted = false; /* 句柄失效，重新选择 */ }
    show('welcome');
    toast('请重新授权文件夹访问');
  } else {
    savedGranted = false;
    show('welcome');
  }
}

init();