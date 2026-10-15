"""防止行动力溢出读数来源：账本优先、失败保持原排程（2026-10-15 事故回归）。

线上背景（2026-10-15）：任务被抢占切换时游戏已退出大世界，收尾的防溢出
检查仍去弹窗读行动力——15s 进窗超时后在整屏上 OCR 出 0/10/22/42 等垃圾值，
并当作真值落盘：污染 Dashboard、统计图与行动力账本，还把防溢出任务排到
31 小时后（1900 分钟），保护形同虚设。

本模块锁定修复后的契约：
1. 账本记录足够新 → 直接用账本值，完全不弹窗；
2. 账本关闭 / 无记录 / 记录过期 → 回退现场读弹窗；
3. 现场读不到有效值 → 返回 None，调用方保持原排程，绝不写垃圾值。
"""
import unittest
from datetime import timedelta
from types import MethodType
from unittest.mock import Mock

from module.config.time_source import now as current_time
from module.os.tasks.prevent_action_point_overflow import OpsiPreventActionPointOverflow
from module.os_handler.action_point_ledger import ActionPointLedger

TASK = 'OpsiPreventActionPointOverflow'


class FakeOverflowRunner:
    """最小替身：账本与设备交互全部可控/可记录。"""

    def __init__(self, ledger=None, ledger_enabled=True, live_ok=True,
                 live_current=88, live_total=5408):
        self.config = Mock()
        self.config.OS_ACTION_POINT_BOX_USE = True
        self.TASK_NAME_PREVENT_AP_OVERFLOW = TASK
        self._ledger = ledger
        self._ledger_enabled = ledger_enabled
        self._live_ok = live_ok
        self._live_current = live_current
        self._live_total = live_total
        self.calls = []

    # ---- 账本接口 ----

    def _ap_ledger_enabled(self):
        return self._ledger_enabled

    def _get_ap_ledger(self):
        return self._ledger

    # ---- 设备接口（记录调用） ----

    def action_point_enter(self):
        self.calls.append('enter')

    def action_point_safe_get(self):
        self.calls.append('safe_get')
        if not self._live_ok:
            return False
        self._action_point_current = self._live_current
        self._action_point_total = self._live_total
        return True

    def action_point_quit(self):
        self.calls.append('quit')

    # ---- 阈值（避开真实配置） ----

    def _get_prevent_action_point_overflow_thresholds(self):
        return 190, 0

    def bind(self, *names):
        for name in names:
            setattr(self, name, MethodType(getattr(OpsiPreventActionPointOverflow, name), self))
        return self


def make_runner(**kwargs):
    runner = FakeOverflowRunner(**kwargs)
    runner.PREVENT_AP_OVERFLOW_LEDGER_MAX_AGE_SECONDS = (
        OpsiPreventActionPointOverflow.PREVENT_AP_OVERFLOW_LEDGER_MAX_AGE_SECONDS
    )
    runner.bind(
        '_read_action_point_from_ledger_for_overflow',
        '_read_action_point_live_for_overflow',
        '_get_current_action_point_for_overflow',
        'update_prevent_action_point_overflow_schedule',
    )
    return runner


class LedgerFirstReadTests(unittest.TestCase):
    """账本记录足够新时必须直接采用，不再弹窗。"""

    def test_fresh_ledger_used_without_popup(self):
        ledger = ActionPointLedger()
        ledger.observe(123, 5408, source='map_bar', at=current_time())
        runner = make_runner(ledger=ledger)
        self.assertEqual(runner._get_current_action_point_for_overflow(), 123)
        # 完全没有发生任何设备交互
        self.assertEqual(runner.calls, [])
        # 实例属性按顶栏读数路径同一口径同步
        self.assertEqual(runner._action_point_current, 123)
        self.assertEqual(runner._action_point_total_with_box, 5408)
        self.assertEqual(runner._action_point_total, 5408)

    def test_expired_ledger_falls_back_to_live_read(self):
        ledger = ActionPointLedger()
        ledger.observe(100, 5000, at=current_time() - timedelta(minutes=45))
        runner = make_runner(ledger=ledger, live_current=88, live_total=5400)
        self.assertEqual(runner._get_current_action_point_for_overflow(), 88)
        self.assertEqual(runner.calls, ['enter', 'safe_get', 'quit'])

    def test_ledger_disabled_uses_live_read(self):
        runner = make_runner(ledger=None, ledger_enabled=False, live_current=66)
        self.assertEqual(runner._get_current_action_point_for_overflow(), 66)
        self.assertIn('safe_get', runner.calls)

    def test_ledger_without_timestamp_treated_as_expired(self):
        ledger = ActionPointLedger()
        ledger.observe(100, 5000)
        ledger.recorded_at = None
        runner = make_runner(ledger=ledger, live_current=77)
        self.assertEqual(runner._get_current_action_point_for_overflow(), 77)
        self.assertIn('enter', runner.calls)


class ReadFailureTests(unittest.TestCase):
    """读不到有效值时的行为：保持原排程，绝不写垃圾值。"""

    def test_failed_live_read_returns_none(self):
        runner = make_runner(ledger=None, ledger_enabled=False, live_ok=False)
        self.assertIsNone(runner._get_current_action_point_for_overflow())
        self.assertEqual(runner.calls, ['enter', 'safe_get', 'quit'])

    def test_schedule_update_keeps_schedule_when_read_fails(self):
        runner = make_runner(ledger=None, ledger_enabled=False, live_ok=False)
        runner.update_prevent_action_point_overflow_schedule()
        runner.config.task_delay.assert_not_called()

    def test_schedule_update_uses_fresh_ledger_value(self):
        ledger = ActionPointLedger()
        ledger.observe(130, 5408, source='map_bar', at=current_time())
        runner = make_runner(ledger=ledger)
        runner.update_prevent_action_point_overflow_schedule()
        runner.config.task_delay.assert_called_once()
        kwargs = runner.config.task_delay.call_args.kwargs
        self.assertEqual(kwargs['task'], TASK)
        # 上限 190 - 当前 130 = 60 点 × 600 秒 = 600 分钟
        self.assertAlmostEqual(kwargs['minute'], 600)


if __name__ == '__main__':
    unittest.main()
