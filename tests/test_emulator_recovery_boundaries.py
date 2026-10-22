"""任务重试异常改写与原生 IPC 残留的回归测试，不使用真实设备。"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

from alas import AzurLaneAutoScript
from module.config.config import AzurLaneConfig, TaskEnd
from module.device.native_call import NativeCall
from module.device.session import DeviceSession, RecoveryStopped
from module.exception import EmulatorNotRunningError, GameStuckError, RequestHumanTakeover
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
            with self.assertRaises(RecoveryStopped):
                call.run(native, timeout=0.05)
            self.assertTrue(entered.is_set())
            self.assertTrue(call.thread.is_alive())
            with self.assertRaises(RecoveryStopped):
                call.ensure_idle()
            with self.assertRaises(RecoveryStopped):
                call.run(lambda: effects.append('禁止执行'))
        finally:
            release.set()
            call.thread.join(3)
        self.assertEqual(effects, ['原调用完成'])
        with self.assertRaises(RecoveryStopped):
            call.ensure_idle()
        with self.assertRaises(RecoveryStopped):
            call.run(lambda: effects.append('迟到后也禁止执行'))

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
            with self.assertRaises(RecoveryStopped):
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
        with self.assertRaises(RecoveryStopped):
            device.close_session()
        self.assertIs(device.nemu_ipc, ipc)
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
