"""卡死检测回归：状态确认、跨任务旧结果和后台日志不能触发错误恢复。"""

import logging
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

from module.device.session import DeviceSession
from module.handler.emulator_crash_guard import CrashGuard
from module.handler.emulator_recovery import EmulatorRecovery
from module.handler.task_failure_protection import Watchdog, _LogHeartbeatHandler


def make_guard():
    config = SimpleNamespace(EmulatorManagement_CrashProtection=True,
                             EmulatorManagement_CrashDryRun=False)
    alas = SimpleNamespace(config=config, config_name=None, stop_event=None, device=DeviceSession())
    alas.recovery = EmulatorRecovery(alas)
    guard = CrashGuard(alas)
    guard.activate('Main')
    return guard


class TestCrashGuard(unittest.TestCase):
    def test_failure_count_and_duration_are_both_required(self):
        guard = make_guard()
        guard._fail_count = 2
        guard._first_fail_time = time.monotonic() - 120
        self.assertFalse(guard._should_trigger()[0])
        guard._fail_count = 3
        guard._first_fail_time = time.monotonic()
        self.assertFalse(guard._should_trigger()[0])

    def test_unknown_instance_or_adb_server_never_triggers(self):
        guard = make_guard()
        guard._fail_count = 3
        guard._first_fail_time = time.monotonic() - 120
        for status, alive in [('unknown', True), ('unauthorized', True), ('device', None)]:
            with self.subTest(status=status, alive=alive), \
                    patch.object(CrashGuard, '_probe_device_status', autospec=True, return_value=status), \
                    patch.object(CrashGuard, '_probe_emulator_process', autospec=True, return_value=(alive, '实例')):
                self.assertFalse(guard._should_trigger()[0])

    def test_confirmed_failure_requests_task_exit_without_starting_emulator(self):
        guard = make_guard()
        guard._fail_count = 3
        guard._first_fail_time = time.monotonic() - 120
        with patch.object(CrashGuard, 'probe_heartbeat', autospec=True, return_value=False), \
                patch.object(CrashGuard, '_probe_device_status', autospec=True, return_value='device'), \
                patch.object(CrashGuard, '_probe_emulator_process', autospec=True, return_value=(True, '实例')), \
                patch.object(EmulatorRecovery, 'restart', autospec=True) as restart:
            guard._tick()
        self.assertIsNotNone(guard.alas.recovery.pending)
        self.assertIn('_session_invalid', guard.alas.device.__dict__)
        restart.assert_not_called()

    def test_late_probe_from_previous_task_is_discarded(self):
        guard = make_guard()
        def probe(_):
            guard.activate('Research')
            return False
        with patch.object(CrashGuard, 'probe_heartbeat', autospec=True, side_effect=probe):
            guard._tick()
        self.assertEqual(guard._fail_count, 0)
        self.assertIsNone(guard.alas.recovery.pending)

    def test_disable_during_probe_prevents_request(self):
        guard = make_guard()
        guard._fail_count = 3
        guard._first_fail_time = time.monotonic() - 120
        def confirmed(_):
            guard.alas.config.EmulatorManagement_CrashProtection = False
            return True, '已确认'
        with patch.object(CrashGuard, 'probe_heartbeat', autospec=True, return_value=False), \
                patch.object(CrashGuard, '_should_trigger', autospec=True, side_effect=confirmed):
            guard._tick()
        self.assertIsNone(guard.alas.recovery.pending)

    def test_stop_keeps_reference_to_live_thread(self):
        guard = make_guard()
        thread = create_autospec(threading.Thread, instance=True)
        thread.is_alive.return_value = True
        guard._thread = thread
        self.assertFalse(guard.stop())
        self.assertIs(guard._thread, thread)
        self.assertFalse(guard.start())
        self.assertTrue(guard._stop_event.is_set())

    def test_successful_probe_resets_failure_streak(self):
        guard = make_guard()
        guard._fail_count = 4
        with patch.object(CrashGuard, 'probe_heartbeat', autospec=True, return_value=True):
            guard._tick()
        self.assertEqual(guard._fail_count, 0)


class TestWatchdogSafety(unittest.TestCase):
    def test_background_logs_cannot_refresh_main_thread_heartbeat(self):
        handler = _LogHeartbeatHandler()
        before = handler.last_log_time
        record = logging.LogRecord('test', logging.INFO, '', 0, '后台日志', (), None)
        record.thread = -1
        handler.emit(record)
        self.assertEqual(before, handler.last_log_time)

    def test_timeout_does_not_acquire_or_release_recovery_lock(self):
        alas = make_guard().alas
        alas._emulator_lock = threading.Lock()
        watchdog = Watchdog(alas)
        watchdog.activate('Main')
        with alas._emulator_lock:
            watchdog._recover(300, reason='log_timeout')
            watchdog._recover(7200, reason='task_timeout')
            self.assertTrue(alas._emulator_lock.locked())
        self.assertIsNone(alas.recovery.pending)
