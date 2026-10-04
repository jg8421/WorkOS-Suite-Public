"""Synthetic meeting-state transitions; never open audio devices or send keys."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


def load_engine():
    com = types.ModuleType('comtypes')
    com.CLSCTX_ALL = 23
    audio = types.ModuleType('pycaw.pycaw')
    audio.AudioUtilities = object
    audio.IAudioSessionManager2 = types.SimpleNamespace(_iid_='synthetic-session-manager')
    audio.IAudioSessionControl2 = object
    package = types.ModuleType('pycaw')
    with patch.dict(sys.modules, {'comtypes': com, 'pycaw': package, 'pycaw.pycaw': audio}):
        source = Path(__file__).resolve().parents[1] / 'components/qwen/engine.py'
        spec = importlib.util.spec_from_file_location('synthetic_qwen_engine', source)
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
    return engine


class MeetingDetectionTests(unittest.TestCase):
    def setUp(self):
        self.engine = load_engine()
        self.cfg = copy.deepcopy(self.engine.DEFAULT_CONFIG)
        self.cfg['apps'] = {'meeting': {'enabled': True, 'label': 'Synthetic meeting',
            'processes': ['meeting.exe', 'meeting-audio.exe']}}
        self.cfg['archive']['enabled'] = False
        self.cfg['exclude_processes'] = ['typing.exe']
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.engine.LOG_PATH = folder / 'test.log'
        self.engine.STATE_PATH = folder / 'test-state.json'
        self.engine.CONFIG_PATH = folder / 'test-config.json'
        self.engine.CONFIG_PATH.write_text(json.dumps(self.cfg), encoding='utf-8')

    def detect(self, capture, render, since, now=110):
        return self.engine.detect_active_calls(self.cfg, capture, render, since, now)

    def test_previous_playback_cannot_qualify_a_silent_microphone(self):
        self.assertEqual(self.detect({'meeting.exe': 'Active'},
            {'meeting.exe': 'Inactive'}, {'meeting.exe': 100}), [])

    def test_microphone_and_playback_can_belong_to_same_apps_different_processes(self):
        self.assertEqual(self.detect({'meeting.exe': 'Active'},
            {'meeting-audio.exe': 'Active'}, {'meeting-audio.exe': 100}), ['meeting'])

    def test_short_notification_and_voice_typing_are_not_meetings(self):
        self.assertEqual(self.detect({'meeting.exe': 'Active'},
            {'meeting.exe': 'Active'}, {'meeting.exe': 109}), [])
        self.assertEqual(self.detect({'typing.exe': 'Active'},
            {'meeting.exe': 'Active'}, {'meeting.exe': 100}), [])

    def tick(self, watcher, capture, render, now):
        with patch.object(self.engine, 'capture_sessions', return_value=capture), \
             patch.object(self.engine, 'render_sessions', return_value=render), \
             patch.object(self.engine, 'processes_running', return_value={}), \
             patch.object(self.engine.time, 'time', return_value=now):
            watcher._tick(set())

    def test_disappeared_playback_session_resets_its_duration(self):
        watcher = self.engine.Watcher()
        self.tick(watcher, {'meeting.exe': 'Active'}, {'meeting.exe': 'Active'}, 1000)
        self.tick(watcher, {'meeting.exe': 'Active'}, {}, 1008)
        self.assertEqual(watcher.snapshot()['active_calls'], [])
        self.assertEqual(watcher._render_since, {})

    def test_debounced_meeting_triggers_once_and_existing_recording_is_protected(self):
        watcher = self.engine.Watcher()
        with patch.object(self.engine, 'trigger_with_launch') as trigger, \
             patch.object(watcher, '_verify_soon') as verify:
            for now in (1000, 1003, 1006, 1008):
                self.tick(watcher, {'meeting.exe': 'Active'}, {'meeting.exe': 'Active'}, now)
            trigger.assert_called_once()
            verify.assert_called_once_with('meeting')
            self.assertEqual(watcher.trigger_count, 1)
        watcher = self.engine.Watcher()
        watcher.last_trigger_at = 0
        with patch.object(self.engine, 'trigger_with_launch') as trigger:
            for now in (2000, 2003, 2006):
                self.tick(watcher, {'meeting.exe': 'Active', 'qianwen.exe': 'Active'},
                    {'meeting.exe': 'Active'}, now)
            trigger.assert_not_called()

    def test_diagnostic_sample_reports_real_playback_without_inventing_duration(self):
        capture = {'meeting.exe': 'Active'}
        render = {'meeting-audio.exe': 'Active'}
        with patch.object(self.engine, 'capture_sessions', return_value=capture), \
             patch.object(self.engine, 'render_sessions', return_value=render), \
             patch.object(self.engine, 'processes_running', return_value={'meeting.exe': 123}), \
             patch.object(self.engine.time, 'time', return_value=110):
            result = self.engine.detect_once(self.cfg)
        self.assertEqual(result['render'], render)
        self.assertEqual(result['candidate_calls'], ['meeting'])
        self.assertEqual(result['active_calls'], [])
        self.assertTrue(result['observation_only'])

    def test_nondefault_meeting_headset_is_observed_for_both_audio_directions(self):
        def device(state):
            ctl = Mock()
            ctl.QueryInterface.return_value = ctl
            ctl.GetProcessId.return_value = 123
            ctl.GetState.return_value = state
            sessions = Mock()
            sessions.GetCount.return_value = 1
            sessions.GetSession.return_value = ctl
            interface = Mock()
            interface.QueryInterface.return_value.GetSessionEnumerator.return_value = sessions
            endpoint = Mock()
            endpoint.Activate.return_value = interface
            return endpoint
        endpoints = Mock()
        endpoints.GetCount.return_value = 2
        endpoints.Item.side_effect = lambda index: [device(0), device(1)][index]
        enumerator = Mock()
        enumerator.EnumAudioEndpoints.return_value = endpoints
        utilities = Mock()
        utilities.GetDeviceEnumerator.return_value = enumerator
        with patch.object(self.engine, 'AudioUtilities', utilities), \
             patch.object(self.engine, 'ensure_com'), \
             patch.object(self.engine.psutil, 'Process', return_value=types.SimpleNamespace(name=lambda: 'meeting.exe')):
            self.assertEqual(self.engine.capture_sessions(), {'meeting.exe': 'Active'})
            self.assertEqual(self.engine.render_sessions(), {'meeting.exe': 'Active'})
        self.assertEqual(enumerator.EnumAudioEndpoints.call_args_list[0].args, (1, 1))
        self.assertEqual(enumerator.EnumAudioEndpoints.call_args_list[1].args, (0, 1))

    def test_missing_client_is_not_reported_as_a_successful_trigger(self):
        watcher = self.engine.Watcher()
        with patch.object(self.engine, 'ensure_qianwen', return_value=False), \
             patch.object(self.engine, 'trigger') as trigger:
            watcher.trigger_now()
        trigger.assert_not_called()
        self.assertEqual(watcher.trigger_count, 0)
        self.assertTrue(watcher.last_error)

    def test_paused_or_stopped_listener_cancels_delayed_hotkey_retries(self):
        watcher = self.engine.Watcher()
        for stopped in (False, True):
            watcher._paused.clear()
            watcher._stop.clear()
            (watcher._stop if stopped else watcher._paused).set()
            with patch.object(self.engine.threading, 'Thread') as thread, \
                 patch.object(self.engine.time, 'sleep'), \
                 patch.object(self.engine, 'capture_sessions') as capture, \
                 patch.object(self.engine, 'trigger') as trigger:
                watcher._verify_soon('meeting')
                thread.call_args.kwargs['target']()
                capture.assert_not_called()
                trigger.assert_not_called()


if __name__ == '__main__':
    unittest.main()
