"""Trusted original-listener discovery and private Qwen configuration continuity."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request
import math

import psutil


class QwenError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_HOTKEY_KEYS = {'ctrl', 'control', 'leftctrl', 'rightctrl', 'rctrl', 'alt', 'leftalt', 'rightalt', 'ralt', 'shift', 'leftshift', 'rightshift', 'rshift', 'win', '/', '?', 'space', '.', ',', 'a', 's', 'd', 'q', 'r', 'n', 'm', 'v', 'z'}


def normalized_hotkey(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    hotkey = value.casefold().replace(' ', '')
    parts = hotkey.split('+')
    return hotkey if 1 <= len(parts) <= 5 and all(key in _HOTKEY_KEYS for key in parts) else None


def read_config(path):
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256_000:
            return None
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def configuration_update(body, cfg):
    """The original panel supports this subset too; no user supplied executable/path."""
    if not isinstance(body, dict) or set(body) - {'apps', 'trigger', 'poll_interval_sec', 'start_debounce_sec', 'retrigger_cooldown_sec'}:
        raise QwenError('千问设置包含不支持的字段')
    result = copy.deepcopy(cfg)
    if 'apps' in body:
        apps = body['apps']
        if not isinstance(apps, dict) or len(apps) > 32:
            raise QwenError('请选择要监听的会议应用')
        for key, value in apps.items():
            if key not in cfg.get('apps', {}) or not isinstance(value, dict) or set(value) != {'enabled'} or not isinstance(value['enabled'], bool):
                raise QwenError('会议应用设置无效')
            result['apps'][key]['enabled'] = value['enabled']
    if 'trigger' in body:
        value = body['trigger']
        if not isinstance(value, dict) or set(value) != {'hotkey'} or not isinstance(value['hotkey'], str):
            raise QwenError('请输入千问客户端中已设置的录音快捷键')
        hotkey = normalized_hotkey(value['hotkey'])
        if hotkey is None:
            raise QwenError('录音快捷键格式不支持，请与千问客户端保持一致')
        result['trigger'] = {'method': 'hotkey', 'hotkey': hotkey}
    for key, minimum, maximum in [('poll_interval_sec', .5, 30), ('start_debounce_sec', 0, 120), ('retrigger_cooldown_sec', 0, 3600)]:
        if key in body:
            value = body[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                raise QwenError('监听时间设置超出支持范围')
            result[key] = value
    return result


def public_config(cfg, source='suite'):
    def number(key, default):
        value = cfg.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 3600 else default
    apps = []
    source_apps = cfg.get('apps') if isinstance(cfg.get('apps'), dict) else {}
    for key, item in list(source_apps.items())[:32]:
        if not isinstance(key, str) or not isinstance(item, dict):
            continue
        apps.append({'key': key[:80], 'label': str(item.get('label', key))[:80], 'enabled': bool(item.get('enabled'))})
    trigger = cfg.get('trigger') if isinstance(cfg.get('trigger'), dict) else {}
    archive = cfg.get('archive') if isinstance(cfg.get('archive'), dict) else {}
    return {'source': source, 'apps': apps, 'hotkey': normalized_hotkey(trigger.get('hotkey')) or '',
            'require_playback': cfg.get('require_playback', True) is not False,
            'min_playback_sec': number('min_playback_sec', 3),
            'poll_interval_sec': number('poll_interval_sec', 2),
            'start_debounce_sec': number('start_debounce_sec', 3),
            'retrigger_cooldown_sec': number('retrigger_cooldown_sec', 30),
            'archive_enabled': archive.get('enabled', True) is not False}


def safe_snapshot(snapshot, *, source, cfg=None):
    cfg = cfg or {}
    running = snapshot.get('running') is True
    paused = snapshot.get('paused') is True
    recording = snapshot.get('recording') is True
    listening = running and not paused
    snapshot_apps = snapshot.get('apps') if isinstance(snapshot.get('apps'), list) else []
    keys = {a.get('key') for a in snapshot_apps if isinstance(a, dict) and isinstance(a.get('key'), str)}
    def names(field):
        values = snapshot.get(field) if isinstance(snapshot.get(field), list) else []
        return [key for key in values[:32] if isinstance(key, str) and key in keys]
    count = snapshot.get('trigger_count', 0)
    count = count if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 10**9 else 0
    triggered_at = snapshot.get('last_trigger_at', 0)
    triggered_at = triggered_at if isinstance(triggered_at, (int, float)) and not isinstance(triggered_at, bool) and math.isfinite(triggered_at) and 0 <= triggered_at <= 10**11 else 0
    audio_errors = snapshot.get('audio_errors') if isinstance(snapshot.get('audio_errors'), dict) else {}
    detail = '千问正在录音' if recording else '自动监听已暂停' if paused else '自动监听中，等待双向通话' if listening else '自动监听未启动'
    return {'component': 'qwen', 'status': 'running' if listening else 'paused' if paused and running else 'stopped',
            'source': source, 'borrowed': source == 'original', 'running': running, 'listening': listening,
            'paused': paused, 'recording': recording, 'trigger_count': count,
            'last_app': snapshot.get('last_app') if snapshot.get('last_app') in keys | {'manual'} else '',
            'last_trigger_at': triggered_at,
            'active_calls': names('active_calls'), 'pending': names('pending'),
            'hotkey': normalized_hotkey(snapshot.get('hotkey')) or public_config(cfg)['hotkey'],
            'apps': public_config(cfg, source)['apps'] if cfg else [{'key': a.get('key', '')[:80], 'label': str(a.get('label', ''))[:80], 'enabled': a.get('enabled') is True} for a in snapshot_apps[:32] if isinstance(a, dict) and isinstance(a.get('key'), str)],
            'detail': detail, 'error': '千问操作或音频检测未完成，请检查客户端登录、音频设备和快捷键' if snapshot.get('last_error') else '',
            'can_start': not listening, 'can_stop': listening, 'can_trigger': not recording,
            'configuration': public_config(cfg, source),
            'recording_verified': recording,
            'diagnostics': {'capture_error': bool(audio_errors.get(1) or audio_errors.get('1')), 'playback_error': bool(audio_errors.get(0) or audio_errors.get('0')), 'device_scope': 'all_active_endpoints' if 'audio_errors' in snapshot else 'original_engine', 'shortcut_sent_is_not_recording_proof': True},
            'control_note': '暂停监听不会结束千问内已有录音，请在千问中手动停止录音；退出Suite不停止原监听' if source == 'original' else '停止自动化不会结束千问内已有录音，请在千问中手动停止录音'}


class OriginalQwenBridge:
    """No ambient URL or PID file is trusted: socket owner, birth and script are checked."""
    def __init__(self, enabled=False):
        self.enabled = enabled
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self._cache_lock = threading.RLock()
        self._checked_at = 0
        self._cached_reference = None

    def processes(self):
        if not self.enabled:
            return []
        result = []
        for process in psutil.process_iter(['pid', 'name']):
            try:
                name = process.info.get('name') or ''
                if not re.fullmatch(r'(?:python(?:w|\d+(?:\.\d+)?)?(?:\.exe)?|py\.exe)', name, re.I):
                    continue
                argv = process.cmdline() or []
                script = next((x for x in argv[1:] if Path(x).name.casefold() in ('tray_app.py', 'qwen_auto_record.py')), None)
                if not script:
                    continue
                script = Path(script)
                if not script.is_absolute():
                    script = Path(process.cwd()) / script
                if script.is_symlink() or not script.is_file() or not (script.parent / 'engine.py').is_file():
                    continue
                config = script.parent / 'config.json'
                if '--config' in argv:
                    index = argv.index('--config')
                    if index + 1 < len(argv):
                        config = Path(argv[index + 1])
                        if not config.is_absolute():
                            config = Path(process.cwd()) / config
                result.append({'pid': process.pid, 'birth': process.create_time(), 'root': script.parent, 'config': config, 'process': process})
            except (psutil.Error, OSError, ValueError, TypeError):
                continue
        return result

    @staticmethod
    def _alive(reference):
        try:
            process = psutil.Process(reference['pid'])
            return process.is_running() and process.create_time() == reference['birth']
        except psutil.Error:
            return False

    def _request(self, reference, path, method='GET', body=None, timeout=.6):
        if not self._alive(reference) or not reference.get('port'):
            raise QwenError('原千问监听状态尚未就绪，未启动第二个监听')
        url = f'http://127.0.0.1:{reference["port"]}/api/{path}'
        request = urllib.request.Request(url, data=json.dumps(body or {}).encode() if method == 'POST' else None, method=method, headers={'Content-Type': 'application/json'})
        with self._opener.open(request, timeout=timeout) as response:
            if not response.headers.get('Server', '').startswith('QwenAutoRecord/') or response.geturl() != url:
                raise QwenError('原千问监听身份未通过校验')
            raw = response.read(256_001)
        if len(raw) > 256_000:
            raise QwenError('原千问监听响应过大')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise QwenError('原千问监听状态格式无效')
        return value

    def discover(self, fresh=False):
        if not self.enabled:
            return None
        with self._cache_lock:
            if not fresh and time.monotonic() - self._checked_at < .8:
                if self._cached_reference is None or self._alive(self._cached_reference):
                    return self._cached_reference
            reference = self._discover()
            self._cached_reference = reference
            self._checked_at = time.monotonic()
            return reference

    def _discover(self):
        references = self.processes()
        for reference in references:
            try:
                ports = sorted({connection.laddr.port for connection in reference['process'].net_connections(kind='tcp') if connection.status == psutil.CONN_LISTEN and connection.laddr.ip == '127.0.0.1'})
                cfg = read_config(reference['config']) or {}
                configured = cfg.get('panel', {}).get('port', 8765)
                allowed = set(range(8765, 8777))
                if isinstance(configured, int) and not isinstance(configured, bool) and 1 <= configured <= 65535:
                    allowed.update(range(configured, min(configured + 12, 65536)))
                for port in ports:
                    if port not in allowed:
                        continue
                    candidate = {**reference, 'port': port}
                    snapshot = self._request(candidate, 'status')
                    if not all(isinstance(snapshot.get(key), bool) for key in ('running', 'paused', 'recording')) or not isinstance(snapshot.get('apps'), list) or not isinstance(snapshot.get('trigger_count'), int) or isinstance(snapshot.get('trigger_count'), bool) or len(snapshot['apps']) > 32 or any(not isinstance(app, dict) or not isinstance(app.get('key'), str) for app in snapshot['apps']):
                        continue
                    candidate['snapshot'] = snapshot
                    candidate['cfg'] = cfg
                    return candidate
            except (OSError, ValueError, psutil.Error):
                continue
        return references[0] if references else None

    def status(self, reference):
        if not reference.get('snapshot'):
            return {'component': 'qwen', 'status': 'starting', 'source': 'original', 'borrowed': True, 'running': False, 'listening': False, 'recording': False, 'paused': False, 'can_start': False, 'can_stop': False, 'can_trigger': False, 'detail': '检测到原千问后台进程，等待监听状态；未启动第二个监听', 'error': '', 'archive': {'enabled': False, 'count': 0}, 'control_note': '请使用原千问托盘检查状态，退出Suite不会关闭原监听'}
        result = safe_snapshot(reference['snapshot'], source='original', cfg=reference.get('cfg'))
        result['panel_url'] = f'http://127.0.0.1:{reference["port"]}/'
        result['archive'] = {'enabled': result['configuration']['archive_enabled'], 'count': None, 'count_known': False, 'source': 'original'}
        return result

    def action(self, reference, action, body=None):
        if not reference.get('snapshot'):
            raise QwenError('原千问后台进程已运行但面板未就绪，请检查原托盘；未启动第二个监听')
        if action == 'trigger' and reference['snapshot'].get('recording'):
            return {**self.status(reference), 'trigger_requested': False, 'already_recording': True}
        if action in ('start', 'stop'):
            if not reference['snapshot'].get('running'):
                raise QwenError('原千问监听线程已停止，请使用原托盘重新启动')
            self._request(reference, 'pause', 'POST', {'paused': action == 'stop'}, timeout=5)
        elif action == 'trigger':
            self._request(reference, 'trigger', 'POST', timeout=45)
        elif action == 'config':
            configuration_update(body, reference.get('cfg') or {})
            self._request(reference, 'config', 'POST', body, timeout=5)
        else:
            raise QwenError('不支持的千问操作')
        reference = {**reference, 'snapshot': self._request(reference, 'status'), 'cfg': read_config(reference['config']) or {}}
        result = self.status(reference)
        with self._cache_lock:
            self._cached_reference = reference
            self._checked_at = time.monotonic()
        if action == 'trigger':
            result.update(trigger_requested=True, recording_verified=result['recording'])
            if result['error']:
                raise QwenError(result['error'])
        return result


def prepare_config(engine, data, original=None):
    """Preserve edited settings; replace only the old wrapper's exact generated defaults."""
    data = Path(data)
    path = data / 'config.json'
    marker = data / 'configuration-origin.json'
    existing = read_config(path)
    legacy = copy.deepcopy(engine.DEFAULT_CONFIG)
    legacy['archive'] = {**legacy.get('archive', {}), 'enabled': True, 'dir': str(data / 'recordings')}
    eligible = existing is None or existing == legacy and not marker.exists()
    source = read_config(original['config']) if original and eligible else None
    if source:
        # Only the known engine schema is imported; arbitrary credential fields are ignored.
        cfg = copy.deepcopy(engine.DEFAULT_CONFIG)
        def known_value(default, value):
            if isinstance(default, dict):
                return {key: known_value(item, value.get(key, item)) for key, item in default.items()} if isinstance(value, dict) else copy.deepcopy(default)
            if isinstance(default, bool):
                return value if isinstance(value, bool) else default
            if isinstance(default, (int, float)):
                return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else default
            if isinstance(default, str):
                return value if isinstance(value, str) and len(value) <= 4000 else default
            if isinstance(default, list):
                return list(value) if isinstance(value, list) and len(value) <= 128 and all(isinstance(item, str) and len(item) <= 512 for item in value) else copy.deepcopy(default)
            return copy.deepcopy(default)
        for key in cfg:
            if key in source and key != 'apps':
                cfg[key] = known_value(cfg[key], source[key])
        source_apps = source.get('apps') if isinstance(source.get('apps'), dict) else {}
        app_schema = {'enabled': False, 'label': '', 'processes': []}
        for key, value in list(source_apps.items())[:32]:
            if isinstance(key, str) and len(key) <= 80 and isinstance(value, dict):
                cfg['apps'][key] = known_value(cfg['apps'].get(key, app_schema), value)
        if cfg.get('archive', {}).get('dir', '') == '':
            cfg['archive']['dir'] = str(Path(original['root']) / '录音')
        engine.save_config(cfg, path)
        marker.write_text(json.dumps({'source': 'original', 'imported': True}), encoding='utf-8')
        return cfg, {'source': 'original', 'imported': True}
    if existing is not None:
        cfg = engine.load_config(path)
        return cfg, read_config(marker) or {'source': 'suite', 'imported': False}
    cfg = copy.deepcopy(engine.DEFAULT_CONFIG)
    cfg['archive']['dir'] = str(data / 'recordings')
    engine.save_config(cfg, path)
    return cfg, {'source': 'defaults', 'imported': False}


class ListenerLease:
    """Process-held byte lock; thread exit cannot abandon it like a Win32 mutex."""
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None
        self._lock = threading.RLock()

    def acquire(self):
        with self._lock:
            if self.handle:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink():
                raise QwenError('千问监听锁路径无效')
            handle = self.path.open('a+b')
            try:
                if self.path.stat().st_size == 0:
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                raise QwenError('已有Suite千问监听运行，未启动第二个实例') from None
            self.handle = handle

    def release(self):
        with self._lock:
            if self.handle:
                self.handle.close()
                self.handle = None
