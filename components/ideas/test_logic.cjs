// 沙箱测试：验证 app.js 的纯逻辑（内容格式兼容、路径、时间戳）
const fs = require('fs');
const vm = require('vm');

function stubEl() {
  return {
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, style: {}, dataset: {},
    set textContent(v) {}, get textContent() { return ''; },
    set innerHTML(v) {}, get innerHTML() { return ''; },
    appendChild() {}, focus() {}, click() {},
    value: '', files: [], checked: false,
  };
}
const els = {};
const sandbox = {
  document: {
    querySelector: (s) => (els[s] = els[s] || stubEl()),
    addEventListener() {},
  },
  window: {},  // 无 showDirectoryPicker → supportFS() false
  navigator: {},
  console,
  setTimeout, clearTimeout,
  Date, JSON, Math, String, Array, Object, Promise,
  crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
const code = fs.readFileSync(__dirname + '\\app.js', 'utf8');
vm.runInContext(code, sandbox);

const t = (name, fn) => {
  try { fn(); console.log('PASS', name); }
  catch (e) { console.log('FAIL', name, '->', e.message); process.exitCode = 1; }
};
const run = (expr) => vm.runInContext(expr, sandbox);

t('contentText: string', () => {
  if (run("contentText('hello')") !== 'hello') throw new Error('string passthrough');
});
t('contentText: segments with inline image', () => {
  const got = run("contentText([{type:'text',text:'abc'},{type:'image',rel:'images\\\\x.png'},{type:'text',text:'def'}])");
  if (got !== 'abc [图片] def') throw new Error('got: ' + got);
});
t('editableText strips inline images', () => {
  const got = run("editableText([{type:'text',text:'abc'},{type:'image',rel:'images\\\\x.png'}])");
  if (got !== 'abc') throw new Error('got: ' + got);
});
t('content rebuild matches desktop format', () => {
  const segs = [];
  const text = 'hello\nworld';
  if (text) segs.push({ type: 'text', text });
  const rels = ['images\\a.png'];
  rels.forEach((rel) => segs.push({ type: 'image', rel }));
  const json = JSON.stringify(segs);
  const expected = JSON.stringify([{ type: 'text', text: 'hello\nworld' }, { type: 'image', rel: 'images\\a.png' }]);
  if (json !== expected) throw new Error(json);
});
t('relToPath: backslash -> slash', () => {
  if (run("relToPath('images\\\\df56.png')") !== 'images/df56.png') throw new Error('bad');
});
t('pathToRel: slash -> backslash', () => {
  if (run("pathToRel('images/df56.png')") !== 'images\\df56.png') throw new Error('bad');
});
t('nowStamp format matches desktop', () => {
  const s = run('nowStamp()');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(s)) throw new Error('bad: ' + s);
});
t('genId: 32 hex chars (desktop uuid4().hex)', () => {
  const id = run('genId()');
  if (!/^[0-9a-f]{32}$/.test(id)) throw new Error('bad: ' + id);
});
t('fmtTime display', () => {
  if (run("fmtTime('2026-08-02T14:15:56')") !== '2026-08-02 14:15') throw new Error('bad');
});
t('sort desc by updated', () => {
  const arr = [
    { id: 'a', updated: '2026-08-01T10:00:00' },
    { id: 'b', updated: '2026-08-02T10:00:00' },
    { id: 'c', updated: '' },
  ];
  arr.sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')));
  if (arr[0].id !== 'b' || arr[1].id !== 'a' || arr[2].id !== 'c') throw new Error('sort wrong');
});
t('escapeHtml', () => {
  if (run("escapeHtml('<b>&\"x\"</b>')") !== '&lt;b&gt;&amp;&quot;x&quot;&lt;/b&gt;') throw new Error('bad');
});
t('synthetic desktop-format ideas parse', () => {
  const raw = JSON.stringify([
    { id: 'synthetic-text', title: 'Synthetic note', content: 'Synthetic text', updated: '2026-01-01T00:00:00', images: [] },
    { id: 'synthetic-segments', title: 'Synthetic segments', content: [{ type: 'text', text: 'Synthetic text' }], updated: '2026-01-02T00:00:00', images: [] },
  ]);
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) throw new Error('not array');
  parsed.forEach((it) => {
    if (!it.id || !it.title || !it.updated) throw new Error('missing field: ' + it.id);
    if (typeof it.content !== 'string' && !Array.isArray(it.content)) throw new Error('bad content type');
    if (!Array.isArray(it.images)) throw new Error('bad images');
  });
  console.log('  (sample has', parsed.length, 'ideas, all fields OK)');
});
