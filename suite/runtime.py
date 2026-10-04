"""Fixed-component lifecycle control. Never signal an unowned/reused process."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import re
import socket
import subprocess
import threading
import time
from typing import Callable
import urllib.request

import psutil


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


class _OwnedJob:
    """Windows kernel ownership closes the launcher/descendant creation race."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes=ctypes;self.wintypes=wintypes
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
                      ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
                      ('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_=[(name,ctypes.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_=[('BasicLimitInformation',Basic),('IoInfo',Counters),('ProcessMemoryLimit',ctypes.c_size_t),
                      ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        self.kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];self.kernel.CreateJobObjectW.restype=wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];self.kernel.OpenProcess.restype=wintypes.HANDLE
        self.kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        class ThreadEntry(ctypes.Structure):
            _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ThreadID',wintypes.DWORD),
                      ('th32OwnerProcessID',wintypes.DWORD),('tpBasePri',wintypes.LONG),('tpDeltaPri',wintypes.LONG),('dwFlags',wintypes.DWORD)]
        self.ThreadEntry=ThreadEntry
        self.kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD];self.kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
        self.kernel.Thread32First.argtypes=[wintypes.HANDLE,ctypes.POINTER(ThreadEntry)]
        self.kernel.Thread32Next.argtypes=[wintypes.HANDLE,ctypes.POINTER(ThreadEntry)]
        self.kernel.OpenThread.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];self.kernel.OpenThread.restype=wintypes.HANDLE
        self.kernel.ResumeThread.argtypes=[wintypes.HANDLE];self.kernel.ResumeThread.restype=wintypes.DWORD
        class Accounting(ctypes.Structure):
            _fields_=[(name,ctypes.c_longlong) for name in ('TotalUserTime','TotalKernelTime','ThisPeriodTotalUserTime','ThisPeriodTotalKernelTime')]+[(name,wintypes.DWORD) for name in ('TotalPageFaultCount','TotalProcesses','ActiveProcesses','TotalTerminatedProcesses')]
        self.Accounting=Accounting
        self.kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        if not self.handle:raise OSError('Owned process containment unavailable')
        limits=Extended();limits.BasicLimitInformation.LimitFlags=0x2000|0x800  # kill-on-close; explicit breakaway for external Qianwen client only
        if not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            self.close();raise OSError('Owned process containment unavailable')

    def assign(self,process):
        # Popen's Windows process HANDLE refers to this exact created process,
        # even if the integer PID is later recycled.
        if not self.kernel.AssignProcessToJobObject(self.handle,int(process._handle)):raise OSError('Cannot own component process')

    def resume(self,process):
        # Start suspended, assign before any user code can spawn, then resume
        # this newly-created process's sole primary thread. Popen closes its
        # original thread HANDLE, so reacquire the exact suspended thread.
        snapshot=self.kernel.CreateToolhelp32Snapshot(4,0)
        if not snapshot or snapshot==self.ctypes.c_void_p(-1).value:raise OSError('Cannot resume owned component')
        resumed=False
        try:
            entry=self.ThreadEntry();entry.dwSize=self.ctypes.sizeof(entry)
            success=self.kernel.Thread32First(snapshot,self.ctypes.byref(entry))
            while success:
                if entry.th32OwnerProcessID==process.pid:
                    thread=self.kernel.OpenThread(2,False,entry.th32ThreadID)
                    if not thread:raise OSError('Cannot resume owned component')
                    try:
                        if self.kernel.ResumeThread(thread)==0xFFFFFFFF:raise OSError('Cannot resume owned component')
                        resumed=True
                    finally:self.kernel.CloseHandle(thread)
                success=self.kernel.Thread32Next(snapshot,self.ctypes.byref(entry))
        finally:self.kernel.CloseHandle(snapshot)
        if not resumed:raise OSError('Cannot resume owned component')

    def assign_descendants(self,process,identity):
        # A fast venv launcher can spawn before its parent is assigned. Once a
        # process is in the job, future descendants inherit the same job.
        for _ in range(2):
            try:
                root=psutil.Process(process.pid)
                if root.create_time()!=identity:return
                children=root.children(recursive=True)
            except psutil.Error:return
            for child in children:
                try:
                    if not any(p.pid==root.pid and p.create_time()==identity for p in child.parents()):continue
                    created=child.create_time()
                    handle=self.kernel.OpenProcess(0x0100|0x1000,False,child.pid)  # SET_QUOTA + QUERY_LIMITED_INFORMATION
                    if not handle:continue
                    try:
                        stamps=[self.wintypes.FILETIME() for _ in range(4)]
                        if not self.kernel.GetProcessTimes(handle,*[self.ctypes.byref(x) for x in stamps]):continue
                        actual=((stamps[0].dwHighDateTime<<32)|stamps[0].dwLowDateTime)/10_000_000-11_644_473_600
                        if abs(actual-created)<.00001:self.kernel.AssignProcessToJobObject(self.handle,handle)
                    finally:self.kernel.CloseHandle(handle)
                except psutil.Error:pass

    def close(self):
        if getattr(self,'handle',None):
            self.kernel.TerminateJobObject(self.handle,1)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                state=self.Accounting()
                if not self.kernel.QueryInformationJobObject(self.handle,1,self.ctypes.byref(state),self.ctypes.sizeof(state),None) or not state.ActiveProcesses:break
                time.sleep(.03)
            self.kernel.CloseHandle(self.handle);self.handle=None

    def pids(self):
        if not self.handle:return []
        for count in (64,512,4096):
            buffer=self.ctypes.create_string_buffer(8+self.ctypes.sizeof(self.ctypes.c_size_t)*count)
            if self.kernel.QueryInformationJobObject(self.handle,3,buffer,len(buffer),None):
                used=self.ctypes.c_uint32.from_buffer(buffer,4).value
                return list((self.ctypes.c_size_t*min(used,count)).from_buffer(buffer,8))
        return []


