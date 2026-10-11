"""智能调度「买行动力模式」每月购买周数预算的离线行为验证。

覆盖三件事：
1. 预算判定：默认 0 不限、达上限拦截、当前周已计入预算则放行；
2. 记账：购买成功与 OCR 同步都计入本周、同周幂等、跨月自动清空；
3. 接线：预算用尽时买行动力模式入口直接让位，不进入弹窗同步。

时间锚点：2026-10 的四个周三分属 ISO 周 W41~W44，
2026-11-01（周日）仍属 W44——同一天 ISO 周跨月边界的天然样例。
"""
import copy
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from module.config.deep import deep_get, deep_set
from module.os.tasks.scheduling import CoinTaskMixin, OpsiScheduling


CONFIG_PATH_BUY_AP_MONTH_WEEKS = CoinTaskMixin.CONFIG_PATH_BUY_AP_MONTH_WEEKS
STATE_KEY_BUY_AP_MONTH_ID = CoinTaskMixin.STATE_KEY_BUY_AP_MONTH_ID
STATE_KEY_BUY_AP_MONTH_WEEKS = CoinTaskMixin.STATE_KEY_BUY_AP_MONTH_WEEKS


NOW_W42 = datetime(2026, 10, 14, 12, 0, 0)
NOW_W43 = datetime(2026, 10, 21, 12, 0, 0)
NOW_W44 = datetime(2026, 10, 28, 12, 0, 0)
# 11-01（周日）与 10-28 同属 2026-W44，但月份已跨到 11 月
NOW_NOV_SAME_WEEK = datetime(2026, 11, 1, 12, 0, 0)
NOW_NOV_W45 = datetime(2026, 11, 4, 12, 0, 0)
WEEK_W42 = '2026-W42'
WEEK_W43 = '2026-W43'


class Config:
    """最小配置替身：cross_get / cross_set / save + 简单属性键。"""

    def __init__(self):
        self.data = {
            'OpsiScheduling': {
                'Scheduler': {'Enable': True},
                'OpsiScheduling': {
                    'BuyActionPointMode': 'off',
                    'BuyActionPointMonthWeeks': 0,
                },
                'Storage': {'Storage': {}},
            },
        }
        self.modified = {}
        self.OpsiGeneral_BuyActionPointLimit = 5
        self.OpsiGeneral_OilLimit = 1000

    def cross_get(self, keys, default=None):
        return deep_get(self.data, keys, default)

    def cross_set(self, keys, value):
        deep_set(self.data, keys, copy.deepcopy(value))

    def save(self):
        for keys, value in self.modified.items():
            self.cross_set(keys, value)
        self.modified.clear()


