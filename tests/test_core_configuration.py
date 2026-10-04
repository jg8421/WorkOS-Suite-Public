"""Explicit Core configuration and real, synthetic remote-auth boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import psutil

from suite.core import CoreProxy
from suite.core_lease import CoreLease
from suite.runtime import RuntimeManager


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ORIGIN = 'https://suite.example.invalid'


class CoreConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.runtime = Mock()

    def test_explicit_data_never_adopts_legacy_port_and_captures_only_allowed_settings(self):
        selected = self.folder / 'selected-core'
        settings = {'WORKOS_SYNC_ROOT': str(self.folder / 'mirror'),
                    'WORKOS_PUBLIC_ORIGIN': PUBLIC_ORIGIN,
                    'WORKOS_PUBLIC_AUTH_MODE': 'password'}
        with patch.object(CoreProxy, '_existing', return_value=True) as legacy, \
             patch.object(CoreProxy, 'json', return_value={'data_dir': str(self.folder / 'legacy-core')}), \
             patch('suite.core.free_port', return_value=25432):
            core = CoreProxy(ROOT, self.folder / 'suite', self.runtime,
                             core_data_dir=selected, core_settings=settings)
        self.addCleanup(core.close)
        legacy.assert_called_once_with()
        self.assertEqual(core.port, 25432)
        self.assertEqual(core.data_dir, selected.resolve())
        spec = self.runtime.register.call_args.args[0]
        self.assertEqual(spec.command[-2:], ('--data-dir', str(selected.resolve())))
        self.assertEqual(json.loads(spec.command[-5]), settings)
        self.assertIn("k.startswith('WORKOS_')", spec.command[4])
        self.assertIn('os.environ.update(settings)', spec.command[4])
        self.runtime.ensure.assert_called_once_with('workos')

    def test_legacy_borrowing_is_available_only_without_explicit_data(self):
        with patch.object(CoreProxy, '_existing', return_value=True) as legacy, \
             patch('suite.core.free_port') as free:
            core = CoreProxy(ROOT, self.folder, self.runtime)
        legacy.assert_called_once_with()
        free.assert_not_called()
        self.assertEqual(core.port, 18866)
        self.assertEqual(core.data_dir, self.folder.resolve() / 'workos')

    def test_requested_sync_never_silently_borrows_an_unconfigured_legacy_core(self):
        with patch.object(CoreProxy, '_existing', return_value=True), \
             patch.object(CoreProxy, 'json', return_value={'data_dir': str(self.folder / 'other')}), \
             patch('suite.core.free_port', return_value=25432):
            core = CoreProxy(ROOT, self.folder, self.runtime,
                             core_settings={'WORKOS_SYNC_ROOT': str(self.folder / 'mirror')})
        self.addCleanup(core.close)
        self.assertEqual(core.port, 25432)
        self.assertIsNotNone(core.lease)

    def test_invalid_configuration_is_rejected_before_registration_or_launch(self):
        cases = [
            {'core_data_dir': Path('relative-core')},
            {'core_settings': {'WORKOS_SYNC_ROOT': 'relative-mirror'}},
            {'core_settings': {'WORKOS_API_KEY': 'synthetic-forbidden-setting'}},
            {'core_settings': {'WORKOS_PUBLIC_AUTH_MODE': 'access'}},
            {'core_settings': {'WORKOS_SYNC_ROOT': None}},
            {'core_settings': {'WORKOS_PUBLIC_ORIGIN': 42}},
            {'core_settings': {'WORKOS_PUBLIC_AUTH_MODE': False}},
            {'core_settings': {'WORKOS_SYNC_ROOT': 'C:/\x00invalid'}},
        ]
        for origin in ('http://suite.example.invalid', 'https://suite.example.invalid/path',
                       'https://suite.example.invalid?query=1', 'https://suite.example.invalid#fragment',
                       'https://user:password@suite.example.invalid', 'https://'):
            cases.append({'core_settings': {'WORKOS_PUBLIC_ORIGIN': origin}})
        for kwargs in cases:
            with self.subTest(configuration=list(kwargs)), self.assertRaises(ValueError):
                CoreProxy(ROOT, self.folder, self.runtime, **kwargs)
        self.runtime.register.assert_not_called()
        self.runtime.ensure.assert_not_called()

    def test_explicit_data_refuses_same_or_unknown_legacy_location_before_launch(self):
        selected = self.folder / 'selected-core'
        for existing in ({'data_dir': str(selected)}, {}):
            with self.subTest(existing_fields=list(existing)), \
                 patch.object(CoreProxy, '_existing', return_value=True), \
                 patch.object(CoreProxy, 'json', return_value=existing), \
                 patch('suite.core.free_port') as free, self.assertRaises(ValueError):
                CoreProxy(ROOT, self.folder / 'suite', self.runtime, core_data_dir=selected)
            free.assert_not_called()
        self.runtime.register.assert_not_called()
        self.runtime.ensure.assert_not_called()

    def test_owned_proxy_close_releases_only_its_selected_directory_lease(self):
        selected = self.folder / 'selected-core'
        with patch.object(CoreProxy, '_existing', return_value=False):
            first = CoreProxy(ROOT, self.folder, self.runtime, core_data_dir=selected)
            self.addCleanup(first.close)
            with self.assertRaises(ValueError):
                CoreProxy(ROOT, self.folder, Mock(), core_data_dir=selected)
            first.close()
            second = CoreProxy(ROOT, self.folder, Mock(), core_data_dir=selected)
            first.close()  # Must not release a later owner's lock.
            with self.assertRaises(ValueError):
                CoreLease(selected)
            second.close()

    def test_directory_lease_excludes_other_process_until_owner_closes(self):
        selected = self.folder / 'selected-core'
        lease = CoreLease(selected)
        code = ("import sys;sys.path.insert(0,sys.argv[1]);from suite.core_lease import CoreLease;"
                "exec('try:\\n lease=CoreLease(sys.argv[2])\\nexcept ValueError:\\n sys.exit(23)\\n');"
                "lease.close()")
        def probe():
            return subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT), str(selected)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {})).returncode
        try:
            self.assertEqual(probe(), 23)
            with self.assertRaises(ValueError):
                CoreLease(selected / '.')
        finally:
            lease.close()
        self.assertEqual(probe(), 0)

    def test_remote_bridge_preserves_session_and_never_forwards_caller_host_or_identity(self):
        with patch.object(CoreProxy, '_existing', return_value=False), \
             patch('suite.core.free_port', return_value=25432):
            core = CoreProxy(ROOT, self.folder, self.runtime,
                             core_settings={'WORKOS_PUBLIC_ORIGIN': PUBLIC_ORIGIN})
        self.addCleanup(core.close)
        response = Mock()
        response.status = 200
        response.getheader.return_value = '2'
        response.read.return_value = b'{}'
        response.getheaders.return_value = []
        connection = Mock()
        connection.getresponse.return_value = response
        with patch('suite.core.http.client.HTTPConnection', return_value=connection) as connect:
            core.exchange('POST', '/api/projects', b'{}', {
                'Cookie': '__Host-workos-session=synthetic-session',
                'X-CSRF-Token': 'synthetic-session-csrf', 'X-Workspace': 'personal',
                'Host': 'attacker.invalid', 'Origin': 'https://attacker.invalid',
                'CF-Access-Jwt-Assertion': 'synthetic-forged-identity',
                'CF-Connecting-IP': '192.0.2.1', 'X-Forwarded-For': '192.0.2.2',
            }, remote_origin=PUBLIC_ORIGIN)
            sent = connection.request.call_args.kwargs['headers']
            self.assertEqual(sent['Host'], 'suite.example.invalid')
            self.assertEqual(sent['Origin'], PUBLIC_ORIGIN)
            self.assertEqual(sent['Cookie'], '__Host-workos-session=synthetic-session')
            self.assertEqual(sent['X-CSRF-Token'], 'synthetic-session-csrf')
            self.assertEqual(sent['X-Forwarded-For'], '127.0.0.1')
            self.assertNotIn('CF-Access-Jwt-Assertion', sent)
            self.assertNotIn('CF-Connecting-IP', sent)
            connection.close.assert_called_once_with()
            connect.reset_mock()
            with self.assertRaises(PermissionError):
                core.exchange('GET', '/api/state', remote_origin='https://other.example.invalid')
            connect.assert_not_called()


class RealCoreConfigurationTests(unittest.TestCase):
    """Launch only one owned Core, with an empty temp DB and no native adapters."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.manager = RuntimeManager(cls.folder / 'runtime')
        cls.password = secrets.token_urlsafe(24)
        cls.core = None
        try:
            # Inherited settings must not point this fixture at any existing data,
            # memory library, or credential provider.
            with patch.object(CoreProxy, '_existing', return_value=False), patch.dict(os.environ, {
                'WORKOS_SYNC_ROOT': str(cls.folder / 'not-selected-mirror'),
                'WORKOS_PUBLIC_ORIGIN': 'https://not-selected.example.invalid',
            }):
                cls.core = CoreProxy(ROOT, cls.folder / 'suite', cls.manager, isolated=True,
                    core_data_dir=cls.folder / 'core', core_settings={
                        'WORKOS_SYNC_ROOT': str(cls.folder / 'selected-mirror'),
                        'WORKOS_PUBLIC_ORIGIN': PUBLIC_ORIGIN,
                        'WORKOS_PUBLIC_AUTH_MODE': 'password',
                    })
            state = cls.manager.status('workos')
            if not state.get('owned') or state['status'] != 'running':
                raise AssertionError('Synthetic owned Core did not become ready')
            cls.identity = (state['pid'], psutil.Process(state['pid']).create_time())
            cls.local_boot = cls.request('GET', '/api/bootstrap')[2]
            cls.initial_personal = cls.request('GET', '/api/state')[2]
            status, _, account = cls.request('POST', '/auth/setup', {'password': cls.password},
                                             csrf=cls.local_boot['csrf'])
            if status != 200:
                raise AssertionError('Synthetic local password setup failed')
            cls.username = account['username']
        except BaseException:
            cls.manager.close()
            if cls.core is not None:
                cls.core.close()
            cls.cleanup_directory()
            raise

    @classmethod
    def cleanup_directory(cls):
        deadline = time.monotonic() + 5
        while True:
            try:
                cls.temp.cleanup()
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.core.json('POST', '/api/shutdown', {}, timeout=3)
        except (ValueError, OSError):
            pass
        cls.manager.close()
        cls.core.close()
        pid, birth = cls.identity
        try:
            process = psutil.Process(pid)
            if process.create_time() == birth and process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                raise AssertionError('Owned synthetic Core remained alive after close')
        except psutil.NoSuchProcess:
            pass
        finally:
            cls.cleanup_directory()

    @classmethod
    def request(cls, method, path, body=None, *, remote=False, cookie='', csrf='', headers=None):
        supplied = dict(headers or {})
        if body is not None:
            supplied['Content-Type'] = 'application/json'
        if cookie:
            supplied['Cookie'] = cookie
        if csrf:
            supplied['X-CSRF-Token'] = csrf
        raw = json.dumps(body).encode() if body is not None else None
        status, response_headers, response = cls.core.exchange(method, path, raw, supplied,
            timeout=12, remote_origin=PUBLIC_ORIGIN if remote else None)
        try:
            response = json.loads(response)
        except (ValueError, UnicodeError):
            pass
        return status, dict(response_headers), response

    def login(self):
        status, challenge_headers, challenge = self.request('GET', '/auth/challenge', remote=True)
        self.assertEqual(status, 200)
        cookie = challenge_headers['Set-Cookie'].split(';', 1)[0]
        status, login_headers, _ = self.request('POST', '/auth/login',
            {'username': self.username, 'password': self.password}, remote=True,
            cookie=cookie, csrf=challenge['csrf'])
        self.assertEqual(status, 200)
        self.assertIn('; Secure;', login_headers['Set-Cookie'])
        self.assertIn('; HttpOnly;', login_headers['Set-Cookie'])
        session = login_headers['Set-Cookie'].split(';', 1)[0]
        status, _, boot = self.request('GET', '/api/bootstrap', remote=True, cookie=session)
        self.assertEqual(status, 200)
        self.assertTrue(boot['auth']['public_login'])
        self.assertNotEqual(boot['csrf'], self.local_boot['csrf'])
        return session, boot['csrf']

    def test_selected_data_and_mirror_are_independent_of_inherited_settings(self):
        status, _, public = self.request('GET', '/api/public/status')
        self.assertEqual(status, 200)
        self.assertEqual(public['origin'], PUBLIC_ORIGIN)
        self.assertEqual(public['auth_mode'], 'password')
        self.assertTrue(public['password_configured'])
        self.assertEqual(self.core.data_dir, self.folder / 'core')
        self.assertTrue((self.folder / 'core' / 'personal.sqlite3').is_file())
        self.assertTrue((self.folder / 'selected-mirror' / 'Sync' / 'workspace-personal.json').is_file())
        self.assertFalse((self.folder / 'not-selected-mirror').exists())
        self.assertEqual(self.initial_personal['projects'], [])

    def test_anonymous_remote_cannot_read_or_mutate_core(self):
        for route in ('/api/bootstrap', '/api/state', '/api/backup'):
            with self.subTest(route=route):
                self.assertEqual(self.request('GET', route, remote=True)[0], 401)
        self.assertEqual(self.request('POST', '/api/projects',
            {'name': 'Must not persist without login'}, remote=True, csrf=self.local_boot['csrf'])[0], 401)
        self.assertFalse(any(p['name'] == 'Must not persist without login'
            for p in self.request('GET', '/api/state')[2]['projects']))

    def test_session_csrf_is_required_for_real_remote_writes(self):
        cookie, csrf = self.login()
        body = {'name': 'Synthetic authenticated project'}
        for invalid in ('', self.local_boot['csrf']):
            status, _, error = self.request('POST', '/api/projects', body,
                                            remote=True, cookie=cookie, csrf=invalid)
            self.assertEqual(status, 403)
            self.assertEqual(error['code'], 'csrf_expired')
        status, _, project = self.request('POST', '/api/projects', body,
                                         remote=True, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 201)
        self.assertTrue(any(p['id'] == project['id'] for p in self.request('GET', '/api/state')[2]['projects']))

    def test_authenticated_remote_cannot_use_local_configuration(self):
        cookie, csrf = self.login()
        for method, path, body in (
            ('GET', '/api/public/status', None),
            ('GET', '/auth/setup', None),
            ('POST', '/auth/setup', {'password': secrets.token_urlsafe(24)}),
            ('POST', '/api/artifacts/config', {'roots': []}),
        ):
            with self.subTest(route=path, method=method):
                self.assertEqual(self.request(method, path, body, remote=True, cookie=cookie, csrf=csrf)[0], 403)

    def test_core_rejects_cross_site_origin_even_with_valid_session(self):
        cookie, csrf = self.login()
        # Direct loopback transport reproduces what a wrongly configured proxy
        # would send, without DNS, TLS, an actual tunnel, or internet access.
        with self.assertRaises(PermissionError):
            self.core.exchange('GET', '/api/state', remote_origin='https://other.example.invalid')
        import http.client
        connection = http.client.HTTPConnection('127.0.0.1', self.core.port, timeout=12)
        try:
            connection.request('POST', '/api/projects', body=b'{"name":"Must not persist cross-site"}',
                headers={'Host': 'suite.example.invalid', 'Origin': 'https://attacker.example.invalid',
                         'Content-Type': 'application/json', 'Cookie': cookie, 'X-CSRF-Token': csrf})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()
        self.assertFalse(any(p['name'] == 'Must not persist cross-site'
            for p in self.request('GET', '/api/state')[2]['projects']))


if __name__ == '__main__':
    unittest.main()
