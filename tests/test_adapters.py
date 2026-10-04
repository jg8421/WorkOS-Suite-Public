from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import psutil
from unittest.mock import patch

from suite.adapters import AdapterError, NativeAdapters
from suite.runtime import RuntimeManager
from suite.workers.qwen_worker import _ClientLauncher

ROOT = Path(__file__).resolve().parents[1]


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve()
        self.runtime = RuntimeManager(self.folder/'runtime')
        self._owned_births = {}
        original_start=self.runtime.start
        def track_start(component_id):
            result=original_start(component_id)
            record=self.runtime._records[component_id]
            process=record.get('process')
            if record.get('owned') and process:
                self._owned_births[process.pid]=record['identity']
                try:
                    for child in psutil.Process(process.pid).children(recursive=True):
                        self._owned_births[child.pid]=child.create_time()
                except psutil.Error:pass
            return result
        self.runtime.start=track_start
        self.adapters = NativeAdapters(ROOT/'components', self.folder/'private', self.runtime)

    def tearDown(self):
        self.adapters.close()
        self.runtime.close()
        def live_owned():
            living=[]
            for pid,birth in self._owned_births.items():
                try:
                    process=psutil.Process(pid)
                    if process.create_time()==birth and process.is_running() and process.status()!=psutil.STATUS_ZOMBIE:living.append(pid)
                except psutil.NoSuchProcess:pass
            return living
        deadline=time.monotonic()+3
        while live_owned() and time.monotonic()<deadline:time.sleep(.05)
        self.assertEqual(live_owned(),[], 'Runtime left an exact owned worker alive; cleanup must not conceal it')
        # Windows can retain a terminated process's working-directory handle
        # briefly under load. Retry only after every recorded identity exited;
        # never ignore a cleanup error or tolerate a surviving worker.
        for attempt in range(100):
            try:self.temp.cleanup();break
            except PermissionError:
                if attempt==99:raise
                time.sleep(.05)

    def test_navigation_does_not_start_capture_or_import(self):
        description=self.adapters.describe()
        self.assertEqual(description['memory']['status'],'stopped')
        self.assertEqual(description['qwen']['status'],'stopped')
        self.assertFalse(any(description['memory']['settings'].values()))
        self.assertEqual(self.adapters.get('memory','recent')['events'],[])
        self.assertEqual(self.adapters.get('qwen','recordings')['recordings'],[])
        self.assertTrue(all(not x['owned'] for x in self.runtime.status()))
        self.assertFalse((self.folder/'private'/'memory'/'data').exists())
        public=json.dumps(description)
        self.assertNotIn(str(self.folder),public)
        self.assertNotIn(self.adapters._workers['memory']['token'],public)

    def test_unknown_operations_parameters_and_capture_types_are_rejected(self):
        with self.assertRaises(AdapterError):self.adapters.post('memory','shell',{'command':'whoami'})
        with self.assertRaises(AdapterError):self.adapters.post('memory','start',{'argv':['whoami']})
        with self.assertRaises(AdapterError):self.adapters.post('memory','settings',{'dataRoot':'elsewhere'})
        with self.assertRaises(AdapterError):self.adapters.post('memory','settings',{'desktop_capture':'true'})
        with self.assertRaises(AdapterError):self.adapters.get('memory','search',{'limit':1000})
        with self.assertRaises(AdapterError):self.adapters.post('memory','add',{'text':''})
        with self.assertRaises(AdapterError):self.adapters.post('phone','qr',{})
        self.assertFalse(any(x['owned'] for x in self.runtime.status()))

    def test_real_memory_worker_crud_search_forget_restart_and_auth(self):
        # Original MemoryStore really runs behind a Node HTTP worker with isolated storage.
        self.assertTrue(shutil.which('node'),'Node must be present for the complete-suite test')
        state=self.adapters.post('memory','start',{})
        self.assertEqual(state['status'],'running')
        self.assertFalse(any(state['settings'].values()))
        receipt=self.adapters._receipt('memory')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f'http://127.0.0.1:{receipt["port"]}/recent',timeout=2)
        self.assertEqual(caught.exception.code,401)
        event=self.adapters.post('memory','add',{'text':'Synthetic telescope plan','title':'Synthetic example','tags':['fixture']})['event']
        self.assertEqual(event['text'],'Synthetic telescope plan')
        self.assertNotIn('metadata',event)
        found=self.adapters.get('memory','search',{'q':'telescope','limit':5})
        self.assertIn(event['id'],[e['id'] for e in found['events']])
        recent=self.adapters.get('memory','recent',{'limit':5})
        self.assertEqual(recent['events'][0]['id'],event['id'])
        self.assertEqual(self.adapters.post('memory','settings',{k:False for k in self.adapters.MEMORY_FLAGS})['settings'],{k:False for k in self.adapters.MEMORY_FLAGS})
        stopped=self.adapters.post('memory','stop',{})
        self.assertEqual(stopped['status'],'stopped')
        self.adapters.post('memory','start',{})
        self.assertTrue(self.runtime.status('suite-memory')['owned'])
        self.assertEqual(self.adapters.get('memory','recent')['events'][0]['id'],event['id'])
        forgotten=self.adapters.post('memory','forget',{'id':event['id']})
        self.assertTrue(forgotten['forgotten'])
        self.assertEqual(forgotten['retention'],'soft_delete')
        self.assertEqual(self.adapters.get('memory','recent')['events'],[])
        self.assertIn('Synthetic telescope plan',(self.folder/'private'/'memory'/'data'/'events.jsonl').read_text())
        self.adapters.post('memory','stop',{})
        self.assertFalse(self.runtime.status('suite-memory')['owned'])

    def test_worker_identity_and_token_are_verified(self):
        self.adapters.post('memory','start',{})
        worker=self.adapters._workers['memory'];old=worker['token']
        worker['token']='wrong-token-but-not-a-secret'
        try:
            self.assertFalse(self.adapters._healthy('memory'))
            with self.assertRaisesRegex(AdapterError,'身份校验'):self.adapters._call('memory','recent')
        finally:worker['token']=old
        self.assertTrue(self.adapters._healthy('memory'))

    def test_phone_only_uses_explicit_connected_serial_and_owned_mirror(self):
        devices='List of devices attached\nSERIAL_ONE device product:x model:Fixture_One\nSERIAL_TWO unauthorized\n'
        with patch.object(self.adapters,'_adb',return_value=devices) as adb:
            with self.assertRaises(AdapterError):self.adapters.post('phone','mirror',{})
            with self.assertRaises(AdapterError):self.adapters.post('phone','mirror',{'serial':'SERIAL_TWO'})
            with self.assertRaises(AdapterError):self.adapters.post('phone','mirror',{'serial':'SERIAL_ONE','command':'anything'})
            self.assertTrue(all(call.args[0]==['devices','-l'] for call in adb.call_args_list))
        binary=self.folder/'scrcpy.exe';binary.write_bytes(b'fixture, never executed')
        adb_binary=self.folder/'adb.exe';adb_binary.write_bytes(b'fixture, never executed')
        with patch.object(self.adapters,'_adb',return_value=devices),patch.object(self.adapters,'_ensure_adb',return_value=34567),patch.object(self.adapters,'_binary',side_effect=lambda name:adb_binary if name=='adb' else binary),patch.object(self.runtime,'start',return_value={'status':'running'}) as start:
            mirrored=self.adapters.post('phone','mirror',{'serial':'SERIAL_ONE','no_audio':True})
            key=start.call_args.args[0];spec=self.runtime._specs[key]
            self.assertEqual(mirrored['serial'],'SERIAL_ONE')
            self.assertIn('SERIAL_ONE',spec.command)
            self.assertIn('--no-audio',spec.command)
            self.assertEqual(spec.environment['ADB'],str(adb_binary))
            self.assertEqual(spec.environment['ADB_SERVER_SOCKET'],'tcp:127.0.0.1:34567')
            self.assertNotIn('disconnect',spec.command)
        with patch.object(self.runtime,'stop',return_value={'status':'stopped'}) as stop:
            self.adapters.post('phone','stop',{})
            self.assertEqual([c.args[0] for c in stop.call_args_list],[key])

    def test_phone_pair_connect_and_screenshot_are_bounded(self):
        with patch.object(self.adapters,'_adb',return_value='Successfully paired') as adb:
            self.assertTrue(self.adapters.post('phone','pair',{'address':'192.168.50.2:34567','code':'123456'})['paired'])
            self.assertEqual(adb.call_args.args[0],['pair','192.168.50.2:34567','123456'])
            with self.assertRaises(AdapterError):self.adapters.post('phone','pair',{'address':'8.8.8.8:5555','code':'123456'})
            with self.assertRaises(AdapterError):self.adapters.post('phone','pair',{'address':'192.168.50.2:5555','code':'123456;anything'})
            self.assertEqual(adb.call_count,1)
        with patch.object(self.adapters,'_adb',return_value='failed to connect'):
            with self.assertRaises(AdapterError):self.adapters.post('phone','connect',{'address':'192.168.50.2:34567'})
        png=b'\x89PNG\r\n\x1a\nfixture'
        with patch.object(self.adapters,'_adb',side_effect=['SERIAL_ONE device model:Fixture\n',png]) as adb:
            result=self.adapters.post('phone','screenshot',{'serial':'SERIAL_ONE'})
            self.assertEqual(result['mime'],'image/png')
            self.assertEqual(adb.call_args.args[0],['-s','SERIAL_ONE','exec-out','screencap','-p'])
        with patch.object(self.adapters,'_adb',side_effect=['SERIAL_ONE device\n',b'bad']):
            with self.assertRaises(AdapterError):self.adapters.post('phone','screenshot',{'serial':'SERIAL_ONE'})

    def test_packaged_phone_binaries_take_precedence(self):
        root=self.folder/'package';components=root/'app'/'components'
        executable='adb.exe' if os.name=='nt' else 'adb'
        packaged=root/'runtime'/'phone'/'platform-tools'/executable
        packaged.parent.mkdir(parents=True);packaged.write_bytes(b'fixture')
        with patch.object(self.adapters,'components_dir',components):
            self.assertEqual(self.adapters._binary('adb'),packaged)

    def test_only_official_qwen_client_explicitly_leaves_worker_job(self):
        launcher=_ClientLauncher()
        with patch('suite.workers.qwen_worker.os.name','nt'),patch('suite.workers.qwen_worker.subprocess.Popen') as popen:
            launcher.Popen(['qianwen.exe'],creationflags=0)
            self.assertTrue(popen.call_args.kwargs['creationflags']&0x01000000)
            launcher.Popen(['other.exe'],creationflags=0)
            self.assertEqual(popen.call_args.kwargs['creationflags'],0)
            launcher.Popen(['qianwen.exe','unexpected'],creationflags=0)
            self.assertEqual(popen.call_args.kwargs['creationflags'],0)

    def test_adb_foreground_registration_and_clients_use_private_port(self):
        adb=self.folder/'adb.exe';adb.write_bytes(b'synthetic; never execute')
        with patch.object(self.adapters,'_binary',return_value=adb),patch.object(self.adapters,'_adb_healthy',return_value=True),patch.object(self.runtime,'start',return_value={'status':'running'}),patch('suite.adapters.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'List of devices attached\n',b'')) as run:
            self.assertEqual(self.adapters._devices()['devices'],[])
            port=self.adapters._adb_port;spec=self.runtime._specs['suite-adb']
            self.assertEqual(spec.command,(str(adb),'-L',f'tcp:127.0.0.1:{port}','server','nodaemon'))
            self.assertEqual(spec.environment['ADB_MDNS_AUTO_CONNECT'],'0')
            self.assertEqual(run.call_args.args[0],[str(adb),'-H','127.0.0.1','-P',str(port),'devices','-l'])
            self.assertNotEqual(port,5037)
        with patch.object(self.runtime,'stop',return_value={'status':'stopped'}) as stop:
            self.adapters.close()
            self.assertIn('suite-adb',[call.args[0] for call in stop.call_args_list])
            self.assertFalse(any('kill-server' in str(call) for call in stop.call_args_list))


if __name__=='__main__':unittest.main()
