"""Authenticated wrapper around the original Watcher, never its public tray server."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import subprocess


class _ClientLauncher:
    """Only the official client leaves the automation job; other children stay owned."""
    def __getattr__(self,name):return getattr(subprocess,name)
    def Popen(self,args,**kwargs):
        if os.name=='nt' and isinstance(args,(list,tuple)) and len(args)==1 and Path(args[0]).name.casefold()=='qianwen.exe':
            kwargs['creationflags']=kwargs.get('creationflags',0)|getattr(subprocess,'CREATE_BREAKAWAY_FROM_JOB',0x01000000)
        return subprocess.Popen(args,**kwargs)


def main():
    root = Path(os.environ['SUITE_COMPONENT_ROOT']).resolve()
    data = Path(os.environ['SUITE_WORKER_DATA']).resolve()
    data.mkdir(parents=True, exist_ok=True)
    token = os.environ['SUITE_WORKER_TOKEN']
    parent = int(os.environ['SUITE_PARENT_PID'])
    spec = importlib.util.spec_from_file_location('suite_qwen_engine', root / 'engine.py')
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.subprocess = _ClientLauncher()
    engine.HERE = data
    engine.CONFIG_PATH = data / 'config.json'
    engine.LOG_PATH = data / 'auto_record.log'
    engine.STATE_PATH = data / 'auto_record.state.json'
    watcher = None
    lock = threading.RLock()
    def current():
        nonlocal watcher
        if watcher is None:
            cfg = engine.load_config(engine.CONFIG_PATH)
            cfg['archive'] = {**cfg.get('archive', {}), 'enabled':True, 'dir':str(data / 'recordings')}
            engine.CONFIG_PATH.write_text(json.dumps(cfg,ensure_ascii=False),encoding='utf-8')
            watcher = engine.Watcher(engine.CONFIG_PATH)
        return watcher
    def status():
        state = watcher.snapshot() if watcher else {}
        return {'component':'qwen','status':'running' if state.get('running') else 'stopped',
                'running':bool(state.get('running')),'recording':bool(state.get('recording')),
                'trigger_count':state.get('trigger_count',0),'last_app':state.get('last_app',''),
                'last_trigger_at':state.get('last_trigger_at',0),
                'detail':'自动化运行中' if state.get('running') else '自动化已停止',
                'error':'千问操作未完成，请检查客户端登录和录音快捷键' if state.get('last_error') else '',
                'archive':{'enabled':bool(state.get('running')),'count':len(recordings())},
                'control_note':'停止自动化不结束千问内的录音，请在千问中手动停止录音'}
    def recordings():
        folder=data/'recordings'
        if not folder.is_dir():return []
        result=[]
        for entry in sorted(folder.iterdir(),key=lambda p:p.name,reverse=True)[:100]:
            if entry.is_symlink() or not entry.is_dir():continue
            files=[f for f in entry.rglob('*') if f.is_file() and not f.is_symlink()]
            result.append({'id':entry.name,'name':entry.name,'files':len(files),'size_bytes':sum(f.stat().st_size for f in files),'modified_at':entry.stat().st_mtime})
        return result
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def response(self,code,data):
            payload=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)))
            self.end_headers();self.wfile.write(payload)
        def handle_action(self):
            if not secrets.compare_digest(self.headers.get('Authorization',''),f'Bearer {token}'):
                return self.response(401,{'error':'unauthorized'})
            try:
                if self.command=='POST':
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<=size<=1000:raise ValueError()
                    payload=json.loads(self.rfile.read(size) or b'{}')
                    if payload!={}:raise ValueError()
                with lock:
                    if self.command=='GET' and self.path=='/health':return self.response(200,{'service':'suite-qwen','pid':os.getpid()})
                    if self.command=='GET' and self.path=='/status':return self.response(200,status())
                    if self.command=='GET' and self.path=='/recordings':
                        items=recordings();return self.response(200,{'recordings':items,'total':len(items)})
                    if self.command=='POST' and self.path=='/start':current().start();return self.response(200,status())
                    if self.command=='POST' and self.path=='/stop':
                        if watcher:watcher.stop()
                        return self.response(200,status())
                    if self.command=='POST' and self.path=='/trigger':
                        current().trigger_now();result=status();result['trigger_requested']=True
                        return self.response(200,result)
                return self.response(404,{'error':'unknown action'})
            except Exception:return self.response(400,{'error':'本地千问操作未完成，请检查客户端和依赖'})
        do_GET=handle_action
        do_POST=handle_action
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    Path(os.environ['SUITE_WORKER_RECEIPT']).write_text(json.dumps({'port':server.server_port,'pid':os.getpid()}),encoding='utf-8')
    def monitor():
        import psutil
        while psutil.pid_exists(parent):time.sleep(2)
        if watcher:watcher.stop()
        server.shutdown()
    threading.Thread(target=monitor,daemon=True).start()
    try:server.serve_forever()
    finally:
        if watcher:watcher.stop()
        server.server_close()


if __name__=='__main__':main()
