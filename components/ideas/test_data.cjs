const fs = require('fs');
const vm = require('vm');

class MockFS { constructor() { this.root = new MockDir('sync', {}); } }
class MockDir {
  constructor(name, map) { this.name = name; this.map = map; }
  async getDirectoryHandle(name, opts) {
    if (!this.map[name]) {
      if (!(opts && opts.create)) { const e = new Error('not found: ' + name); e.name = 'NotFoundError'; throw e; }
      this.map[name] = new MockDir(name, {});
    }
    return this.map[name];
  }
  async getFileHandle(name, opts) {
    if (!this.map[name]) {
      if (!(opts && opts.create)) { const e = new Error('not found: ' + name); e.name = 'NotFoundError'; throw e; }
      this.map[name] = mockFileHandle(this, name);
    }
    return this.map[name];
  }
  async removeEntry(name, opts) { delete this.map[name]; }
}
function mockFileHandle(parent, name) {
  const fh = { name, parent };
  fh.getFile = async () => ({ name, text: async () => (fh.data == null ? '' : fh.data) });
  fh.createWritable = async () => ({
    write: async (s) => { fh.data = (s && typeof s.text === 'function') ? await s.text() : String(s); },
    close: async () => {},
  });
  return fh;
}
function installIn(root, path, data) {
  const parts = path.split('/');
  let d = root;
  for (let i = 0; i < parts.length - 1; i++) {
    if (!d.map[parts[i]]) d.map[parts[i]] = new MockDir(parts[i], {});
    d = d.map[parts[i]];
  }
  d.map[parts[parts.length - 1]] = mockFileHandle(d, parts[parts.length - 1]);
  if (data !== undefined) d.map[parts[parts.length - 1]].data = data;
  return d;
}
function stubEl() {
  return {
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, style: {}, dataset: {},
    set textContent(v) {}, get textContent() { return ''; },
    set innerHTML(v) {}, get innerHTML() { return ''; },
    appendChild() {}, focus() {}, click() {}, value: '', files: [],
  };
}
const els = {};
const syntheticIdeas = JSON.stringify(Array.from({ length: 19 }, (_, i) => ({
  type: 'text', title: 'Synthetic idea ' + (i + 1), id: 'synthetic-' + i,
  content: i % 2 ? 'Synthetic note' : [{ type: 'text', text: 'Synthetic segment' }],
  created: '2026-01-01T00:00:00', updated: '2026-01-' + String(i + 1).padStart(2, '0') + 'T00:00:00', images: [],
})));
const fsMock = new MockFS();
const dataDir = installIn(fsMock.root, 'data/ideas.json', syntheticIdeas);
const flatMock = new MockFS();
installIn(flatMock.root, 'ideas.json', JSON.stringify([{ type:'text', title:'flat', content:'', id:'x', created:'2026-08-01T00:00:00', updated:'2026-08-01T00:00:00', images:[] }]));

const sandbox = {
  document: { querySelector: (s) => (els[s] = els[s] || stubEl()), addEventListener() {} },
  window: {}, navigator: {}, console, setTimeout, clearTimeout,
  Date, JSON, Math, String, Array, Object, Promise, Set, Map, Error, RegExp,
  Blob, crypto: { randomUUID: () => 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' },
  fsMock, dataDir, flatMock,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(__dirname + '\\app.js', 'utf8'), sandbox);

let failed = 0;
const t = async (name, fn) => {
  try { await fn(); console.log('PASS', name); }
  catch (e) { console.log('FAIL', name, '->', e && e.stack ? e.stack.split('\n').slice(0, 2).join(' | ') : e); failed = 1; }
};

(async () => {
  await t('loadIdeas reads synthetic desktop-format data via mock FS', async () => {
    const arr = await vm.runInContext('(async () => { state.dir = fsMock.root; state.dataDir = dataDir; state.useSub = true; state.ideas = await loadIdeas(); return state.ideas; })()', sandbox);
    if (!Array.isArray(arr) || arr.length !== 19) throw new Error('expected 19, got ' + (arr && arr.length));
    if (arr[0].updated < arr[arr.length - 1].updated) throw new Error('not sorted desc');
  });

  await t('add + save + reload roundtrip', async () => {
    await vm.runInContext(`(async () => {
      const now = nowStamp();
      state.ideas.unshift({ type:'text', title:'测试条目', content:[{type:'text',text:'hello'}], id:genId(), created:now, updated:now, images:[] });
      await saveIdeas();
    })()`, sandbox);
    const parsed = JSON.parse(dataDir.map['ideas.json'].data);
    if (parsed.length !== 20) throw new Error('expected 20, got ' + parsed.length);
    if (parsed[0].title !== '测试条目') throw new Error('bad title');
    const arr = await vm.runInContext('(async () => await loadIdeas())()', sandbox);
    if (arr.length !== 20) throw new Error('reload failed');
  });

  await t('image write/read roundtrip with backslash rel', async () => {
    const rel = await vm.runInContext(`(async () => await writeImageFile('abcdef123456.png', new Blob(['x'])) )()`, sandbox);
    if (rel !== 'images\\abcdef123456.png') throw new Error('bad rel: ' + rel);
    const file = await vm.runInContext(`(async () => await getFileByRel('images\\\\abcdef123456.png'))()`, sandbox);
    const text = await file.text();
    if (text !== 'x') throw new Error('content mismatch');
  });

  await t('deleteFileByRel removes image file', async () => {
    await vm.runInContext(`(async () => await deleteFileByRel('images\\\\abcdef123456.png'))()`, sandbox);
    const imgs = dataDir.map['images'];
    if (imgs && imgs.map['abcdef123456.png']) throw new Error('file still exists');
  });

  await t('delete idea removes its images files (desktop parity)', async () => {
    installIn(fsMock.root, 'data/images/delme000001.png', 'img-bytes');
    await vm.runInContext(`(async () => {
      state.ideas = await loadIdeas();
      const it = { type:'image', title:'图', content:'', id:'imgtest', images:['images\\\\delme000001.png'] };
      const rels = (it.images || []).slice();
      state.ideas = state.ideas.filter((x) => x.id !== it.id);
      for (const rel of rels) await deleteFileByRel(rel);
      await saveIdeas();
    })()`, sandbox);
    const imgs = dataDir.map['images'];
    if (imgs && imgs.map['delme000001.png']) throw new Error('image file should have been removed on delete');
  });

  await t('resolveDataFiles: data/ subdir + root fallback', async () => {
    const res = await vm.runInContext('(async () => await resolveDataFiles(fsMock.root))()', sandbox);
    if (!res.useSub || !res.ideasFile) throw new Error('data/ subdir not resolved');
    const res2 = await vm.runInContext('(async () => await resolveDataFiles(flatMock.root))()', sandbox);
    if (res2.useSub || !res2.ideasFile) throw new Error('root fallback failed');
  });

  process.exit(failed);
})();
