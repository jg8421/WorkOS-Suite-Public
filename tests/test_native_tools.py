from __future__ import annotations
import ast
from contextlib import redirect_stdout
import http.client
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from suite.native_tools import NativeTools,NativeToolUnavailable,TOOLS,PROBE_CODE,_python_executable,_runtime_environment
from suite.server import Handler,LocalServer


def available(*missing,tk_resources=True):
    names=set(module for tool in TOOLS.values() for module in tool['required'])|{'pythoncom','win32com.client'}
    return {'modules':{name:name not in missing for name in names},'tk_resources':tk_resources,'version':[3,13,12]}


class NativeToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.app=self.base/'app';self.app.mkdir()
        for key,spec in TOOLS.items():
            folder=self.app/'components'/key;folder.mkdir(parents=True);(folder/spec['script']).write_text('raise RuntimeError("must not execute GUI fixture")')
        self.exe=self.base/'runtime'/'python'/'python.exe';self.exe.parent.mkdir(parents=True);self.exe.write_bytes(b'synthetic executable')
        self.tools=NativeTools(self.app)

    def tearDown(self):self.tools.close();self.temp.cleanup()

    def ready(self,probe=None):
        with patch('suite.native_tools.WINDOWS',True),patch.object(self.tools,'_candidates',return_value=[(self.exe,'packaged')]),patch.object(self.tools,'_probe',return_value=probe or available()):
            return self.tools.describe()

    def test_missing_files_dependencies_do_not_disable_valid_process_gui(self):
        report=self.ready(available('tksheet','tkinterweb','pythoncom','win32com.client'))
        by_id={item['id']:item for item in report['tools']}
        self.assertFalse(by_id['files']['can_launch']);self.assertIn('tksheet',by_id['files']['reason'])
        self.assertTrue(by_id['processes']['can_launch']);self.assertFalse(by_id['processes']['sensor_com_available'])
        self.assertNotIn(str(self.exe),json.dumps(report))

    def test_tk_resources_are_required_even_if_python_import_succeeds(self):
        report=self.ready(available(tk_resources=False))
        self.assertTrue(all(not item['can_launch'] for item in report['tools']))
        self.assertTrue(all('Tk 界面资源' in item['reason'] for item in report['tools']))

    def test_known_native_runtime_fallback_after_embedded_missing_tk(self):
        stable=self.base/'native-runtime'/'Scripts'/'python.exe';stable.parent.mkdir(parents=True);stable.write_bytes(b'fixture')
        with patch('suite.native_tools.WINDOWS',True),patch.object(self.tools,'_candidates',return_value=[(self.exe,'packaged'),(stable,'native-runtime')]),patch.object(self.tools,'_probe',side_effect=[available('tkinter'),available()]):
            report=self.tools.describe()
        self.assertTrue(all(item['can_launch'] and item['runtime_source']=='native-runtime' for item in report['tools']))

    def test_discovery_prioritizes_package_and_stable_runtime_not_request_values(self):
        local=self.base/'local';stable=local/'WorkOS-Suite'/'native-runtime'/'Scripts'/'python.exe'
        stable.parent.mkdir(parents=True);stable.write_bytes(b'fixture')
        with patch.dict(os.environ,{'LOCALAPPDATA':str(local)}),patch('suite.native_tools._registry_pythons',return_value=[]),patch('suite.native_tools._launcher_pythons',return_value=[]),patch('suite.native_tools.shutil.which',return_value=None):
            values=self.tools._candidates()
        self.assertEqual(values[:2],[(self.exe,'packaged'),(stable,'native-runtime')])

    def test_dependency_probes_are_bounded_and_fail_closed_no_raw_error(self):
        for result in (SimpleNamespace(returncode=1,stdout='private trace'),SimpleNamespace(returncode=0,stdout='not JSON')):
            with patch('suite.native_tools.subprocess.run',return_value=result):self.assertIsNone(self.tools._probe(self.exe,.5))
        with patch('suite.native_tools.subprocess.run',side_effect=subprocess.TimeoutExpired('fixed',.5)) as run:
            self.assertIsNone(self.tools._probe(self.exe,.5))
            self.assertEqual(run.call_args.kwargs['timeout'],.5)
            self.assertEqual(run.call_args.args[0][1:3],('-I','-B'))
        with patch('suite.native_tools.WINDOWS',True),patch.object(self.tools,'_candidates',return_value=[(self.exe,'packaged')]),patch.object(self.tools,'_probe',return_value=None):
            report=self.tools.describe()
        self.assertTrue(all(not item['can_launch'] for item in report['tools']))
        self.assertNotIn('private trace',json.dumps(report))

    def test_dependency_library_stdout_cannot_corrupt_readiness_json(self):
        (self.base/'tk8.6').mkdir();(self.base/'tk8.6'/'tk.tcl').write_text('fixture')
        tkinter=SimpleNamespace(TkVersion=8.6,Tcl=lambda:SimpleNamespace(eval=lambda query:str(self.base/'tcl8.6')))
        def imported(name):
            print('synthetic dependency deprecation warning')
            return SimpleNamespace(open=lambda:None,HtmlFrame=object)
        output=io.StringIO()
        with redirect_stdout(output),patch('importlib.import_module',side_effect=imported),patch.dict(sys.modules,{'tkinter':tkinter}):
            exec(PROBE_CODE,{})
        report=json.loads(output.getvalue())
        self.assertTrue(all(report['modules'].values()));self.assertTrue(report['tk_resources'])
        self.assertNotIn('deprecation',output.getvalue())

    def test_fixed_launch_does_not_register_worker_or_kill_on_close(self):
        self.ready();process=MagicMock();process.poll.return_value=None
        with patch('suite.native_tools.subprocess.Popen',return_value=process) as launch:
            result=self.tools.launch('files',{})
        self.assertTrue(result['launched']);self.assertFalse(result['window_verified'])
        command=launch.call_args.args[0]
        self.assertEqual(command[0],str(self.exe));self.assertEqual(command[1:4],('-I','-B','-c'))
        self.assertEqual(command[-1],str(self.app/'components'/'files'/'file_workbench.py'))
        self.assertEqual(launch.call_args.kwargs['cwd'],str(self.app/'components'/'files'))
        self.assertTrue(launch.call_args.kwargs['creationflags']&0x01000000)
        self.assertTrue(launch.call_args.kwargs['creationflags']&8)
        self.assertTrue(launch.call_args.kwargs['close_fds'])
        self.tools.close();process.terminate.assert_not_called();process.kill.assert_not_called();process.wait.assert_not_called()

    def test_unknown_tool_or_argv_env_root_never_reaches_process_creation(self):
        self.ready()
        for key,body in [('shell',{}),('files',{'root_id':'fixture'}),('files',{'argv':['calc.exe']}),('processes',{'env':{'PYTHONPATH':'untrusted'}}),('files',None)]:
            with self.subTest(key=key,body=body),patch('suite.native_tools.subprocess.Popen') as launch:
                with self.assertRaises(ValueError):self.tools.launch(key,body)
                launch.assert_not_called()

    def test_removed_script_and_python_do_not_launch_cached_ready_record(self):
        self.ready();self.exe.unlink()
        with patch('suite.native_tools.subprocess.Popen') as launch:
            with self.assertRaises(NativeToolUnavailable):self.tools.launch('files',{})
            launch.assert_not_called()
        self.exe.write_bytes(b'fixture');(self.app/'components'/'files'/'file_workbench.py').unlink()
        with patch('suite.native_tools.subprocess.Popen') as launch:
            with self.assertRaises(NativeToolUnavailable):self.tools.launch('files',{})
            launch.assert_not_called()

    def test_interpreter_path_and_environment_avoid_user_code_injection(self):
        self.assertEqual(_python_executable(self.exe),self.exe)
        self.assertIsNone(_python_executable(self.base/'cmd.exe'));self.assertIsNone(_python_executable('python.exe'))
        with patch.dict(os.environ,{'PYTHONPATH':'untrusted','PYTHONHOME':'untrusted','FILE_WORKBENCH_SELFTEST':'1','TCL_LIBRARY':'untrusted','TK_LIBRARY':'untrusted'}):
            environment=_runtime_environment(self.exe)
        self.assertFalse({'PYTHONPATH','PYTHONHOME','FILE_WORKBENCH_SELFTEST','TCL_LIBRARY','TK_LIBRARY'}&environment.keys())
        folder=self.exe.parent/'tcl'
        for name,file in [('tcl8.6','init.tcl'),('tk8.6','tk.tcl')]:
            (folder/name).mkdir(parents=True);(folder/name/file).write_text('synthetic Tcl resource')
        environment=_runtime_environment(self.exe)
        self.assertEqual(environment['TCL_LIBRARY'],str(folder/'tcl8.6'))
        self.assertEqual(environment['TK_LIBRARY'],str(folder/'tk8.6'))

    def test_original_source_settings_do_not_modify_program_directory(self):
        source=Path(__file__).resolve().parents[1]/'components'/'files'/'file_workbench.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        branch=next(node for node in tree.body if isinstance(node,ast.If) and any(isinstance(child,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='BUNDLE_DIR' for target in child.targets) for child in node.body))
        appdata=self.base/'user-settings';program=self.base/'program'/'file_workbench.py'
        namespace={'sys':SimpleNamespace(frozen=False),'os':SimpleNamespace(environ={'APPDATA':str(appdata)}),'Path':Path,'__file__':str(program)}
        exec(compile(ast.Module(body=[branch],type_ignores=[]),'directory_setup','exec'),namespace)
        self.assertEqual(namespace['BASE_DIR'],appdata/'FileWorkbench')
        self.assertEqual(namespace['BUNDLE_DIR'],program.parent)
        self.assertFalse(program.parent.exists())

    def test_original_direct_launch_adds_own_helper_directory_not_caller_folder(self):
        source=Path(__file__).resolve().parents[1]/'components'/'files'/'file_workbench.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        position=next(index for index,node in enumerate(tree.body) if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='_MODULE_DIR' for target in node.targets))
        program=self.base/'program'/'file_workbench.py';untrusted=self.base/'project'
        namespace={'sys':SimpleNamespace(path=[str(untrusted)]),'Path':Path,'__file__':str(program)}
        setup=compile(ast.Module(body=tree.body[position:position+2],type_ignores=[]),'native_source_setup','exec')
        exec(setup,namespace);exec(setup,namespace)
        self.assertEqual(namespace['sys'].path,[str(program.parent),str(untrusted)])
        self.assertFalse(program.parent.exists())


