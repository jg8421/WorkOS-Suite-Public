from __future__ import annotations
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import psutil

from suite.adapters import NativeAdapters, AdapterError
from suite.qwen import OriginalQwenBridge, QwenError, ListenerLease, prepare_config, configuration_update, public_config, safe_snapshot
from suite.workers.qwen_worker import QwenController

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = {'apps': {'teams': {'label': 'Teams', 'enabled': True, 'processes': ['teams.exe']}},
           'trigger': {'method': 'hotkey', 'hotkey': 'rightctrl+/'}, 'recorder_process': 'qianwen.exe',
           'poll_interval_sec': 2, 'start_debounce_sec': 3, 'retrigger_cooldown_sec': 30,
           'require_playback': True, 'min_playback_sec': 3, 'exclude_processes': ['ctfmon.exe'],
           'panel': {'host': '127.0.0.1', 'port': 8765},
           'archive': {'enabled': True, 'dir': '', 'min_age_sec': 60, 'interval_sec': 120},
           'launcher': {'auto_launch': True, 'exe': '', 'wait_ready_sec': 12, 'retry_hotkey': 2, 'retry_delay_sec': 7}}


def fake_engine(data):
    engine = SimpleNamespace(DEFAULT_CONFIG=copy.deepcopy(DEFAULT), CONFIG_PATH=data/'config.json',
                             log=Mock(), capture_sessions=Mock(return_value={}), render_sessions=Mock(return_value={}),
                             trigger=Mock(return_value=True), parse_hotkey=Mock(return_value=[(0x1d, True)]))
    def load(path):
        result = copy.deepcopy(DEFAULT)
        if Path(path).is_file(): result.update(json.loads(Path(path).read_text(encoding='utf-8')))
        return result
    def save(cfg, path): Path(path).write_text(json.dumps(cfg), encoding='utf-8')
    engine.load_config, engine.save_config = load, save
    class Watcher:
        def __init__(self, path):
            self.cfg=load(path);self.running=False;self.last_error='';self.recording=False;self.trigger_count=0
        def start(self): self.running=True
        def stop(self): self.running=False
        def reload_config(self): self.cfg=load(engine.CONFIG_PATH)
        def snapshot(self):
            return {'running': self.running, 'paused': False, 'recording': self.recording,
                    'trigger_count': self.trigger_count, 'last_error': self.last_error, 'last_app': 'manual',
                    'active_calls': [], 'pending': [], 'hotkey': self.cfg['trigger']['hotkey'],
                    'apps': [{'key': key, **value} for key, value in self.cfg['apps'].items()]}
        def trigger_now(self):
            try: engine.trigger(self.cfg);self.trigger_count+=1
            except Exception: self.last_error='synthetic send failure'
    engine.Watcher=Watcher
    return engine


class QwenBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.original=self.root/'original';self.original.mkdir();(self.original/'tray_app.py').write_text('# fixture');(self.original/'engine.py').write_text('# fixture')
        self.requests=[];self.header='QwenAutoRecord/1.0'
        self.state={'running':True,'paused':False,'recording':False,'trigger_count':4,'last_app':'teams','last_trigger_at':1,'active_calls':[],'pending':[], 'last_error':'','hotkey':'rightctrl+/', 'apps':[{'key':'teams','label':'Teams','enabled':True,'processes':['teams.exe']}]}
        self.recordings={'dir':'synthetic private original path','items':[{'folder':'2026-01-01-fixture','path':'synthetic private recording path','bytes':123456,'file':'fixture.aac','mtime':'2026-01-01 10:12:34'}]}
        test=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def version_string(self):return test.header
            def reply(self,value):
                raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                test.requests.append(('GET',self.path,None));self.reply(test.state if self.path=='/api/status' else test.recordings if self.path=='/api/recordings' else test.cfg)
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}');test.requests.append(('POST',self.path,body))
                if self.path=='/api/pause':test.state['paused']=body['paused']
                if self.path=='/api/trigger':test.state['trigger_count']+=1
                if self.path=='/api/config':test.cfg=configuration_update(body,test.cfg);test.config.write_text(json.dumps(test.cfg))
                self.reply({'ok':True})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.05));self.thread.start()
        self.cfg=copy.deepcopy(DEFAULT);self.cfg['panel']['port']=self.server.server_port
        self.config=self.original/'config.json';self.config.write_text(json.dumps(self.cfg))
        self.process=Mock();self.process.info={'pid':4321,'name':'pythonw.exe'};self.process.pid=4321;self.process.cmdline.return_value=['pythonw.exe',str(self.original/'tray_app.py')]
        self.process.is_running.return_value=True;self.process.create_time.return_value=123.0
        self.process.net_connections.return_value=[SimpleNamespace(status=psutil.CONN_LISTEN,laddr=SimpleNamespace(ip='127.0.0.1',port=self.server.server_port))]
        self.iter_patch=patch('suite.qwen.psutil.process_iter',return_value=[self.process]);self.iter_patch.start()
        self.pid_patch=patch('suite.qwen.psutil.Process',return_value=self.process);self.pid_patch.start()
        self.bridge=OriginalQwenBridge(True)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.pid_patch.stop();self.iter_patch.stop();self.temp.cleanup()
    def adapters(self):
        runtime=Mock();runtime.status.return_value={'status':'stopped','owned':False,'pid':None,'can_start':True,'can_stop':False,'detail':'','dependency_reason':''}
        with patch.object(NativeAdapters,'_qwen_dependency',return_value=''):
            adapters=NativeAdapters(ROOT/'components',self.root/'data',runtime,qwen_external=True)
        return adapters,runtime
    def test_borrowed_actual_status_no_worker_start_and_close_never_pauses(self):
        adapters,runtime=self.adapters();state=adapters.get('qwen','status')
        self.assertTrue(state['borrowed']);self.assertTrue(state['listening']);self.assertEqual(state['source'],'original');runtime.start.assert_not_called()
        self.assertEqual(state['hotkey'],'rightctrl+/');self.assertEqual(state['apps'][0]['key'],'teams')
        self.assertEqual(state['panel_url'],f'http://127.0.0.1:{self.server.server_port}/')
        adapters.close();self.assertTrue(self.state['running']);self.assertFalse(self.state['paused']);self.assertFalse(any(method=='POST' for method,_,_ in self.requests))
    def test_borrowed_pause_resume_controls_watcher_not_process(self):
        adapters,runtime=self.adapters();paused=adapters.post('qwen','stop',{});self.assertTrue(paused['paused']);self.assertFalse(paused['listening']);runtime.stop.assert_not_called()
        resumed=adapters.post('qwen','start',{});self.assertTrue(resumed['listening']);self.assertFalse(resumed['paused']);runtime.start.assert_not_called()
        self.assertEqual([body for method,path,body in self.requests if method=='POST' and path=='/api/pause'],[{'paused':True},{'paused':False}]);adapters.close()
    def test_real_trigger_receipt_is_not_recording_proof(self):
        result=self.bridge.action(self.bridge.discover(),'trigger');self.assertTrue(result['trigger_requested']);self.assertFalse(result['recording_verified']);self.assertFalse(result['recording'])
    def test_recording_already_active_never_sends_second_hotkey(self):
        self.state['recording']=True;result=self.bridge.action(self.bridge.discover(),'trigger');self.assertTrue(result['already_recording']);self.assertFalse(result['trigger_requested']);self.assertFalse(any(path=='/api/trigger' for _,path,_ in self.requests))
    def test_trigger_failure_does_not_return_success_receipt(self):
        self.state['last_error']='synthetic confidential path must not leak'
        with self.assertRaises(QwenError) as error:self.bridge.action(self.bridge.discover(),'trigger')
        self.assertNotIn('confidential',str(error.exception))
    def test_changed_birth_prevents_mutation_even_with_cached_reference(self):
        reference=self.bridge.discover();self.process.create_time.return_value=124.0
        with self.assertRaises(QwenError):self.bridge.action(reference,'stop')
        self.assertFalse(any(method=='POST' for method,_,_ in self.requests))
    def test_unrelated_http_server_not_adopted_original_pending_blocks_duplicates(self):
        self.header='UnrelatedService/1.0';reference=self.bridge.discover();self.assertNotIn('snapshot',reference)
        self.assertEqual(self.bridge.status(reference)['status'],'starting')
        self.assertNotIn('panel_url',self.bridge.status(reference))
        with self.assertRaises(QwenError):self.bridge.action(reference,'start')
    def test_non_python_process_command_lines_never_read(self):
        unrelated=Mock();unrelated.info={'pid':99,'name':'browser.exe'}
        with patch('suite.qwen.psutil.process_iter',return_value=[unrelated,self.process]):self.assertTrue(self.bridge.discover())
        unrelated.cmdline.assert_not_called()
    def test_cache_is_short_and_action_still_checks_identity(self):
        self.bridge.discover();requests=len(self.requests);self.bridge.discover();self.assertEqual(len(self.requests),requests)
        self.bridge.discover(fresh=True);self.assertGreater(len(self.requests),requests)
    def test_configuration_supports_apps_without_executable_path_mutation(self):
        result=self.bridge.action(self.bridge.discover(),'config',{'apps':{'teams':{'enabled':False}}});self.assertFalse(result['apps'][0]['enabled'])
        with self.assertRaises(QwenError):self.bridge.action(self.bridge.discover(),'config',{'launcher':{'exe':'not allowed'}})
    def test_restoration_observes_original_without_resume_or_capture(self):
        adapters,runtime=self.adapters();adapters._save_qwen_enabled(True);self.state['paused']=True
        state=adapters.restore_qwen();self.assertTrue(state['paused']);runtime.start.assert_not_called();self.assertFalse(any(method=='POST' for method,_,_ in self.requests));adapters.close()
    def test_original_recording_list_maps_actual_fields_and_excludes_source_paths(self):
        adapters,runtime=self.adapters();result=adapters.get('qwen','recordings')
        self.assertEqual(result['source'],'original');self.assertEqual(result['total'],1)
        self.assertEqual(result['recordings'][0],{'id':'2026-01-01-fixture','name':'2026-01-01-fixture','files':None,'size_bytes':123456,'modified_at':'2026-01-01 10:12:34'})
        self.assertNotIn('private',json.dumps(result));runtime.start.assert_not_called();adapters.close()
    def test_original_recording_bad_paths_and_invalid_fields_are_not_exposed(self):
        self.recordings['items'].extend([{'folder':'C:\\private\\recording','bytes':5},{'folder':'2026-fixture-safe','bytes':'synthetic secret','mtime':'synthetic secret'}])
        adapters,runtime=self.adapters();result=adapters.get('qwen','recordings')
        self.assertEqual(result['total'],2);self.assertEqual(result['recordings'][1]['size_bytes'],0);self.assertIsNone(result['recordings'][1]['modified_at']);self.assertNotIn('secret',json.dumps(result));adapters.close()
    def test_fresh_install_does_not_restore_without_explicit_authorization(self):
        adapters,runtime=self.adapters()
        with patch.object(adapters._qwen_bridge,'discover',return_value=None),patch.object(adapters,'post') as post:
            state=adapters.restore_qwen();self.assertFalse(state['running']);post.assert_not_called();runtime.start.assert_not_called()
        adapters.close()
    def test_saved_authorization_restores_only_own_listener_and_persists(self):
        adapters,runtime=self.adapters();adapters._save_qwen_enabled(True)
        listening={'source':'suite','status':'running','running':True,'listening':True,'recording':False}
        with patch.object(adapters._qwen_bridge,'discover',return_value=None),patch.object(adapters,'_start',return_value=listening) as start,patch.object(adapters,'_call',return_value=listening) as call:
            result=adapters.restore_qwen();self.assertTrue(result['listening']);start.assert_called_once_with('qwen');call.assert_called_once_with('qwen','start',method='POST',timeout=45)
            self.assertTrue(adapters._qwen_enabled())
        self.assertFalse(any(method=='POST' for method,_,_ in self.requests));adapters.close()


class QwenControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=Path(self.temp.name);self.engine=fake_engine(self.data);self.bridge=Mock();self.bridge.processes.return_value=[]
        self.controller=None
    def tearDown(self):
        if self.controller:self.controller.stop()
        self.temp.cleanup()
    def controller_for(self,original=None):
        self.controller=QwenController(self.engine,self.data,bridge=self.bridge,original=original);return self.controller
    def test_configuration_import_preserves_actual_shortcut_apps_playback_and_archive(self):
        source=self.data/'original';source.mkdir();cfg=copy.deepcopy(DEFAULT);cfg['apps']['teams']['enabled']=False;cfg['require_playback']=False;cfg['archive']['enabled']=False;cfg['trigger']['hotkey']='ctrl+space';cfg['api_key']='synthetic excluded secret'
        cfg['launcher']['api_key']='nested secret';cfg['apps']['teams']['password']='nested secret'
        config=source/'config.json';config.write_text(json.dumps(cfg));controller=self.controller_for({'root':source,'config':config})
        self.assertFalse(controller.cfg['apps']['teams']['enabled']);self.assertFalse(controller.cfg['require_playback']);self.assertFalse(controller.cfg['archive']['enabled']);self.assertEqual(controller.cfg['trigger']['hotkey'],'ctrl+space');self.assertNotIn('api_key',controller.cfg)
        self.assertTrue(controller.origin['imported']);self.assertEqual(json.loads(config.read_text()),cfg)
        self.assertNotIn('nested secret',json.dumps(controller.cfg))
    def test_existing_suite_custom_config_is_not_overwritten(self):
        existing=copy.deepcopy(DEFAULT);existing['apps']['teams']['enabled']=False;self.engine.save_config(existing,self.engine.CONFIG_PATH)
        source=self.data/'original';source.mkdir();config=source/'config.json';config.write_text(json.dumps(DEFAULT));controller=self.controller_for({'root':source,'config':config});self.assertFalse(controller.cfg['apps']['teams']['enabled']);self.assertFalse(controller.origin['imported'])
    def test_old_generated_defaults_can_migrate_instead_of_resetting_user_settings(self):
        legacy=copy.deepcopy(DEFAULT);legacy['archive'].update(enabled=True,dir=str(self.data/'recordings'));self.engine.save_config(legacy,self.engine.CONFIG_PATH)
        source=self.data/'original';source.mkdir();config=source/'config.json';cfg=copy.deepcopy(DEFAULT);cfg['trigger']['hotkey']='ctrl+space';config.write_text(json.dumps(cfg));controller=self.controller_for({'root':source,'config':config});self.assertEqual(controller.cfg['trigger']['hotkey'],'ctrl+space');self.assertTrue(controller.origin['imported'])
    def test_two_workers_cannot_hold_same_listener_lease(self):
        first=ListenerLease(self.data/'shared.lock');second=ListenerLease(self.data/'shared.lock');first.acquire()
        try:
            with self.assertRaises(QwenError):second.acquire()
        finally:first.release()
        second.acquire();second.release()
    def test_stopped_or_original_takeover_blocks_delayed_hotkey(self):
        controller=self.controller_for();controller.start();self.engine.trigger(controller.watcher.cfg);original=self.engine.trigger;old_cfg=controller.watcher.cfg
        controller.stop()
        with self.assertRaises(QwenError):original(old_cfg)
        controller.start();self.bridge.processes.return_value=[{'pid':123}]
        with self.assertRaises(QwenError):original(controller.watcher.cfg)
    def test_active_original_prevents_watcher_creation(self):
        controller=self.controller_for();self.bridge.processes.return_value=[{'pid':123}]
        with self.assertRaises(QwenError):controller.start()
        self.assertIsNone(controller.watcher);self.assertIsNone(controller.lease.handle)
    def test_failed_manual_trigger_never_reports_requested_success(self):
        self.engine.trigger.side_effect=OSError('synthetic SendInput failed');controller=self.controller_for()
        with self.assertRaises(QwenError):controller.trigger()
        self.assertIsNone(controller.lease.handle);self.assertFalse(controller.status()['listening'])
    def test_stop_and_start_reuses_config_and_keeps_successful_status_observational(self):
        controller=self.controller_for();self.assertTrue(controller.start()['listening']);old=controller.watcher;controller.stop();self.assertTrue(controller.start()['listening']);self.assertIsNot(controller.watcher,old);self.assertEqual(controller.watcher.cfg,old.cfg);self.assertFalse(controller.status()['recording_verified'])
        with self.assertRaises(QwenError):self.engine.trigger(old.cfg)
        self.engine.trigger(controller.watcher.cfg)
    def test_saving_settings_does_not_create_watcher_or_send_shortcut(self):
        original_trigger=self.engine.trigger;controller=self.controller_for();result=controller.update({'apps':{'teams':{'enabled':False}},'trigger':{'hotkey':'ctrl+space'}})
        self.assertIsNone(controller.watcher);self.assertIsNone(controller.lease.handle);self.assertFalse(result['running']);self.assertFalse(result['recording_verified'])
        self.assertEqual(result['configuration']['hotkey'],'ctrl+space');original_trigger.assert_not_called()
    def test_malformed_snapshot_error_strings_and_timestamps_are_never_public(self):
        state=safe_snapshot({'last_error':'synthetic private error','last_trigger_at':'synthetic secret','audio_errors':'synthetic private error'},source='suite',cfg=DEFAULT)
        self.assertEqual(state['last_trigger_at'],0);self.assertNotIn('synthetic',json.dumps(state));self.assertFalse(state['diagnostics']['capture_error'])
    def test_unsupported_hotkey_and_bad_configuration_fail_before_save(self):
        controller=self.controller_for();before=self.engine.CONFIG_PATH.read_bytes()
        for body in [{'trigger':{'hotkey':'ctrl+f12'}},{'apps':{'unknown':{'enabled':True}}},{'poll_interval_sec':float('inf')},{'archive':{'dir':'not allowed'}}]:
            with self.assertRaises(QwenError):controller.update(body)
        self.assertEqual(before,self.engine.CONFIG_PATH.read_bytes())
    def test_public_configuration_does_not_expose_paths_or_unknown_secrets(self):
        cfg=copy.deepcopy(DEFAULT);cfg['archive']['dir']='private path';cfg['launcher']['exe']='private executable';cfg['api_key']='synthetic excluded secret';cfg['poll_interval_sec']='synthetic excluded secret'
        public=json.dumps(public_config(cfg));self.assertNotIn('private',public);self.assertNotIn('excluded secret',public)


if __name__=='__main__':unittest.main()
