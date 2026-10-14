"""行动力开箱失败重试与「含箱充足不判不足」的回归测试（2026-10-14 真机事故）。

场景：USE 按钮因弹窗刷新动画/网络波动短暂消失，旧逻辑单帧检测失败即抛
ActionPointLimit，智能调度 except 分支直接拿异常携带的含箱总数（6477）
当 total 走「不足」轨道——推送「总行动力 6477 低于最低保留 200」并把
智能调度推迟到次日。修复后三层防线：
1. action_point_use 点击前等待重试（AP_USE_RETRY_SECONDS），
   未点击就不存在未确认状态，不违反单发原则；
2. except 统一出口 _handle_ap_limit_exception：
   含箱总数 > 保留值 → 30 分钟重跑，不推送不足告警、不推迟到次日；
3. _delay_smart_scheduling_for_ap_limit 推迟前复核回退账本最近记录
   （顶栏读不到时不再静默放弃复核）。
"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.os_handler.action_point import ActionPointHandler, ActionPointLimit
from module.os.tasks.scheduling import OpsiScheduling

ROOT = Path(__file__).parent.parent


class _FakeTimer:
    """可编程 reached() 序列的假 Timer，避免真实时间等待。"""

    def __init__(self, reached_sequence):
        self._sequence = list(reached_sequence)
        self._calls = 0

    def start(self):
        return self

    def reached(self):
        if self._calls < len(self._sequence):
            result = self._sequence[self._calls]
        else:
            result = self._sequence[-1] if self._sequence else True
        self._calls += 1
        return result


def make_handler():
    """构造 ActionPointHandler 最小桩：真实弹窗确认链，不连游戏。"""
    handler = ActionPointHandler.__new__(ActionPointHandler)
    handler.config = SimpleNamespace()
    handler.device = SimpleNamespace(
        image='img',
        click=Mock(),
        sleep=Mock(),
        screenshot=Mock(),
        click_record_remove=Mock(),
    )
    handler._action_point_current = 97
    handler._action_point_total = 6477
    # 库存[1] = 3 瓶（与 2026-10-14 日志「库存[1] 4->3」同场景）
    handler._action_point_box = [0, 3, 0, 0]
    return handler


class TestUseButtonRetry(unittest.TestCase):
    """action_point_use 点击前 USE 按钮消失时的重试行为。"""

    def test_button_reappears_within_retry_window(self):
        # 前 2 帧按钮消失（动画/网络波动），第 3 帧恢复 → 正常点击并确认
        handler = make_handler()
        appear_results = [False, False, True]
        with (
            patch.object(ActionPointHandler, 'AP_USE_RETRY_SECONDS', 5),
            patch('module.os_handler.action_point.Timer',
                  return_value=_FakeTimer([False, False])),
            patch.object(ActionPointHandler, 'appear',
                         side_effect=appear_results),
            patch.object(ActionPointHandler, 'handle_popup_confirm',
                         return_value=False),
            patch.object(ActionPointHandler, 'is_current_ap_visible',
                         return_value=True),
            patch.object(ActionPointHandler, 'action_point_update',
                         side_effect=lambda: (
                             setattr(handler, '_action_point_current', 117),
                             setattr(handler, '_action_point_box', [0, 2, 0, 0]),
                         )),
        ):
            result = handler.action_point_use(selected_index=1)
        self.assertTrue(result)
        handler.device.click.assert_called_once()

    def test_button_missing_until_timeout_raises_limit(self):
        # 重试窗口内按钮始终未出现 → 仍抛 ActionPointLimit（延后重跑语义不变）
        handler = make_handler()
        with (
            patch.object(ActionPointHandler, 'AP_USE_RETRY_SECONDS', 5),
            patch('module.os_handler.action_point.Timer',
                  return_value=_FakeTimer([True])),
            patch.object(ActionPointHandler, 'appear', return_value=False),
        ):
            with self.assertRaises(ActionPointLimit):
                handler.action_point_use(selected_index=1)
        # 未点击（单发原则：重试期间不产生实际点击）
        handler.device.click.assert_not_called()

    def test_retry_actually_refreshes_frames(self):
        # 重试窗口内至少刷新过一帧截图（否则是拿旧帧空转）
        handler = make_handler()
        with (
            patch.object(ActionPointHandler, 'AP_USE_RETRY_SECONDS', 5),
            patch('module.os_handler.action_point.Timer',
                  return_value=_FakeTimer([False, True])),
            patch.object(ActionPointHandler, 'appear', return_value=False),
        ):
            with self.assertRaises(ActionPointLimit):
                handler.action_point_use(selected_index=1)
        handler.device.screenshot.assert_called_once()


def make_recheck_scheduler(ledger_total_with_box=6477):
    """构造 OpsiScheduling 最小桩：保留真实 _delay_smart_scheduling_for_ap_limit。

    只 stub 复核依赖与延迟/告警出口——被测方法本身绝不 stub。
    """
    scheduler = OpsiScheduling.__new__(OpsiScheduling)
    scheduler.config = SimpleNamespace(task_stop=Mock(), task_delay=Mock())
    scheduler._ap_ledger_enabled = Mock(return_value=True)
    scheduler.ap_observe_from_map_bar = Mock(return_value=None)
    scheduler._get_ap_ledger = Mock(
        return_value=SimpleNamespace(total_with_box=ledger_total_with_box))
    scheduler._notify_ap_insufficient = Mock()
    scheduler._delay_smart_scheduling_to_server_update = Mock()
    return scheduler


def make_scheduler(ledger_total_with_box=6477):
    """构造 OpsiScheduling 最小桩：只接延迟/告警出口，不连游戏。"""
    scheduler = make_recheck_scheduler(ledger_total_with_box)
    scheduler._delay_smart_scheduling_with_minutes = Mock()
    scheduler._delay_smart_scheduling_for_ap_limit = Mock()
    return scheduler


class TestApLimitExceptionUnifiedExit(unittest.TestCase):
    """except ActionPointLimit 统一出口：含箱充足走短延迟重跑。"""

    def test_sufficient_total_uses_short_retry(self):
        # 2026-10-14 事故场景：total=6477 > 保留 200 → 5 分钟重跑，不判不足
        scheduler = make_scheduler()
        e = ActionPointLimit(current=117, total=6477)
        scheduler._handle_ap_limit_exception(e, total_ap=6477, cl1_ap_preserve=200)
        scheduler._delay_smart_scheduling_with_minutes.assert_called_once_with(
            '开箱动作未成功（含箱行动力充足）', 5)
        scheduler._delay_smart_scheduling_for_ap_limit.assert_not_called()
        scheduler.config.task_stop.assert_called_once()

    def test_truly_insufficient_keeps_server_update_delay(self):
        # 真不足（total=117 <= 200）→ 保持「延迟到服务器刷新」轨道
        scheduler = make_scheduler()
        e = ActionPointLimit(current=117, total=117)
        scheduler._handle_ap_limit_exception(e, total_ap=117, cl1_ap_preserve=200)
        scheduler._delay_smart_scheduling_for_ap_limit.assert_called_once_with(117, 200)
        scheduler._delay_smart_scheduling_with_minutes.assert_not_called()

    def test_preserve_from_exception_wins(self):
        # 「达到保留上限」路径抛出的异常自带 preserve（如 1000），优先于默认 200
        scheduler = make_scheduler()
        e = ActionPointLimit(current=500, total=500, preserve=1000)
        scheduler._handle_ap_limit_exception(e, total_ap=500, cl1_ap_preserve=200)
        scheduler._delay_smart_scheduling_for_ap_limit.assert_called_once_with(500, 1000)

    def test_zero_total_falls_back_to_decision_read(self):
        # 异常未携带有效数值 → 回退本轮决策读数（6477），仍走短延迟重跑
        scheduler = make_scheduler()
        e = ActionPointLimit(current=0, total=0)
        scheduler._handle_ap_limit_exception(e, total_ap=6477, cl1_ap_preserve=200)
        scheduler._delay_smart_scheduling_with_minutes.assert_called_once()
        scheduler._delay_smart_scheduling_for_ap_limit.assert_not_called()


class TestRecheckFallsBackToLedger(unittest.TestCase):
    """推迟前复核：顶栏读不到时回退账本最近记录，不再静默放弃。"""

    def test_ledger_sufficient_aborts_delay(self):
        # 弹窗流程中顶栏不可读，但账本刚记录含箱 6477 > 200 → 放弃推迟
        scheduler = make_recheck_scheduler(ledger_total_with_box=6477)
        scheduler._delay_smart_scheduling_for_ap_limit(6477, 200)
        scheduler._delay_smart_scheduling_to_server_update.assert_not_called()
        scheduler._notify_ap_insufficient.assert_not_called()
        scheduler.config.task_stop.assert_not_called()

    def test_ledger_insufficient_proceeds(self):
        # 账本含箱 100 <= 200 → 真不足，照常告警 + 延迟到服务器刷新
        scheduler = make_recheck_scheduler(ledger_total_with_box=100)
        scheduler._delay_smart_scheduling_for_ap_limit(100, 200)
        scheduler._notify_ap_insufficient.assert_called_once_with(100, 200)
        scheduler._delay_smart_scheduling_to_server_update.assert_called_once()
        scheduler.config.task_stop.assert_called_once()

    def test_ledger_disabled_keeps_old_path(self):
        # 账本关闭 → 无复核，保持旧行为直接延迟
        scheduler = make_recheck_scheduler(ledger_total_with_box=6477)
        scheduler._ap_ledger_enabled.return_value = False
        scheduler._delay_smart_scheduling_for_ap_limit(6477, 200)
        scheduler._delay_smart_scheduling_to_server_update.assert_called_once()

    def test_ledger_without_total_proceeds(self):
        # 账本无含箱总数（从没弹过窗）→ 无法复核，保持旧行为延迟
        scheduler = make_recheck_scheduler(ledger_total_with_box=None)
        scheduler._delay_smart_scheduling_for_ap_limit(6477, 200)
        scheduler._delay_smart_scheduling_to_server_update.assert_called_once()


class TestSourceLock(unittest.TestCase):
    """源码锁：防止重试结构/统一出口被误删回归（同 EXP_INFO 节流测试思路）。"""

    def test_use_retry_structure_exists(self):
        src = (ROOT / 'module/os_handler/action_point.py').read_text(encoding='utf-8')
        self.assertIn('AP_USE_RETRY_SECONDS', src)
        self.assertIn('timeout.reached()', src)
        self.assertIn('USE 按钮未出现（已重试', src)

    def test_except_uses_unified_handler(self):
        src = (ROOT / 'module/os/tasks/scheduling.py').read_text(encoding='utf-8')
        self.assertIn('self._handle_ap_limit_exception(e, total_ap, cl1_ap_preserve)', src)
        self.assertIn('AP_RETRY_DELAY_MINUTES', src)
        # 统一出口必须先判「含箱充足」再走不足轨道
        self.assertIn('if current > preserve:', src)

    def test_recheck_fallback_exists(self):
        src = (ROOT / 'module/os/tasks/scheduling.py').read_text(encoding='utf-8')
        # 复核不再以「顶栏读数成功」为前提：先读顶栏（刷新账本），再恒取账本复核
        self.assertIn('self.ap_observe_from_map_bar()', src)
        self.assertIn("getattr(ledger, 'total_with_box', None)", src)


if __name__ == '__main__':
    unittest.main()
