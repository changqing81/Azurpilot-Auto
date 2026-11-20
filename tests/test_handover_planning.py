"""作战委托与出击时间窗协同的回归测试。

移植自上游 tests/test_handover_planning.py 的核心用例，改写为标准库 unittest
（本项目不使用 pytest），只覆盖不依赖游戏的纯函数与配置读取逻辑：

- planning.next_sortie：找出最近一次已启用的出击任务
- planning.fit_sortie_window：按下次出击时间限制本批委托次数
- planning.daily_runs：只统计服务器刷新后、已确认启动的批次
- OperationHandover.handover_time_deficit：额度只需覆盖最后一次开始前的消耗
"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from module.handover.planning import daily_runs, fit_sortie_window, next_sortie
from module.handover.handover import OperationHandover


def make_config(**kwargs):
    """构造一个只带 data/Scheduler 的最小 config 替身。"""
    data = {}
    for name, value in kwargs.items():
        data[name] = {'Scheduler': value}
    return SimpleNamespace(data=data)


class TestNextSortie(unittest.TestCase):
    def test_picks_earliest_enabled_sortie(self):
        config = make_config(
            Main={'Enable': True, 'NextRun': datetime(2026, 11, 20, 12, 0)},
            Event={'Enable': True, 'NextRun': datetime(2026, 11, 20, 9, 0)},
        )
        name, target = next_sortie(config)
        self.assertEqual(name, 'Event')
        self.assertEqual(target, datetime(2026, 11, 20, 9, 0))

    def test_disabled_sorties_are_ignored(self):
        """禁用的出击任务不应挡住空闲的委托。"""
        config = make_config(
            Main={'Enable': False, 'NextRun': datetime(2026, 11, 20, 9, 0)},
            Event={'Enable': True, 'NextRun': datetime(2026, 11, 20, 12, 0)},
        )
        name, _ = next_sortie(config)
        self.assertEqual(name, 'Event')

    def test_returns_none_when_nothing_enabled(self):
        config = make_config(Main={'Enable': False, 'NextRun': datetime(2026, 11, 20, 12, 0)})
        self.assertIsNone(next_sortie(config))

    def test_invalid_target_is_skipped(self):
        """NextRun 不是 datetime 的计划视为无效，不参与比较。"""
        config = make_config(Main={'Enable': True, 'NextRun': 'not a datetime'})
        self.assertIsNone(next_sortie(config))


class TestFitSortieWindow(unittest.TestCase):
    def test_limits_count_to_finish_before_next_sortie(self):
        """4 次委托共需 4 小时，离下次出击 2 小时 → 只能做 1 次。

        单次耗时 1 小时，另有 30 秒界面切换余量，所以 2 小时窗口只够一次。
        """
        now = datetime(2026, 11, 20, 8, 0)
        needed = timedelta(hours=4)
        allowed = fit_sortie_window(4, needed, now + timedelta(hours=2), now)
        self.assertEqual(allowed, 1)

    def test_window_shorter_than_one_run_gives_up(self):
        """窗口连一次都放不下时返回 0，调用方据此推迟而不是硬开。"""
        now = datetime(2026, 11, 20, 8, 0)
        allowed = fit_sortie_window(4, timedelta(hours=4), now + timedelta(hours=1), now)
        self.assertEqual(allowed, 0)

    def test_keeps_full_count_when_window_is_large(self):
        now = datetime(2026, 11, 20, 8, 0)
        allowed = fit_sortie_window(4, timedelta(hours=4), now + timedelta(hours=24), now)
        self.assertEqual(allowed, 4)

    def test_returns_zero_when_window_already_passed(self):
        now = datetime(2026, 11, 20, 8, 0)
        allowed = fit_sortie_window(4, timedelta(hours=4), now - timedelta(hours=1), now)
        self.assertEqual(allowed, 0)

    def test_zero_count_or_duration_returns_zero(self):
        now = datetime(2026, 11, 20, 8, 0)
        deadline = now + timedelta(hours=4)
        self.assertEqual(fit_sortie_window(0, timedelta(hours=1), deadline, now), 0)
        self.assertEqual(fit_sortie_window(4, timedelta(), deadline, now), 0)


class TestDailyRuns(unittest.TestCase):
    def test_ignores_records_from_previous_day(self):
        """记录时间早于本次服务器刷新 → 视为今天还没启动过。"""
        reset = datetime(2026, 11, 20, 0, 0)
        now = datetime(2026, 11, 20, 10, 0)
        config = SimpleNamespace(
            OperationHandover_DailyRunCountRecord=datetime(2026, 11, 19, 10, 0),
            OperationHandover_DailyRunCountUsed=3,
        )
        self.assertEqual(daily_runs(config, reset, now), 0)

    def test_ignores_future_records(self):
        """记录时间在未来（时钟回拨等）同样不可信。"""
        reset = datetime(2026, 11, 20, 0, 0)
        now = datetime(2026, 11, 20, 10, 0)
        config = SimpleNamespace(
            OperationHandover_DailyRunCountRecord=datetime(2026, 11, 20, 12, 0),
            OperationHandover_DailyRunCountUsed=2,
        )
        self.assertEqual(daily_runs(config, reset, now), 0)

    def test_counts_records_within_today(self):
        reset = datetime(2026, 11, 20, 0, 0)
        now = datetime(2026, 11, 20, 10, 0)
        config = SimpleNamespace(
            OperationHandover_DailyRunCountRecord=datetime(2026, 11, 20, 8, 0),
            OperationHandover_DailyRunCountUsed=2,
        )
        self.assertEqual(daily_runs(config, reset, now), 2)

    def test_missing_record_returns_zero(self):
        config = SimpleNamespace()
        self.assertEqual(daily_runs(config, datetime(2026, 11, 20), datetime(2026, 11, 21)), 0)


class TestHandoverTimeDeficit(unittest.TestCase):
    def test_last_start_may_exceed_remaining_quota(self):
        """额度只需覆盖最后一次开始前的消耗，最后一次允许超出。"""
        needed = timedelta(hours=4)   # 4 次共 4 小时，单次 1 小时
        remaining = timedelta(hours=3)
        deficit = OperationHandover.handover_time_deficit(needed, remaining, 4)
        # 需要 4 - 1 + 1s = 3h1s，已有 3h → 差 1 秒
        self.assertEqual(deficit, timedelta(seconds=1))

    def test_no_deficit_when_quota_is_enough(self):
        deficit = OperationHandover.handover_time_deficit(
            timedelta(hours=4), timedelta(hours=10), 4)
        self.assertEqual(deficit, timedelta())

    def test_invalid_arguments_raise(self):
        with self.assertRaises(ValueError):
            OperationHandover.handover_time_deficit(timedelta(), timedelta(hours=1), 4)
        with self.assertRaises(ValueError):
            OperationHandover.handover_time_deficit(timedelta(hours=1), timedelta(hours=1), 0)


if __name__ == '__main__':
    unittest.main()
