"""Bounded native integrations. Requests select operations, never executable argv."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import psutil

from .runtime import ComponentSpec
from .qwen import OriginalQwenBridge, QwenError, public_config, read_config, configuration_update


class AdapterError(ValueError):
    """Safe user-facing integration error without worker stderr or private paths."""


class NativeAdapters:
    MEMORY_FLAGS = ('codex_import', 'dsh_import', 'desktop_capture', 'cloud_inbox')

    def __init__(self, components_dir, data_dir, runtime, node_path=None, python_path=sys.executable, *, qwen_external=False):
        self.components_dir = Path(components_dir).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runtime = runtime
        self.node_path = str(Path(node_path or shutil.which('node') or '').resolve())
        self.python_path = str(Path(python_path).resolve())
        self._lock = threading.RLock()
        self._workers = {}
        self._mirrors = []
        self._adb_port = None
        self._adb_registered = False
        self._closed = False
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._qwen_bridge = OriginalQwenBridge(qwen_external)
        self._register_worker('memory', 'memory_worker.mjs', self.node_path, self._memory_dependency())
        self._register_worker('qwen', 'qwen_worker.py', self.python_path, self._qwen_dependency())

    def _memory_dependency(self):
        if not Path(self.node_path).is_file(): return '记忆服务需要 Node.js 22.15 或更新版本，请使用完整套件'
        if not (self.components_dir / 'memory' / 'src' / 'store.mjs').is_file(): return '记忆组件源码缺失，请重新解压完整套件'
        return ''

    def _qwen_dependency(self):
        if os.name != 'nt': return '千问自动录音需要 Windows 和已登录的千问客户端'
        if not (self.components_dir / 'qwen' / 'engine.py').is_file(): return '千问组件源码缺失，请重新解压完整套件'
        # This only examines module metadata; importing the audio stack here could touch hardware.
        missing = [name for name in ('pycaw', 'comtypes', 'psutil', 'soundcard', 'numpy', 'websocket') if importlib.util.find_spec(name) is None]
        if missing: return '千问自动录音依赖尚未安装，请使用完整 Windows 套件'
        return ''

    def _register_worker(self, component, filename, executable, reason):
        folder = self.data_dir / component
        folder.mkdir(parents=True, exist_ok=True)
        worker = {'token':secrets.token_urlsafe(40), 'receipt':folder / 'worker-receipt.json', 'data':folder, 'id':f'suite-{component}'}
        self._workers[component] = worker
        env = {'SUITE_COMPONENT_ROOT':str(self.components_dir/component), 'SUITE_WORKER_DATA':str(folder),
               'SUITE_WORKER_RECEIPT':str(worker['receipt']), 'SUITE_WORKER_TOKEN':worker['token'], 'SUITE_PARENT_PID':str(os.getpid())}
        if component == 'qwen':
            env['SUITE_QWEN_EXTERNAL'] = '1' if self._qwen_bridge.enabled else '0'
            env['SUITE_QWEN_LOCK_FILE'] = str(Path(os.environ.get('LOCALAPPDATA', self.data_dir)) / 'QwenAutoRecord' / 'suite-listener.lock') if self._qwen_bridge.enabled else str(folder / 'listener.lock')
            original = self._qwen_bridge.discover()
            if original:
                env['SUITE_QWEN_ORIGINAL_CONFIG'] = str(original['config'])
                env['SUITE_QWEN_ORIGINAL_ROOT'] = str(original['root'])
        argv=(executable,'-B',str(Path(__file__).parent/'workers'/filename)) if filename.endswith('.py') else (executable,str(Path(__file__).parent/'workers'/filename))
        self.runtime.register(ComponentSpec(id=worker['id'], command=argv,
            cwd=folder, environment=env, dependency_reason=reason, label='个人记忆' if component=='memory' else '千问自动录音',
            healthcheck=lambda:self._healthy(component), start_timeout=8))

    def _receipt(self, component):
        try:
            receipt=json.loads(self._workers[component]['receipt'].read_text(encoding='utf-8'))
            if not isinstance(receipt,dict) or isinstance(receipt.get('port'),bool) or not isinstance(receipt.get('port'),int) or not 1<=receipt['port']<=65535: raise ValueError()
            if isinstance(receipt.get('pid'),bool) or not isinstance(receipt.get('pid'),int) or receipt['pid']<=0: raise ValueError()
            return receipt
        except (ValueError,OSError): raise AdapterError('组件尚未就绪，请先启动或重试') from None

    def _call(self, component, action, method='GET', body=None, query=None, timeout=5):
        receipt=self._receipt(component)
        path=f'http://127.0.0.1:{receipt["port"]}/{action}'
        if query: path+='?'+urllib.parse.urlencode(query)
        data=json.dumps(body or {},ensure_ascii=False).encode() if method=='POST' else None
        request=urllib.request.Request(path,data=data,method=method,headers={'Authorization':'Bearer '+self._workers[component]['token'],'Content-Type':'application/json'})
        try:
            with self._opener.open(request,timeout=timeout) as response:
                raw=response.read(4_000_001)
                if len(raw)>4_000_000: raise AdapterError('组件响应过大，请减少查询数量')
                result=json.loads(raw)
                if not isinstance(result,dict): raise ValueError()
                return result
        except urllib.error.HTTPError as error:
            if error.code==401: raise AdapterError('组件身份校验未通过，请重新启动套件') from None
            raise AdapterError('本地操作未完成，请检查输入或组件状态') from None
        except (urllib.error.URLError,TimeoutError,OSError,ValueError):
            raise AdapterError('本地组件未响应，请检查状态或重新启动') from None

    def _healthy(self, component):
        try:
            state=self.runtime.status(self._workers[component]['id'])
            # These ephemeral workers are private children, never an ambient
            # service to borrow. Reject old receipts during stop/restart.
            if not state['owned'] or not state['pid']:return False
            receipt=self._receipt(component)
            if receipt['pid']!=state['pid'] and not any(p.pid==state['pid'] for p in psutil.Process(receipt['pid']).parents()):return False
            value=self._call(component,'health',timeout=.4)
            return value.get('service')==f'suite-{component}' and value.get('pid')==receipt['pid']
        except (AdapterError,psutil.Error):return False

    def _start(self, component):
        state=self.runtime.start(self._workers[component]['id'])
        if state['status'] not in ('running','borrowed'):return self._status(component)
        if not self._healthy(component):raise AdapterError('组件未就绪，请重试')
        return self._status(component)

    def _settings(self):
        value={name:False for name in self.MEMORY_FLAGS}
        try:
            saved=json.loads((self.data_dir/'memory'/'settings.json').read_text(encoding='utf-8'))
            if isinstance(saved,dict):
                for name in value:
                    if isinstance(saved.get(name),bool):value[name]=saved[name]
        except (ValueError,OSError):pass
        return value

    def _status(self, component):
        if component == 'qwen':
            original = self._qwen_bridge.discover()
            if original:
                return self._qwen_bridge.status(original)
        state=self.runtime.status(self._workers[component]['id'])
        if state['status'] in ('running','borrowed') and self._healthy(component):
            result=self._call(component,'status');result['can_stop']=state['can_stop'];return result
        result={'component':component,'status':state['status'],'running':False,'detail':state['detail'],
                'dependency_reason':state['dependency_reason'],'can_start':state['can_start'],'can_stop':state['can_stop']}
        if component=='memory':result.update(count=0,settings=self._settings(),local_only=True)
        else:result.update(source='suite',borrowed=False,listening=False,paused=False,recording=False,active_calls=[],pending=[],archive={'enabled':False,'count':len(self._recordings())},control_note='停止自动化不结束千问内的录音，请在千问中手动停止录音')
        return result

    def _qwen_enabled(self):
        return (read_config(self.data_dir / 'qwen' / 'automation.json') or {}).get('enabled') is True

    def _save_qwen_enabled(self, enabled, original=None):
        folder = self.data_dir / 'qwen'
        target = folder / 'automation.json'
        if target.is_symlink():raise AdapterError('千问自动化设置路径无效')
        temporary = folder / 'automation.json.tmp'
        if temporary.is_symlink():raise AdapterError('千问自动化设置路径无效')
        temporary.write_text(json.dumps({'enabled': enabled}), encoding='utf-8')
        os.replace(temporary, target)
        if original:
            target = folder / 'original-config-reference.json'
            if target.is_symlink():raise AdapterError('原千问设置引用路径无效')
            temporary = folder / 'original-config-reference.json.tmp'
            if temporary.is_symlink():raise AdapterError('原千问设置引用路径无效')
            temporary.write_text(json.dumps({'config': str(original['config']), 'root': str(original['root'])}), encoding='utf-8')
            os.replace(temporary, target)

    def restore_qwen(self):
        """Only previously explicit Suite authorization resumes its own watcher."""
        with self._lock:
            original=self._qwen_bridge.discover(fresh=True)
            if original:return self._qwen_bridge.status(original)
            if not self._qwen_enabled():return self._status('qwen')
            return self.post('qwen', 'start', {})

    def _binary(self, name):
        folder='platform-tools' if name=='adb' else 'scrcpy'
        package=self.components_dir.parent.parent if self.components_dir.parent.name=='app' else self.components_dir.parent
        filename=name+('.exe' if os.name=='nt' else '')
        for candidate in (package/'runtime'/'phone'/folder/filename,self.components_dir/'phone'/'bin'/folder/filename):
            if candidate.is_file():return candidate
        return None

    def _recordings(self):
        folder=self.data_dir/'qwen'/'recordings'
        if not folder.is_dir():return []
        items=[]
        for entry in sorted(folder.iterdir(),key=lambda p:p.name,reverse=True)[:100]:
            if entry.is_symlink() or not entry.is_dir():continue
            try:
                files=[f for f in entry.rglob('*') if f.is_file() and not f.is_symlink()]
                items.append({'id':entry.name,'name':entry.name,'files':len(files),'size_bytes':sum(f.stat().st_size for f in files),'modified_at':entry.stat().st_mtime})
            except OSError:continue
        return items

    def _phone_status(self):
        available=bool(self._binary('adb') and self._binary('scrcpy'))
        mirrors=[self.runtime.status(key) for key in self._mirrors]
        return {'component':'phone','status':'running' if any(s['status']=='running' for s in mirrors) else ('stopped' if available else 'needs_setup'),
                'dependency_reason':'' if available else '手机控制需要完整套件内的官方 ADB 和 scrcpy',
                'mirrors':mirrors,'qr_supported':False,'pairing_guidance':'手机打开开发者选项 → 无线调试 → 使用配对码配对，填写手机显示的地址和六位配对码；然后连接无线调试地址。'}

    def _adb_healthy(self):
        if not self._adb_registered:return False
        try:
            if not self.runtime.status('suite-adb')['owned']:return False
            with socket.create_connection(('127.0.0.1',self._adb_port),timeout=.4) as client:
                client.sendall(b'000chost:version')
                def read(size):
                    result=b''
                    while len(result)<size:
                        chunk=client.recv(size-len(result))
                        if not chunk:raise OSError()
                        result+=chunk
                    return result
                if read(4)!=b'OKAY':return False
                length=int(read(4),16)
                return 1<=length<=16 and bool(re.fullmatch(rb'[0-9a-fA-F]+',read(length)))
        except (OSError,ValueError):return False

    def _ensure_adb(self):
        adb=self._binary('adb')
        if not adb:raise AdapterError('ADB 缺失，请使用完整 Windows 套件')
        if not self._adb_registered:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0));self._adb_port=probe.getsockname()[1]
            folder=self.data_dir/'phone';folder.mkdir(exist_ok=True)
            self.runtime.register(ComponentSpec(id='suite-adb',label='手机连接服务',
                command=(str(adb),'-L',f'tcp:127.0.0.1:{self._adb_port}','server','nodaemon'),cwd=folder,
                environment={'ADB_MDNS_AUTO_CONNECT':'0'},health_port=self._adb_port,healthcheck=self._adb_healthy,start_timeout=12))
            self._adb_registered=True
        state=self.runtime.start('suite-adb')
        if state['status']!='running' or not self._adb_healthy():raise AdapterError('Suite 手机连接服务未就绪，请重试；原 ADB 服务未被改动')
        return self._adb_port

    def describe(self):
        with self._lock:return {'qwen':self._status('qwen'),'memory':self._status('memory'),'phone':self._phone_status()}

    @staticmethod
    def _dict(value):
        if not isinstance(value,dict):raise AdapterError('参数必须是对象')
        return value

    @staticmethod
    def _keys(value, allowed):
        if set(value)-set(allowed):raise AdapterError('包含不支持的参数')

    @staticmethod
    def _limit(value):
        try:
            if isinstance(value,bool):raise ValueError()
            number=int(value)
            if not 1<=number<=50:raise ValueError()
            return number
        except (ValueError,TypeError):raise AdapterError('数量应为 1 至 50') from None

    def get(self, component, action, querydict=None):
        query=self._dict(querydict or {})
        with self._lock:
            if self._closed:raise AdapterError('套件正在关闭')
            if component=='phone':
                self._keys(query,())
                if action=='status':return self._phone_status()
                if action=='devices':return self._devices()
            if component in ('memory','qwen') and action=='status':
                self._keys(query,());return self._status(component)
            if component=='qwen' and action in ('config','diagnostics'):
                self._keys(query,())
                original=self._qwen_bridge.discover()
                if original:
                    result=self._qwen_bridge.status(original)
                    return {'configuration':public_config(original.get('cfg') or {}, 'original')} if action=='config' else result
                if self._healthy('qwen'):return self._call('qwen','config' if action=='config' else 'status')
                cfg=read_config(self.data_dir/'qwen'/'config.json')
                return {'configuration':public_config(cfg or {},'suite' if cfg else 'defaults')} if action=='config' else self._status('qwen')
            if component=='memory' and action=='settings':
                self._keys(query,());return {'settings':self._settings()}
            if component=='memory' and action in ('recent','search'):
                self._keys(query,('limit','q') if action=='search' else ('limit',))
                safe={'limit':self._limit(query.get('limit',20))}
                if action=='search':
                    q=query.get('q','')
                    if not isinstance(q,str) or len(q)>2000:raise AdapterError('搜索文字过长')
                    safe['q']=q
                if not self._healthy('memory'):return {'events':[],'total':0,'status':'stopped','detail':'请先启动个人记忆服务'}
                return self._call('memory',action,query=safe)
            if component=='qwen' and action=='recordings':
                self._keys(query,())
                original=self._qwen_bridge.discover()
                if original and original.get('snapshot'):
                    try:
                        value=self._qwen_bridge._request(original,'recordings')
                        items=value.get('items',[])
                        if not isinstance(items,list):raise ValueError()
                        safe=[]
                        for item in items[:100]:
                            if not isinstance(item,dict):continue
                            name=item.get('folder')
                            if not isinstance(name,str) or not name or len(name)>160 or '/' in name or '\\' in name:continue
                            size=item.get('bytes',0)
                            size=size if isinstance(size,int) and not isinstance(size,bool) and 0<=size<=10**16 else 0
                            modified=item.get('mtime')
                            modified=modified if isinstance(modified,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',modified) else None
                            safe.append({'id':name,'name':name,'files':None,'size_bytes':size,'modified_at':modified})
                        return {'recordings':safe,'total':len(safe),'source':'original'}
                    except (OSError,ValueError):raise AdapterError('原千问录音列表暂未就绪') from None
                items=self._recordings();return {'recordings':items,'total':len(items)}
            raise AdapterError('不支持的组件操作')

    def post(self, component, action, bodydict=None):
        body=self._dict(bodydict or {})
        with self._lock:
            if self._closed:raise AdapterError('套件正在关闭')
            if component in ('memory','qwen') and action in ('start','stop','trigger'):
                self._keys(body,())
                if component=='memory' and action=='trigger':raise AdapterError('不支持的组件操作')
                if component=='qwen':
                    original=self._qwen_bridge.discover(fresh=True)
                    if original:
                        try:result=self._qwen_bridge.action(original,action)
                        except (OSError,ValueError):raise AdapterError('原千问操作未完成，请检查原托盘监听和客户端状态') from None
                        if action in ('start','stop'):self._save_qwen_enabled(action=='start',original)
                        return result
                if action=='stop':
                    if component=='qwen' and self._healthy('qwen'):self._call('qwen','stop',method='POST')
                    self.runtime.stop(self._workers[component]['id'])
                    if component=='qwen':self._save_qwen_enabled(False)
                    return self._status(component)
                state=self._start(component)
                if state['status'] not in ('running','stopped'):return state
                if component=='qwen':
                    result=self._call('qwen',action,method='POST',timeout=45)
                    if action=='start' and result.get('listening'):self._save_qwen_enabled(True)
                    return result
                return state
            if component=='qwen' and action=='config':
                original=self._qwen_bridge.discover(fresh=True)
                if original:
                    try:return self._qwen_bridge.action(original,'config',body)
                    except QwenError as error:raise AdapterError(str(error)) from None
                    except (OSError,ValueError):raise AdapterError('原千问设置未保存，请检查原托盘状态') from None
                if not self._healthy('qwen'):
                    state=self._start('qwen')
                    if state['status'] not in ('running','stopped'):return state
                return self._call('qwen','config',method='POST',body=body)
            if component=='memory' and action in ('add','forget','settings'):
                self._keys(body,('text','title','type','tags') if action=='add' else ('id',) if action=='forget' else self.MEMORY_FLAGS)
                if action=='add':
                    if not isinstance(body.get('text'),str) or not body['text'].strip() or len(body['text'])>65536:raise AdapterError('请输入不超过 65536 字符的记忆内容')
                    if 'title' in body and (not isinstance(body['title'],str) or len(body['title'])>500):raise AdapterError('记忆标题无效')
                    if 'tags' in body and (not isinstance(body['tags'],list) or len(body['tags'])>32 or any(not isinstance(x,str) or len(x)>100 for x in body['tags'])):raise AdapterError('记忆标签无效')
                elif action=='forget':
                    if not isinstance(body.get('id'),str) or not re.fullmatch(r'evt_[a-zA-Z0-9_]{1,90}',body['id']):raise AdapterError('记忆标识无效')
                elif any(not isinstance(v,bool) for v in body.values()):raise AdapterError('捕获开关必须是开或关')
                state=self._start('memory')
                if state['status']!='running':return state
                return self._call('memory',action,method='POST',body=body,timeout=20)
            if component=='phone':return self._phone_post(action,body)
            raise AdapterError('不支持的组件操作')

    def _adb(self, args, *, binary=False, timeout=12):
        adb=self._binary('adb')
        if not adb:raise AdapterError('ADB 缺失，请使用完整 Windows 套件')
        port=self._ensure_adb()
        options={'stdin':subprocess.DEVNULL,'capture_output':True,'timeout':timeout}
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
        try:
            result=subprocess.run([str(adb),'-H','127.0.0.1','-P',str(port),*args],**options)
            if result.returncode!=0:raise AdapterError('ADB 操作未完成，请检查设备授权和连接状态')
            return result.stdout if binary else result.stdout.decode('utf-8',errors='replace')
        except (OSError,subprocess.TimeoutExpired):raise AdapterError('ADB 未响应，请检查手机连接后重试') from None

    def _devices(self):
        text=self._adb(['devices','-l'])
        devices=[]
        for line in text.splitlines():
            parts=line.split()
            if len(parts)<2 or parts[0]=='List' or not re.fullmatch(r'[a-zA-Z0-9_.:\[\]-]{1,200}',parts[0]):continue
            if parts[1] not in ('device','offline','unauthorized','recovery','sideload'):continue
            model=next((p[6:] for p in parts[2:] if p.startswith('model:')),'')
            devices.append({'serial':parts[0],'state':parts[1],'model':model[:100]})
        return {'devices':devices,'total':len(devices)}

    @staticmethod
    def _address(value):
        if not isinstance(value,str) or len(value)>100:raise AdapterError('请输入手机显示的 IP 地址和端口')
        try:
            host,port=value.rsplit(':',1);ip=ipaddress.ip_address(host.strip('[]'));number=int(port)
            if not (ip.is_private or ip.is_loopback) or ip.is_unspecified or ip.is_multicast or not 1<=number<=65535:raise ValueError()
            return (f'[{ip}]' if ip.version==6 else str(ip))+f':{number}'
        except ValueError:raise AdapterError('请输入有效的局域网 IP 地址和端口') from None

    def _serial(self,value):
        if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9_.:\[\]-]{1,200}',value):raise AdapterError('请明确选择一台已连接的手机')
        if not any(d['serial']==value and d['state']=='device' for d in self._devices()['devices']):raise AdapterError('手机未连接或尚未授权，请刷新设备列表')
        return value

    def _phone_post(self, action, body):
        if action in ('pair','connect'):
            self._keys(body,('address','code') if action=='pair' else ('address',))
            address=self._address(body.get('address'))
            if action=='pair':
                code=body.get('code')
                if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code):raise AdapterError('请输入手机显示的六位配对码')
                result=self._adb(['pair',address,code],timeout=30)
                if 'Successfully paired' not in result:raise AdapterError('配对未完成，请重新打开手机配对码页面')
                return {'paired':True,'detail':'配对完成，请继续连接手机无线调试地址'}
            result=self._adb(['connect',address],timeout=15)
            if not any(x in result.lower() for x in ('connected to','already connected')):raise AdapterError('连接未完成，请核对无线调试地址')
            return {'connected':True,'detail':'设备已连接，请刷新列表并选择手机'}
        if action=='screenshot':
            self._keys(body,('serial',));serial=self._serial(body.get('serial'))
            data=self._adb(['-s',serial,'exec-out','screencap','-p'],binary=True)
            if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)>20_000_000:raise AdapterError('手机截图无效或过大')
            return {'mime':'image/png','base64':base64.b64encode(data).decode()}
        if action=='stop':
            self._keys(body,())
            for key in self._mirrors:self.runtime.stop(key)
            return self._phone_status()
        if action=='mirror':
            self._keys(body,('serial','max_size','max_fps','no_audio'))
            serial=self._serial(body.get('serial'))
            size=body.get('max_size',1280);fps=body.get('max_fps',60);no_audio=body.get('no_audio',True)
            if isinstance(size,bool) or not isinstance(size,int) or not 320<=size<=3840:raise AdapterError('镜像尺寸应为 320 至 3840')
            if isinstance(fps,bool) or not isinstance(fps,int) or not 1<=fps<=120:raise AdapterError('镜像帧率应为 1 至 120')
            if not isinstance(no_audio,bool):raise AdapterError('声音开关无效')
            binary=self._binary('scrcpy')
            if not binary:raise AdapterError('scrcpy 缺失，请使用完整 Windows 套件')
            port=self._ensure_adb()
            args=(str(binary),'-s',serial,f'--max-size={size}',f'--max-fps={fps}',*(['--no-audio'] if no_audio else []))
            key='phone-mirror-'+hashlib.sha256(json.dumps(args).encode()).hexdigest()[:16]
            if key not in self._mirrors:
                if len(self._mirrors)>=32:raise AdapterError('本次会话的镜像配置过多，请重新打开套件')
                self.runtime.register(ComponentSpec(id=key,command=args,cwd=binary.parent,
                    environment={'ADB':str(self._binary('adb')),'ADB_SERVER_SOCKET':f'tcp:127.0.0.1:{port}','ADB_MDNS_AUTO_CONNECT':'0'},label='手机镜像'))
                self._mirrors.append(key)
            for old in self._mirrors:
                if old!=key:self.runtime.stop(old)
            state=self.runtime.start(key)
            return {'component':'phone','status':state['status'],'mirror':state,'serial':serial}
        if action=='qr':raise AdapterError('原二维码协议与官方 ADB 不兼容，请使用手机的六位配对码')
        raise AdapterError('不支持的手机操作')

    def close(self):
        with self._lock:
            if self._closed:return
            for component in self._workers:
                if component=='qwen' and self._healthy(component):
                    try:self._call(component,'stop',method='POST')
                    except AdapterError:pass
                self.runtime.stop(self._workers[component]['id'])
            for key in self._mirrors:self.runtime.stop(key)
            if self._adb_registered:self.runtime.stop('suite-adb')
            self._closed=True
