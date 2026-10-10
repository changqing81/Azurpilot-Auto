"""低耗检测（LowCostChecker）回归测试。

覆盖：基线建立、下降累计、阈值边界、自然恢复忽略、滑动窗口淘汰、
跨任务抢占重新基线、无效读数与超过上限读数的过滤。
"""

import unittest
from datetime import datetime, timedelta

from module.campaign.low_cost import LowCostChecker


class LowCostCheckerTest(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 10, 10, 9, 0, 0)

    def test_first_read_only_baselines(self):
        """首次读数只建立基线，不判定、不累计。"""
        checker = LowCostChecker(3600, 800)
        self.assertFalse(checker.update(12000, self.t0))
        self.assertEqual(checker.consumed, 0)
        self.assertEqual(checker.last_oil, 12000)

    def test_accumulate_below_limit(self):
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(11500, self.t0 + timedelta(minutes=5)))
        self.assertFalse(checker.update(11400, self.t0 + timedelta(minutes=10)))
        self.assertEqual(checker.consumed, 600)

    def test_trigger_over_limit(self):
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(11600, self.t0 + timedelta(minutes=5)))
        self.assertTrue(checker.update(11100, self.t0 + timedelta(minutes=10)))
        self.assertEqual(checker.consumed, 900)

    def test_exactly_limit_not_triggered(self):
        """阈值判定为严格大于。"""
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(11200, self.t0 + timedelta(minutes=5)))

    def test_recovery_ignored(self):
        """石油自然恢复（上升）不计入，且基线随之抬高。"""
        checker = LowCostChecker(3600, 800)
        checker.update(10000, self.t0)
        self.assertFalse(checker.update(10050, self.t0 + timedelta(minutes=5)))
        self.assertEqual(checker.consumed, 0)
        self.assertFalse(checker.update(9700, self.t0 + timedelta(minutes=10)))
        self.assertEqual(checker.consumed, 350)

    def test_sliding_window_evicts_old_events(self):
        """滑出窗口的旧消耗事件被剔除。"""
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        checker.update(11500, self.t0 + timedelta(minutes=5))  # -500
        # 65 分钟后，-500 已滑出 1 小时窗口
        self.assertFalse(checker.update(11400, self.t0 + timedelta(minutes=70)))
        self.assertEqual(checker.consumed, 100)

    def test_cross_task_preemption_rebaselines(self):
        """任务被抢占后实例销毁，恢复时重新基线，不把缺口算作自己的消耗。"""
        first_session = LowCostChecker(3600, 800)
        first_session.update(12000, self.t0)

        # 抢占期间其它任务消耗了 3000 油；恢复时新建 checker
        resumed_session = LowCostChecker(3600, 800)
        self.assertFalse(resumed_session.update(9000, self.t0 + timedelta(minutes=20)))
        self.assertEqual(resumed_session.consumed, 0)

    def test_invalid_readings_ignored(self):
        """无效读数不污染基线，也不产生事件。"""
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        for bad in (None, 0, -5, 3.5, True, 'x'):
            self.assertFalse(checker.update(bad, self.t0 + timedelta(minutes=1)))
        self.assertEqual(checker.consumed, 0)
        self.assertEqual(checker.last_oil, 12000)

    def test_reading_above_cap_skipped(self):
        """超过石油上限的读数视为 OCR 误读，跳过且不更新基线。"""
        checker = LowCostChecker(3600, 800, oil_cap=25000)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(99000, self.t0 + timedelta(minutes=1)))
        self.assertEqual(checker.last_oil, 12000)
        self.assertFalse(checker.update(11500, self.t0 + timedelta(minutes=2)))
        self.assertEqual(checker.consumed, 500)

    def test_zero_cap_disables_check(self):
        """上限缺失/为 0 时不做上限校验。"""
        checker = LowCostChecker(3600, 800, oil_cap=0)
        self.assertIsNone(checker.oil_cap)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(11500, self.t0 + timedelta(minutes=1)))
        self.assertEqual(checker.consumed, 500)


if __name__ == '__main__':
    unittest.main()
