import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('suite_packager',ROOT/'tools/package_suite.py')
packager=importlib.util.module_from_spec(spec);spec.loader.exec_module(packager)


class PackageTests(unittest.TestCase):
    def test_private_or_traversal_source_never_enters_package(self):
        for name in ['data/private.db','suite/.env','components/qwen/录音/private.wav','components/qwen/config.json',
                     'components/memory/state/token.json','components/memory/events.jsonl','components/workos/runtime/models.json',
                     'components/phone/bin/platform-tools/adb.exe','../launch.py','C:/launch.py','suite/custom-models.json','suite/session-token.json',
                     'components/phone/core/last-addr.txt','components/phone/core/adbkey.txt','components/phone/core/phone-pairing-state.json',
                     'components/processes/hardware_sensor_bridge.json','components/processes/hardware_monitor_path.txt']:
            with self.subTest(name=name):self.assertFalse(packager.allowed_source(name))
        for name in ['launch.py','suite/runtime.py','components/qwen/config.example.json','components/memory/src/config.mjs',
                     'components/memory/releases/PersonalMemory-0.4.0.apk','components/workos/workos/server.py','web/suite.js']:
            with self.subTest(name=name):self.assertTrue(packager.allowed_source(name))

    def test_archive_traversal_devices_links_and_budget_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for name in ['../outside','/absolute','C:/drive','dir/NUL.txt','dir/trailing.','dir/x:stream','dir/a?b']:
                archive=root/'input.zip'
                with zipfile.ZipFile(archive,'w') as z:z.writestr(name,'bad')
                with self.subTest(name=name),self.assertRaises(ValueError):packager.extract(archive,root/'target')
            with zipfile.ZipFile(root/'input.zip','w') as z:
                info=zipfile.ZipInfo('link');info.external_attr=(0o120777<<16);z.writestr(info,'outside')
            with self.assertRaises(ValueError):packager.extract(root/'input.zip',root/'target')
            self.assertFalse((root/'outside').exists())

    def test_archive_preserves_licenses_and_only_installs_wheel_libraries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with zipfile.ZipFile(root/'input.whl','w') as z:
                z.writestr('a.dist-info/licenses/LICENSE','license')
                z.writestr('a.data/purelib/a.py','x=1')
                z.writestr('a.data/scripts/install.cmd','malicious')
                z.writestr('a.data/headers/a.h','unused')
            packager.extract(root/'input.whl',root/'site',wheel=True)
            self.assertEqual((root/'site/a.py').read_text(),'x=1')
            self.assertTrue((root/'site/a.dist-info/licenses/LICENSE').is_file())
            self.assertFalse((root/'site/a.data').exists())

    def test_download_origin_filename_and_digest_are_checked_before_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache=Path(temporary);raw=b'cached software';(cache/'a.zip').write_bytes(raw)
            good={'url':'https://www.python.org/a.zip','filename':'a.zip','sha256':packager.digest(raw)}
            self.assertEqual(packager.fetch(good,cache),cache/'a.zip')
            for overrides in [{'url':'https://attacker.test/a.zip'},{'url':'https://user:secret@www.python.org/a.zip'},
                              {'url':'https://github.com/other/project/a.zip'},{'filename':'../a.zip'},{'sha256':'0'*64}]:
                with self.subTest(overrides=overrides),self.assertRaises(ValueError):packager.fetch({**good,**overrides},cache)

    def test_production_npm_lock_is_official_pinned_and_script_free(self):
        lock=json.loads((ROOT/'deployment/memory-package-lock.json').read_text())
        packager.validate_npm_lock(lock)
        sample={'resolved':'https://registry.npmjs.org/a/-/a-1.0.tgz','integrity':'sha512-YWJjZA=='}
        for entry in [{**sample,'resolved':'https://mirror.test/a.tgz'},{**sample,'integrity':''},{**sample,'hasInstallScript':True}]:
            with self.assertRaises(ValueError):packager.validate_npm_lock({'lockfileVersion':3,'packages':{'node_modules/a':entry}})

    def test_runtime_lock_contains_complete_offline_windows_inputs(self):
        lock=json.loads((ROOT/'deployment/runtime-lock.json').read_text())
        names={e['name'] for e in lock['entries']}
        self.assertTrue({'python','node','platform-tools','scrcpy','psutil','pycaw','comtypes','pystray','numpy','soundcard',
                         'websocket-client','protobuf','qrcode','cryptography','python-docx','python-pptx','openpyxl'}<=names)
        for entry in lock['entries']:
            self.assertRegex(entry['sha256'],r'^[a-f0-9]{64}$')
            self.assertTrue(entry['url'].startswith('https://'))

    def fixture(self, root):
        files={'suite/__init__.py':'__version__ = "1.0.0"\n','suite/server.py':'pass\n','suite/runtime.py':'pass\n',
               'launch.py':'pass\n','web/index.html':'<html>Suite</html>','tools/deployment_preflight.py':'pass\n',
               'docs/DEPLOYMENT.md':'instructions','deployment/Start-Suite.cmd':'@echo off',
               'deployment/Check-Environment.cmd':'@echo off','deployment/Stop-Suite.cmd':'@echo off','deployment/memory-package-lock.json':'{}',
               'components.lock.json':json.dumps({'components':[{'id':name,'source_revision':'a'*40} for name in packager.COMPONENTS]}),
               'components/qwen/config.json':'{"token":"private-fixture-secret"}','data/private.db':'private fixture business',
               'components/phone/core/last-addr.txt':'private fixture address','components/processes/hardware_sensor_bridge.json':'private fixture sensor config',
               'deployment/runtime-lock.json':json.dumps({'entries':[{'name':'python','filename':'python.zip','url':'https://www.python.org/python.zip','sha256':'0'*64}]})}
        for name in packager.COMPONENTS:files[f'components/{name}/README.md']=name
        for name,body in files.items():
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
        subprocess.run(['git','init',str(root)],check=True,capture_output=True)
        subprocess.run(['git','-C',str(root),'add','.'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(root),'-c','user.name=PackageTest','-c','user.email=package@example.invalid','commit','-m','fixture'],check=True,capture_output=True)

    def test_clean_head_and_per_file_manifest_are_real_and_private_files_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'source';root.mkdir();self.fixture(root)
            archive=base/'python.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('python.exe','synthetic interpreter');z.writestr('LICENSE.txt','license')
            with patch.object(packager,'fetch',return_value=archive),patch.object(packager,'install_memory_dependencies'):
                result=packager.build(root,base/'output',base/'cache')
            self.assertEqual(result['build_status'],'release')
            with zipfile.ZipFile(result['path']) as z:
                prefix=Path(result['path']).stem+'/'
                manifest=json.loads(z.read(prefix+'package-manifest.json'))
                self.assertEqual(manifest['source_revision'],subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD']).decode().strip())
                self.assertFalse(manifest['working_tree_changes'])
                for entry in manifest['files']:self.assertEqual(packager.digest(z.read(prefix+entry['path'])),entry['sha256'])
                self.assertEqual({e['id'] for e in manifest['components']},packager.COMPONENTS)
                self.assertFalse(any('config.json' in name or 'private.db' in name or 'last-addr.txt' in name or 'hardware_sensor_bridge.json' in name for name in z.namelist()))
                pth=z.read(prefix+'runtime/python/python313._pth').decode()
                self.assertNotIn('import site',pth)
                self.assertNotIn(str(Path.home()),pth)

    def test_dirty_release_rejects_and_explicit_preview_is_labelled(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'source';root.mkdir();self.fixture(root)
            (root/'suite/server.py').write_text('changed\n')
            with self.assertRaisesRegex(ValueError,'clean Git HEAD'):packager.build(root,base/'output',base/'cache')
            archive=base/'python.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('python.exe','synthetic interpreter')
            with patch.object(packager,'fetch',return_value=archive),patch.object(packager,'install_memory_dependencies'):
                result=packager.build(root,base/'preview',base/'cache',allow_dirty=True)
            self.assertEqual(result['build_status'],'preview');self.assertIn('-preview.zip',result['path'])


if __name__=='__main__':unittest.main()
