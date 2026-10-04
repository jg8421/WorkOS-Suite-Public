"""Measured process state and explicit, identity-checked single-process control."""
from __future__ import annotations
import math
import os
import threading
import time
import psutil


class ProcessConflict(ValueError):
    status_code = 409


class ProcessService:
    CRITICAL = frozenset({'system','system idle process','registry','secure system','smss.exe','csrss.exe',
        'wininit.exe','winlogon.exe','lsass.exe','services.exe','svchost.exe','dwm.exe','fontdrvhost.exe',
        'init','systemd','launchd','kernel_task','kthreadd'})

    def __init__(self, protected_pids=None):
        self.protected_pids = protected_pids or (lambda: ())
        self._lock = threading.RLock()
        self._samples = {}
        self._system_cpu = None
        self._self = psutil.Process(os.getpid())
        self._owner = self._self.username()
        self._ancestors = {os.getpid(),*(p.pid for p in self._self.parents()),0,1,4}

    def _protected_ids(self):
        try:return self._ancestors | {p for p in self.protected_pids() if isinstance(p,int) and not isinstance(p,bool)}
        except Exception:raise ValueError('进程保护列表尚未就绪，请稍后重试') from None

    def _protected(self, process, protected):
        try:
            return process.pid in protected or process.name().casefold() in self.CRITICAL or process.username()!=self._owner
        except (psutil.Error,OSError):return True

    def list(self):
        with self._lock:
            try:protected=self._protected_ids();protection_ready=True
            except ValueError:protected=set();protection_ready=False
            now=time.monotonic();seen=set();result=[];cores=psutil.cpu_count() or 1
            for process in psutil.process_iter(['pid','name','create_time','memory_info','cpu_times']):
                try:
                    info=process.info;pid=info['pid'];created=info['create_time']
                    times=info['cpu_times'];total=times.user+times.system
                    key=(pid,created);previous=self._samples.get(key);cpu=None
                    if previous and now>previous[0]:cpu=max(0,min(100,(total-previous[1])/(now-previous[0])/cores*100))
                    self._samples[key]=(now,total);seen.add(key)
                    result.append({'pid':pid,'name':info['name'] or '', 'cpu':round(cpu,2) if cpu is not None else None,
                        'memory_mb':round(info['memory_info'].rss/1048576,2),'creation_time':created,
                        'protected':not protection_ready or self._protected(process,protected)})
                except (psutil.Error,OSError,TypeError,AttributeError):continue
            self._samples={key:value for key,value in self._samples.items() if key in seen}
            memory=psutil.virtual_memory()
            cpu_times=psutil.cpu_times()
            total=sum(getattr(cpu_times,name,0) for name in ('user','nice','system','idle','iowait','irq','softirq','steal'))
            busy=total-cpu_times.idle-getattr(cpu_times,'iowait',0)
            system_cpu=None
            if self._system_cpu is not None:
                elapsed=total-self._system_cpu[0]
                system_cpu=round(max(0,min(100,(busy-self._system_cpu[1])/elapsed*100)),2) if elapsed>0 else 0.0
            self._system_cpu=(total,busy)
            temperatures=[]
            try:
                for group,entries in getattr(psutil,'sensors_temperatures',lambda:{})().items():
                    for item in entries:
                        if isinstance(item.current,(int,float)) and math.isfinite(item.current):
                            temperatures.append({'label':item.label or group,'celsius':item.current})
            except (psutil.Error,OSError):pass
            return {'processes':sorted(result,key=lambda p:p['memory_mb'],reverse=True),
                    'system':{'cpu_percent':system_cpu,'memory_percent':memory.percent,'memory_used_mb':round(memory.used/1048576,1),
                        'memory_total_mb':round(memory.total/1048576,1),'temperature_available':bool(temperatures),
                        'temperatures':temperatures,'protection_ready':protection_ready}}

    def control(self,body):
        if not isinstance(body,dict) or set(body)-{'pid','creation_time','action','priority','confirm'}:raise ValueError('进程操作参数无效')
        pid=body.get('pid');created=body.get('creation_time');action=body.get('action')
        if isinstance(pid,bool) or not isinstance(pid,int) or pid<=0:raise ValueError('请选择一个有效进程')
        if isinstance(created,bool) or not isinstance(created,(float,int)) or not math.isfinite(created) or created<=0:raise ValueError('请刷新进程列表后重试')
        if body.get('confirm') is not True:raise ValueError('请明确确认这次进程操作')
        if action not in ('terminate','suspend','resume','priority'):raise ValueError('不支持的进程操作')
        if 'priority' in body and action!='priority':raise ValueError('此操作不接受优先级参数')
        levels={'idle':psutil.IDLE_PRIORITY_CLASS if os.name=='nt' else 19,
            'below_normal':psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name=='nt' else 10,
            'normal':psutil.NORMAL_PRIORITY_CLASS if os.name=='nt' else 0,
            'above_normal':psutil.ABOVE_NORMAL_PRIORITY_CLASS if os.name=='nt' else -5,
            'high':psutil.HIGH_PRIORITY_CLASS if os.name=='nt' else -10}
        if action=='priority' and body.get('priority') not in levels:raise ValueError('请选择受支持的优先级')
        with self._lock:
            try:
                process=psutil.Process(pid)
                if process.create_time()!=created:raise ProcessConflict('进程已经变化，请刷新列表；未执行操作')
                if self._protected(process,self._protected_ids()):raise ValueError('此进程受保护，不能通过 Suite 控制')
                if process.create_time()!=created:raise ProcessConflict('进程已经变化，请刷新列表；未执行操作')
                if action=='priority':process.nice(levels[body['priority']])
                else:getattr(process,action)()
                return {'pid':pid,'creation_time':created,'action':action,'status':'requested',
                        'detail':'已向所选进程发送操作；请刷新确认结果'}
            except psutil.NoSuchProcess:raise ProcessConflict('进程已退出，请刷新列表；未执行操作') from None
            except (psutil.AccessDenied,psutil.Error,OSError):raise ValueError('进程操作未完成，请检查权限后重试') from None
