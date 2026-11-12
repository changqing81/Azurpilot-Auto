"""维护等待期间 Watchdog 静默的回归测试。

背景（2026-11-12 事故）：
服务器维护时 `_close_emulator_for_maintenance` 会关掉模拟器、每 60 秒
HTTP 查询一次开服状态。该轮询循环刻意不打日志（避免刷屏），但维护可能
持续数小时；而 `watchdog.deactivate()` 要等任务返回才执行，于是 Watchdog
一直认为「任务执行中 + 无日志输出」，每 30 秒刷一条 WARNING，持续一小时
（实测 120+ 条），把真正的日志全淹了。

修复：调度器加「正在维护等待」标志，两条维护等待路径（关模拟器轮询 /
开模拟器轮询）都在置位窗口内运行；Watchdog 见到标志就跳过
心跳告警、任务超时与强制定时重启。
"""
import inspect
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import alas as alas_module
from module.handler.task_failure_protection import Watchdog


def _make_script():
    """构造不跑 __init__ 的调度器实例，只带本次用到的状态字段。

    状态文件相关方法整体替换掉：本测试只关心标志的生命周期，
    不该真的往 log/ 写文件（会污染工作区，也让测试依赖 cwd）。
    """
    script = object.__new__(alas_module.AzurLaneAutoScript)
    script._maintenance_waiting = False
    script._maintenance_since = 0.0
    script.config_name = 'unittest'
    script._write_maintenance_state = lambda **kwargs: None
    script._clear_maintenance_state = lambda: None
    return script


def _make_watchdog(config):
    wd = Watchdog(MagicMock())
    wd.alas = SimpleNamespace(config=config)
    wd._active = True
    wd._task_start = 0.0
    wd._task_name = 'OpsiScheduling'
    wd._heartbeat.last_log_time = time.monotonic() - 10_000
    # 第一次 wait 返回 False（继续循环体），第二次 True（退出循环）
    wd._stop_event = MagicMock()
    wd._stop_event.wait.side_effect = [False, True]
    return wd


def _config():
    return SimpleNamespace(
        TaskFailureProtection_WatchdogEnable=True,
        TaskFailureProtection_WatchdogLogTimeout=300,
        TaskFailureProtection_WatchdogTaskTimeout=0,
        EmulatorManagement_CrashProtection=True,
    )


class TestMaintenanceWaitFlag(unittest.TestCase):
    def test_flag_set_and_cleared(self):
        script = _make_script()
        with script._maintenance_wait():
            self.assertTrue(script._maintenance_waiting)
        self.assertFalse(script._maintenance_waiting)

    def test_flag_cleared_on_exception(self):
        """异常路径也必须清标志，否则 Watchdog 会永久静默。"""
        script = _make_script()
        with self.assertRaises(RuntimeError):
            with script._maintenance_wait():
                raise RuntimeError('boom')
        self.assertFalse(script._maintenance_waiting)

    def test_all_maintenance_paths_are_wrapped(self):
        """关模拟器轮询 + 3 处开模拟器轮询都要套进标志窗口。

        第一处（关模拟器轮询）带 `emulator_closed=True`，用于让 WebUI 横幅
        区分「模拟器已关闭」与「模拟器仍开着」两种等待形态，所以按前缀统计。
        """
        close_src = inspect.getsource(
            alas_module.AzurLaneAutoScript._close_emulator_for_maintenance)
        self.assertIn('with self._maintenance_wait(emulator_closed=True):', close_src)

        source = inspect.getsource(alas_module.AzurLaneAutoScript)
        self.assertEqual(source.count('with self._maintenance_wait('), 4)


class TestWatchdogHonoursMaintenanceFlag(unittest.TestCase):
    def test_no_warning_while_maintenance_waiting(self):
        """维护等待期间不得刷「无日志输出」告警。"""
        wd = _make_watchdog(_config())
        wd.alas._maintenance_waiting = True
        with patch.object(Watchdog, '_check_force_scheduled_restart',
                          return_value=False) as force, \
                patch('module.handler.task_failure_protection.logger') as log:
            wd._loop()
        force.assert_not_called()
        warned = ' '.join(str(c.args[0]) for c in log.warning.call_args_list)
        self.assertNotIn('无日志输出', warned)

    def test_warning_still_fires_without_flag(self):
        """非维护等待时保持原有告警行为（不能把检测整体关掉）。"""
        wd = _make_watchdog(_config())
        wd.alas._maintenance_waiting = False
        with patch.object(Watchdog, '_check_force_scheduled_restart',
                          return_value=False), \
                patch('module.handler.task_failure_protection.logger') as log:
            wd._loop()
        warned = ' '.join(str(c.args[0]) for c in log.warning.call_args_list)
        self.assertIn('无日志输出', warned)


if __name__ == '__main__':
    unittest.main()
