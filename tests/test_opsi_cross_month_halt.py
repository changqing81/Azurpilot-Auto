import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import TaskEnd
from module.exception import (EmulatorNotRunningError, GameBugError,
                             GameNotRunningError, GamePageUnknownError,
                             GameStuckError, GameTooManyClickError)
from module.os.tasks.cross_month import ENVIRONMENT_ERRORS, OpsiCrossMonth

NEXT_RESET = datetime(2026, 10, 1, 0, 0, 0)


class CrossMonthHaltTestBase(unittest.TestCase):
    """跨月每日「完成后停止调度 / 失败按敏感任务停止」的行为锁。"""

    def setUp(self):
        self.task = OpsiCrossMonth.__new__(OpsiCrossMonth)
        self.switches = {}
        self.config = SimpleNamespace(
            overridden={},
            _disable_task_switch=False,
            config_name='test',
            Error_OnePushConfig={},
            task_delay=Mock(),
            override=Mock(),
            bind=Mock(),
            task_stop=Mock(side_effect=TaskEnd),
            cross_get=Mock(side_effect=self.cross_get),
        )
        self.task.config = self.config
        for name in ('get_os_next_reset',):
            patcher = patch(f'module.os.tasks.cross_month.{name}', return_value=NEXT_RESET)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('handle_notify', 'notify_webui'):
            patcher = patch(f'module.os.tasks.cross_month.{name}')
            self.mocks = getattr(self, 'mocks', {})
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def cross_get(self, keys=None, default=None):
        if keys == 'OpsiCrossMonth.OpsiCrossMonth.RehearsalDebug':
            return 'off'
        return self.switches.get(keys, default)

    def make_config(self):
        return self.config


class TestCrossMonthEndHalt(CrossMonthHaltTestBase):
    def test_halt_after_complete_stops_azurpilot(self):
        """开关打开时：规划下次运行时间后直接结束进程等待人工。"""
        with self.assertRaises(SystemExit) as ctx:
            self.task.os_cross_month_end(halt=True)

        self.assertEqual(ctx.exception.code, 1)
        self.config.task_delay.assert_called_once_with(
            target=NEXT_RESET - timedelta(minutes=10))
        self.config.task_stop.assert_not_called()
        self.mocks['handle_notify'].assert_called_once()
        self.mocks['notify_webui'].assert_called_once()

    def test_without_halt_keeps_normal_task_end(self):
        """开关关闭时：只按原逻辑结束本任务，不推送、不退出。"""
        with self.assertRaises(TaskEnd):
            self.task.os_cross_month_end()

        self.config.task_delay.assert_called_once()
        self.config.task_stop.assert_called_once()
        self.mocks['handle_notify'].assert_not_called()
        self.mocks['notify_webui'].assert_not_called()


class TestCrossMonthDailyHaltSwitch(CrossMonthHaltTestBase):
    def run_daily(self, enabled):
        self.switches['OpsiCrossMonth.OpsiCrossMonth.StopAfterComplete'] = enabled
        # os_finish_daily_mission 由运行期组合类 OperationSiren(OpsiCrossMonth, OpsiDaily, ...)
        # 提供，裸类上没有，这里用 create=True 直接替换。
        with patch.object(self.task, 'os_mission_overview_accept', return_value=True), \
                patch.object(self.task, 'zone_init'), \
                patch.object(self.task, 'os_finish_daily_mission', return_value=1, create=True), \
                patch.object(self.task, '_os_cross_month_clear_action_point'), \
                patch.object(self.task, 'os_cross_month_end') as end:
            self.task._os_cross_month_daily()
        return end

    def test_switch_on_passes_halt(self):
        end = self.run_daily(True)
        end.assert_called_once_with(halt=True)

    def test_switch_off_passes_no_halt(self):
        end = self.run_daily(False)
        end.assert_called_once_with(halt=False)


class TestCrossMonthFailureHalt(CrossMonthHaltTestBase):
    def run_failed(self, sensitive, error=None):
        error = error or ValueError('boom')
        self.switches['OpsiCrossMonth.Scheduler.Sensitive'] = sensitive
        with patch.object(self.task, '_os_cross_month', side_effect=error), \
                patch.object(self.task, '_cross_month_fail_handover') as handover, \
                patch.object(self.task, '_notify_cross_month_failed') as notify_failed:
            if sensitive and not isinstance(error, ENVIRONMENT_ERRORS):
                with self.assertRaises(SystemExit) as ctx:
                    self.task.os_cross_month()
                return ctx.exception.code, handover, notify_failed
            self.task.os_cross_month()
            return None, handover, notify_failed

    def test_sensitive_task_failure_stops_azurpilot(self):
        """敏感开关打开：失败即停止调度，不再自动交接。"""
        code, handover, notify_failed = self.run_failed(True)

        self.assertEqual(code, 1)
        handover.assert_not_called()
        notify_failed.assert_not_called()
        # 停止前仍要规划下次运行时间，否则重启后会立刻重跑
        self.config.task_delay.assert_called_once_with(
            target=NEXT_RESET + timedelta(minutes=10))

    def test_non_sensitive_task_failure_keeps_handover(self):
        """敏感开关关闭：保持原有自动交接行为。"""
        code, handover, notify_failed = self.run_failed(False)

        self.assertIsNone(code)
        handover.assert_called_once()
        notify_failed.assert_called_once()

    def test_environment_errors_keep_scheduler_alive(self):
        """被踢出/掉线/卡死/模拟器离线/客户端 bug：交给调度器自愈，不停 AP。"""
        for error in (
            GameNotRunningError('游戏已退出'),
            GamePageUnknownError('无法识别当前页面'),
            GameStuckError('卡死'),
            GameTooManyClickError('点击过多'),
            GameBugError('客户端异常'),
            EmulatorNotRunningError('模拟器离线'),
        ):
            with self.subTest(error=type(error).__name__):
                for mock in (self.config.task_delay, self.config.task_stop,
                             self.mocks['handle_notify'], self.mocks['notify_webui']):
                    mock.reset_mock()
                code, handover, notify_failed = self.run_failed(True, error)

                self.assertIsNone(code)
                handover.assert_called_once()
                notify_failed.assert_called_once()
                self.mocks['handle_notify'].assert_not_called()
                self.mocks['notify_webui'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