class NativeToolsHttpTests(unittest.TestCase):
    def setUp(self):
        self.native=MagicMock();self.native.describe.return_value={'tools':[{'id':'files','can_launch':False,'reason':'Tk missing'}]}
        self.native.launch.return_value={'launched':True,'component':'files','window_verified':False}
        self.server=LocalServer(('127.0.0.1',0),Handler)
        self.server.app=SimpleNamespace(port=self.server.server_port,csrf='synthetic-csrf',native_tools=self.native)
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):self.server.shutdown();self.thread.join(3);self.server.server_close()

    def request(self,method,path,body=None,headers=None):
        values={'Origin':self.origin,'X-CSRF-Token':'synthetic-csrf','Content-Type':'application/json',**(headers or {})}
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        try:
            connection.request(method,path,json.dumps(body).encode() if body is not None else None,values)
            response=connection.getresponse();return response.status,json.loads(response.read())
        finally:connection.close()

    def test_metadata_is_read_only_and_launch_requires_local_same_origin_csrf(self):
        self.assertEqual(self.request('GET','/api/suite/native-tools')[0],200)
        self.native.launch.assert_not_called()
        for headers in ({'Origin':'https://example.invalid'},{'X-CSRF-Token':'wrong'},{'Host':'example.invalid'},{'Forwarded':'for=127.0.0.1'}):
            self.assertEqual(self.request('POST','/api/suite/native-tools/files/launch',{},headers)[0],403)
        self.native.launch.assert_not_called()
        self.assertEqual(self.request('POST','/api/suite/native-tools/files/launch',{})[0],200)
        self.native.launch.assert_called_once_with('files',{})
        self.assertEqual(self.request('POST','/api/suite/native-tools/shell/launch',{})[0],404)
        self.assertEqual(self.request('GET','/api/suite/native-tools?path=untrusted')[0],400)


if __name__=='__main__':unittest.main()
