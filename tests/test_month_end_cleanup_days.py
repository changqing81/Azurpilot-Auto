"""
月末清理行动力天数（MonthEndActionPointCleanupDays）新语义的回归测试。

新语义（2026-10-25 起）：
- -1 = 禁用；
- 0 = 大世界重置当天（最后一天）清理；
- N >= 1 = 距重置剩余天数 <= N 时清理（1 = 倒数第二天起）。
旧语义中 0 表示禁用，已废弃。
"""
import unittest
from unittest.mock import MagicMock, patch

from module.os.tasks.scheduling import CoinTaskMixin, OpsiScheduling


def make_mixin(days_value):
    obj = OpsiScheduling.__new__(OpsiScheduling)
    obj.config = MagicMock()
    # _is_month_end_cleanup_active 里开关读取走 _config_enabled → cross_get，
    # 用 side_effect 按路径分派，避免同一个 mock 把开关读成天数
    def cross_get(keys, default=None, **kwargs):
        if keys == CoinTaskMixin.CONFIG_PATH_MONTH_END_CLEANUP_DAYS:
            return days_value
        if keys == CoinTaskMixin.CONFIG_PATH_MONTH_END_CLEANUP_ENABLE:
            return True
        return default
    obj.config.cross_get = MagicMock(side_effect=cross_get)
    return obj


class TestMonthEndCleanupDays(unittest.TestCase):

    def test_days_parsing(self):
        for raw, want in [(-1, -1), (0, 0), (1, 1), (3, 3), ('2', 2),
                          (-5, -1), ('abc', 0), (None, 0)]:
            with self.subTest(raw=raw):
                obj = make_mixin(raw)
                self.assertEqual(obj._get_month_end_cleanup_days(), want)

    @patch('module.os.tasks.scheduling.get_os_reset_remain', return_value=0)
    def test_zero_means_cleanup_on_reset_day(self, _):
        # 0 不再是禁用：重置当天（remain=0）要触发
        obj = make_mixin(0)
        self.assertTrue(obj._is_month_end_cleanup_active())

    @patch('module.os.tasks.scheduling.get_os_reset_remain', return_value=1)
    def test_one_starts_day_before_reset(self, _):
        # 1 = 倒数第二天（remain=1）已触发
        obj = make_mixin(1)
        self.assertTrue(obj._is_month_end_cleanup_active())

    @patch('module.os.tasks.scheduling.get_os_reset_remain', return_value=1)
    def test_zero_not_active_before_reset_day(self, _):
        # 0 = 只在最后一天：remain=1（倒数第二天）不触发
        obj = make_mixin(0)
        self.assertFalse(obj._is_month_end_cleanup_active())

    @patch('module.os.tasks.scheduling.get_os_reset_remain', return_value=0)
    def test_minus_one_disables(self, _):
        # -1 = 禁用：即使重置当天也不触发
        obj = make_mixin(-1)
        self.assertFalse(obj._is_month_end_cleanup_active())


if __name__ == '__main__':
    unittest.main()
