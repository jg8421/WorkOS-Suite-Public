"""Fixed, local GUI entry points; independent windows are never Suite workers."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

WINDOWS=os.name=='nt'
TOOLS={
    'files':{'title':'完整文件工作台','script':'file_workbench.py',
        'required':('tkinter','fitz','PIL.ImageTk','tksheet','openpyxl','markdown','tkinterweb'),
        'features':['目录标签与收藏','视觉 PDF / 图片 / Office 预览','MSG 与压缩包内容','Markdown / Excel 编辑'],
        'requires_office':True},
    'processes':{'title':'完整进程管理器','script':'process_manager.py','required':('tkinter',),
        'features':['原版进程界面','功耗与温度页','传感器明细和历史曲线'],
        'requires_office':False},
}
MODULE_LABELS={'tkinter':'Tk / Tcl','fitz':'PyMuPDF','PIL.ImageTk':'Pillow / ImageTk',
    'tksheet':'tksheet','openpyxl':'openpyxl','markdown':'Markdown','tkinterweb':'tkinterweb',
    'pythoncom':'pywin32','win32com.client':'pywin32'}
PROBE_CODE=r'''
import contextlib,importlib,io,json,pathlib,sys
modules={}
for name in ('tkinter','fitz','PIL.ImageTk','tksheet','openpyxl','markdown','tkinterweb','pythoncom','win32com.client'):
    try:
        with contextlib.redirect_stdout(io.StringIO()):module=importlib.import_module(name)
        if name=='fitz' and not callable(getattr(module,'open',None)):raise ImportError()
        if name=='tkinterweb' and not hasattr(module,'HtmlFrame'):raise ImportError()
        modules[name]=True
    except Exception:modules[name]=False
tk_resources=False
if modules['tkinter']:
    try:
        import tkinter
        interpreter=tkinter.Tcl()
        library=pathlib.Path(interpreter.eval('info library'))
        tk_resources=(library.parent/('tk'+str(tkinter.TkVersion))/'tk.tcl').is_file()
    except Exception:modules['tkinter']=False
print(json.dumps({'modules':modules,'tk_resources':tk_resources,'version':list(sys.version_info[:3])}))
'''
LAUNCH_CODE="import sys,runpy;from pathlib import Path;script=Path(sys.argv[1]).resolve();sys.path.insert(0,str(script.parent));sys.argv=[str(script)];runpy.run_path(str(script),run_name='__main__')"


class NativeToolUnavailable(ValueError):
    status_code=503


def _python_executable(value):
    """Only trusted discovery outputs, never a browser-supplied path."""
    try:
        path=Path(value)
        if not path.is_absolute() or str(path).startswith(('\\\\','//')):return None
        if not re.fullmatch(r'python(?:\d+(?:\.\d+)?)?w?\.exe',path.name,re.I):return None
        for part in (path,*path.parents):
            info=part.lstat()
            if part.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:return None
        return path.resolve() if path.is_file() else None
    except (OSError,ValueError,TypeError):return None


def _registry_pythons():
    if not WINDOWS:return []
    import winreg
    results=[]
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        for view in (getattr(winreg,'KEY_WOW64_64KEY',0),getattr(winreg,'KEY_WOW64_32KEY',0)):
            try:base=winreg.OpenKey(hive,r'Software\Python',0,winreg.KEY_READ|view)
            except OSError:continue
            with base:
                for vendor_index in range(20):
                    try:vendor_name=winreg.EnumKey(base,vendor_index)
                    except OSError:break
                    try:vendor=winreg.OpenKey(base,vendor_name)
                    except OSError:continue
                    with vendor:
                        for version_index in range(30):
                            try:version=winreg.EnumKey(vendor,version_index)
                            except OSError:break
                            try:installation=winreg.OpenKey(vendor,version+r'\InstallPath')
                            except OSError:continue
                            with installation:
                                try:results.append(winreg.QueryValueEx(installation,'ExecutablePath')[0])
                                except OSError:
                                    try:results.append(str(Path(winreg.QueryValueEx(installation,'')[0])/'python.exe'))
                                    except (OSError,TypeError):pass
    return results


def _launcher_pythons():
    launcher=shutil.which('py')
    if not launcher:return []
    path=Path(launcher)
    try:
        if not path.is_absolute() or path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:return []
        options={'capture_output':True,'text':True,'encoding':'utf-8','errors':'replace','timeout':3}
        if WINDOWS:options['creationflags']=getattr(subprocess,'CREATE_NO_WINDOW',0x08000000)
        result=subprocess.run((str(path),'-0p'),**options)
        if result.returncode or len(result.stdout)>65536:return []
        return [match.group(1) for line in result.stdout.splitlines()
                if (match:=re.search(r'([A-Za-z]:[\\/].*?python(?:\d+(?:\.\d+)?)?\.exe)\s*$',line,re.I))]
    except (OSError,subprocess.TimeoutExpired):return []


def _runtime_environment(executable):
    environment={key:value for key,value in os.environ.items() if key.upper() not in
        {'PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONINSPECT','TCL_LIBRARY','TK_LIBRARY','FILE_WORKBENCH_SELFTEST'}}
    # Packaged Tk libraries can be colocated with the embedded interpreter.
    # No path or environment setting comes from a web request.
    base=Path(executable).parent
    for folder in (base/'tcl',base/'Lib'/'tcl'):
        for version in ('8.6','9.0'):
            tcl=folder/('tcl'+version);tk=folder/('tk'+version)
            if (tcl/'init.tcl').is_file() and (tk/'tk.tcl').is_file():
                environment['TCL_LIBRARY']=str(tcl);environment['TK_LIBRARY']=str(tk)
                return environment
    return environment


class NativeTools:
    def __init__(self,app_dir,*,cache_seconds=60):
        self.app_dir=Path(app_dir).resolve()
        self.cache_seconds=cache_seconds
        self._lock=threading.RLock();self._checked=-float('inf');self._selected={};self._statuses=[]
        self._children=[];self._closed=False

    def _script(self,component):
        path=self.app_dir/'components'/component/TOOLS[component]['script']
        try:
            for part in (path,*path.parents):
                if part==self.app_dir:break
                if part.is_symlink() or getattr(part.lstat(),'st_file_attributes',0)&0x400:return None
            resolved=path.resolve();resolved.relative_to(self.app_dir)
            return resolved if resolved.is_file() else None
        except (OSError,ValueError):return None

    def _candidates(self):
        packaged=self.app_dir.parent/'runtime'/'python'/'python.exe'
        local=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local'/'share')/'WorkOS-Suite'/'native-runtime'/'Scripts'/'python.exe'
        values=[(packaged,'packaged'),(local,'native-runtime'),*( (path,'registered') for path in _registry_pythons()),
                (sys.executable,'current'),*((path,'launcher') for path in _launcher_pythons()),
                *((path,'path') for name in ('python','python3') if (path:=shutil.which(name)))]
        output=[];seen=set()
        for value,source in values:
            path=_python_executable(value)
            if path and str(path).lower() not in seen:
                seen.add(str(path).lower());output.append((path,source))
            if len(output)==8:break
        return output

    def _probe(self,executable,timeout):
        options={'cwd':str(self.app_dir),'capture_output':True,'timeout':timeout,
                 'env':_runtime_environment(executable),'encoding':'utf-8','errors':'replace'}
        if WINDOWS:options['creationflags']=getattr(subprocess,'CREATE_NO_WINDOW',0x08000000)
        try:
            result=subprocess.run((str(executable),'-I','-B','-c',PROBE_CODE),**options)
            if result.returncode or len(result.stdout)>10000:return None
            value=json.loads(result.stdout)
            if not isinstance(value,dict) or not isinstance(value.get('modules'),dict) or not isinstance(value.get('tk_resources'),bool):return None
            return value
        except (OSError,ValueError,subprocess.TimeoutExpired):return None

    def _refresh(self):
        self._selected={};failures={key:[] for key in TOOLS};deadline=time.monotonic()+20
        if WINDOWS:
            for index,(executable,source) in enumerate(self._candidates()):
                if len(self._selected)==len(TOOLS):break
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                # Freshly extracted GUI libraries may incur a first-use scan.
                # Only the first bundled candidate gets the longer cold-start
                # allowance; host runtimes retain their shorter bound.
                allowance=12 if index==0 and source=='packaged' else 5
                probe=self._probe(executable,min(allowance,remaining))
                if probe is None:continue
                modules=probe['modules']
                for key,spec in TOOLS.items():
                    if key in self._selected:continue
                    missing=[MODULE_LABELS[module] for module in spec['required'] if modules.get(module) is not True]
                    if modules.get('tkinter') is True and not probe['tk_resources']:missing.append('Tk 界面资源')
                    if missing:failures[key].append(missing);continue
                    self._selected[key]=(executable,source,probe)
        self._statuses=[]
        for key,spec in TOOLS.items():
            script=self._script(key)
            ready=key in self._selected and script is not None
            missing=min(failures[key],key=len) if failures[key] else []
            reason='' if ready else '完整原版窗口需要 Windows' if not WINDOWS else '原版程序文件缺失或路径无效，请重新解压完整包' if script is None else ('Python 缺少 '+ '、'.join(dict.fromkeys(missing))+'；请使用包含原版界面依赖的完整包' if missing else '未找到通过 Tk 与依赖检查的 Python；请使用包含原版界面依赖的完整包')
            selected=self._selected.get(key)
            optional=bool(selected and selected[2]['modules'].get('pythoncom') is True and selected[2]['modules'].get('win32com.client') is True)
            self._statuses.append({'id':key,'title':spec['title'],'can_launch':ready,'reason':reason,
                'runtime_source':selected[1] if ready else '', 'features':spec['features'],
                'requires_office':spec['requires_office'],'sensor_com_available':optional if key=='processes' else None,
                'accepts_root':False,'scope':'independent_local_gui',
                'detail':'完整窗口使用原工具设置，可在本机选择目录；退出 Suite 不会关闭窗口' if key=='files' else '完整原版独立窗口；硬件传感器是否可读取决于本机，退出 Suite 不会关闭窗口'})
        self._checked=time.monotonic()

    def describe(self):
        with self._lock:
            if self._closed:raise NativeToolUnavailable('套件正在关闭')
            all_ready=bool(self._statuses) and all(item['can_launch'] for item in self._statuses)
            cache_seconds=self.cache_seconds if all_ready else min(3,self.cache_seconds)
            if time.monotonic()-self._checked>=cache_seconds:self._refresh()
            return {'tools':[dict(item,features=list(item['features'])) for item in self._statuses]}

    def launch(self,component,body):
        if component not in TOOLS:raise ValueError('未注册的原版工具')
        if not isinstance(body,dict) or body:raise ValueError('原版窗口不接受目录、命令或运行环境参数')
        with self._lock:
            state=next(item for item in self.describe()['tools'] if item['id']==component)
            if not state['can_launch']:raise NativeToolUnavailable(state['reason'])
            executable=self._selected[component][0]
            if not _python_executable(executable):raise NativeToolUnavailable('Python 运行环境已变化，请重新检查完整包')
            script=self._script(component)
            if script is None:raise NativeToolUnavailable('原版程序文件已变化，请重新解压完整包')
            windowed=_python_executable(executable.with_name('pythonw.exe'))
            executable=windowed if windowed else executable
            # Not registered in RuntimeManager: there may be unsaved user edits.
            # Explicit breakaway prevents inheriting any Suite worker JobObject.
            flags=getattr(subprocess,'CREATE_BREAKAWAY_FROM_JOB',0x01000000)|getattr(subprocess,'DETACHED_PROCESS',8)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0x00000200)
            try:process=subprocess.Popen((str(executable),'-I','-B','-c',LAUNCH_CODE,str(script)),
                cwd=str(script.parent),env=_runtime_environment(executable),stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags,close_fds=True)
            except OSError:raise NativeToolUnavailable('原版窗口未能启动，请检查完整包与本机 Python / Tk 环境') from None
            self._children=[child for child in self._children if child.poll() is None]
            self._children.append(process)
            return {'launched':True,'component':component,'window_verified':False,
                'detail':'已启动独立原版进程，请在新窗口操作；退出 Suite 不会关闭它'}

    def close(self):
        # Keep Popen objects for normal interpreter teardown; manually closing
        # their handles would make Popen's final poll race an invalid HANDLE.
        # No process is registered in the worker manager or terminated here.
        with self._lock:
            self._closed=True
