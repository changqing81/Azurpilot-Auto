"""uiautomator2 agent 404 降级到 ADB 的回归测试。

真实链路（2026-11-09 事故，两次踩坑）：

1. MuMu12 冷启动/agent 崩溃期间，atx-agent 的 /shell 返回 404，
   u2 抛 RuntimeError('device agent responds with an error code 404', '')。
2. 该异常不是 OSError，绕过 u2 重试白名单，`@retry` 装饰的
   _app_start_u2_am / _app_start_u2_monkey 在 retry_wrapper 顶层把它
   归类为 **EmulatorNotRunningError**（注意：EmulatorNotRunningError
   直接继承 Exception，不是 RuntimeError）。
3. 第一版修复只在 app_start_uiautomator2 外层写了 `except RuntimeError`，
   抓不到 EmulatorNotRunningError，完全没生效——而测试因为把
   _app_start_u2_am mock 成抛 RuntimeError 而"通过"，属测试空转。
4. 第二版修复：retry_wrapper 记录最后一次异常，对启动类方法在 404 时
   改抛 U2AgentNotReady，由 app_start_uiautomator2 捕获后降级 app_start_adb。

因此本测试**不 mock _app_start_u2_am**，只 mock u2 的 HTTP 侧，
让真实 retry_wrapper 参与，确保测的是线上真实异常路径。
"""
import unittest
from unittest.mock import MagicMock, patch

from module.device.method.adb import Adb
from module.device.method.uiautomator_2 import Uiautomator2
from module.exception import EmulatorNotRunningError, U2AgentNotReady


def _agent_404():
    return RuntimeError('device agent responds with an error code 404', '')


_ADB_FOCUS_OUTPUT = (
    '  mCurrentFocus=Window{41b37570 u0 com.bilibili.azurlane/'
    'com.manjuu.azurlane.MainActivity}'
)
_PKG = 'com.bilibili.azurlane'
_ACT = 'com.manjuu.azurlane.MainActivity'


class TestAppCurrent404Fallback(unittest.TestCase):
    """app_current_uiautomator2: agent 404 时当轮降级 ADB 查询前台包名。"""

    def _make_device(self):
        device = MagicMock(spec=Uiautomator2)
        device.app_current_uiautomator2 = Uiautomator2.app_current_uiautomator2.__get__(device)
        device.u2 = MagicMock()
        device.u2.app_current.side_effect = _agent_404()
        device.adb_shell = MagicMock(return_value=_ADB_FOCUS_OUTPUT)
        device.app_current_adb = Adb.app_current_adb.__get__(device)
        return device

    def test_404_falls_back_to_adb(self):
        device = self._make_device()
        self.assertEqual(device.app_current_uiautomator2(), _PKG)
        device.adb_shell.assert_called_once()

    def test_u2_preferred_again_after_recovery(self):
        device = self._make_device()
        device.u2.app_current.side_effect = [_agent_404(), _agent_404(), {'package': _PKG}]
        for _ in range(3):
            self.assertEqual(device.app_current_uiautomator2(), _PKG)
        self.assertEqual(device.u2.app_current.call_count, 3)
        self.assertEqual(device.adb_shell.call_count, 2)

    def test_adb_fallback_failure_raises_emulator_not_running(self):
        device = self._make_device()
        device.adb_shell = MagicMock(side_effect=OSError("Couldn't get focused app"))
        with self.assertRaises(EmulatorNotRunningError):
            device.app_current_uiautomator2()

    def test_other_runtime_error_not_swallowed(self):
        device = self._make_device()
        device.u2.app_current.side_effect = RuntimeError('USB device 127.0.0.1:5555 is offline')
        device.adb_shell = MagicMock()
        with self.assertRaises(EmulatorNotRunningError):
            device.app_current_uiautomator2()
        device.adb_shell.assert_not_called()


class TestAppStop404Fallback(unittest.TestCase):
    """app_stop_uiautomator2: agent 404 时当轮降级 am force-stop。"""

    def _make_device(self):
        device = MagicMock(spec=Uiautomator2)
        device.app_stop_uiautomator2 = Uiautomator2.app_stop_uiautomator2.__get__(device)
        device.u2 = MagicMock()
        device.u2.app_stop.side_effect = _agent_404()
        device.package = _PKG
        device.adb_shell = MagicMock(return_value='')
        return device

    def test_404_falls_back_to_adb_force_stop(self):
        device = self._make_device()
        device.app_stop_uiautomator2()
        device.adb_shell.assert_called_once_with(['am', 'force-stop', _PKG])

    def test_u2_success_skips_adb(self):
        device = self._make_device()
        device.u2.app_stop.side_effect = None
        device.app_stop_uiautomator2()
        device.adb_shell.assert_not_called()


