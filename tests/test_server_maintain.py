"""维护公告查询回归：query_next_maintain 不限「今天」，query_maintain_today 只认今天。

回归 2026-11-05 事故：批量科研 21:10 排程跨过次日 10:00 维护，只查「今天」的
公告查不到，NextRun 被排到次日 10:08，维护前 1 小时的强制运行点一次没醒。
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from module import server_maintain
from module.server_maintain import query_maintain_today, query_next_maintain

# 用本机时区拼公告时区，保证断言里的 naive 时间与公告写的字面时间一致（CI 可能不在 UTC+8）
_OFFSET_MINUTES = int(datetime.now().astimezone().utcoffset().total_seconds() // 60)
_HOURS, _MINUTES = divmod(abs(_OFFSET_MINUTES), 60)
LOCAL_TZ = 'UTC{}{}'.format('+' if _OFFSET_MINUTES >= 0 else '-', _HOURS) + (
    f':{_MINUTES:02d}' if _MINUTES else '')

NEXT_DAY = {
    'name': '十字军行动',
    'maintenance_date': '2026-11-05',
    'start_time': '10:00',
    'timezone': LOCAL_TZ,
}
TODAY = dict(NEXT_DAY, maintenance_date='2026-11-04')
PAST = dict(NEXT_DAY, maintenance_date='2026-11-03')

# 事故当晚：11-04 21:10，明天的维护公告已经能查到
NIGHT = datetime(2026, 11, 4, 21, 10, 0)
# 维护当天上午 8:00，今天的维护还没开始
MORNING = datetime(2026, 11, 4, 8, 0, 0)


def query(fn, payload, now, default=timedelta(hours=8), error=''):
    """顶掉网络与当前时间，只验证解析和日期判定。"""
    with patch.object(server_maintain, 'query_maintain_payload', autospec=True,
                      return_value=(payload, default, error)), \
            patch.object(server_maintain, 'current_time', autospec=True, return_value=now):
        return fn('cn')


class TestQueryNextMaintain(unittest.TestCase):
    def test_next_day_maintenance_is_returned(self):
        """次日的维护必须返回：跨夜排程要靠它钳强制运行点。"""
        start, reason, queried = query(query_next_maintain, NEXT_DAY, NIGHT)
        self.assertIs(queried, True)
        self.assertEqual(start, datetime(2026, 11, 5, 10, 0))
        self.assertIn('2026-11-05 10:00', reason)

    def test_today_future_maintenance_is_returned(self):
        start, _, queried = query(query_next_maintain, TODAY, MORNING)
        self.assertIs(queried, True)
        self.assertEqual(start, datetime(2026, 11, 4, 10, 0))

    def test_started_maintenance_is_excluded(self):
        """已经开始（今天 10:00 已是过去）的维护不返回，避免维护中反复进游戏。"""
        start, _, queried = query(query_next_maintain, TODAY, NIGHT)
        self.assertIsNone(start)
        self.assertIs(queried, True)

        past_start, past_reason, past_queried = query(query_next_maintain, PAST, NIGHT)
        self.assertIsNone(past_start)
        self.assertIs(past_queried, True)
        self.assertIn('已经开始', past_reason)

    def test_query_failure_reports_not_queried(self):
        start, reason, queried = query(query_next_maintain, None, NIGHT,
                                       error='查询维护时间失败')
        self.assertIsNone(start)
        self.assertIs(queried, False)
        self.assertEqual(reason, '查询维护时间失败')

    def test_unparsable_time_reports_not_queried(self):
        start, reason, queried = query(query_next_maintain,
                                       dict(NEXT_DAY, start_time='10点'), NIGHT)
        self.assertIsNone(start)
        self.assertIs(queried, False)
        self.assertIn('无法识别', reason)


class TestQueryMaintainToday(unittest.TestCase):
    """原有「只认今天」语义不变（维护当天作战委托在用）。"""

    def test_today_not_started_returns_start(self):
        start, reason, queried = query(query_maintain_today, TODAY, MORNING)
        self.assertEqual(start, datetime(2026, 11, 4, 10, 0))
        self.assertIs(queried, True)
        self.assertIn('停服维护', reason)

    def test_today_already_started_still_returns_start(self):
        start, reason, queried = query(query_maintain_today, TODAY, NIGHT)
        self.assertEqual(start, datetime(2026, 11, 4, 10, 0))
        self.assertIs(queried, True)
        self.assertIn('已经过去', reason)

    def test_next_day_is_not_today(self):
        start, reason, queried = query(query_maintain_today, NEXT_DAY, NIGHT)
        self.assertIsNone(start)
        self.assertIs(queried, True)
        self.assertIn('不是今天', reason)

class TestCheckSlot(unittest.TestCase):
    """公告只在这三个检查点查：00:05 / 06:00 / 18:00。"""

    @staticmethod
    def slot(text):
        hour, minute = (int(x) for x in text.split(':'))
        return server_maintain._check_slot(datetime(2026, 11, 5, hour, minute))

    def test_slots(self):
        self.assertEqual(self.slot('00:04'), datetime(2026, 11, 4, 18, 0))
        self.assertEqual(self.slot('00:05'), datetime(2026, 11, 5, 0, 5))
        self.assertEqual(self.slot('05:59'), datetime(2026, 11, 5, 0, 5))
        self.assertEqual(self.slot('06:00'), datetime(2026, 11, 5, 6, 0))
        self.assertEqual(self.slot('17:59'), datetime(2026, 11, 5, 6, 0))
        self.assertEqual(self.slot('18:00'), datetime(2026, 11, 5, 18, 0))
        self.assertEqual(self.slot('23:59'), datetime(2026, 11, 5, 18, 0))


class TestScheduledQuery(unittest.TestCase):
    """同一检查点内只请求一次接口，失败才按 30 分钟间隔重试至多 3 次。"""

    def setUp(self):
        server_maintain._check_cache.clear()

    @staticmethod
    def call(now):
        with patch.object(server_maintain, 'current_time', autospec=True, return_value=now):
            return server_maintain.query_next_maintain_cached('cn')

    def test_three_queries_per_day(self):
        calls = []

        def fake(server):
            calls.append(server)
            return datetime(2026, 11, 5, 10, 0), '下次维护', True

        with patch.object(server_maintain, 'query_next_maintain', autospec=True, side_effect=fake):
            for hour, minute in ((0, 5), (3, 0), (5, 59), (6, 0), (12, 0), (18, 0), (23, 59)):
                self.call(datetime(2026, 11, 5, hour, minute))

        self.assertEqual(len(calls), 3)

    def test_cache_hit_returns_cached_result(self):
        calls = []

        def fake(server):
            calls.append(server)
            return datetime(2026, 11, 5, 10, 0), '下次维护', True

        with patch.object(server_maintain, 'query_next_maintain', autospec=True, side_effect=fake):
            first = self.call(datetime(2026, 11, 5, 7, 0))
            second = self.call(datetime(2026, 11, 5, 8, 0))

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_failed_query_retries_at_most_limit(self):
        calls = []

        def fake(server):
            calls.append(server)
            return None, '查询失败', False

        with patch.object(server_maintain, 'query_next_maintain', autospec=True, side_effect=fake):
            self.call(datetime(2026, 11, 5, 6, 0))
            self.call(datetime(2026, 11, 5, 6, 10))    # 未到 30 分钟，不重试
            self.call(datetime(2026, 11, 5, 6, 35))    # 第 2 次
            self.call(datetime(2026, 11, 5, 7, 10))    # 第 3 次
            self.call(datetime(2026, 11, 5, 8, 0))     # 已达上限，不再查

        self.assertEqual(len(calls), server_maintain.MAINTAIN_FAILED_RETRY_LIMIT)


if __name__ == '__main__':
    unittest.main()
