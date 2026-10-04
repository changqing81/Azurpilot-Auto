"""买行动力模式的箱子禁用与购满免查策略的离线行为验证。

覆盖三件事：
1. 买行动力模式期间（_os_ap_box_forbidden=True）handle_action_point
   不再消耗行动力箱子，缺口抛 ActionPointLimit 交回主循环做中央购买；
2. action_point_buy 在本地持久化计数已确认购满时，不再点击石油查看
   剩余购买次数（多余交互），直接按已达上限处理；
3. _run_buy_action_point_mode 进入模式时设置禁用标志，退出（含异常）时清除。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from module.os.tasks.scheduling import OpsiScheduling
from module.os_handler.action_point import ActionPointHandler, ActionPointLimit


def make_action_point_runner(buy_limit=0, current=105, total=1375, boxes=None):
    """构造只含 handle_action_point / action_point_buy 所需成员的替身实例。"""
    runner = ActionPointHandler.__new__(ActionPointHandler)
    runner.config = SimpleNamespace(
        OS_ACTION_POINT_PRESERVE=0,
        OpsiGeneral_BuyActionPointLimit=buy_limit,
    )
    runner._action_point_current = current
    runner._action_point_total = total
    runner._action_point_box = [8654, 6, 0, 11] if boxes is None else boxes
    runner._ap_use_blocked = False
    runner._is_in_action_point = Mock(return_value=True)
    runner._load_ap_use_pending = Mock(return_value=None)
    runner.action_point_safe_get = Mock()
    runner.action_point_quit = Mock()
    runner.action_point_set_button = Mock(return_value=True)
    runner.action_point_use = Mock(
        side_effect=lambda selected_index: setattr(
            runner, '_action_point_current', current + 20 * selected_index))
    runner.action_point_get_buy_remain = Mock(return_value=2)
    return runner


class HandleActionPointBoxForbiddenTests(unittest.TestCase):
    """买行动力模式期间禁止使用箱子，缺口交回中央购买。"""

    def test_box_forbidden_raises_instead_of_using_box(self):
        runner = make_action_point_runner()
        runner._os_ap_box_forbidden = True
        with self.assertRaises(ActionPointLimit) as ctx:
            runner.handle_action_point(zone=None, pinned=None, cost=120)
        self.assertEqual(ctx.exception.current, 105)
        self.assertEqual(ctx.exception.total, 1375)
        self.assertEqual(ctx.exception.cost, 120)
        runner.action_point_set_button.assert_not_called()
        runner.action_point_use.assert_not_called()
        runner.action_point_quit.assert_called()

    def test_box_allowed_by_default_keeps_existing_behavior(self):
        runner = make_action_point_runner()
        # 无禁用标志时保持原行为：排序后先上 20 箱（105+20=125 不顶破 200 上限）
        self.assertTrue(runner.handle_action_point(zone=None, pinned=None, cost=120))
        runner.action_point_set_button.assert_called_once_with(1)
        runner.action_point_use.assert_called_once_with(selected_index=1)

    def test_box_forbidden_skips_check_when_ap_already_enough(self):
        runner = make_action_point_runner(current=125)
        runner._os_ap_box_forbidden = True
        self.assertTrue(runner.handle_action_point(zone=None, pinned=None, cost=120))
        runner.action_point_use.assert_not_called()


class ActionPointBuyStoredCountTests(unittest.TestCase):
    """本地持久化计数已购满时，不再点击石油查看剩余购买次数。"""

    def test_skip_clicking_oil_when_weekly_purchase_completed(self):
        runner = make_action_point_runner(buy_limit=5)
        runner._get_buy_action_point_count = Mock(return_value=5)
        self.assertFalse(runner.action_point_buy(preserve=1000))
        runner.action_point_set_button.assert_not_called()
        runner.action_point_get_buy_remain.assert_not_called()

    def test_skip_clicking_oil_when_user_limit_reached(self):
        runner = make_action_point_runner(buy_limit=3)
        runner._get_buy_action_point_count = Mock(return_value=3)
        self.assertFalse(runner.action_point_buy(preserve=1000))
        runner.action_point_set_button.assert_not_called()

    def test_proceeds_to_oil_check_when_stored_count_below_limit(self):
        runner = make_action_point_runner(buy_limit=5)
        runner._get_buy_action_point_count = Mock(return_value=3)
        self.assertTrue(runner.action_point_buy(preserve=1000))
        runner.action_point_set_button.assert_called_once_with(0)
        runner.action_point_use.assert_called_once_with(selected_index=0)

    def test_proceeds_without_counter_method(self):
        # 非智能调度实例没有计数器方法，保持原 OCR 核对流程
        runner = make_action_point_runner(buy_limit=5)
        self.assertTrue(runner.action_point_buy(preserve=1000))
        runner.action_point_set_button.assert_called_once_with(0)
        runner.action_point_use.assert_called_once_with(selected_index=0)


class BuyActionPointModeBoxFlagTests(unittest.TestCase):
    """买行动力模式入口设置禁用标志，退出（含异常）时清除。"""

    def make_mode_runner(self, loop_side_effect):
        runner = OpsiScheduling.__new__(OpsiScheduling)
        runner.config = SimpleNamespace(OpsiGeneral_BuyActionPointLimit=5)
        runner._is_in_month_end_purchase_block_week = Mock(return_value=False)
        runner._is_buy_action_point_month_budget_available = Mock(return_value=True)
        runner._sync_buy_action_point_count_with_game = Mock()
        runner._get_smart_scheduling_state_value = Mock(return_value=None)
        runner._clear_smart_scheduling_state_value = Mock()
        runner._get_buy_action_point_count = Mock(return_value=0)
        runner._is_buy_action_point_hazard1_mode = Mock(return_value=False)
        runner._is_buy_action_point_meowfficer_mode = Mock(return_value=True)
        runner._run_buy_ap_hazard1_loop = Mock()
        runner._run_buy_ap_meowfficer_loop = Mock(side_effect=loop_side_effect)
        return runner

    def test_flag_set_inside_mode_and_cleared_after(self):
        seen = {}

        def loop(buy_limit):
            seen['flag_inside'] = getattr(self.runner, '_os_ap_box_forbidden', False)
            return False

        self.runner = self.make_mode_runner(loop)
        self.assertFalse(self.runner._run_buy_action_point_mode())
        self.assertTrue(seen['flag_inside'])
        self.assertFalse(getattr(self.runner, '_os_ap_box_forbidden', False))

    def test_flag_cleared_when_mode_loop_raises(self):
        self.runner = self.make_mode_runner(RuntimeError('boom'))
        with self.assertRaises(RuntimeError):
            self.runner._run_buy_action_point_mode()
        self.assertFalse(getattr(self.runner, '_os_ap_box_forbidden', False))


if __name__ == '__main__':
    unittest.main()
