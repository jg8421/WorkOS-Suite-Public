"""Private loopback gateway for seven local work tools."""
from __future__ import annotations
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import threading
from urllib.parse import parse_qs, quote, urlsplit

APP = Path(__file__).resolve().parents[1]
# This checked-in vendor is needed by Files and WorkOS; no host site override.
vendor = APP/'components'/'workos'/'vendor'
if vendor.is_dir(): sys.path.insert(0,str(vendor))
sys.dont_write_bytecode = True
from . import __version__
from .adapters import NativeAdapters
from .core import CoreProxy
from .files import FileService
from .ideas import IdeaStore
from .native_tools import NativeTools
from .processes import ProcessService
from .runtime import RuntimeManager

MAX_BODY = 28_000_000
CORE_ASSETS = {'/app.js','/api-client.js','/markdown.js','/valuation.js','/style.css'}
IDEA_ASSETS = {'index.html','app.js','app.css','sw.js','manifest.webmanifest','icon-192.png','icon-512.png'}
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"


def default_roots():
    # Fresh installations register folders in the Files UI; existing registrations persist.
    # Deployments may explicitly provide a JSON array, without embedding a user's paths.
    try:
        configured = json.loads(os.environ.get('WORKOS_SUITE_ROOTS', '[]'))
        if not isinstance(configured, list) or any(not isinstance(p, str) for p in configured):
            raise ValueError('Expected directory list')
        return [Path(p).expanduser() for p in configured if p and Path(p).expanduser().is_dir()]
    except (ValueError, OSError):
        logging.warning('Directory setting could not be read; add folders in the Files page')
        return []


