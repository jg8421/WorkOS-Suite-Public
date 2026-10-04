"""Explicit password-protected Suite access; no tunnel or account discovery."""
from __future__ import annotations
import html
import importlib.util
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit


APP = Path(__file__).resolve().parents[1]
# Reuse the bundled account/session implementation without starting its server.
_spec = importlib.util.spec_from_file_location('suite._password_auth', APP/'components'/'workos'/'workos'/'password_auth.py')
_passwords = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_passwords)
LoginChallengeExpired = _passwords.LoginChallengeExpired
TooManyLogins = _passwords.TooManyLogins


class LoginRequired(PermissionError):
    pass


class CsrfExpired(PermissionError):
    pass


class RemoteAccess:
    def __init__(self, origin, authentication_dir):
        if not isinstance(origin, str):
            raise ValueError('公网配置需要固定 HTTPS 地址')
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError('公网配置地址无效') from None
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?', parsed.netloc)
                or port is not None and not 1 <= port <= 65535):
            raise ValueError('公网配置只接受固定 HTTPS 网站地址')
        self.origin = 'https://' + parsed.netloc.lower()
        self.host = parsed.netloc.lower()
        if not isinstance(authentication_dir, (str, Path)) or not Path(authentication_dir).is_absolute():
            raise ValueError('账户文件夹必须是明确的本机绝对地址')
        self.authentication_dir = Path(authentication_dir).resolve()
        self._lock = threading.RLock()
        self._stamp = None
        self._auth = None
        if not self.auth.configured:
            raise ValueError('公网账户尚未配置；请先在本机配置账户')

    @classmethod
    def from_config(cls, path, expected_authentication_dir):
        if not Path(path).is_absolute():
            raise ValueError('公网配置文件必须是明确的本机绝对地址')
        try:
            raw = Path(path).read_bytes()
            if len(raw) > 8192:
                raise ValueError()
            config = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(config, dict) or set(config) != {'origin', 'authentication_dir'}:
                raise ValueError()
        except (OSError, UnicodeError, ValueError):
            raise ValueError('公网配置未能读取，或包含未支持的字段') from None
        access = cls(config['origin'], config['authentication_dir'])
        if access.authentication_dir != Path(expected_authentication_dir).resolve():
            raise ValueError('公网账户必须与套件研究后台使用同一账户文件夹')
        return access

    @property
    def auth(self):
        # A local password change invalidates both the gateway and Core sessions.
        # Retain challenges/throttling while the account file is unchanged.
        with self._lock:
            try:
                info = (self.authentication_dir/'password-account.json').stat()
                stamp = (info.st_mtime_ns, info.st_size)
            except OSError:
                stamp = None
            if self._auth is None or stamp != self._stamp:
                self._auth = _passwords.PasswordAuth(self.authentication_dir)
                self._stamp = stamp
            return self._auth

    def session(self, cookie):
        result = self.auth.get_session(cookie)
        if not result:
            raise LoginRequired('请先登录 WorkOS Suite')
        return result

    @staticmethod
    def check_capability(method, path):
        # Exact paths only: encoded aliases must not evade a host-only fence.
        if '%' in path or '\\' in path or '//' in path or any(part in ('.', '..') for part in path.split('/')):
            raise PermissionError('此地址不支持远程访问')
        if path.startswith('/api/suite/'):
            read = {'/api/suite/bootstrap', '/api/suite/components', '/api/suite/ideas', '/api/suite/roots',
                    '/api/suite/native-tools', '/api/suite/files', '/api/suite/files/search',
                    '/api/suite/file-preview', '/api/suite/file', '/api/suite/recordings'}
            if method in ('GET', 'HEAD') and (path in read or re.fullmatch(r'/api/suite/memory/(status|settings|recent|search)', path)
                    or re.fullmatch(r'/api/suite/qwen/(status|config|diagnostics)', path)):
                return
            if method == 'POST' and (path in {'/api/suite/ideas', '/api/suite/files/operation', '/api/suite/files/import'}
                    or re.fullmatch(r'/api/suite/ideas/[a-f0-9]{32}/share', path)
                    or re.fullmatch(r'/api/suite/memory/(add|forget)', path)):
                return
            if method == 'DELETE' and re.fullmatch(r'/api/suite/ideas/[a-f0-9]{32}', path):
                return
            raise PermissionError('此操作只能在运行 Suite 的本机使用')
        if path.startswith('/auth/'):
            allowed = {'GET': {'/auth/login', '/auth/challenge', '/auth/ui.js'},
                       'HEAD': {'/auth/login', '/auth/challenge', '/auth/ui.js'},
                       'POST': {'/auth/login', '/auth/logout'}}
            if path not in allowed.get(method, set()):
                raise PermissionError('账户初始化与更改只能在本机进行')
        if path.startswith('/api/'):
            if (path in {'/api/shutdown', '/api/public/status', '/api/memory/scan', '/api/memory/import', '/api/artifacts/config'}
                    or method not in ('GET', 'HEAD') and (path in {'/api/ai/settings', '/api/artifacts/config'}
                    or re.fullmatch(r'/api/models/custom(?:/[^/]+)?', path)
                    or re.fullmatch(r'/api/projects/[^/]+/artifacts/bind', path))):
                raise PermissionError('此配置或主机操作只能在本机使用')

    def auth_get(self, path, peer):
        if path == '/auth/ui.js':
            return (APP/'components'/'workos'/'web'/'auth.js').read_bytes(), 'application/javascript; charset=utf-8', ()
        auth = self.auth
        nonce = auth.issue_challenge(peer)
        headers = [('Set-Cookie', auth.challenge_cookie(nonce))]
        if path == '/auth/challenge':
            return {'csrf': nonce}, 'application/json; charset=utf-8', headers
        values = {'__USERNAME__': '', '__USERNAME_READONLY__': '', '__MODE__': 'login', '__PUBLIC_URL__': self.origin,
                  '__NONCE__': nonce, '__HEADING__': '登录 WorkOS Suite', '__EXPLANATION__': '使用这台机器已配置的账户进入统一工作台。',
                  '__MIN_LENGTH__': '', '__AUTOCOMPLETE__': 'current-password', '__REMEMBER_HIDDEN__': '',
                  '__BUTTON__': '登录', '__FOOTNOTE__': '登录会话受 HTTPS 和 HttpOnly Cookie 保护。'}
        page = (APP/'components'/'workos'/'web'/'login.html').read_text(encoding='utf-8')
        for key, value in values.items():
            page = page.replace(key, html.escape(value, quote=True))
        return page, 'text/html; charset=utf-8', headers

    def login(self, body, headers, peer):
        auth = self.auth
        token, seconds = auth.login(body.get('username'), body.get('password'), peer,
                                    headers.get('X-CSRF-Token', ''),
                                    _passwords.cookie_value(headers.get('Cookie', ''), _passwords.CHALLENGE_COOKIE),
                                    body.get('remember') is True)
        return {'ok': True}, [('Set-Cookie', auth.session_cookie(token, seconds))]

    def logout(self, cookie):
        auth = self.auth
        auth.logout(cookie)
        return {'ok': True}, [('Set-Cookie', auth.session_cookie('', 0))]
