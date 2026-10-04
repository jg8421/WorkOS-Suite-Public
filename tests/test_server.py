from __future__ import annotations
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import psutil
from suite.core import free_port

ROOT=Path(__file__).resolve().parents[1]


class SuiteHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.data=Path(cls.temp.name)
        cls.port=free_port();cls.origin=f'http://127.0.0.1:{cls.port}'
        cls.log=(cls.data/'fixture.log').open('wb')
        cls.process=subprocess.Popen([sys.executable,'-B','-m','suite.server','--port',str(cls.port),
            '--isolated','--empty-roots','--data-dir',str(cls.data/'app')],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=cls.log,stderr=subprocess.STDOUT,
            **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            try:
                status,_,boot=cls.request('GET','/api/suite/bootstrap')
                if status==200:cls.boot=boot;break
            except (OSError,http.client.HTTPException):pass
            if cls.process.poll() is not None:raise RuntimeError('Isolated Suite startup failed')
            time.sleep(.15)
        else:raise RuntimeError('Isolated Suite startup timeout')
        status,_,cls.core_boot=cls.request('GET','/api/bootstrap')
        if status!=200:raise RuntimeError('Isolated Core startup failed')

    @classmethod
    def request(cls,method,path,body=None,headers=None,suite=True):
        headers={**(headers or {})}
        if method not in ('GET','HEAD'):
            headers.setdefault('Origin',cls.origin)
            headers.setdefault('X-CSRF-Token',(cls.boot if suite else cls.core_boot)['csrf'])
        raw=None
        if body is not None:
            raw=json.dumps(body).encode();headers.setdefault('Content-Type','application/json')
        connection=http.client.HTTPConnection('127.0.0.1',cls.port,timeout=20)
        try:
            connection.request(method,path,raw,headers);response=connection.getresponse()
            data=response.read();result_headers=dict(response.getheaders())
            try:data=json.loads(data)
            except (ValueError,UnicodeError):pass
            return response.status,result_headers,data
        finally:connection.close()

    @classmethod
    def tearDownClass(cls):
        identities=[]
        try:
            for process in psutil.Process(cls.process.pid).children(recursive=True):
                identities.append((process.pid,process.create_time()))
        except psutil.Error:pass
        try:cls.request('POST','/api/suite/shutdown',{})
        except Exception:pass
        try:cls.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            cls.process.terminate()
            try:cls.process.wait(timeout=5)
            finally:raise AssertionError('Owned Suite did not finish graceful shutdown')
        finally:cls.log.close()
        deadline=time.monotonic()+5
        while True:
            live=[]
            for pid,birth in identities:
                try:
                    process=psutil.Process(pid)
                    if process.create_time()==birth and process.is_running() and process.status()!=psutil.STATUS_ZOMBIE:live.append(pid)
                except psutil.Error:pass
            if not live:break
            if time.monotonic()>=deadline:raise AssertionError('Owned fixture descendants did not exit')
            time.sleep(.05)
        for attempt in range(100):
            try:cls.temp.cleanup();break
            except PermissionError:
                if attempt==99:raise
                time.sleep(.05)

    def test_01_bootstrap_private_scopes(self):
        self.assertEqual(self.boot['roots'],[])
        self.assertEqual(len(self.boot['components']),7)
        self.assertEqual(len(self.boot['repositories']),19)
        self.assertTrue(all(not entry.get('private') for entry in self.boot['repositories']))
        self.assertNotEqual(self.boot['csrf'],self.core_boot['csrf'])
        self.assertFalse(self.boot['capabilities']['automatic_capture'])
        self.assertEqual(self.request('GET','/api/health')[2]['app'],'workos-suite')
        for source in ('memory','qwen'):
            self.assertNotEqual(self.request('GET',f'/api/suite/{source}/status')[2]['status'],'running')
        settings=self.request('GET','/api/suite/memory/settings')[2]['settings']
        self.assertTrue(all(v is False for v in settings.values()))

    def test_02_csrf_origin_and_host(self):
        for headers in ({'X-CSRF-Token':''},{'Origin':'https://evil.invalid'},{'Host':'evil.invalid'},
                        {'X-Forwarded-For':'198.51.100.1'},{'X-CSRF-Token':self.core_boot['csrf']}):
            self.assertEqual(self.request('POST','/api/suite/ideas',{'text':'must not persist'},headers)[0],403)
        self.assertEqual(self.request('GET','/suite/server.py')[0],404)
        self.assertEqual(self.request('GET','/idea/../components.lock.json')[0],404)
        self.assertEqual(self.request('POST','/api/shutdown',{},suite=False)[0],403)

    def test_03_project_idea_retry_and_recovery(self):
        status,_,project=self.request('POST','/api/projects',{'name':'Synthetic HTTP project'},suite=False)
        self.assertEqual(status,201)
        status,_,idea=self.request('POST','/api/suite/ideas',{'text':'Actual local idea','project_id':project['id'],'request_id':'test-idea-1'})
        self.assertEqual(status,201)
        repeated=self.request('POST','/api/suite/ideas',{'text':'Actual local idea','project_id':project['id'],'request_id':'test-idea-1'})[2]
        self.assertEqual(idea['id'],repeated['id'])
        self.assertEqual(self.request('POST','/api/suite/ideas',{'text':'Changed request','request_id':'test-idea-1'})[0],400)
        endpoint=f"/api/suite/ideas/{idea['id']}/share"
        first=self.request('POST',endpoint,{'project_id':project['id']})
        self.assertEqual(first[0],200)
        again=self.request('POST',endpoint,{'project_id':project['id']})
        self.assertEqual(first[2]['note_id'],again[2]['note_id'])
        status,_,changed=self.request('PATCH',f"/api/notes/{first[2]['note_id']}",{'body':'Edited user note'},suite=False)
        self.assertEqual(status,200)
        self.assertEqual(self.request('POST',endpoint,{'project_id':project['id']})[2]['note']['body'],'Edited user note')
        self.assertEqual(self.request('DELETE',f"/api/suite/ideas/{idea['id']}")[0],200)
        self.assertTrue(any(n['id']==first[2]['note_id'] for n in self.request('GET','/api/state')[2]['notes']))

    def test_04_files_import_versions_and_safe_download(self):
        folder=self.data/'selected';folder.mkdir();(folder/'sample.txt').write_text('PROJECT_FILE_CONTENT',encoding='utf-8')
        (folder/'sample.html').write_text('<script>alert(1)</script>',encoding='utf-8')
        status,_,root=self.request('POST','/api/suite/roots',{'path':str(folder),'label':'Synthetic test files'})
        self.assertEqual(status,201)
        scope=f"root_id={root['id']}&path="
        self.assertIn('PROJECT_FILE_CONTENT',str(self.request('GET','/api/suite/file-preview?'+scope+'sample.txt')[2]))
        for path in ('../outside.txt','C:%5CWindows%5Cwin.ini'):
            self.assertNotEqual(self.request('GET','/api/suite/file?'+scope+path)[0],200)
        status,headers,raw=self.request('GET','/api/suite/file?'+scope+'sample.html')
        self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'application/octet-stream')
        self.assertTrue(headers['Content-Disposition'].startswith('attachment'))
        project=self.request('POST','/api/projects',{'name':'Synthetic import project'},suite=False)[2]
        body={'root_id':root['id'],'paths':['sample.txt'],'project_id':project['id']}
        first=self.request('POST','/api/suite/files/import',body)
        self.assertEqual(first[0],200)
        again=self.request('POST','/api/suite/files/import',body)
        self.assertEqual(first[2]['results'][0]['document_id'],again[2]['results'][0]['document_id'])
        self.assertTrue(again[2]['results'][0]['reused'])
        (folder/'sample.txt').write_text('CHANGED_VERSION',encoding='utf-8')
        newer=self.request('POST','/api/suite/files/import',body)
        self.assertNotEqual(first[2]['results'][0]['document_id'],newer[2]['results'][0]['document_id'])
        documents=[x for x in self.request('GET','/api/state')[2]['documents'] if x['project_id']==project['id']]
        self.assertEqual(len(documents),2)

    def test_05_embedded_assets_and_no_arbitrary_command(self):
        for path in ('/','/suite.js','/suite.css','/workos/','/app.js','/style.css','/idea/','/idea/sw.js'):
            status,headers,_=self.request('GET',path)
            self.assertEqual(status,200,path)
            self.assertEqual(headers['X-Frame-Options'],'SAMEORIGIN')
        self.assertEqual(self.request('POST','/api/suite/components/unknown/start',{})[0],400)
        self.assertEqual(self.request('POST','/api/suite/components/workos/start',{'command':['whoami']})[0],400)
        self.assertEqual(self.request('POST','/api/suite/phone/pair',{'address':'https://evil.invalid','code':'123456'})[0],400)

if __name__=='__main__':unittest.main()
