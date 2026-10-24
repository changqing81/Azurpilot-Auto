"""恢复事务故障注入：不连接 ADB，不启停实际模拟器。"""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

from PIL import Image

from module.device.session import DeviceSession, DeviceSessionExpired, RecoveryStopped, session_guard
from module.exception import RecoveryFailed
from module.exception import EmulatorOpBusy
from module.handler.emulator_health import AdbProbe
from module.handler.emulator_recovery import EmulatorRecovery
from module.handler.emulator_recovery_state import InstanceLease, RecoveryBlocked
from module.handler.emulator_target import RecoveryTarget


class TestPersistentBudget(unittest.TestCase):
    def test_failed_attempt_is_retained_across_new_coordinator(self):
        with tempfile.TemporaryDirectory() as root:
            for now in (1000, 1700, 2400):
                with InstanceLease(('MuMuPlayer12', '/mumu', '0'), root) as lease:
                    lease.reserve(now=now)
            with InstanceLease(('MuMuPlayer12', '/mumu', '0'), root) as lease:
                with self.assertRaises(RecoveryBlocked):
                    lease.reserve(now=3100)

    def test_cooldown_and_clock_rollback_do_not_restore_quota(self):
        with tempfile.TemporaryDirectory() as root:
            with InstanceLease(('模拟器', '/exe', '0'), root) as lease:
                lease.reserve(now=1000)
                for now in (900, 1001):
                    with self.assertRaises(RecoveryBlocked):
                        lease.reserve(now=now)

    def test_other_instance_has_independent_budget(self):
        with tempfile.TemporaryDirectory() as root:
            with InstanceLease(('模拟器', '/exe', '0'), root) as first:
                first.reserve(now=1000)
                with InstanceLease(('模拟器', '/exe', '1'), root) as second:
                    second.reserve(now=1000)

    def test_corrupt_record_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            with InstanceLease(('模拟器', '/exe', '0'), root) as lease:
                lease.path.write_text('损坏', encoding='utf-8')
                with self.assertRaises(RecoveryBlocked):
                    lease.reserve()

    def test_second_process_cannot_acquire_same_instance(self):
        with tempfile.TemporaryDirectory() as root:
            identity = ('模拟器', '/exe', '0')
            with InstanceLease(identity, root):
                code = (
                    'import sys; from module.handler.emulator_recovery_state import InstanceLease; '
                    'from module.exception import EmulatorOpBusy\n'
                    'try:\n with InstanceLease(tuple(sys.argv[2:]), sys.argv[1]): pass\n'
                    'except EmulatorOpBusy: sys.exit(17)\n'
                )
                result = subprocess.run([sys.executable, '-B', '-c', code, root, *identity],
                                        capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 17, result.stderr.decode(errors='replace'))


class TestAdbProbe(unittest.TestCase):
    def test_auto_serial_never_runs_global_command(self):
        with patch('module.handler.emulator_health.subprocess.run', autospec=True) as run:
            self.assertFalse(AdbProbe('adb', 'auto').heartbeat())
        run.assert_not_called()

    def test_heartbeat_requires_exact_response(self):
        with patch('module.handler.emulator_health.subprocess.run', autospec=True,
                   return_value=SimpleNamespace(returncode=0, stdout=b'error pong')):
            self.assertFalse(AdbProbe('adb', '127.0.0.1:1').heartbeat())

    def test_ready_requires_boot_package_and_valid_frame_without_device(self):
        data = io.BytesIO()
        Image.new('RGB', (1280, 720), 'blue').save(data, format='PNG')
        responses = [(True, 'device'), (True, '1'),
                     (True, 'package:com.bilibili.azurlane'), (True, data.getvalue())]
        with patch.object(AdbProbe, 'run', autospec=True, side_effect=responses) as run:
            self.assertTrue(AdbProbe('adb', '127.0.0.1:1').ready(threading.Event(), timeout=5))
        self.assertEqual(len(run.call_args_list), 4)
        self.assertTrue(all(0 < call.args[2] <= 5 for call in run.call_args_list))

    def test_wrong_package_or_incomplete_boot_never_reports_ready(self):
        for responses in ([(True, 'device'), (True, '0')],
                          [(True, 'device'), (True, '1'), (True, 'package:other')]):
            event = create_autospec(threading.Event, instance=True)
            event.is_set.return_value = False
            event.wait.return_value = True
            with patch.object(AdbProbe, 'run', autospec=True, side_effect=responses):
                self.assertFalse(AdbProbe('adb', '127.0.0.1:1').ready(event, timeout=5))

    def test_cancelled_ready_does_not_execute_adb(self):
        event = threading.Event()
        event.set()
        with patch.object(AdbProbe, 'run', autospec=True) as run:
            self.assertFalse(AdbProbe('adb', '127.0.0.1:1').ready(event))
        run.assert_not_called()


