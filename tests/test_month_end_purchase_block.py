"""月末封锁周（跨月购买封锁期）的回归护栏。

封锁定义：从「包含下月 1 号的 ISO 周的周一 0 点」到「下月 1 号 0 点」，
进入新月后购买立即可用（`ActionPointHandler._is_in_month_end_purchase_block_week`）。

用户样例：31 号为周二、1 号为周三 → 周一(30)、周二(31) 都封锁，周三(1) 起恢复。

这是买行动力 / 行动点补充的前置前提，任何改动都不得绕过：
- 封锁周内模式入口直接让位（不弹窗同步、不记月度账本、不判周数预算）；
- 封锁周内单次购买在打开弹窗前就被拦下；
- `action_point_buy` 内部同样跳过点击。
"""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.os.tasks.scheduling import OpsiScheduling


def is_blocked(day):
    """在指定服务器时刻调用封锁判定（patch 掉 action_point 模块内的时钟）。"""
    with patch('module.os_handler.action_point.current_time', lambda: day), \
            patch('module.os_handler.action_point.server_time_offset',
                  lambda: timedelta(0)):
        return OpsiScheduling._is_in_month_end_purchase_block_week()


class MonthEndPurchaseBlockWeekTests(unittest.TestCase):
    """封锁区间 = [包含下月 1 号的 ISO 周的周一, 下月 1 号)。"""

    def assert_blocked(self, date_str, expected):
        day = datetime.strptime(date_str, '%Y-%m-%d').replace(hour=12)
        self.assertEqual(is_blocked(day), expected, f'{date_str} 封锁判定不符')

    def test_user_example_31st_tuesday_1st_wednesday(self):
        """用户样例：31 号周二、1 号周三 → 周一(30)、周二(31) 封锁，周三(1) 起可买。"""
        # 2027-08-31 周二 / 2027-09-01 周三
        self.assert_blocked('2027-08-29', False)  # 周日，上周
        self.assert_blocked('2027-08-30', True)   # 周一
        self.assert_blocked('2027-08-31', True)   # 周二（含）
        self.assert_blocked('2027-09-01', False)  # 周三，新月起恢复
        # 2026-03-31 周二 / 2026-04-01 周三
        self.assert_blocked('2026-03-30', True)
        self.assert_blocked('2026-03-31', True)
        self.assert_blocked('2026-04-01', False)

    def test_single_day_block_when_31st_is_monday(self):
        """31 号周一、1 号周二 → 只封锁 31 号一天（函数 docstring 的样例）。"""
        # 2026-08-31 周一 / 2026-09-01 周二
        self.assert_blocked('2026-08-30', False)
        self.assert_blocked('2026-08-31', True)
        self.assert_blocked('2026-09-01', False)

    def test_no_block_when_1st_is_monday(self):
        """1 号就是周一 → 跨月周无「上月段」，不封锁（封锁区间为空）。"""
        # 2027-02-01 周一
        self.assert_blocked('2027-01-31', False)
        self.assert_blocked('2027-02-01', False)

    def test_whole_week_block_when_1st_is_sunday(self):
        """1 号周日 → 上月那一段是整周 6 天，全封锁。"""
        # 2026-11-01 周日 → 封锁 2026-10-26 ~ 2026-10-31
        self.assert_blocked('2026-10-25', False)  # 周日，上周
        for day in range(26, 32):
            self.assert_blocked(f'2026-10-{day}', True)
        self.assert_blocked('2026-11-01', False)

    def test_block_equals_old_month_part_of_straddling_week(self):
        """对所有月份边界：封锁集合 == 跨月 ISO 周里属于上个月的那些天。"""
        for year in (2026, 2027, 2028):
            for month in range(1, 13):
                first = datetime(year, month, 1)
                monday = first - timedelta(days=first.weekday())
                for offset in range(7):
                    day = monday + timedelta(days=offset)
                    self.assert_blocked(
                        day.strftime('%Y-%m-%d'),
                        day.date() < first.date(),
                    )


class BlockWeekWiringTests(unittest.TestCase):
    """封锁周内必须整体短路：不弹窗、不记账、不碰周数预算。"""

    def make_runner(self, blocked):
        runner = OpsiScheduling.__new__(OpsiScheduling)
        runner.config = SimpleNamespace(OpsiGeneral_BuyActionPointLimit=5)
        runner._is_in_month_end_purchase_block_week = Mock(return_value=blocked)
        runner._backfill_buy_action_point_month_week = Mock()
        runner._is_buy_action_point_month_budget_available = Mock(return_value=True)
        runner._sync_buy_action_point_count_with_game = Mock()
        runner._clear_smart_scheduling_state_value = Mock()
        runner.action_point_enter = Mock()
        runner.action_point_safe_get = Mock()
        runner.action_point_quit = Mock()
        runner.action_point_buy = Mock(return_value=True)
        runner._get_buy_action_point_count = Mock(return_value=0)
        return runner

    def test_mode_entry_short_circuits_in_block_week(self):
        runner = self.make_runner(blocked=True)
        self.assertFalse(runner._run_buy_action_point_mode())
        runner._backfill_buy_action_point_month_week.assert_not_called()
        runner._is_buy_action_point_month_budget_available.assert_not_called()
        runner._sync_buy_action_point_count_with_game.assert_not_called()

    def test_buy_one_action_point_short_circuits_in_block_week(self):
        runner = self.make_runner(blocked=True)
        self.assertFalse(runner._buy_one_action_point())
        runner.action_point_enter.assert_not_called()
        runner.action_point_buy.assert_not_called()
        runner._backfill_buy_action_point_month_week.assert_not_called()

    def test_non_block_week_still_proceeds(self):
        """反向护栏：非封锁周不能被误拦。"""
        runner = self.make_runner(blocked=False)
        runner.config = SimpleNamespace(OpsiGeneral_BuyActionPointLimit=5)
        runner._is_buy_action_point_hazard1_mode = Mock(return_value=False)
        runner._is_buy_action_point_meowfficer_mode = Mock(return_value=False)
        self.assertFalse(runner._run_buy_action_point_mode())
        runner._sync_buy_action_point_count_with_game.assert_called_once()


if __name__ == '__main__':
    unittest.main()