@dataclass(frozen=True)
class ComponentSpec:
    id: str
    command: tuple[str, ...] | list[str]
    cwd: Path | str
    health_port: int | None = None
    health_path: str = "/health"
    environment: dict[str, str] = field(default_factory=dict)
    dependency_reason: str = ""
    healthcheck: Callable[[], bool] | None = None
    health_identity: dict | None = None
    label: str = ""
    start_timeout: float = 8.0


class RuntimeManager:
    """Only source-registered argv are executable; public results omit argv/logs."""

    def __init__(self, runtime_dir: Path | str):
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._specs: dict[str, ComponentSpec] = {}
        self._records: dict[str, dict] = {}
        self._events = deque(maxlen=120)
        self._lock = threading.RLock()
        self._closed = False

    def register(self, spec: ComponentSpec):
        if not isinstance(spec, ComponentSpec) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", spec.id):
            raise ValueError("组件标识无效")
        command = tuple(spec.command)
        if not command or any(not isinstance(x, str) or not x or "\0" in x for x in command):
            raise ValueError("组件启动命令无效")
        cwd = Path(spec.cwd).resolve()
        if spec.health_port is not None and (isinstance(spec.health_port, bool) or not 1 <= spec.health_port <= 65535):
            raise ValueError("组件端口无效")
        if not isinstance(spec.health_path, str) or not spec.health_path.startswith("/") or "\r" in spec.health_path or "\n" in spec.health_path:
            raise ValueError("组件健康检查路径无效")
        if not isinstance(spec.environment, dict) or any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v or "=" in k for k, v in spec.environment.items()):
            raise ValueError("组件环境无效")
        if not isinstance(spec.start_timeout, (float, int)) or not 0.1 <= spec.start_timeout <= 30:
            raise ValueError("组件启动期限无效")
        normalized = ComponentSpec(**{**spec.__dict__, "command": command, "cwd": cwd, "environment": dict(spec.environment)})
        with self._lock:
            if self._closed: raise RuntimeError("套件正在关闭")
            if spec.id in self._specs: raise ValueError("组件已经注册")
            self._specs[spec.id] = normalized
            self._records[spec.id] = {"status": "stopped", "process": None, "identity": None, "owned": False, "job": None, "detail": "尚未启动"}
        return self.status(spec.id)

    def _spec(self, component_id):
        if component_id not in self._specs: raise ValueError("未注册的组件")
        return self._specs[component_id]

    def _event(self, component_id, action, detail):
        self._events.append({"component_id": component_id, "action": action, "detail": detail, "time": time.time()})

    @staticmethod
    def _same(pid, identity):
        try:
            p = psutil.Process(pid)
            return p.is_running() and p.create_time() == identity and p.status() != psutil.STATUS_ZOMBIE
        except (psutil.Error, OSError): return False

    def _owned_alive(self, record):
        p = record["process"]
        return bool(record["owned"] and p and p.poll() is None and self._same(p.pid, record["identity"]))

    @staticmethod
    def _dispose_exited(process):
        # Retaining a signalled Windows process HANDLE can retain references
        # to its CWD/files. Close only after Popen has confirmed actual exit.
        if process is not None and process.poll() is not None and os.name=='nt':
            handle=getattr(process,'_handle',None)
            if handle is not None:
                try:handle.Close()
                except OSError:pass

    def _health(self, spec, *, borrowed=False):
        try:
            if spec.healthcheck is not None: return bool(spec.healthcheck())
            if spec.health_port is None: return False
            if borrowed and not spec.health_identity: return False
            request = urllib.request.Request(f"http://127.0.0.1:{spec.health_port}{spec.health_path}")
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=.7) as response:
                if response.status != 200: return False
                if spec.health_identity:
                    body = json.loads(response.read(65_537))
                    return isinstance(body, dict) and all(body.get(k) == v for k, v in spec.health_identity.items())
                return True
        except Exception: return False

    @staticmethod
    def _port_busy(port):
        if port is None: return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.3): return True
        except OSError: return False

    def status(self, component_id=None):
        with self._lock:
            if component_id is None: return [self.status(key) for key in self._specs]
            spec = self._spec(component_id); record = self._records[component_id]
            if record["owned"] and not self._owned_alive(record):
                process = record["process"]
                code = process.poll() if process else None
                if code is not None and record.get('job') is not None:record['job'].close();record['job']=None
                if code is not None:self._dispose_exited(process)
                record.update(status="stopped" if code == 0 else "failed", owned=False, detail="组件已退出" if code == 0 else "组件已退出，请检查环境或重新启动")
            elif record["status"] == "borrowed" and not self._health(spec, borrowed=True):
                record.update(status="stopped", detail="外部组件已停止")
            return {"id": spec.id, "label": spec.label or spec.id, "status": record["status"], "owned": record["owned"],
                    "borrowed": record["status"] == "borrowed", "pid": record["process"].pid if record["owned"] else None,
                    "detail": record["detail"], "can_stop": record["owned"], "can_start": not self._closed and record["status"] not in ("running", "starting", "borrowed"),
                    "dependency_reason": spec.dependency_reason}

    def events(self, component_id=None):
        with self._lock:
            if component_id is not None: self._spec(component_id)
            return [dict(e) for e in self._events if component_id is None or e["component_id"] == component_id]

    def protected_pids(self):
        """Current owned kernel-job members; explicit external clients excluded."""
        with self._lock:
            protected=set()
            for record in self._records.values():
                if not record['owned'] or not self._owned_alive(record):continue
                job=record.get('job')
                if job:
                    for pid in job.pids():
                        try:
                            p=psutil.Process(pid)
                            if p.is_running() and p.status()!=psutil.STATUS_ZOMBIE:protected.add(pid)
                        except psutil.Error:pass
                    continue
                try:
                    root=psutil.Process(record['process'].pid)
                    if root.create_time()!=record['identity']:continue
                    protected.add(root.pid)
                    for child in root.children(recursive=True):
                        try:
                            if any(parent.pid==root.pid and parent.create_time()==record['identity'] for parent in child.parents()):protected.add(child.pid)
                        except psutil.Error:pass
                except psutil.Error:pass
            return sorted(protected)

    def ensure(self, component_id): return self.start(component_id)

    def start(self, component_id):
        with self._lock:
            spec = self._spec(component_id); record = self._records[component_id]
            if self._closed: raise RuntimeError("套件正在关闭")
            if self._owned_alive(record): return self.status(component_id)
            if self._health(spec, borrowed=True):
                record.update(status="borrowed", process=None, identity=None, owned=False, detail="组件已在外部运行；套件不会结束它")
                return self.status(component_id)
            if spec.dependency_reason:
                record.update(status="needs_setup", detail=spec.dependency_reason)
                self._event(component_id, "needs_setup", spec.dependency_reason); return self.status(component_id)
            if not spec.cwd.is_dir() or not Path(spec.command[0]).is_absolute() or not Path(spec.command[0]).is_file():
                record.update(status="needs_setup", detail="启动文件或目录缺失，请重新解压完整套件")
                self._event(component_id, "needs_setup", record["detail"]); return self.status(component_id)
            if self._port_busy(spec.health_port):
                record.update(status="blocked", detail="端口被其他程序占用；请停止冲突服务或选择其他端口")
                self._event(component_id, "blocked", record["detail"]); return self.status(component_id)
            environment = {k:v for k,v in os.environ.items() if k.upper() not in {'PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','NODE_OPTIONS','NODE_PATH'}}
            environment.update(spec.environment)
            log_dir = self.runtime_dir / "component-logs"; log_dir.mkdir(exist_ok=True)
            log = log_dir / f"{component_id}-{time.time_ns()}.log"
            process = None
            job = None
            try:
                if os.name=='nt':job=_OwnedJob()
                with log.open("ab") as output:
                    options = {"stdout": output, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "cwd": str(spec.cwd), "env": environment}
                    if os.name == "nt": options["creationflags"] = subprocess.CREATE_NO_WINDOW | 4  # CREATE_SUSPENDED until assigned to owned job
                    process = subprocess.Popen(spec.command, **options)
                if job:job.assign(process);job.resume(process)
                identity = psutil.Process(process.pid).create_time()
                if job:job.assign_descendants(process,identity)
                record.update(status="starting", process=process, identity=identity, owned=True, job=job, detail="正在启动")
                self._event(component_id, "starting", "正在启动")
            except (OSError, psutil.Error):
                if job:job.close()
                if process is not None and process.poll() is None:
                    # This newly-created Popen handle is ours even if identity
                    # inspection fails; do not leak it or adopt another PID.
                    try: process.terminate(); process.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        try: process.kill(); process.wait(timeout=2)
                        except (OSError, subprocess.TimeoutExpired): pass
                self._dispose_exited(process)
                record.update(status="failed", process=None, identity=None, owned=False, detail="启动失败，请检查组件依赖")
                self._event(component_id, "failed", record["detail"]); return self.status(component_id)
            deadline = time.monotonic() + spec.start_timeout
            while time.monotonic() < deadline:
                if not self._owned_alive(record): return self.status(component_id)
                if (spec.health_port is None and spec.healthcheck is None) or self._health(spec):
                    record.update(status="running", detail="组件正在运行")
                    self._event(component_id, "running", record["detail"]); return self.status(component_id)
                time.sleep(.08)
            self._stop_owned(component_id)
            record.update(status="failed", detail="启动未在期限内就绪，已清理套件启动的进程")
            self._event(component_id, "failed", record["detail"]); return self.status(component_id)

    def _stop_owned(self, component_id):
        record = self._records[component_id]
        if not self._owned_alive(record): return False
        try: root = psutil.Process(record["process"].pid)
        except psutil.Error: return False
        targets = []
        try:
            for child in root.children(recursive=True):
                try:
                    if any(p.pid == root.pid and p.create_time() == record["identity"] for p in child.parents()):
                        targets.append((child.pid, child.create_time()))
                except psutil.Error: pass
            targets.append((root.pid, record["identity"]))
        except psutil.Error: targets=[(record['process'].pid,record['identity'])]
        if record.get('job') is not None:
            # The kernel job contains the suspended-start root and future
            # descendants. Close this owned handle, then wait for handles to
            # drain before declaring stop/restart complete.
            record['job'].close();record['job']=None
            process=record['process']
            if process.poll() is None:
                # The process HANDLE still points to our exact creation; this
                # fallback cannot signal a recycled PID or a borrowed service.
                try:process.terminate()
                except OSError:pass
            try:process.wait(timeout=5)
            except (subprocess.TimeoutExpired,OSError):
                record.update(status='stopping',detail='停止请求已发出，正在等待系统释放进程')
                return True
            deadline=time.monotonic()+2
            while time.monotonic()<deadline and any(self._same(pid,identity) for pid,identity in targets):time.sleep(.04)
            self._dispose_exited(process)
            record.update(status='stopped',owned=False,detail='已停止套件启动的组件')
            return True
        signalled = []
        for pid, identity in targets:
            if not self._same(pid, identity): continue
            try:
                p = psutil.Process(pid); p.terminate(); signalled.append((pid, identity))
            except psutil.Error: pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(self._same(pid, identity) for pid, identity in signalled): time.sleep(.05)
        for pid, identity in signalled:
            if self._same(pid, identity):
                try: psutil.Process(pid).kill()
                except psutil.Error: pass
        try: record["process"].wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError): pass
        self._dispose_exited(record['process'])
        record.update(status="stopped", owned=False, detail="已停止套件启动的组件")
        return True

    def stop(self, component_id):
        with self._lock:
            self._spec(component_id); record = self._records[component_id]
            if record["status"] == "borrowed":
                record["detail"] = "外部组件不归套件管理，请在原应用中停止它"
                self._event(component_id, "borrowed", record["detail"])
            elif self._stop_owned(component_id): self._event(component_id, record['status'], record["detail"])
            else:
                record.update(status="stopped", owned=False, detail="没有可停止的套件进程")
            return self.status(component_id)

    def restart(self, component_id):
        with self._lock:
            self._spec(component_id)
            if self._records[component_id]["status"] == "borrowed": return self.stop(component_id)
            self.stop(component_id); return self.start(component_id)

    def close(self):
        with self._lock:
            self._closed = True
            for component_id in self._specs:
                if self._records[component_id]["owned"]: self.stop(component_id)
                job=self._records[component_id].get('job')
                if job:job.close();self._records[component_id]['job']=None
