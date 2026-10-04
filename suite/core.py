"""Fixed-loopback WorkOS bridge; the original service keeps its own database."""
from __future__ import annotations
import http.client
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit
from .runtime import ComponentSpec


class CoreUnavailable(ValueError):
    status_code = 503


class CoreResponseError(ValueError):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class CoreProxy:
    def __init__(self, app_dir, data_dir, runtime, isolated=False, *, core_data_dir=None, core_settings=None):
        self.runtime = runtime
        self.root = Path(app_dir).resolve() / 'components' / 'workos'
        settings = dict(core_settings or {})
        if set(settings) - {'WORKOS_SYNC_ROOT', 'WORKOS_PUBLIC_ORIGIN', 'WORKOS_PUBLIC_AUTH_MODE'}:
            raise ValueError('研究后台配置包含未支持的字段')
        if any(not isinstance(value, str) or len(value) > 8192 or '\x00' in value for value in settings.values()):
            raise ValueError('研究后台配置值不正确')
        self.public_origin = settings.get('WORKOS_PUBLIC_ORIGIN', '')
        if self.public_origin:
            public = urlsplit(self.public_origin)
            if public.scheme != 'https' or not public.hostname or public.username or public.password or public.path or public.query or public.fragment:
                raise ValueError('公网地址必须是固定 HTTPS 网站地址')
        if settings.get('WORKOS_PUBLIC_AUTH_MODE', 'password') != 'password':
            raise ValueError('套件公网入口当前只支持已配置的密码账户')
        if settings.get('WORKOS_SYNC_ROOT') and not Path(settings['WORKOS_SYNC_ROOT']).is_absolute():
            raise ValueError('同步文件夹必须是绝对路径')
        if core_data_dir is not None and not Path(core_data_dir).is_absolute():
            raise ValueError('研究后台数据文件夹必须是绝对路径')
        self.data_dir = Path(core_data_dir).resolve() if core_data_dir is not None else Path(data_dir).resolve() / 'workos'
        self.port = 18866
        self.lease = None
        requires_owned = core_data_dir is not None or bool(settings)
        if requires_owned and self._existing():
            try:
                existing = self.json('GET', '/api/bootstrap', timeout=2)
                same = Path(existing['data_dir']).resolve() == self.data_dir
            except (KeyError, ValueError, OSError):
                raise ValueError('原研究后台运行中，无法确认数据位置；请先安全退出它') from None
            if same:
                raise ValueError('原研究后台仍占用此数据，请先安全退出再启动新版 Suite')
        # An explicit personal data location starts the packaged Core, rather
        # than silently adopting a legacy server with a different data set.
        if isolated or requires_owned or not self._existing():
            self.port = free_port()
            from .core_lease import CoreLease
            self.lease = CoreLease(self.data_dir)
        # A trusted, source-fixed bootstrap clears inherited project/sync roots.
        # The parent watcher prevents an orphan if the Windows window is closed.
        bootstrap = (
            "import os,sys,json,runpy,threading,time,psutil;"
            "root=sys.argv.pop(1);parent=int(sys.argv.pop(1));birth=float(sys.argv.pop(1));settings=json.loads(sys.argv.pop(1));"
            "[os.environ.pop(k,None) for k in list(os.environ) if k.startswith('WORKOS_')];"
            "os.environ.update(settings);"
            "sys.path[:0]=[root,root+'/vendor'];"
            "exec(\"def watch():\\n while True:\\n  time.sleep(1)\\n  try:\\n   p=psutil.Process(parent)\\n   alive=p.is_running() and p.create_time()==birth\\n  except psutil.Error: alive=False\\n  if not alive: os._exit(0)\\n\");"
            "threading.Thread(target=watch,daemon=True).start();"
            "runpy.run_module('workos.server',run_name='__main__')"
        )
        import psutil
        args = (sys.executable, '-I', '-B', '-c', bootstrap, str(self.root), str(os.getpid()),
                str(psutil.Process().create_time()), json.dumps(settings), '--port', str(self.port),
                '--data-dir', str(self.data_dir))
        runtime.register(ComponentSpec(id='workos', command=args, cwd=self.root,
            label='研究与交付', health_port=self.port, health_path='/api/health',
            health_identity={'app':'local-workos'}, start_timeout=20))
        try:
            self.runtime.ensure('workos')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.lease is not None:
            self.lease.close()
            self.lease = None

    def _existing(self):
        try:
            code, _, raw = self.exchange('GET', '/api/health', timeout=.7)
            return code == 200 and json.loads(raw).get('app') == 'local-workos'
        except (OSError, ValueError, http.client.HTTPException):
            return False

    def exchange(self, method, path, body=None, headers=None, timeout=130, *, remote_origin=None):
        if not path.startswith('/') or path.startswith('//') or '\r' in path or '\n' in path:
            raise ValueError('工作台请求路径无效')
        filtered = {key:value for key,value in (headers or {}).items()
                    if key.lower() in {'content-type','x-csrf-token','cookie','x-workspace','accept','x-workos-request-id'}}
        filtered['Host'] = f'127.0.0.1:{self.port}'
        if method not in ('GET','HEAD'):
            filtered['Origin'] = f'http://127.0.0.1:{self.port}'
        if remote_origin:
            if not self.public_origin or remote_origin != self.public_origin:
                raise PermissionError('公网请求地址与后台配置不一致')
            filtered['Host'] = urlsplit(self.public_origin).netloc
            filtered['X-Forwarded-For'] = '127.0.0.1'
            if method not in ('GET', 'HEAD'):
                filtered['Origin'] = self.public_origin
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        try:
            connection.request(method, path, body=body, headers=filtered)
            result = connection.getresponse()
            if result.getheader('Content-Length') and int(result.getheader('Content-Length')) > 40_000_000:
                raise CoreUnavailable('工作台响应过大，请分批导出')
            raw = result.read(40_000_001)
            if len(raw) > 40_000_000: raise CoreUnavailable('工作台响应过大，请分批导出')
            return result.status, result.getheaders(), raw
        except (OSError, http.client.HTTPException) as exc:
            raise CoreUnavailable('研究工作台暂不可用，请在运行管理中启动；输入仍保留') from None
        finally:
            connection.close()

    def json(self, method, path, body=None, *, timeout=130):
        headers = {'Content-Type':'application/json'}
        if method != 'GET':
            boot = self.json('GET', '/api/bootstrap', timeout=timeout)
            headers['X-CSRF-Token'] = boot['csrf']
        raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        status, _, response = self.exchange(method, path, raw, headers, timeout=timeout)
        try: result = json.loads(response)
        except (ValueError, UnicodeError): raise CoreUnavailable('研究工作台返回异常，请重试') from None
        if status >= 400:
            raise CoreResponseError(str(result.get('error') or '研究工作台暂不可用'), status)
        return result