class Application:
    def __init__(self, data_dir, port, *, isolated=False, empty_roots=False):
        self.data = Path(data_dir).resolve();self.data.mkdir(parents=True,exist_ok=True)
        self.port = port
        self.csrf = secrets.token_urlsafe(40)
        self.runtime = RuntimeManager(self.data/'runtime')
        self.ideas = IdeaStore(self.data)
        self.files = FileService(self.data, default_roots=[] if empty_roots or isolated else default_roots())
        self.native_tools = NativeTools(APP)
        node = APP.parent/'runtime'/'node'/'node.exe'
        self.adapters = NativeAdapters(APP/'components',self.data,self.runtime,node_path=str(node) if node.is_file() else None,qwen_external=not isolated)
        self.adapters.restore_qwen()
        self.core = CoreProxy(APP,self.data,self.runtime,isolated=isolated)
        self.processes = ProcessService(protected_pids=self.runtime.protected_pids)
        try: self.repositories = json.loads((APP/'repositories.json').read_text(encoding='utf-8'))
        except (OSError,ValueError): self.repositories = []
        if isinstance(self.repositories,dict): self.repositories = self.repositories.get('repositories',[])
        self.closed = False
        self.close_lock = threading.Lock()

    def components(self):
        values = [dict(self.runtime.status('workos'),description='基本面研究、会议、模型与交付')]
        native = self.adapters.describe()
        descriptions = {'qwen':('千问录制','监测通话与手动触发官方千问录音'),
                        'memory':('个人记忆','本地持久记忆与主动选择的来源'),
                        'phone':('手机互联','ADB授权设备、scrcpy镜像与按需截屏')}
        for id, (label,description) in descriptions.items():
            state = native[id]
            values.append(dict(state,id=id,label=label,description=description))
        for id,label,description in [('files','文件工作台','选定目录、预览、整理、撤销与项目导入'),
                ('processes','进程管理','真实资源读数与具体进程操作'),('ideas','想法收集','快速保存、项目关联与原版PWA')]:
            values.append({'id':id,'label':label,'description':description,'status':'running','running':True,
                           'owned':False,'can_start':False,'can_stop':False,'ownership':'inprocess','detail':'工作台内运行'})
        for value in values:
            if 'ownership' not in value:value['ownership']='borrowed' if value.get('borrowed') else 'owned' if value.get('owned') or value.get('can_stop') else 'external' if value['id']=='phone' else 'none'
        return values

    def component_action(self, component, action):
        if action not in ('start','stop','restart'): raise ValueError('组件操作无效')
        if component=='workos': return getattr(self.runtime,action)('workos')
        if component in ('memory','qwen'):
            if action=='restart':self.adapters.post(component,'stop',{})
            return self.adapters.post(component,'start' if action=='restart' else action,{})
        if component=='phone':
            if action=='stop':return self.adapters.post('phone','stop',{})
            return {**self.adapters.get('phone','status'),'detail':'请在手机互联中选择已授权设备，然后开始镜像'}
        if component in ('files','processes','ideas'):
            return {'id':component,'status':'running','detail':'此模块与工作台一同运行，无需单独启动或停止'}
        raise ValueError('未注册的组件')

    def close(self):
        with self.close_lock:
            if self.closed:return
            self.closed=True
            if self.runtime.status('workos')['owned']:
                try:self.core.json('POST','/api/shutdown',{},timeout=3)
                except (ValueError,OSError):logging.warning('Owned WorkOS graceful shutdown unavailable; completing owned-process cleanup')
            self.native_tools.close();self.adapters.close();self.runtime.close();self.ideas.close();self.files.close()


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = 'WorkOS-Suite'
    @property
    def app(self):return self.server.app
    def log_message(self,format,*args):
        # URLs may contain a search term or document name; no access-body logging.
        logging.debug('HTTP %s',self.command)

    def check_local(self, mutation=False, suite=False):
        allowed={f'127.0.0.1:{self.app.port}',f'localhost:{self.app.port}'}
        if self.client_address[0]!='127.0.0.1' or self.headers.get('Host','').lower() not in allowed:
            raise PermissionError('此工作台仅允许本机访问')
        if any(self.headers.get(h) for h in ('Forwarded','X-Forwarded-For','X-Forwarded-Host','CF-Connecting-IP')):
            raise PermissionError('本机工作台不接受隧道转发，请使用单独配置的 WorkOS 公网服务')
        origin=self.headers.get('Origin')
        origins={f'http://{host}' for host in allowed}
        if origin and origin not in origins: raise PermissionError('请从本机工作台页面操作')
        if self.headers.get('Sec-Fetch-Site') in ('cross-site',):raise PermissionError('不接受跨站请求')
        if mutation and origin not in origins:raise PermissionError('操作缺少本机页面来源，请刷新后重试')
        if suite and mutation and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),self.app.csrf):
            raise PermissionError('页面已更新，请刷新后重试；草稿仍保留')

    def json_body(self):
        if self.headers.get('Transfer-Encoding'):raise ValueError('请求编码不支持')
        content_type=self.headers.get('Content-Type','').split(';',1)[0].strip().lower()
        if content_type!='application/json':raise ValueError('请以工作台的表单提交内容')
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:raise ValueError('请求长度不正确') from None
        if not 0<length<=MAX_BODY:raise ValueError('请求过大或没有内容')
        raw=self.rfile.read(length)
        if len(raw)!=length:raise ValueError('请求未完整送达，请重试')
        try:body=json.loads(raw)
        except (ValueError,UnicodeError):raise ValueError('内容格式未能读取，请重试') from None
        if not isinstance(body,dict):raise ValueError('内容应为一个对象')
        return body,raw

    def response(self, data, status=200, mime='application/json; charset=utf-8', headers=()):
        if mime.startswith('application/json') and not isinstance(data,bytes):raw=json.dumps(data,ensure_ascii=False,allow_nan=False).encode()
        elif isinstance(data,str):raw=data.encode()
        else:raw=data
        self.send_response(status)
        self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','SAMEORIGIN');self.send_header('Content-Security-Policy',CSP)
        self.send_header('Referrer-Policy','no-referrer')
        for key,value in headers:self.send_header(key,value)
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(raw)

    def error(self, exc):
        if isinstance(exc,(BrokenPipeError,ConnectionResetError)):return
        if isinstance(exc,PermissionError):status,message=403,str(exc)
        elif isinstance(exc,FileNotFoundError):status,message=404,'文件或记录已不存在，请刷新后重试'
        elif isinstance(exc,ValueError):status,message=getattr(exc,'status_code',400),str(exc)
        elif isinstance(exc,OSError):status,message=409,'文件当前不可操作，请检查同步或占用状态后重试'
        else:
            logging.exception('Suite operation failed')
            status,message=500,'暂时未能完成，请重试；已保存的内容仍保留'
        self.response({'error':message},status)

    def static(self, path):
        if not path.is_file():raise FileNotFoundError()
        self.response(path.read_bytes(),mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')

    def proxy(self, path, raw=None):
        if path.split('?',1)[0]=='/api/shutdown':
            raise PermissionError('请在原 WorkOS 服务中管理其关闭；退出套件只停止套件启动的组件')
        status,headers,body=self.app.core.exchange(self.command,path,raw,dict(self.headers))
        mime=next((value for key,value in headers if key.lower()=='content-type'),'application/octet-stream')
        forwarded=[(k,v) for k,v in headers if k.lower() in ('set-cookie','location','content-disposition')]
        self.response(body,status,mime,forwarded)

    def do_GET(self):
        try:
            self.check_local()
            url=urlsplit(self.path);path=url.path
            query=parse_qs(url.query,keep_blank_values=True)
            if any(len(v)!=1 for v in query.values()):raise ValueError('同一参数不能重复')
            query={k:v[0] for k,v in query.items()}
            if path=='/api/health':return self.response({'app':'workos-suite','version':__version__,'status':'ok'})
            if path=='/api/suite/bootstrap':return self.response({'version':__version__,'csrf':self.app.csrf,
                'components':self.app.components(),'roots':self.app.files.roots(),'repositories':self.app.repositories,
                'capabilities':{'local_only':True,'modules':7,'capture_defaults_off':True,
                    'automatic_capture':any(self.app.adapters.get('memory','settings')['settings'].values())}})
            if path=='/api/suite/components':return self.response({'components':self.app.components(),'events':self.app.runtime.events()})
            if path=='/api/suite/ideas':return self.response(self.app.ideas.list())
            if path=='/api/suite/roots':return self.response({'roots':self.app.files.roots()})
            if path=='/api/suite/native-tools':
                if query:raise ValueError('原版工具检查不接受参数')
                return self.response(self.app.native_tools.describe())
            if path in ('/api/suite/files','/api/suite/files/search','/api/suite/file-preview','/api/suite/file'):
                if set(query)-{'root_id','path','q'}:raise ValueError('文件请求参数无效')
                root=query.get('root_id');relative=query.get('path','')
                if path.endswith('/search'):return self.response(self.app.files.search(root,relative,query.get('q','')))
                if path.endswith('/files'):return self.response(self.app.files.list(root,relative,query.get('q','')))
                if path.endswith('file-preview'):return self.response(self.app.files.preview(root,relative))
                file=self.app.files.resolve(root,relative)
                if not file.is_file():raise ValueError('请选择一个文件')
                if file.stat().st_size>20_000_000:raise ValueError('预览下载最多20MB，请在文件夹中打开大文件')
                raw=file.read_bytes()
                if len(raw)>20_000_000:raise ValueError('文件过大，请在文件夹中打开')
                disposition='attachment; filename=download; filename*=UTF-8\'\''+quote(file.name)
                return self.response(raw,mime='application/octet-stream',headers=[('Content-Disposition',disposition)])
            if path=='/api/suite/processes':return self.response(self.app.processes.list())
            if path=='/api/suite/recordings':return self.response(self.app.adapters.get('qwen','recordings'))
            match=re.fullmatch(r'/api/suite/(memory|qwen|phone)/([a-z-]+)',path)
            if match:return self.response(self.app.adapters.get(*match.groups(),query))
            if path.startswith('/api/suite/'):raise FileNotFoundError()
            if path.startswith('/api/') or path.startswith('/auth/') or path in CORE_ASSETS:return self.proxy(self.path)
            if path in ('/workos','/workos/'):return self.proxy('/')
            if path=='/workos-embed.css':return self.response('#sidebar,.sidebar-scrim{display:none!important}.app-shell{grid-template-columns:minmax(0,1fr)!important}.main-area{margin-left:0!important}',mime='text/css; charset=utf-8')
            if path in ('/','/index.html'):return self.static(APP/'web'/'index.html')
            if path in ('/suite.js','/suite.css','/icon.svg'):return self.static(APP/'web'/path[1:])
            if path in ('/idea','/idea/'):return self.static(APP/'components'/'ideas'/'index.html')
            if path.startswith('/idea/') and path[6:] in IDEA_ASSETS:return self.static(APP/'components'/'ideas'/path[6:])
            if path=='/favicon.ico':return self.response(b'',204,'image/x-icon')
            raise FileNotFoundError()
        except Exception as exc:self.error(exc)
    do_HEAD=do_GET

    def do_POST(self):
        try:
            path=urlsplit(self.path).path
            self.check_local(mutation=True,suite=path.startswith('/api/suite/'))
            body,raw=self.json_body()
            if path=='/api/suite/ideas':return self.response(self.app.ideas.add(body,self.app.core),201)
            match=re.fullmatch(r'/api/suite/ideas/([a-f0-9]{32})/share',path)
            if match:return self.response(self.app.ideas.share(match[1],body,self.app.core))
            if path=='/api/suite/roots':
                if set(body)-{'path','label'}:raise ValueError('目录参数无效')
                return self.response(self.app.files.add_root(body.get('path'),body.get('label','')),201)
            if path=='/api/suite/files/operation':return self.response(self.app.files.operation(body))
            if path=='/api/suite/files/clipboard':return self.response(self.app.files.clipboard(body))
            if path=='/api/suite/files/open':return self.response(self.app.files.open(body))
            if path=='/api/suite/files/import':return self.response(self.app.ideas.import_files(body,self.app.files,self.app.core))
            match=re.fullmatch(r'/api/suite/native-tools/(files|processes)/launch',path)
            if match:return self.response(self.app.native_tools.launch(match[1],body))
            if path=='/api/suite/processes/control':return self.response(self.app.processes.control(body))
            match=re.fullmatch(r'/api/suite/components/([a-z-]+)/([a-z]+)',path)
            if match:
                if body:raise ValueError('组件启动不接受额外命令')
                return self.response(self.app.component_action(*match.groups()))
            match=re.fullmatch(r'/api/suite/(memory|qwen|phone)/([a-z-]+)',path)
            if match:return self.response(self.app.adapters.post(*match.groups(),body))
            if path=='/api/suite/shutdown':
                if body:raise ValueError('关闭参数无效')
                self.response({'stopping':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start();return
            if path.startswith('/api/suite/'):raise FileNotFoundError()
            if path.startswith('/api/') or path.startswith('/auth/'):return self.proxy(self.path,raw)
            raise FileNotFoundError()
        except Exception as exc:self.error(exc)

    def do_PUT(self):
        try:
            path=urlsplit(self.path).path
            self.check_local(mutation=True)
            _,raw=self.json_body()
            if path.startswith('/api/') and not path.startswith('/api/suite/'):return self.proxy(self.path,raw)
            raise FileNotFoundError()
        except Exception as exc:self.error(exc)

    do_PATCH=do_PUT

    def do_DELETE(self):
        try:
            path=urlsplit(self.path).path
            self.check_local(mutation=True,suite=path.startswith('/api/suite/'))
            match=re.fullmatch(r'/api/suite/ideas/([a-f0-9]{32})',path)
            if match:return self.response(self.app.ideas.delete(match[1]))
            if path.startswith('/api/') and not path.startswith('/api/suite/'):return self.proxy(self.path)
            raise FileNotFoundError()
        except Exception as exc:self.error(exc)


def main():
    parser=argparse.ArgumentParser(description='WorkOS Suite 本机工作台')
    parser.add_argument('--port',type=int,default=18880)
    parser.add_argument('--data-dir',type=Path,default=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local'/'share')/'WorkOS-Suite')
    parser.add_argument('--isolated',action='store_true',help='独立测试Core，不连接现有WorkOS')
    parser.add_argument('--empty-roots',action='store_true',help='不添加默认资料目录')
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('端口应在1024至65535之间')
    # Bind before starting any children, so port conflicts cannot leave workers.
    try:server=LocalServer(('127.0.0.1',args.port),Handler)
    except OSError:
        print('本机端口已占用，请打开现有套件或选择其他端口。',flush=True);return 1
    args.data_dir.mkdir(parents=True,exist_ok=True)
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO,handlers=[RotatingFileHandler(args.data_dir/'suite.log',maxBytes=1_000_000,backupCount=2,encoding='utf-8')])
    app=None
    try:
        app=Application(args.data_dir,args.port,isolated=args.isolated,empty_roots=args.empty_roots);server.app=app
        def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
        if hasattr(signal,'SIGBREAK'):signal.signal(signal.SIGBREAK,stop)
        print(f'WorkOS Suite ready at http://127.0.0.1:{args.port}',flush=True)
        server.serve_forever()
    finally:
        server.server_close()
        if app:app.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
