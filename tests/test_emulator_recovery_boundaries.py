"""任务重试异常改写与原生 IPC 残留的回归测试，不使用真实设备。"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

from alas import AzurLaneAutoScript
from module.config.config import AzurLaneConfig, TaskEnd
from module.device.native_call import NativeCall
from module.device.session import DeviceSession, RecoveryStopped
from module.exception import (
    EmulatorNotRunningError,
    GameStuckError,
    NativeIpcTimeout,
    RequestHumanTakeover,
)
from module.handler.emulator_recovery import EmulatorRecovery


class TestTaskExceptionBoundary(unittest.TestCase):
    def test_rewritten_exception_consumes_original_request_once(self):
        for exception in (EmulatorNotRunningError, GameStuckError, RequestHumanTakeover,
                          RuntimeError, TaskEnd):
            with self.subTest(exception=exception.__name__):
                script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
                config = create_autospec(AzurLaneConfig, instance=True)
                config.EmulatorManagement_CrashProtection = True
                config.EmulatorManagement_CrashDryRun = False
                script.config = config
                script.config_name = None
                script.device = DeviceSession()
                script._emulator_lock = threading.Lock()
                script._channel_float_done = True
                script.recovery = EmulatorRecovery(script)

                def task():
                    script.recovery.request('持续心跳失败')
                    # 模拟底层重试把会话失效包装为另一种异常。
                    raise exception('包装后的异常')

                script.boundary_task = task
                with patch.object(EmulatorRecovery, 'restart', autospec=True, return_value=True) as restart, \
                        patch.object(AzurLaneAutoScript, '_check_sensitive_exit', autospec=True):
                    result = script.run('boundary_task', skip_first_screenshot=True)
                self.assertEqual(result, 'recoverable')
                self.assertIsNone(script.recovery.pending)
                restart.assert_called_once_with(script.recovery, 'crash')
                config.task_call.assert_called_once_with('Restart')

    def test_pending_request_does_not_override_terminal_native_failure(self):
        alas = SimpleNamespace(config=SimpleNamespace(), config_name=None)
        recovery = EmulatorRecovery(alas)
        with self.assertRaisesRegex(RecoveryStopped, '原生调用'):
            with recovery.task_scope():
                recovery.pending = ('通信异常', 'crash')
                raise RecoveryStopped('原生调用未退出')


class TestNativeCallLifetime(unittest.TestCase):
    def test_timeout_cannot_cancel_thread_or_reuse_instance_even_after_late_return(self):
        call = NativeCall()
        entered = threading.Event()
        release = threading.Event()
        effects = []

        def native():
            entered.set()
            release.wait(3)
            effects.append('原调用完成')
            return 0

        try:
            # 超时不再抛 SystemExit（那是杀脚本）。设备无响应应按"模拟器
            # 失去运行"处理，交给调度器重启实例后继续挂机。
            with self.assertRaises(NativeIpcTimeout):
                call.run(native, timeout=0.05)
            self.assertTrue(entered.is_set())
            self.assertTrue(call.thread.is_alive())
            # 句柄仍被占用时依旧禁止释放/重启，避免并发碰同一 DLL 句柄。
            # 但不使用 SystemExit：脚本要继续运行并重启模拟器。
            with self.assertRaises(NativeIpcTimeout):
                call.ensure_idle()
            with self.assertRaises(NativeIpcTimeout):
                call.run(lambda: effects.append('禁止执行'))
        finally:
            release.set()
            call.thread.join(3)
        self.assertEqual(effects, ['原调用完成'])
        with self.assertRaises(NativeIpcTimeout):
            call.ensure_idle()
        with self.assertRaises(NativeIpcTimeout):
            call.run(lambda: effects.append('迟到后也禁止执行'))

    def test_timeout_is_not_a_system_exit_so_scheduler_can_recover(self):
        """回归：超时曾抛 RecoveryStopped(SystemExit)，把整个配置进程打死。

        设备卡死时正确动作是重启模拟器，不能连脚本一起杀。
        """
        call = NativeCall()
        release = threading.Event()

        def native():
            release.wait(3)

        try:
            with self.assertRaises(NativeIpcTimeout) as ctx:
                call.run(native, timeout=0.05)
            self.assertNotIsInstance(ctx.exception, SystemExit)
            self.assertIsInstance(ctx.exception, EmulatorNotRunningError)
        finally:
            release.set()
            call.thread.join(3)

    def test_default_budget_is_one_minute_not_half_second(self):
        """回归：默认预算 0.5 秒会把战斗切场景的正常抖动误判成设备卡死。"""
        from module.device.native_call import NATIVE_CALL_TIMEOUT
        self.assertGreaterEqual(NATIVE_CALL_TIMEOUT, 60.0)
        self.assertEqual(NativeCall().timeout, NATIVE_CALL_TIMEOUT)

    def test_late_nested_query_cannot_continue_to_another_native_operation(self):
        call = NativeCall()
        entered = threading.Event()
        release = threading.Event()
        effects = []

        def query():
            entered.set()
            release.wait(3)
            return 0

        def screenshot():
            call.run(query)
            effects.append('不应开始第二次原生调用')

        try:
            with self.assertRaises(NativeIpcTimeout):
                call.run(screenshot, timeout=0.05)
            self.assertTrue(entered.is_set())
        finally:
            release.set()
            call.thread.join(3)
        self.assertEqual(effects, [])

    def test_nested_calls_share_worker_and_success_remains_reusable(self):
        call = NativeCall()
        def nested():
            return call.run(threading.get_ident)
        self.assertEqual(call.run(nested), call.thread.ident)
        self.assertEqual(call.run(lambda: 7), 7)
        call.ensure_idle()

    def test_error_propagates_without_replaying_native_side_effect(self):
        call = NativeCall()
        effects = []
        def native():
            effects.append(1)
            raise ValueError('调用失败')
        with self.assertRaises(ValueError):
            call.run(native)
        self.assertEqual(effects, [1])
        call.ensure_idle()

    def test_nemu_disconnect_refuses_poisoned_native_handle(self):
        from module.device.method.nemu_ipc import NemuIpcImpl
        ipc = NemuIpcImpl.__new__(NemuIpcImpl)
        ipc.connect_id = 123
        ipc.native_call = NativeCall()
        ipc.native_call.poisoned = True
        ipc.lib = SimpleNamespace(nemu_disconnect=lambda _: self.fail('不允许释放在用句柄'))
        device = DeviceSession()
        device.nemu_ipc = ipc
        # 关闭会话不得终止脚本：跳过仍在占用的句柄，丢弃引用即可。
        device.close_session()
        self.assertNotIn('nemu_ipc', device.__dict__)
        # 句柄从未被并发 disconnect 触碰，connect_id 保持原值。
        self.assertEqual(ipc.connect_id, 123)

    def test_nemu_error_diagnostics_do_not_execute_function_twice(self):
        from module.device.method.nemu_ipc import NemuIpcError, NemuIpcImpl
        ipc = NemuIpcImpl.__new__(NemuIpcImpl)
        effects = []
        def nemu_input_event_touch_down():
            effects.append(1)
            return 1
        with self.assertRaises(NemuIpcError):
            ipc.run_func(nemu_input_event_touch_down)
        self.assertEqual(effects, [1])


class TestLateAdbStream(unittest.TestCase):
    def test_stream_created_after_invalidation_is_closed(self):
        import socket
        from module.device.connection import Connection
        from module.device.session import DeviceSessionExpired
        connection = Connection.__new__(Connection)
        connection.config = SimpleNamespace(DEVICE_OVER_HTTP=False)
        stream = create_autospec(socket.socket, instance=True)

        def shell(*args, **kwargs):
            connection.invalidate_session('连接建立期间收到恢复请求')
            return stream

        connection.adb = SimpleNamespace(shell=shell)
        with self.assertRaises(DeviceSessionExpired):
            connection.adb_shell('echo pong', stream=True, recvall=False)
        stream.close.assert_called_once()
        self.assertNotIn('_recovery_adb_stream', connection.__dict__)


class TestPersistedRecoverySwitch(unittest.TestCase):
    def test_new_settings_path_overrides_stale_cached_and_legacy_switch(self):
        import json
        import tempfile
        from pathlib import Path
        config = SimpleNamespace(EmulatorManagement_CrashProtection=True)
        recovery = EmulatorRecovery(SimpleNamespace(config=config, config_name='test'))
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'test.json'
            target.write_text(json.dumps({
                'Alas': {'EmulatorManagement': {'CrashProtection': True}},
                'EmulatorSettings': {'EmulatorManagement': {'CrashProtection': False}},
            }), encoding='utf-8')
            with patch('module.handler.emulator_recovery.Path', return_value=Path(root)):
                self.assertFalse(recovery.allowed('crash'))

    def test_malformed_settings_disable_recovery(self):
        import tempfile
        from pathlib import Path
        config = SimpleNamespace(EmulatorManagement_CrashProtection=True)
        recovery = EmulatorRecovery(SimpleNamespace(config=config, config_name='test'))
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'test.json'
            target.write_text('[]', encoding='utf-8')
            with patch('module.handler.emulator_recovery.Path', return_value=Path(root)):
                self.assertFalse(recovery.allowed('crash'))


class TestRetryInitializationBoundary(unittest.TestCase):
    def test_retry_initializers_cannot_reconnect_invalidated_device(self):
        from module.device.connection import Connection
        from module.device.session import DeviceSessionExpired
        for name in ('adb_connect', 'adb_disconnect', 'adb_restart', 'adb_reconnect',
                     'install_uiautomator2', 'restart_atx', 'adb_start_server'):
            with self.subTest(method=name):
                connection = Connection.__new__(Connection)
                connection.config = SimpleNamespace(DEVICE_OVER_HTTP=False)
                connection.invalidate_session('重试间隔内设备会话已失效')
                with self.assertRaises(DeviceSessionExpired):
                    getattr(connection, name)()

    def test_invalidating_session_closes_reverse_listener(self):
        import socket
        connection = DeviceSession()
        listener = create_autospec(socket.socket, instance=True)
        connection.reverse_server = listener
        connection.invalidate_session('恢复前关闭反向监听')
        listener.close.assert_called_once()


class TestStopToleratesAlreadyStoppedMuMu(unittest.TestCase):
    """回归：停止命令把"实例本来没运行"当成故障，导致恢复永远走不到启动。

    实机日志（2026-10-23）：实例已停止时 MuMuManager 返回 -2
    （Windows 侧 4294967294）并打印 player not running。旧实现 check=True
    让 stop() 抛 CalledProcessError，recover() 直接跳进 except，start()
    永远执行不到，表现为"模拟器关了以后再也拉不起来"。
    """

    def _target(self):
        from module.handler.emulator_target import RecoveryTarget
        target = RecoveryTarget.__new__(RecoveryTarget)
        target.platform = SimpleNamespace()
        target.instance = SimpleNamespace(MuMuPlayer12_id=1, LDPlayer_id=0)
        target.identity = ('MuMuPlayer12', '/mumu', 'MuMuPlayer-15.0-1')
        return target

    def test_stop_does_not_raise_when_player_not_running(self):
        import subprocess
        target = self._target()
        target._console = lambda: 'MuMuManager.exe'

        def fake_run(args, **kwargs):
            # 复刻实机返回：实例没在运行 -> -2
            if kwargs.get('check'):
                raise subprocess.CalledProcessError(4294967294, args)
            return SimpleNamespace(returncode=4294967294)

        with patch('module.handler.emulator_target.subprocess.run', side_effect=fake_run) as run:
            target.stop(lambda: None)
        # 关键：必须显式关闭 check，否则已停止的实例会让整轮恢复中止
        self.assertIs(run.call_args.kwargs.get('check'), False)
        self.assertIn('shutdown_player', run.call_args.args[0])

    def test_other_commands_still_fail_fast_on_non_zero(self):
        """只有停止类命令容忍非零；状态查询等仍应快速失败。"""
        import subprocess
        target = self._target()
        target._console = lambda: 'MuMuManager.exe'
        with patch('module.handler.emulator_target.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, ['x'])) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                target._command(['x'])
        # 默认仍要求零退出码：只有 stop() 显式传 False
        self.assertIs(run.call_args.kwargs.get('check'), True)