class TestDeviceSession(unittest.TestCase):
    def test_old_device_never_accepts_operations_after_invalidation(self):
        class Device(DeviceSession):
            @session_guard
            def click(self):
                raise AssertionError('旧会话不应执行点击')
        device = Device()
        device.invalidate_session('准备恢复')
        with self.assertRaises(DeviceSessionExpired):
            device.click()
        DeviceSession().check_session()

    def test_late_screenshot_is_rejected(self):
        class Device(DeviceSession):
            @session_guard
            def screenshot(self):
                self.invalidate_session('截图返回前已失效')
                return '旧截图'
        with self.assertRaises(DeviceSessionExpired):
            Device().screenshot()

    def test_socket_is_shutdown_and_closed_without_creating_new_client(self):
        import socket
        device = DeviceSession()
        stream = create_autospec(socket.socket, instance=True)
        device._recovery_adb_stream = stream
        device.invalidate_session('停止')
        stream.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        stream.close.assert_called_once()
        self.assertNotIn('adb', device.__dict__)


class TestRecoveryTransaction(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = SimpleNamespace(EmulatorManagement_CrashProtection=True,
                                      EmulatorManagement_CrashDryRun=False, Error_HandleError=True,
                                      Emulator_PackageName='com.bilibili.azurlane')
        self.alas = SimpleNamespace(config=self.config, config_name=None, stop_event=None,
                                    device=DeviceSession(), _emulator_lock=threading.Lock())
        self.recovery = EmulatorRecovery(self.alas)
        self.alas.recovery = self.recovery
        self.platform_patch = patch('module.device.platform.Platform', autospec=True)
        self.platform = self.platform_patch.start().return_value
        self.addCleanup(self.platform_patch.stop)
        self.platform.serial = '127.0.0.1:16384'
        self.platform.adb_binary = 'adb'
        self.target_patch = patch('module.handler.emulator_target.RecoveryTarget', autospec=True)
        self.target = self.target_patch.start().return_value
        self.addCleanup(self.target_patch.stop)
        self.target.identity = ('MuMuPlayer12', '/mumu', '0')
        self.target.wait_stopped.return_value = True
        self.lease_patch = patch('module.handler.emulator_recovery.InstanceLease',
                                 side_effect=lambda identity: InstanceLease(identity, self.temp.name))
        self.lease_patch.start()
        self.addCleanup(self.lease_patch.stop)
        self.ready_patch = patch.object(AdbProbe, 'ready', autospec=True, return_value=True)
        self.ready = self.ready_patch.start()
        self.addCleanup(self.ready_patch.stop)

    def test_success_closes_old_session_before_stop_and_discards_device(self):
        old = self.alas.device
        def stopped(checkpoint):
            checkpoint()
            with self.assertRaises(DeviceSessionExpired):
                old.check_session()
        self.target.stop.side_effect = stopped
        self.assertTrue(self.recovery.restart())
        self.assertNotIn('device', self.alas.__dict__)
        self.target.start.assert_called_once()
        self.assertFalse(self.alas._emulator_lock.locked())

    def test_failed_boot_consumes_attempt_and_can_be_retried(self):
        """启动失败是设备侧问题：计一次额度后抛可重试异常，不终止脚本。"""
        self.ready.return_value = False
        with self.assertRaises(RecoveryFailed):
            self.recovery.restart()
        files = list(Path(self.temp.name).glob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(len(json.loads(files[0].read_text())), 1)
        self.assertTrue(self.recovery.cancel.is_set())

    def test_stop_not_confirmed_never_launches(self):
        self.target.wait_stopped.return_value = False
        with self.assertRaises(RecoveryFailed):
            self.recovery.restart()
        self.target.start.assert_not_called()

    def test_disable_after_stop_prevents_start(self):
        def stopped(checkpoint):
            self.config.EmulatorManagement_CrashProtection = False
        self.target.stop.side_effect = stopped
        with self.assertRaises(RecoveryStopped):
            self.recovery.restart('crash')
        self.target.start.assert_not_called()

    def test_timeout_keeps_lock_with_actual_worker_and_prevents_late_launch(self):
        entered = threading.Event()
        release = threading.Event()
        def blocked(checkpoint):
            entered.set()
            release.wait(3)
            checkpoint()
        self.target.stop.side_effect = blocked
        original_join = threading.Thread.join

        def join_after_stop(worker, timeout=None):
            # 先确认执行者已进入待测阻塞点，再测超时取消与锁归属；
            # 全量测试加载 OCR 后调度可能变慢，不能把启动延迟当成恢复超时。
            if worker is self.recovery.worker:
                self.assertTrue(entered.wait(5), '恢复执行者未进入停止操作')
            return original_join(worker, timeout=timeout)

        try:
            with patch('module.handler.emulator_recovery.RECOVERY_TIMEOUT', 0.1), \
                    patch.object(threading.Thread, 'join', autospec=True, side_effect=join_after_stop):
                with self.assertRaises(RecoveryFailed):
                    self.recovery.restart()
            self.assertTrue(entered.is_set())
            self.assertTrue(self.alas._emulator_lock.locked())
        finally:
            release.set()
            self.recovery.worker.join(3)
        self.target.start.assert_not_called()
        self.assertFalse(self.alas._emulator_lock.locked())

    def test_busy_lock_is_not_released_by_worker_that_did_not_acquire_it(self):
        with self.alas._emulator_lock:
            with self.assertRaises(RecoveryFailed):
                self.recovery.restart()
            self.assertTrue(self.alas._emulator_lock.locked())
        self.target.stop.assert_not_called()

    def test_dry_run_never_stops_or_records_attempt(self):
        self.config.EmulatorManagement_CrashDryRun = True
        self.assertFalse(self.recovery.restart())
        self.target.stop.assert_not_called()
        self.assertEqual(list(Path(self.temp.name).glob('*.json')), [])

    def test_success_sends_restart_notification(self):
        """恢复成功必须推送：用户要能感知到发生过一次实例级重启。"""
        with patch('module.handler.emulator_recovery.handle_notify', autospec=True) as notify:
            self.assertTrue(self.recovery.restart())
        notify.assert_called_once()
        self.assertIn('模拟器已重启', notify.call_args.kwargs['title'])

    def test_failure_sends_warning_notification(self):
        """恢复失败必须推送：脚本改为退避重试后，用户只能靠推送感知故障。"""
        self.ready.return_value = False
        with patch('module.handler.emulator_recovery.handle_notify', autospec=True) as notify:
            with self.assertRaises(RecoveryFailed):
                self.recovery.restart()
        notify.assert_called_once()
        self.assertIn('未成功', notify.call_args.kwargs['content'])

    def test_notification_failure_never_breaks_recovery(self):
        """推送是旁路动作：发送异常不能影响恢复结果本身。"""
        with patch('module.handler.emulator_recovery.handle_notify', autospec=True,
                   side_effect=OSError('推送通道不可用')):
            self.assertTrue(self.recovery.restart())

    def test_request_for_old_device_does_not_invalidate_new_device(self):
        self.assertFalse(self.recovery.request('旧探测', device=DeviceSession()))
        self.alas.device.check_session()

    def test_background_request_does_not_start_recovery_worker(self):
        self.assertTrue(self.recovery.request('心跳持续失败'))
        self.assertIsNone(self.recovery.worker)
        self.target.stop.assert_not_called()


@unittest.skipUnless(os.name == 'nt', 'Windows 实例适配')
class TestInstanceIdentity(unittest.TestCase):
    def make_target(self, kind='BlueStacks5', name='Pie64'):
        instance = SimpleNamespace(type=kind, name=name, path='C:/BlueStacks/HD-Player.exe',
                                   serial='127.0.0.1:5555')
        platform = SimpleNamespace(emulator_instance=instance, serial=instance.serial)
        with patch('module.handler.emulator_target.os.path.isfile', return_value=True):
            return RecoveryTarget(platform)

    def test_exact_argument_match_does_not_select_similarly_named_instance(self):
        import psutil
        target = self.make_target()
        processes = []
        for number, name in enumerate(('Pie64', 'Pie64_2')):
            proc = create_autospec(psutil.Process, instance=True)
            proc.pid = number + 100
            proc.name.return_value = 'HD-Player.exe'
            proc.exe.return_value = 'C:/BlueStacks/HD-Player.exe'
            proc.cmdline.return_value = ['HD-Player.exe', '--instance', name]
            processes.append(proc)
        with patch('psutil.process_iter', return_value=processes):
            self.assertEqual(target.processes(), processes[:1])

    def test_unknown_or_shared_service_platform_is_refused(self):
        with self.assertRaises(RecoveryBlocked):
            self.make_target(kind='MuMuPlayer')

    def test_serial_mismatch_is_refused(self):
        instance = SimpleNamespace(type='BlueStacks5', name='Pie64', path='/exe', serial='wrong')
        with self.assertRaises(RecoveryBlocked):
            RecoveryTarget(SimpleNamespace(emulator_instance=instance, serial='other'))
