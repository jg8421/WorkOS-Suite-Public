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
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from suite.qwen import (ListenerLease, OriginalQwenBridge, QwenError,
                        configuration_update, prepare_config, public_config, safe_snapshot, read_config)


class _ClientLauncher:
    """Only the official client leaves the automation job; other children stay owned."""
    def __getattr__(self,name):return getattr(subprocess,name)
    def Popen(self,args,**kwargs):
        if os.name=='nt' and isinstance(args,(list,tuple)) and len(args)==1 and Path(args[0]).name.casefold()=='qianwen.exe':
            kwargs['creationflags']=kwargs.get('creationflags',0)|getattr(subprocess,'CREATE_BREAKAWAY_FROM_JOB',0x01000000)
        return subprocess.Popen(args,**kwargs)


class QwenController:
    """Reuses Watcher semantics; duplicate prevention also guards delayed hotkey sends."""
    def __init__(self, engine, data, *, bridge=None, lease_path=None, original=None):
        self.engine = engine
        self.data = Path(data)
        self.bridge = bridge or OriginalQwenBridge(False)
        self.lease = ListenerLease(lease_path or self.data / 'listener.lock')
        self.cfg, self.origin = prepare_config(engine, self.data, original)
        self.watcher = None
        self._trigger_lock = threading.RLock()
        self.audio_errors = {'capture': False, 'playback': False}
        original_log = engine.log
        def log(message, *args, **kwargs):
            if '[warn]' in message and '采集' in message:
                self.audio_errors['capture'] = True
            if '[warn]' in message and '回放' in message:
                self.audio_errors['playback'] = True
            return original_log(message, *args, **kwargs)
        engine.log = log
        for name, key in [('capture_sessions', 'capture'), ('render_sessions', 'playback')]:
            original_function = getattr(engine, name)
            def checked(function=original_function, category=key):
                self.audio_errors[category] = False
                return function()
            setattr(engine, name, checked)
        original_trigger = engine.trigger
        def guarded_trigger(cfg):
            with self._trigger_lock:
                if self.lease.handle is None or self.watcher is None or cfg is not self.watcher.cfg or self.bridge.processes():
                    raise QwenError('监听已停止或原监听已经接管，未重复发送录音快捷键')
                return original_trigger(cfg)
        engine.trigger = guarded_trigger

    def current(self):
        if self.watcher is None:
            self.watcher = self.engine.Watcher(self.engine.CONFIG_PATH)
        return self.watcher

    def start(self):
        if self.bridge.processes():
            raise QwenError('原千问监听已经运行，未启动第二个监听')
        self.lease.acquire()
        try:
            if self.bridge.processes():
                raise QwenError('原千问监听已经运行，未启动第二个监听')
            # RecordingArchiver is a Thread and can start only once. A fresh
            # Watcher also keeps an old delayed verifier from gaining permission
            # when the new listener acquires the lease.
            if self.watcher is not None and not self.watcher.running:
                old = self.watcher
                self.watcher = None
                old.stop()
            self.current().start()
            if not self.watcher.running:
                raise QwenError('千问监听线程未启动，请检查音频依赖')
        except Exception:
            if self.watcher:
                self.watcher.stop()
            self.lease.release()
            raise
        return self.status()

    def stop(self):
        if self.watcher:
            self.watcher.stop()
        with self._trigger_lock:
            self.lease.release()
        return self.status()

    def trigger(self):
        if self.bridge.processes():
            raise QwenError('原千问监听已经运行，请通过原监听触发，未重复发送快捷键')
        if self.status()['recording']:
            return {**self.status(), 'trigger_requested': False, 'already_recording': True}
        was_listening = self.lease.handle is not None
        self.lease.acquire()
        try:
            watcher = self.current()
            watcher.last_error = ''
            watcher.trigger_now()
            if watcher.last_error:
                raise QwenError('录音快捷键未发送成功，请检查千问登录和客户端快捷键')
            return {**self.status(), 'trigger_requested': True, 'recording_verified': self.status()['recording']}
        finally:
            if not was_listening:
                self.lease.release()

    def update(self, body):
        self.cfg = configuration_update(body, self.cfg)
        self.engine.parse_hotkey(self.cfg['trigger']['hotkey'])
        self.engine.save_config(self.cfg, self.engine.CONFIG_PATH)
        if self.watcher:
            self.watcher.reload_config()
        return {'configuration': public_config(self.cfg), **self.status()}

    def status(self):
        state = self.watcher.snapshot() if self.watcher else {}
        result = safe_snapshot(state, source='suite', cfg=self.cfg)
        result['configuration'].update(self.origin)
        result['diagnostics']['capture_error'] |= self.audio_errors['capture']
        result['diagnostics']['playback_error'] |= self.audio_errors['playback']
        if result['diagnostics']['capture_error'] or result['diagnostics']['playback_error']:
            result['error'] = '音频会话检测失败，请检查 Windows 音频设备和麦克风权限'
        result['archive'] = {'enabled': self.cfg.get('archive', {}).get('enabled', True) is not False, 'count': len(self.recordings())}
        return result

    def recordings(self):
        folder = Path(self.cfg.get('archive', {}).get('dir') or self.data / 'recordings')
        if not folder.is_dir() or folder.is_symlink():
            return []
        result = []
        for entry in sorted(folder.iterdir(), key=lambda p: p.name, reverse=True)[:100]:
            if entry.is_symlink() or not entry.is_dir():
                continue
            files = [f for f in entry.rglob('*') if f.is_file() and not f.is_symlink()]
            result.append({'id': entry.name, 'name': entry.name, 'files': len(files), 'size_bytes': sum(f.stat().st_size for f in files), 'modified_at': entry.stat().st_mtime})
        return result


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
    external = os.environ.get('SUITE_QWEN_EXTERNAL') == '1'
    original = None
    original_config = os.environ.get('SUITE_QWEN_ORIGINAL_CONFIG')
    original_root = os.environ.get('SUITE_QWEN_ORIGINAL_ROOT')
    if original_config and original_root:
        original = {'config': Path(original_config), 'root': Path(original_root)}
    else:
        saved = read_config(data / 'original-config-reference.json') or {}
        if isinstance(saved.get('config'), str) and isinstance(saved.get('root'), str):
            original = {'config': Path(saved['config']), 'root': Path(saved['root'])}
    controller = QwenController(engine, data, bridge=OriginalQwenBridge(external),
                                lease_path=os.environ.get('SUITE_QWEN_LOCK_FILE'), original=original)
    lock = threading.RLock()
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
                    if not isinstance(payload,dict) or self.path!='/config' and payload!={}:raise ValueError()
                with lock:
                    if self.command=='GET' and self.path=='/health':return self.response(200,{'service':'suite-qwen','pid':os.getpid()})
                    if self.command=='GET' and self.path=='/status':return self.response(200,controller.status())
                    if self.command=='GET' and self.path=='/config':return self.response(200,{'configuration':public_config(controller.cfg)})
                    if self.command=='GET' and self.path=='/recordings':
                        items=controller.recordings();return self.response(200,{'recordings':items,'total':len(items)})
                    if self.command=='POST' and self.path=='/start':return self.response(200,controller.start())
                    if self.command=='POST' and self.path=='/stop':return self.response(200,controller.stop())
                    if self.command=='POST' and self.path=='/trigger':return self.response(200,controller.trigger())
                    if self.command=='POST' and self.path=='/config':return self.response(200,controller.update(payload))
                return self.response(404,{'error':'unknown action'})
            except QwenError as error:return self.response(400,{'error':str(error)})
            except Exception:return self.response(400,{'error':'本地千问操作未完成，请检查客户端和依赖'})
        do_GET=handle_action
        do_POST=handle_action
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    Path(os.environ['SUITE_WORKER_RECEIPT']).write_text(json.dumps({'port':server.server_port,'pid':os.getpid()}),encoding='utf-8')
    def monitor():
        import psutil
        while psutil.pid_exists(parent):
            if external and controller.lease.handle is not None and controller.bridge.processes():
                with lock:controller.stop()
            time.sleep(2)
        controller.stop()
        server.shutdown()
    threading.Thread(target=monitor,daemon=True).start()
    try:server.serve_forever()
    finally:
        controller.stop()
        server.server_close()


if __name__=='__main__':main()
