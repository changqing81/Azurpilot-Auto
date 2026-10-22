"""后宅每日任务喂食的回归测试。

覆盖：最低等食物优先、缺货逐级回退、饱食度装不下时跳过、
每日只喂一次的记录判定、跨服务器刷新的重置，以及
「今天喂了 2000、明天 1000 补货后必须重新喂 1000」这条关键回归。
"""

import unittest
from datetime import datetime

from module.dorm.daily_mission import (
    FOOD_FEED_AMOUNT,
    is_done_today,
    select_food_index,
)


class SelectFoodIndexTest(unittest.TestCase):
    def test_prefers_lowest_grade(self):
        """1000 有货时永远优先选 1000。"""
        self.assertEqual(select_food_index([5, 5, 5, 5, 5, 5], 20000), 0)

    def test_fallback_when_lowest_empty(self):
        """1000 缺货时回退到 2000。"""
        self.assertEqual(select_food_index([0, 3, 0, 0, 0, 0], 20000), 1)

    def test_fallback_to_highest_grade(self):
        """只有 20000 有货时回退到 20000，保证每日任务能完成。"""
        self.assertEqual(select_food_index([0, 0, 0, 0, 0, 2], 20000), 5)

    def test_all_empty_returns_none(self):
        """所有等级都缺货时返回 None，交给调用方记录警告并跳过。"""
        self.assertIsNone(select_food_index([0, 0, 0, 0, 0, 0], 20000))

    def test_short_amount_list(self):
        """库存列表长度不足时按缺货处理，不越界。"""
        self.assertIsNone(select_food_index([], 20000))
        self.assertEqual(select_food_index([0, 1], 20000), 1)

    def test_skip_when_fill_too_small(self):
        """剩余饱食度装不下一份时不能选该等级，避免点了没反应却记为已完成。"""
        # 只剩 300 饱食度：1000 与 2000 都装不下，返回 None
        self.assertIsNone(select_food_index([5, 5, 0, 0, 0, 0], 300))
        # 剩下 1500：装得下 1000，选 1000
        self.assertEqual(select_food_index([5, 5, 0, 0, 0, 0], 1500), 0)
        # 剩下 1500 但 1000 缺货：2000 装不下，返回 None
        self.assertIsNone(select_food_index([0, 5, 0, 0, 0, 0], 1500))

    def test_exactly_enough_fill(self):
        """饱食度刚好等于一份时允许投喂。"""
        self.assertEqual(select_food_index([1, 0, 0, 0, 0, 0], 1000), 0)

    def test_rechoose_after_restock(self):
        """关键回归：今天 1000 缺货喂了 2000，明天 1000 补货后必须重新喂 1000。

        选择函数只依赖当前库存，不保留任何历史选择，
        因此不会出现「第二天继续喂 2000」的问题。
        """
        today = select_food_index([0, 3, 0, 0, 0, 0], 20000)
        self.assertEqual(today, 1, '今天 1000 缺货应回退到 2000')

        tomorrow = select_food_index([4, 3, 0, 0, 0, 0], 20000)
        self.assertEqual(tomorrow, 0, '第二天 1000 补货后必须重新选 1000')

    def test_food_amount_order(self):
        """食物等级必须是 1000 -> 20000 的升序，下标与游戏内按钮顺序一致。"""
        self.assertEqual(FOOD_FEED_AMOUNT, [1000, 2000, 3000, 5000, 10000, 20000])


class IsDoneTodayTest(unittest.TestCase):
    def setUp(self):
        # 服务器 00:00 刷新，用 10 月 22 日与 23 日两天做样本
        self.day1_update = datetime(2026, 10, 22, 0, 0, 0)
        self.day2_update = datetime(2026, 10, 23, 0, 0, 0)

    def test_done_when_record_equals_update(self):
        """记录等于当前的上一次刷新时间，说明今天已经喂过。"""
        self.assertTrue(is_done_today(self.day1_update, self.day1_update))

    def test_done_when_record_after_update(self):
        """记录晚于上一次刷新时间，同样是今天已经喂过。"""
        record = datetime(2026, 10, 22, 9, 30, 0)
        self.assertTrue(is_done_today(record, self.day1_update))

    def test_not_done_after_cross_day(self):
        """跨天后记录早于新的刷新时间，允许再次喂食。"""
        record = datetime(2026, 10, 22, 9, 30, 0)
        self.assertFalse(is_done_today(record, self.day2_update))

    def test_not_done_for_initial_record(self):
        """初始记录（2020 年）一定允许喂食。"""
        record = datetime(2020, 1, 1, 0, 0, 0)
        self.assertFalse(is_done_today(record, self.day1_update))

    def test_not_done_for_invalid_record(self):
        """脏数据按未完成处理，宁可多喂一次也不漏掉每日任务。"""
        self.assertFalse(is_done_today('2026-10-22 09:30:00', self.day1_update))
        self.assertFalse(is_done_today(None, self.day1_update))

    def test_midnight_boundary(self):
        """23:59 喂食、00:01 刷新后应识别为跨天。

        记录写的是「喂食当时的上一次刷新时间」（10-22 00:00），
        00:01 之后的上一次刷新时间变成 10-23 00:00，记录早于它，判定为未完成。
        """
        record = datetime(2026, 10, 22, 0, 0, 0)  # 23:59 喂食时写入的记录
        last_update_before = datetime(2026, 10, 22, 0, 0, 0)  # 23:59 时的上一次刷新
        last_update_after = datetime(2026, 10, 23, 0, 0, 0)  # 00:01 时的上一次刷新

        self.assertTrue(is_done_today(record, last_update_before), '23:59 当天不应重复喂')
        self.assertFalse(is_done_today(record, last_update_after), '00:01 之后应允许再次喂食')


if __name__ == '__main__':
    unittest.main()
