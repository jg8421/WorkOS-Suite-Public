"""Synthetic account and isolated real HTTP gateway; no private config or devices."""
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
from suite.core import free_port
from suite.remote_access import RemoteAccess, _passwords


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://suite.example.test'
SYNTHETIC_PASSWORD = 'synthetic-only-suite-password'
# Poison native dispatch in the fixture: a missing access fence fails a test
# rather than opening a real desktop window, recording, or touching clipboard.
SERVER_FIXTURE = '''
from suite import server
def forbidden(*args, **kwargs):
    raise AssertionError('Synthetic guard: native operation was dispatched')
server.NativeTools.launch = forbidden
server.FileService.open = forbidden
server.FileService.clipboard = forbidden
server.ProcessService.control = forbidden
server.Application.component_action = forbidden
original_post = server.NativeAdapters.post
def safe_post(self, component, action, body):
    if component in ('qwen', 'phone') or component == 'memory' and action in ('start', 'stop', 'settings'):
        return forbidden()
    return original_post(self, component, action, body)
server.NativeAdapters.post = safe_post
raise SystemExit(server.main())
'''


class RemoteConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.account = self.folder/'core'/'authentication'
        _passwords.PasswordAuth(self.account).configure_password(SYNTHETIC_PASSWORD)

    def tearDown(self):
        self.temp.cleanup()

    def test_fixed_https_origin_and_explicit_private_config(self):
        for origin in ('http://example.test', 'https://user@example.test', 'https://example.test/path',
                       'https://example.test?query=1', 'https://example.test#hash', 'https://example.test:99999'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                RemoteAccess(origin, self.account)
        config = self.folder/'remote.json'
        config.write_text(json.dumps({'origin': ORIGIN, 'authentication_dir': str(self.account)}), encoding='utf-8')
        access = RemoteAccess.from_config(config, self.account)
        self.assertEqual(access.origin, ORIGIN)
        with self.assertRaises(ValueError):RemoteAccess.from_config(config, self.folder/'another'/'authentication')
        config.write_text(json.dumps({'origin': ORIGIN, 'authentication_dir': str(self.account), 'password': 'never-supported'}), encoding='utf-8')
        with self.assertRaises(ValueError):RemoteAccess.from_config(config, self.account)

    def test_missing_account_does_not_enable_public_access(self):
        with self.assertRaises(ValueError):RemoteAccess(ORIGIN, self.folder/'missing-account')

    def test_encoded_or_unknown_suite_routes_fail_closed(self):
        for method, path in [('GET', '/api/suite/unknown'), ('POST', '/api/suite/files/%6fpen'),
                             ('POST', '/api/suite/files/../files/open'), ('POST', '/api/suite/files//open'),
                             ('PUT', '/api/suite/ideas'), ('GET', '/api/artifacts/config')]:
            with self.subTest(method=method, path=path), self.assertRaises(PermissionError):
                RemoteAccess.check_capability(method, path)

    def test_local_account_change_refreshes_gateway_validation(self):
        access = RemoteAccess(ORIGIN, self.account)
        old_generation = access.auth.generation
        _passwords.PasswordAuth(self.account).configure_password(SYNTHETIC_PASSWORD+'-changed')
        self.assertTrue(access.auth.generation != old_generation)


class RemoteSuiteHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.temp.name)
        cls.app_data = cls.data/'app'
        cls.account = cls.app_data/'workos'/'authentication'
        _passwords.PasswordAuth(cls.account).configure_password(SYNTHETIC_PASSWORD)
        cls.config = cls.data/'remote.json'
        cls.config.write_text(json.dumps({'origin': ORIGIN, 'authentication_dir': str(cls.account)}), encoding='utf-8')
        cls.port = free_port()
        cls.local_origin = f'http://127.0.0.1:{cls.port}'
        cls.log = (cls.data/'server.log').open('wb')
        cls.process = subprocess.Popen([sys.executable, '-B', '-c', SERVER_FIXTURE, '--port', str(cls.port),
            '--data-dir', str(cls.app_data), '--isolated', '--empty-roots', '--remote-config', str(cls.config)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=cls.log, stderr=subprocess.STDOUT,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        deadline = time.monotonic()+35
        while time.monotonic()<deadline:
            try:
                status, _, boot = cls.request('GET', '/api/suite/bootstrap', remote=False)
                if status==200:cls.local_boot=boot;break
            except (OSError, http.client.HTTPException):pass
            if cls.process.poll() is not None:raise RuntimeError('Synthetic remote Suite startup failed')
            time.sleep(.15)
        else:raise RuntimeError('Synthetic remote Suite startup timeout')
        status, _, cls.core_boot = cls.request('GET', '/api/bootstrap', remote=False)
        if status!=200:raise RuntimeError('Synthetic Core startup failed')
        cls.folder=cls.data/'selected'
        cls.folder.mkdir()
        (cls.folder/'source.txt').write_text('SYNTHETIC REMOTE FILE', encoding='utf-8')
        status, _, cls.file_root = cls.request('POST', '/api/suite/roots', {'path': str(cls.folder), 'label': 'Synthetic selected files'},
                                              remote=False, csrf=cls.local_boot['csrf'])
        if status!=201:raise RuntimeError('Synthetic file root could not be registered')

    @classmethod
    def request(cls, method, path, body=None, *, remote=True, cookie='', csrf=None, headers=None):
        values = {'Host': 'suite.example.test', 'CF-Connecting-IP': '198.51.100.21'} if remote else {}
        if cookie:values['Cookie']=cookie
        if method not in ('GET', 'HEAD'):
            values['Origin']=ORIGIN if remote else cls.local_origin
            if csrf is not None:values['X-CSRF-Token']=csrf
        raw = None
        if body is not None:
            raw=json.dumps(body).encode();values['Content-Type']='application/json'
        values.update(headers or {})
        connection=http.client.HTTPConnection('127.0.0.1', cls.port, timeout=25)
        try:
            connection.request(method, path, raw, values)
            response=connection.getresponse();result=response.read();response_headers=dict(response.getheaders())
            try:result=json.loads(result)
            except (ValueError, UnicodeError):pass
            return response.status, response_headers, result
        finally:connection.close()

    @classmethod
    def login(cls):
        status, headers, challenge=cls.request('GET', '/auth/challenge')
        if status!=200:raise RuntimeError('Synthetic login challenge failed')
        cookie=headers['Set-Cookie'].split(';',1)[0]
        status, headers, result=cls.request('POST', '/auth/login', {'username':'workos-user','password':SYNTHETIC_PASSWORD},
                                         cookie=cookie, csrf=challenge['csrf'])
        if status!=200 or result.get('ok') is not True:raise RuntimeError('Synthetic login failed')
        session=headers['Set-Cookie'].split(';',1)[0]
        status, _, boot=cls.request('GET','/api/suite/bootstrap',cookie=session)
        if status!=200:raise RuntimeError('Synthetic authenticated bootstrap failed')
        return session, boot['csrf'], headers

    @classmethod
    def tearDownClass(cls):
        try:cls.request('POST', '/api/suite/shutdown', {}, remote=False, csrf=cls.local_boot['csrf'])
        except Exception:pass
        try:cls.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            cls.process.terminate();cls.process.wait(timeout=5)
            raise AssertionError('Owned synthetic Suite did not exit')
        finally:cls.log.close()
        for attempt in range(100):
            try:cls.temp.cleanup();break
            except PermissionError:
                if attempt==99:raise
                time.sleep(.05)

    def test_01_anonymous_navigation_api_download_and_head_reveal_no_data(self):
        status, headers, _=self.request('GET','/')
        self.assertEqual(status,303);self.assertEqual(headers['Location'],'/auth/login')
        for path in ('/api/suite/bootstrap','/api/bootstrap','/api/state','/api/backup','/api/suite/ideas',
                     '/api/suite/file?root_id='+self.file_root['id']+'&path=source.txt'):
            with self.subTest(path=path):
                status, _, data=self.request('GET',path)
                self.assertEqual(status,401);self.assertEqual(data.get('code'),'login_required')
                self.assertNotIn('csrf',data)
        self.assertEqual(self.request('HEAD','/api/suite/bootstrap')[0],401)

    def test_02_login_uses_secure_host_only_cookie_and_same_session_csrf_for_core(self):
        cookie, csrf, headers=self.login()
        for flag in ('Secure','HttpOnly','SameSite=Lax','Path=/'):
            self.assertIn(flag,headers['Set-Cookie'])
        self.assertNotIn('Domain=',headers['Set-Cookie'])
        status, _, boot=self.request('GET','/api/bootstrap',cookie=cookie)
        self.assertEqual(status,200)
        self.assertTrue(boot['csrf']==csrf)
        self.assertTrue(csrf!=self.local_boot['csrf'] and csrf!=self.core_boot['csrf'])
        self.assertTrue(boot['auth']['public_login'])
        status, _, suite=self.request('GET','/api/suite/bootstrap',cookie=cookie)
        self.assertEqual(status,200);self.assertTrue(suite['capabilities']['remote_request'])
        self.assertFalse(suite['capabilities']['host_controls'])
        self.assertEqual(self.request('GET','/workos/',cookie=cookie)[0],200)

    def test_03_login_challenge_cookie_binding_and_cross_origin_rejected(self):
        status, headers, challenge=self.request('GET','/auth/challenge')
        self.assertEqual(status,200)
        body={'username':'workos-user','password':SYNTHETIC_PASSWORD}
        self.assertEqual(self.request('POST','/auth/login',body,csrf=challenge['csrf'])[0],409)
        cookie=headers['Set-Cookie'].split(';',1)[0]
        self.assertEqual(self.request('POST','/auth/login',body,cookie=cookie,csrf=challenge['csrf'],headers={'Origin':'https://evil.invalid'})[0],403)
        body['password']='synthetic-wrong-password'
        self.assertEqual(self.request('POST','/auth/login',body,cookie=cookie,csrf=challenge['csrf'])[0],403)

    def test_04_csrf_and_session_required_for_suite_and_core_mutations(self):
        cookie, csrf, _=self.login()
        for token in (None,self.local_boot['csrf'],self.core_boot['csrf'],'synthetic-invalid'):
            with self.subTest(token_kind=type(token).__name__):
                self.assertEqual(self.request('POST','/api/suite/ideas',{'text':'rejected'},cookie=cookie,csrf=token)[0],403)
                self.assertEqual(self.request('POST','/api/projects',{'name':'rejected'},cookie=cookie,csrf=token)[0],403)
        self.assertEqual(self.request('POST','/api/suite/ideas',{'text':'rejected'},csrf=csrf)[0],401)
        self.assertEqual(self.request('POST','/api/suite/ideas',{'text':'rejected'},cookie=cookie,csrf=csrf,headers={'Origin':''})[0],403)

    def test_05_authenticated_ideas_research_and_registered_file_crud_persist(self):
        cookie, csrf, _=self.login()
        status, _, project=self.request('POST','/api/projects',{'name':'Synthetic remote project'},cookie=cookie,csrf=csrf)
        self.assertEqual(status,201)
        status, _, idea=self.request('POST','/api/suite/ideas',{'text':'Synthetic remote idea','project_id':project['id']},cookie=cookie,csrf=csrf)
        self.assertEqual(status,201)
        self.assertEqual(self.request('POST',f"/api/suite/ideas/{idea['id']}/share",{'project_id':project['id']},cookie=cookie,csrf=csrf)[0],200)
        self.assertEqual(self.request('POST','/api/suite/files/import',{'root_id':self.file_root['id'],'paths':['source.txt'],'project_id':project['id']},cookie=cookie,csrf=csrf)[0],200)
        status, _, state=self.request('GET','/api/state',cookie=cookie)
        self.assertEqual(status,200)
        self.assertTrue(any(doc.get('project_id')==project['id'] for doc in state['documents']))
        scope='root_id='+self.file_root['id']+'&path=source.txt'
        status, headers, raw=self.request('GET','/api/suite/file?'+scope,cookie=cookie)
        self.assertEqual(status,200);self.assertEqual(raw,b'SYNTHETIC REMOTE FILE');self.assertIn('attachment',headers['Content-Disposition'])
        operation={'operation':'copy','root_id':self.file_root['id'],'path':'source.txt','target':'remote-copy.txt','request_id':'synthetic-remote-copy'}
        self.assertEqual(self.request('POST','/api/suite/files/operation',operation,cookie=cookie,csrf=csrf)[0],200)
        self.assertEqual((self.folder/'remote-copy.txt').read_text(encoding='utf-8'),'SYNTHETIC REMOTE FILE')
        self.assertNotEqual(self.request('GET','/api/suite/file?root_id='+self.file_root['id']+'&path=../outside.txt',cookie=cookie)[0],200)

    def test_06_host_controls_rejected_before_dispatch_even_with_valid_login(self):
        cookie, csrf, _=self.login()
        posts=['/api/suite/roots','/api/suite/files/open','/api/suite/files/clipboard',
               '/api/suite/native-tools/files/launch','/api/suite/native-tools/processes/launch','/api/suite/processes/control',
               '/api/suite/phone/pair','/api/suite/phone/screenshot','/api/suite/components/memory/start',
               '/api/suite/qwen/start','/api/suite/qwen/stop','/api/suite/qwen/trigger','/api/suite/qwen/config',
               '/api/suite/memory/settings','/api/suite/memory/start','/api/suite/shutdown',
               '/api/shutdown','/api/ai/settings','/api/models/custom','/api/artifacts/config',
               '/api/projects/anything/artifacts/bind','/api/memory/import','/auth/setup']
        for path in posts:
            with self.subTest(path=path):self.assertEqual(self.request('POST',path,{},cookie=cookie,csrf=csrf)[0],403)
        for path in ('/api/suite/processes','/api/suite/phone/devices','/api/artifacts/config','/api/memory/scan','/api/public/status','/auth/setup'):
            with self.subTest(path=path):self.assertEqual(self.request('GET',path,cookie=cookie)[0],403)
        for method in ('PATCH','PUT','DELETE'):
            self.assertEqual(self.request(method,'/api/models/custom/'+'a'*16,{},cookie=cookie,csrf=csrf)[0],403)
        self.assertEqual(self.request('GET','/api/health',remote=False)[0],200)

    def test_07_fixed_host_origin_forwarded_markers_and_cross_site_fences(self):
        cookie, csrf, _=self.login()
        for headers in ({'Host':'evil.invalid'},{'Host':f'127.0.0.1:{self.port}'},
                        {'Origin':'https://evil.invalid'},{'Sec-Fetch-Site':'cross-site'}):
            with self.subTest(headers=headers):self.assertEqual(self.request('GET','/api/suite/bootstrap',cookie=cookie,headers=headers)[0],403)
        self.assertEqual(self.request('GET','/api/suite/bootstrap',cookie=cookie,headers={'CF-Connecting-IP':''})[0],200)
        self.assertEqual(self.request('GET','/api/suite/bootstrap',cookie='__Host-workos-session=synthetic-forgery')[0],401)
        self.assertEqual(self.request('GET','/api/suite/bootstrap',remote=False,headers={'X-Forwarded-For':'198.51.100.22'})[0],403)

    def test_08_expired_sessions_and_logout_invalidate_suite_and_core(self):
        cookie, csrf, _=self.login()
        self.assertEqual(self.request('POST','/auth/logout',{},cookie=cookie,csrf=csrf)[0],200)
        self.assertEqual(self.request('GET','/api/suite/bootstrap',cookie=cookie)[0],401)
        self.assertEqual(self.request('GET','/api/state',cookie=cookie)[0],401)
        cookie, _, _=self.login()
        auth=_passwords.PasswordAuth(self.account)
        with auth.database() as db:db.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.request('GET','/api/suite/bootstrap',cookie=cookie)[0],401)

    def test_09_csp_downloads_and_host_only_status_are_honest(self):
        cookie, _, _=self.login()
        status, headers, _=self.request('GET','/',cookie=cookie)
        self.assertEqual(status,200);self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertIn("connect-src 'self'",headers['Content-Security-Policy'])
        self.assertIn("frame-src 'self'",headers['Content-Security-Policy'])
        status, _, status_data=self.request('GET','/api/suite/qwen/status',cookie=cookie)
        self.assertEqual(status,200);self.assertNotIn('panel_url',status_data)
        self.assertFalse(status_data['can_start']);self.assertFalse(status_data['can_trigger'])


if __name__=='__main__':unittest.main()