class TestAppStart404Fallback(unittest.TestCase):
    """app_start_uiautomator2: agent 404 时当轮降级 ADB am start。

    ⚠ 本组测试刻意**不 mock** _app_start_u2_am / _app_start_u2_monkey，
    以便走真实 retry_wrapper——线上正是由它把 404 归一成
    EmulatorNotRunningError 的，mock 掉就会掩盖真实异常类型（第一版
    修复的测试空转事故）。
    """

    def _make_device(self):
        device = MagicMock(spec=Uiautomator2)
        device.package = _PKG
        device.u2 = MagicMock()
        device.u2.shell.side_effect = _agent_404()
        device.u2.app_info.side_effect = _agent_404()
        # 真实方法（含 @retry 装饰的 retry_wrapper）
        device._app_start_u2_am = Uiautomator2._app_start_u2_am.__get__(device)
        device._app_start_u2_monkey = Uiautomator2._app_start_u2_monkey.__get__(device)
        device.app_start_uiautomator2 = Uiautomator2.app_start_uiautomator2.__get__(device)
        device.app_start_adb = MagicMock(return_value=True)
        return device

    def test_404_falls_back_to_adb_start(self):
        """真实链路：u2.shell 404 → retry_wrapper → 必须降级 app_start_adb。"""
        device = self._make_device()
        result = device.app_start_uiautomator2(_PKG, _ACT, False)
        self.assertTrue(result)
        device.app_start_adb.assert_called_once_with(_PKG, _ACT, False)

    def test_404_does_not_raise_emulator_not_running(self):
        """回归护栏：agent 404 绝不能再以 EmulatorNotRunningError 上抛。"""
        device = self._make_device()
        try:
            device.app_start_uiautomator2(_PKG, _ACT, False)
        except EmulatorNotRunningError:
            self.fail('agent 404 不应被归为 EmulatorNotRunningError（会误判模拟器已死）')

    def test_u2_success_skips_adb(self):
        """u2 正常时不应触碰 ADB 降级路径。"""
        device = MagicMock(spec=Uiautomator2)
        device.package = _PKG
        device.u2 = MagicMock()
        device._app_start_u2_am = MagicMock(return_value=True)
        device._app_start_u2_monkey = MagicMock(return_value=True)
        device.app_start_uiautomator2 = Uiautomator2.app_start_uiautomator2.__get__(device)
        device.app_start_adb = MagicMock()
        self.assertTrue(device.app_start_uiautomator2(_PKG, _ACT, False))
        device.app_start_adb.assert_not_called()


class TestDumpHierarchy404Fallback(unittest.TestCase):
    """dump_hierarchy_uiautomator2: agent 404 时降级 ADB uiautomator dump。"""

    def _make_device(self):
        device = MagicMock(spec=Uiautomator2)
        device.dump_hierarchy_uiautomator2 = Uiautomator2.dump_hierarchy_uiautomator2.__get__(device)
        device.u2 = MagicMock()
        device.u2.dump_hierarchy.side_effect = _agent_404()
        device.dump_hierarchy_adb = MagicMock(return_value='<hierarchy/>')
        return device

    def test_404_falls_back_to_adb_dump(self):
        device = self._make_device()
        self.assertEqual(device.dump_hierarchy_uiautomator2(), '<hierarchy/>')
        device.dump_hierarchy_adb.assert_called_once()

    def test_u2_success_skips_adb(self):
        device = self._make_device()
        device.u2.dump_hierarchy.side_effect = None
        device.u2.dump_hierarchy.return_value = '<hierarchy><node/></hierarchy>'
        device.dump_hierarchy_uiautomator2()
        device.dump_hierarchy_adb.assert_not_called()


class Test404DiagnosticsSuppressed(unittest.TestCase):
    """404 不得走 handle_adb_error 的诊断分支。

    handle_adb_error 对未知文本会打 traceback + 三条
    "Emulator died, please restart emulator" 的 possible_reasons，
    而 404 根本与 ADB/模拟器无关。这些噪音会让用户误以为模拟器真的挂了
    （2026-11-09 用户连续两次因此以为修复没生效）。
    """

    def _make_device(self):
        device = MagicMock(spec=Uiautomator2)
        device.package = _PKG
        device.u2 = MagicMock()
        device.u2.shell.side_effect = _agent_404()
        device.u2.app_info.side_effect = _agent_404()
        device._app_start_u2_am = Uiautomator2._app_start_u2_am.__get__(device)
        device.app_start_uiautomator2 = Uiautomator2.app_start_uiautomator2.__get__(device)
        device.app_start_adb = MagicMock(return_value=True)
        return device

    def test_404_does_not_call_handle_adb_error(self):
        """404 时不应调用 handle_adb_error（它会打误导性的 Emulator died）。"""
        device = self._make_device()
        with patch('module.device.method.uiautomator_2.handle_adb_error') as handler, \
                patch('module.device.method.uiautomator_2.possible_reasons') as reasons:
            device.app_start_uiautomator2(_PKG, _ACT, False)
        handler.assert_not_called()
        reasons.assert_not_called()

    def test_non_404_runtime_error_still_uses_handle_adb_error(self):
        """非 404 的 RuntimeError 仍要走原有诊断链路（不能误删）。"""
        device = MagicMock(spec=Uiautomator2)
        device.package = _PKG
        device.u2 = MagicMock()
        device.u2.shell.side_effect = RuntimeError('device offline')
        device.u2.app_info.side_effect = RuntimeError('device offline')
        device._app_start_u2_am = Uiautomator2._app_start_u2_am.__get__(device)
        device.app_start_uiautomator2 = Uiautomator2.app_start_uiautomator2.__get__(device)
        device.app_start_adb = MagicMock(return_value=True)
        with patch('module.device.method.uiautomator_2.handle_adb_error',
                   return_value=False) as handler:
            with self.assertRaises(EmulatorNotRunningError):
                device.app_start_uiautomator2(_PKG, _ACT, False)
        handler.assert_called()


if __name__ == '__main__':
    unittest.main()
