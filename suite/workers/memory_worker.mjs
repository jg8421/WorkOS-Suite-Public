// Private loopback worker. Never import the original auto-starting service entrypoint.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';

const component = path.resolve(process.env.SUITE_COMPONENT_ROOT || '');
const home = path.resolve(process.env.SUITE_WORKER_DATA || '');
const receipt = process.env.SUITE_WORKER_RECEIPT;
const token = process.env.SUITE_WORKER_TOKEN || '';
const parent = Number(process.env.SUITE_PARENT_PID);
if (!receipt || token.length < 32 || !parent || !fs.existsSync(path.join(component, 'src', 'store.mjs'))) process.exit(2);
for (const name of ['state', 'logs', 'data/events', 'data/memory/daily']) fs.mkdirSync(path.join(home, name), {recursive:true});
const {MemoryStore} = await import(pathToFileURL(path.join(component, 'src', 'store.mjs')).href);
const settingsFile = path.join(home, 'settings.json');
const defaults = {codex_import:false, dsh_import:false, desktop_capture:false, cloud_inbox:false};
let settings = {...defaults};
try { const saved = JSON.parse(fs.readFileSync(settingsFile,'utf8')); for (const k of Object.keys(defaults)) if (typeof saved[k] === 'boolean') settings[k] = saved[k]; } catch {}
const store = new MemoryStore(home, {dataRoot:path.join(home,'data'),privacy:{redactSecrets:true,redactOtp:true}});
store.rebuildViews();
let collectors = [];
let collectorErrors = [];
const logger = {log(){}, error(){collectorErrors = ['捕获或导入未完成，请检查对应应用状态'];}};
async function applyCollectors() {
  for (const item of collectors) item.stop();
  collectors = []; collectorErrors = [];
  const specs = [
    ['codex_import','codex-importer.mjs','CodexImporter',{sessionsRoot:path.join(os.homedir(),'.codex','sessions'),includeAssistant:false,intervalMs:15000}],
    ['dsh_import','dsh-importer.mjs','DshImporter',{sessionsRoot:path.join(os.homedir(),'.dsh','sessions'),includeAssistant:false,intervalMs:15000}],
    ['cloud_inbox','cloud-inbox.mjs','CloudInboxImporter',{intervalMs:15000}],
    ['desktop_capture','desktop-capture.mjs','DesktopCapture',{intervalMs:10000}],
  ];
  for (const [flag,file,name,options] of specs) {
    if (!settings[flag]) continue;
    try {
      if (flag === 'desktop_capture') {
        if (process.platform !== 'win32') throw new Error('unsupported');
        fs.mkdirSync(path.join(home,'scripts'), {recursive:true});
        fs.copyFileSync(path.join(component,'scripts','capture-desktop.ps1'),path.join(home,'scripts','capture-desktop.ps1'));
      }
      const module = await import(pathToFileURL(path.join(component,'src',file)).href);
      const item = new module[name](store,home,{...options,enabled:true},logger);
      collectors.push(item); await item.start();
    } catch { collectorErrors.push('捕获或导入未启动，请检查运行环境'); }
  }
}
const publicEvent = e => e && Object.fromEntries(['id','timestamp','type','title','text','tags','importance'].map(k=>[k,e[k]]));
const safeJson = (res,code,data) => {res.writeHead(code, {'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(data));};
function authenticated(req) {
  const supplied = Buffer.from(req.headers.authorization || '');
  const expected = Buffer.from(`Bearer ${token}`);
  return supplied.length === expected.length && crypto.timingSafeEqual(supplied,expected);
}
async function body(req) {
  let size=0; const parts=[];
  for await (const chunk of req) {size+=chunk.length;if(size>100000)throw new Error('size');parts.push(chunk);}
  const value = JSON.parse(Buffer.concat(parts).toString('utf8') || '{}');
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('shape');
  return value;
}
const server = http.createServer(async(req,res)=>{
  if (!authenticated(req)) return safeJson(res,401,{error:'unauthorized'});
  try {
    const u = new URL(req.url,'http://127.0.0.1'); const route=u.pathname;
    if (req.method==='GET' && route==='/health') return safeJson(res,200,{service:'suite-memory',pid:process.pid});
    if (req.method==='GET' && route==='/status') return safeJson(res,200,{component:'memory',status:'running',count:store.activeEvents().length,settings,collector_errors:collectorErrors,local_only:true});
    if (req.method==='GET' && route==='/settings') return safeJson(res,200,{settings});
    if (req.method==='GET' && (route==='/recent'||route==='/search')) {
      const limit=Math.max(1,Math.min(50,Number(u.searchParams.get('limit'))||20));
      const events=route==='/recent'?store.recent(limit):store.search((u.searchParams.get('q')||'').slice(0,2000),limit);
      return safeJson(res,200,{events:events.map(publicEvent),total:events.length});
    }
    if(req.method==='POST' && route==='/add') {
      const data=await body(req);
      if(typeof data.text!=='string'||!data.text.trim()||data.text.length>65536) throw new Error('text');
      const result=store.remember({text:data.text,title:typeof data.title==='string'?data.title.slice(0,500):'',type:typeof data.type==='string'?data.type:'note',tags:Array.isArray(data.tags)?data.tags.filter(x=>typeof x==='string').slice(0,32):[],source:'suite:manual'});
      return safeJson(res,201,{event:publicEvent(result.event)});
    }
    if(req.method==='POST' && route==='/forget') {
      const data=await body(req);if(typeof data.id!=='string'||data.id.length>100) throw new Error('id');
      return safeJson(res,200,{forgotten:store.forget(data.id),retention:'soft_delete'});
    }
    if(req.method==='POST' && route==='/settings') {
      const data=await body(req);
      if(Object.keys(data).some(k=>!(k in defaults)||typeof data[k]!=='boolean')) throw new Error('settings');
      if(data.desktop_capture===true && process.platform!=='win32')return safeJson(res,400,{error:'桌面捕获需要 Windows'});
      settings={...settings,...data};
      const tmp=`${settingsFile}.${process.pid}.tmp`;fs.writeFileSync(tmp,JSON.stringify(settings));fs.renameSync(tmp,settingsFile);
      await applyCollectors(); return safeJson(res,200,{settings,collector_errors:collectorErrors});
    }
    return safeJson(res,404,{error:'unknown action'});
  } catch {return safeJson(res,400,{error:'输入无效或本地操作未完成'});}
});
function shutdown(){for(const item of collectors)item.stop();server.close(()=>process.exit(0));setTimeout(()=>process.exit(0),1500).unref();}
process.on('SIGTERM',shutdown);process.on('SIGINT',shutdown);
server.listen(0,'127.0.0.1',async()=>{
  fs.writeFileSync(receipt,JSON.stringify({port:server.address().port,pid:process.pid}));
  await applyCollectors();
});
setInterval(()=>{try{process.kill(parent,0);}catch{shutdown();}},2000).unref();
