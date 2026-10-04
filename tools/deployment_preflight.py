"""Read-only setup report and explicit launch of this portable suite."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

APP=Path(__file__).resolve().parents[1]
PACKAGE=APP.parent if APP.name=='app' else APP


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def readiness():
    checks=[]
    for name,module,label in [('core','psutil','统一工作台与进程管理'),('exports','docx','Word导出'),('slides','pptx','PPT导出'),
                              ('xlsx','openpyxl','Excel文件生成'),('qwen','pycaw','会议录音自动化依赖'),('phone','cryptography','手机配对依赖')]:
        ready=importlib.util.find_spec(module) is not None
        checks.append({'id':name,'ready':ready,'label':label,'detail':'已包含' if ready else '依赖缺失，请重新解压完整套件'})
    node=PACKAGE/'runtime/node/node.exe';adb=PACKAGE/'runtime/phone/platform-tools/adb.exe';scrcpy=PACKAGE/'runtime/phone/scrcpy/scrcpy.exe'
    for name,path,label in [('node',node,'记忆服务运行时'),('adb',adb,'手机连接工具'),('scrcpy',scrcpy,'手机镜像工具')]:
        checks.append({'id':name,'ready':path.is_file(),'label':label,'detail':'已包含' if path.is_file() else '便携工具缺失，请重新解压完整套件'})
    checks.extend([{'id':'qwen-client','ready':False,'label':'千问客户端与账号','detail':'需在本机安装并登录官方千问；套件不复制账号'},
                   {'id':'android','ready':False,'label':'手机授权','detail':'需选择自己的设备并授权USB或无线调试'},
                   {'id':'office','ready':False,'label':'Office原生回报与预览','detail':'需本机安装Office；没有Excel时仅保留假设，不宣称已原生重算'},
                   {'id':'models','ready':False,'label':'AI模型','detail':'需在工作台配置可用账户/服务；尚未调用模型'}])
    return {'schema_version':1,'python_version':sys.version.split()[0],'isolated':bool(sys.flags.isolated),'host_site_loaded':bool(sys.flags.no_user_site==0),
            'core_ready':all(x['ready'] for x in checks if x['id'] in ('core','node','adb','scrcpy')),'checks':checks}


def health(port):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect()).open(f'http://127.0.0.1:{port}/api/health',timeout=.5) as response:
            body=json.loads(response.read(16_385))
            return body if body.get('app')=='workos-suite' else None
    except Exception:return None


def _open(url):
    for variable in ('ProgramFiles(x86)','ProgramFiles','LOCALAPPDATA'):
        base=os.environ.get(variable)
        if not base:continue
        edge=Path(base)/'Microsoft/Edge/Application/msedge.exe'
        if edge.is_file():
            kwargs={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            subprocess.Popen([str(edge),'--app='+url],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
            return
    webbrowser.open(url)


def deployment_profile(data, profile=None):
    path = Path(profile) if profile else data / 'deployment.json'
    if not path.exists():
        if profile:
            raise ValueError('启动配置文件不存在')
        return {}
    if path.stat().st_size > 16384:
        raise ValueError('启动配置文件过大')
    try:
        settings = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(settings, dict) or set(settings) - {'core_data_dir', 'sync_root', 'remote_config'}:
            raise ValueError()
        if any(not isinstance(value, str) or not value or not Path(value).is_absolute() for value in settings.values()):
            raise ValueError()
    except (ValueError, OSError):
        raise ValueError('启动配置格式不正确：只接受研究数据、同步目录和公网配置的绝对路径') from None
    return settings


def start(data_dir=None, *, port=18880, isolated=False, empty_roots=False, open_browser=True,
          core_data_dir=None, sync_root=None, remote_config=None, profile=None):
    """Reuse only an identified Suite; never stop or adopt an unrelated listener."""
    report=readiness()
    if not report['core_ready']:raise ValueError('完整套件依赖缺失，请重新解压安装包并运行环境检查')
    if not 1024<=port<=65535:raise ValueError('启动端口无效')
    existing=health(port)
    url=f'http://127.0.0.1:{port}/'
    if existing:
        if open_browser:_open(url)
        return {'started':False,'borrowed':True,'url':url,'health':existing}
    try:
        with socket.socket() as probe:probe.bind(('127.0.0.1',port))
    except OSError:raise ValueError('套件端口被其他应用占用；不会结束其他应用，请关闭冲突应用后重试')
    data=Path(data_dir or Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'WorkOS-Suite').resolve()
    data.mkdir(parents=True,exist_ok=True)
    settings = {} if isolated else deployment_profile(data, profile)
    for key, value in [('core_data_dir', core_data_dir), ('sync_root', sync_root), ('remote_config', remote_config)]:
        if value is not None:
            if not Path(value).is_absolute():
                raise ValueError('启动配置必须使用绝对路径')
            settings[key] = str(value)
    environment=dict(os.environ)
    environment['PATH']=str(PACKAGE/'runtime/node')+os.pathsep+environment.get('PATH','')
    code="import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('suite.server',run_name='__main__')"
    command=[sys.executable,'-I','-B','-c',code,str(APP),'--port',str(port),'--data-dir',str(data)]
    if isolated:command.append('--isolated')
    if empty_roots:command.append('--empty-roots')
    for key, value in settings.items():
        command += ['--' + key.replace('_', '-'), value]
    log=data/'suite-launch.log'
    with log.open('ab') as output:
        options={'cwd':str(APP),'env':environment,'stdin':subprocess.DEVNULL,'stdout':output,'stderr':subprocess.STDOUT}
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
        process=subprocess.Popen(command,**options)
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        if process.poll() is not None:raise ValueError('套件未能启动，请运行环境检查；现有应用未受影响')
        result=health(port)
        if result:
            if open_browser:_open(url)
            return {'started':True,'borrowed':False,'pid':process.pid,'url':url,'health':result}
        time.sleep(.15)
    # Only this Popen handle belongs to the launcher; never use a PID/global-image sweep.
    process.terminate()
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    raise ValueError('套件未在启动期限内就绪，已结束本次启动的进程')


def stop(*,port=18880):
    """Ask the identified local Suite to close; never signal a PID or image."""
    if not 1024<=port<=65535:raise ValueError('启动端口无效')
    if not health(port):return {'stopped':False,'detail':'该端口没有正在运行的WorkOS Suite；其他应用未受影响'}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    url=f'http://127.0.0.1:{port}'
    try:
        with opener.open(url+'/api/suite/bootstrap',timeout=3) as response:body=json.loads(response.read(500_001))
        csrf=body.get('csrf')
        if not isinstance(csrf,str) or not 20<=len(csrf)<=200:raise ValueError('套件会话校验不可用，请刷新工作台后重试停止.cmd')
        request=urllib.request.Request(url+'/api/suite/shutdown',data=b'{}',headers={'Content-Type':'application/json','Origin':url,'X-CSRF-Token':csrf},method='POST')
        with opener.open(request,timeout=5) as response:ack=json.loads(response.read(16_385))
        if ack.get('stopping') is not True:raise ValueError('套件没有确认退出，请在界面重试')
    except ValueError:raise
    except Exception:raise ValueError('退出请求未成功确认；请检查工作台状态，再运行停止.cmd')
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if not health(port):return {'stopped':True,'detail':'套件已退出；借用的服务和其他应用继续运行'}
        time.sleep(.15)
    raise ValueError('已发送安全退出请求，后台仍在关闭；请稍候，未强制结束其他应用')


if __name__=='__main__':
    parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group();mode.add_argument('--start',action='store_true');mode.add_argument('--stop',action='store_true');parser.add_argument('--port',type=int,default=18880)
    parser.add_argument('--data-dir',type=Path);parser.add_argument('--isolated',action='store_true');parser.add_argument('--empty-roots',action='store_true');parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--core-data-dir', type=Path);parser.add_argument('--sync-root', type=Path);parser.add_argument('--remote-config', type=Path);parser.add_argument('--profile', type=Path)
    args=parser.parse_args()
    try:
        result=start(args.data_dir,port=args.port,isolated=args.isolated,empty_roots=args.empty_roots,open_browser=not args.no_browser,
                     core_data_dir=args.core_data_dir,sync_root=args.sync_root,remote_config=args.remote_config,profile=args.profile) if args.start else stop(port=args.port) if args.stop else readiness()
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except ValueError as error:print(str(error));raise SystemExit(1)