class MonthBudgetTests(unittest.TestCase):
    """每月购买周数预算：判定、记账与接线。"""

    def setUp(self):
        self.now = NOW_W42
        for target, new in (
            ('module.os.tasks.scheduling.current_time', lambda: self.now),
            ('module.os.tasks.scheduling.server_time_offset', lambda: timedelta(0)),
        ):
            p = patch(target, new)
            p.start()
            self.addCleanup(p.stop)
        self.runner = OpsiScheduling.__new__(OpsiScheduling)
        self.runner.config = Config()
        # 磁盘兜底会读到开发机真实实例配置，统一 mock 掉保证确定性
        self.runner._read_disk_smart_scheduling_state = Mock(return_value={})
        self.runner.notify_push = Mock()

    def set_month_limit(self, weeks):
        self.runner.config.cross_set(CONFIG_PATH_BUY_AP_MONTH_WEEKS, weeks)

    def prefill_used_weeks(self, weeks, month_id='2026-10'):
        self.runner._set_smart_scheduling_state_value(
            STATE_KEY_BUY_AP_MONTH_ID, month_id)
        self.runner._set_smart_scheduling_state_value(
            STATE_KEY_BUY_AP_MONTH_WEEKS, list(weeks))

    def weeks_used(self):
        return self.runner._get_buy_action_point_month_weeks_used()

    # ==================== 预算判定 ====================

    def test_default_zero_is_unlimited(self):
        """默认 0 = 不限：任何周都放行。"""
        self.set_month_limit(0)
        for now in (NOW_W42, NOW_W43, NOW_W44, NOW_NOV_W45):
            self.now = now
            self.assertTrue(self.runner._is_buy_action_point_month_budget_available())

    def test_budget_blocks_when_weeks_exhausted(self):
        """limit=2 且已用 2 周，进入第 3 周后拦截。"""
        self.set_month_limit(2)
        self.runner._record_buy_action_point_month_week()
        self.now = NOW_W43
        self.runner._record_buy_action_point_month_week()
        self.now = NOW_W44
        self.assertFalse(self.runner._is_buy_action_point_month_budget_available())

    def test_current_week_already_counted_stays_available(self):
        """当前周已计入预算时放行：周内继续买满每周上限不重复占预算。"""
        self.set_month_limit(1)
        self.runner._record_buy_action_point_month_week()
        self.assertTrue(self.runner._is_buy_action_point_month_budget_available())
        self.now = NOW_W43
        self.assertFalse(self.runner._is_buy_action_point_month_budget_available())

    def test_mid_month_limit_decrease_blocks_immediately(self):
        """中途把 limit 调小，已用周数不清理，立即按 len>=limit 拦截。"""
        self.prefill_used_weeks([WEEK_W42, WEEK_W43])
        self.set_month_limit(1)
        self.now = NOW_W44
        self.assertFalse(self.runner._is_buy_action_point_month_budget_available())
        # 调大到 3 后第 3 周重新可用
        self.set_month_limit(3)
        self.assertTrue(self.runner._is_buy_action_point_month_budget_available())

    # ==================== 记账 ====================

    def test_record_is_idempotent_within_week(self):
        """同周多次购买只记一次。"""
        self.runner._record_buy_action_point_month_week()
        self.runner._record_buy_action_point_month_week()
        self.assertEqual(self.weeks_used(), [WEEK_W42])

    def test_record_actually_persists(self):
        """记账必须真正落盘。

        _get_..._weeks_used() 返回的是 config.data 内的活列表，若原地 append，
        _set_... 的「新旧相等就跳过」短路会命中同一对象而跳过 save，
        账本只停在内存里，进程重启即丢。
        """
        save = Mock(wraps=self.runner.config.save)
        self.runner.config.save = save
        self.runner._record_buy_action_point_month_week()
        self.assertTrue(save.called, '记账未触发落盘（被去重短路吞掉）')
        self.assertEqual(
            deep_get(
                self.runner.config.data,
                f'{self.runner.CONFIG_PATH_SMART_STATE}'
                f'.{STATE_KEY_BUY_AP_MONTH_WEEKS}',
            ),
            [WEEK_W42],
        )

    # ==================== 账本补记（冷启动盲区） ====================

    def test_backfill_records_week_when_local_count_positive(self):
        """本地周计数 > 0 即说明本周买过，补记进账本（覆盖账本上线前的购买）。"""
        self.runner._set_buy_action_point_count(5)
        self.runner._backfill_buy_action_point_month_week()
        self.assertEqual(self.weeks_used(), [WEEK_W42])

    def test_backfill_noop_when_local_count_zero(self):
        """本周没买过就不补记，避免凭空占用预算。"""
        self.runner._backfill_buy_action_point_month_week()
        self.assertEqual(self.weeks_used(), [])

    def test_count_month_refreshes_only_on_change(self):
        """计数所属月份只在计数真正变化时刷新（OCR 同步常重复写同值）。"""
        month_key = self.runner.STATE_KEY_BUY_AP_COUNT_MONTH
        self.now = NOW_W44
        self.runner._set_buy_action_point_count(5)
        self.assertEqual(
            self.runner._get_smart_scheduling_state_value(month_key), '2026-10')
        self.now = NOW_NOV_SAME_WEEK
        self.runner._set_buy_action_point_count(5)  # 同值重复写
        self.assertEqual(
            self.runner._get_smart_scheduling_state_value(month_key), '2026-10')

    def test_backfill_skips_straddling_week_inherited_from_prev_month(self):
        """跨月周（W44 = 10-26~11-01）：11-01 时本周计数继承自 10 月，不该记进 11 月。"""
        self.now = NOW_W44
        self.runner._set_buy_action_point_count(5)
        self.runner._record_buy_action_point_month_week()  # 10 月账本记 W44
        self.now = NOW_NOV_SAME_WEEK                        # 11-01，仍是 W44
        self.runner._backfill_buy_action_point_month_week()
        self.assertEqual(self.weeks_used(), [])

    def test_block_week_is_exactly_the_old_month_part_of_straddling_week(self):
        """几何事实：月末封锁区间 == 跨月 ISO 周的「上月那一段」。

        封锁区间 = [包含下月 1 号的 ISO 周的周一, 下月 1 号)，与跨月周的上月段重合；
        这是上面那条跨月保护的「正常路径兜底」依据，但封锁只是脚本侧策略、
        且依赖服务器时间，故跨月保护仍需保留。
        封锁周本身的完整护栏（含 31 号周二/1 号周三样例）见
        `tests/test_month_end_purchase_block.py`，此处只留与月度账本直接相关的一遍。
        """
        for year, month in ((2026, 11), (2026, 12), (2027, 1)):
            first = datetime(year, month, 1)
            monday = first - timedelta(days=first.weekday())
            for offset in range(7):
                day = monday + timedelta(days=offset)
                self.now = day.replace(hour=12)
                with patch('module.os_handler.action_point.current_time',
                           lambda: self.now), \
                        patch('module.os_handler.action_point.server_time_offset',
                              lambda: timedelta(0)):
                    in_block = self.runner._is_in_month_end_purchase_block_week()
                self.assertEqual(
                    in_block,
                    day.date() < first.date(),
                    f'{day.date()} 的封锁判定应等于「属于上月那一段」',
                )

    def test_backfill_records_when_bought_in_new_month_on_straddling_week(self):
        """跨月周（W44）里本月确实买了 → 记进本月账本（同一周可分别计入相邻两月）。"""
        self.now = NOW_W44
        self.runner._record_buy_action_point_month_week()  # 10 月账本记 W44
        self.now = NOW_NOV_SAME_WEEK                        # 11-01，仍是 W44
        self.runner._set_buy_action_point_count(4)          # 11-01 真的买了
        self.runner._backfill_buy_action_point_month_week()
        self.assertEqual(self.weeks_used(), ['2026-W44'])

    def test_entry_backfills_before_early_return_when_week_maxed(self):
        """本周已买满时入口提前返回，但该周必须先被补记进账本。"""
        self.runner._set_buy_action_point_count(5)  # 本周已买满
        self.runner._is_in_month_end_purchase_block_week = Mock(return_value=False)
        self.assertFalse(self.runner._run_buy_action_point_mode())
        self.assertEqual(self.weeks_used(), [WEEK_W42])

    def test_sync_short_circuit_still_records_week(self):
        """同步短路（本周已满）时不弹窗，但本周仍要计入账本。"""
        self.runner._set_buy_action_point_count(5)
        self.runner.action_point_enter = Mock()
        count = self.runner._sync_buy_action_point_count_with_game()
        self.assertEqual(count, 5)
        self.runner.action_point_enter.assert_not_called()
        self.assertEqual(self.weeks_used(), [WEEK_W42])

    def test_cross_month_clears_used_weeks(self):
        """跨月后周列表自动清空，预算重新可用。"""
        self.set_month_limit(1)
        self.runner._record_buy_action_point_month_week()
        self.now = NOW_NOV_W45
        self.assertEqual(self.weeks_used(), [])
        self.assertTrue(self.runner._is_buy_action_point_month_budget_available())

    def test_iso_week_spanning_month_boundary_counts_once_per_month(self):
        """ISO 周跨月边界：同一周 id 可分别计入相邻两个月，各自只记一次。"""
        self.runner._record_buy_action_point_month_week()  # 10 月的 W44
        self.now = NOW_NOV_SAME_WEEK  # 11-01 仍是 W44，但月份已跨月
        self.assertEqual(self.weeks_used(), [])
        self.runner._record_buy_action_point_month_week()
        self.assertEqual(self.weeks_used(), ['2026-W44'])

    def test_buy_one_action_point_success_records_week(self):
        """购买成功后本周计入预算，周计数同步递增。"""
        self.runner._is_in_month_end_purchase_block_week = Mock(return_value=False)
        self.runner.action_point_enter = Mock()
        self.runner.action_point_safe_get = Mock()
        self.runner.action_point_quit = Mock()
        self.runner.action_point_buy = Mock(return_value=True)
        self.assertTrue(self.runner._buy_one_action_point())
        self.assertEqual(self.weeks_used(), [WEEK_W42])
        self.assertEqual(self.runner._get_buy_action_point_count(), 1)

    def test_buy_one_action_point_blocked_when_budget_exhausted(self):
        """预算用尽时单次购买在打开弹窗前就被拦截。"""
        self.set_month_limit(1)
        self.prefill_used_weeks([WEEK_W43])  # 当前周 W42 不在列表，已用 1/1
        self.runner._is_in_month_end_purchase_block_week = Mock(return_value=False)
        self.runner.action_point_enter = Mock()
        self.runner.action_point_buy = Mock(return_value=True)
        self.assertFalse(self.runner._buy_one_action_point())
        self.runner.action_point_enter.assert_not_called()
        self.runner.action_point_buy.assert_not_called()

    def test_sync_counts_game_side_purchases_into_budget(self):
        """OCR 同步出游戏口径本周已购买（如用户手动购买）时，本周计入预算。"""
        self.runner.action_point_enter = Mock()
        self.runner.action_point_safe_get = Mock()
        self.runner.action_point_set_button = Mock()
        self.runner.action_point_quit = Mock()
        self.runner.action_point_get_buy_remain = Mock(return_value=3)
        self.runner._is_buy_action_point_ocr_valid = Mock(return_value=True)
        count = self.runner._sync_buy_action_point_count_with_game()
        self.assertEqual(count, 2)
        self.assertEqual(self.weeks_used(), [WEEK_W42])
        # 同周重复同步不重复记账
        self.runner._sync_buy_action_point_count_with_game()
        self.assertEqual(self.weeks_used(), [WEEK_W42])

    # ==================== 接线 ====================

    def test_entry_returns_false_without_popup_sync_when_budget_exhausted(self):
        """预算用尽时入口直接让位，不进入弹窗同步（纯日期计算前置）。"""
        self.set_month_limit(2)
        self.prefill_used_weeks([WEEK_W42, WEEK_W43])
        self.now = NOW_W44
        self.runner._is_in_month_end_purchase_block_week = Mock(return_value=False)
        self.runner._sync_buy_action_point_count_with_game = Mock()
        self.assertFalse(self.runner._run_buy_action_point_mode())
        self.runner._sync_buy_action_point_count_with_game.assert_not_called()

    def test_entry_proceeds_to_mode_loop_when_budget_available(self):
        """预算内（当前周已计入）时入口正常分发到模式主循环。"""
        self.set_month_limit(2)
        self.prefill_used_weeks([WEEK_W42])
        self.runner.config.cross_set('OpsiScheduling.OpsiScheduling.BuyActionPointMode',
                                     'hazard1_leveling')
        self.runner._is_in_month_end_purchase_block_week = Mock(return_value=False)
        self.runner._sync_buy_action_point_count_with_game = Mock()
        self.runner._run_buy_ap_hazard1_loop = Mock(return_value=True)
        self.assertTrue(self.runner._run_buy_action_point_mode())
        self.runner._sync_buy_action_point_count_with_game.assert_called_once()
        self.runner._run_buy_ap_hazard1_loop.assert_called_once_with(5)


if __name__ == '__main__':
    unittest.main()
