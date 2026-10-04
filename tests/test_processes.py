from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import psutil
from suite.processes import ProcessService,ProcessConflict


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.children=[];self.service=ProcessService()

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:child.terminate()
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)

    def child(self):
        options={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],**options)
        self.children.append(child);return child

    def body(self,child,action,**extra):
        return {'pid':child.pid,'creation_time':psutil.Process(child.pid).create_time(),'action':action,'confirm':True,**extra}

    def test_owned_temporary_process_suspend_resume_and_terminate_other_child_survives(self):
        target=self.child();other=self.child()
        suspended=self.service.control(self.body(target,'suspend'));self.assertEqual(suspended['action'],'suspend')
        self.assertEqual(psutil.Process(target.pid).status(),psutil.STATUS_STOPPED)
        self.service.control(self.body(target,'resume'))
        deadline=time.monotonic()+2
        while time.monotonic()<deadline and psutil.Process(target.pid).status()==psutil.STATUS_STOPPED:time.sleep(.02)
        self.assertNotEqual(psutil.Process(target.pid).status(),psutil.STATUS_STOPPED)
        self.service.control(self.body(target,'terminate'));target.wait(timeout=3)
        self.assertIsNone(other.poll())

    def test_stale_creation_time_is_conflict_before_any_control(self):
        child=self.child();body=self.body(child,'terminate');body['creation_time']+=1
        with self.assertRaises(ProcessConflict) as caught:self.service.control(body)
        self.assertEqual(caught.exception.status_code,409);self.assertIsNone(child.poll())

    def test_protected_callback_and_self_reject_actions(self):
        child=self.child();self.service=ProcessService(protected_pids=lambda:[child.pid])
        with self.assertRaisesRegex(ValueError,'保护'):self.service.control(self.body(child,'terminate'))
        with self.assertRaisesRegex(ValueError,'保护'):self.service.control({'pid':os.getpid(),'creation_time':psutil.Process().create_time(),'action':'terminate','confirm':True})
        self.assertIsNone(child.poll())

    def test_validation_no_batch_name_command_or_realtime_priority(self):
        child=self.child();body=self.body(child,'terminate')
        for bad in ({**body,'confirm':False},{**body,'name':'python.exe'},{**body,'command':'whoami'},
                    {**body,'action':'restart'},{**body,'action':'priority','priority':'realtime'},
                    {**body,'pid':True},{**body,'creation_time':float('nan')}):
            with self.subTest(body=bad),self.assertRaises(ValueError):self.service.control(bad)
        self.assertIsNone(child.poll())

    def test_priority_normal_and_real_identity_metrics(self):
        child=self.child();self.service.control(self.body(child,'priority',priority='normal'))
        process=psutil.Process(child.pid)
        with patch('suite.processes.psutil.process_iter',return_value=iter([process])):
            process.info={k:getattr(process,k)() if k!='pid' else process.pid for k in ['pid','name','create_time','memory_info','cpu_times']}
            first=self.service.list()
        self.assertIsNone(first['processes'][0]['cpu']);self.assertGreater(first['processes'][0]['memory_mb'],0)
        self.assertIsNone(first['system']['cpu_percent']);self.assertGreater(first['system']['memory_used_mb'],0)
        self.assertFalse(first['processes'][0]['protected']);self.assertIn('memory_percent',first['system'])
        time.sleep(.05)
        with patch('suite.processes.psutil.process_iter',return_value=iter([process])):
            process.info['cpu_times']=process.cpu_times();second=self.service.list()
        self.assertIsInstance(second['processes'][0]['cpu'],(int,float));self.assertEqual(second['processes'][0]['creation_time'],process.create_time())

    def test_system_cpu_uses_shared_counter_deltas_across_requests(self):
        counters=[SimpleNamespace(user=100,system=50,idle=850),SimpleNamespace(user=120,system=60,idle=870)]
        with patch('suite.processes.psutil.process_iter',side_effect=[iter([]),iter([])]),patch('suite.processes.psutil.cpu_times',side_effect=counters):
            first=self.service.list();second=self.service.list()
        self.assertIsNone(first['system']['cpu_percent']);self.assertEqual(second['system']['cpu_percent'],60.0)


if __name__=='__main__':unittest.main()
