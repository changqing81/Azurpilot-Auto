import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta

import module.statistics.resource_delta_stats as stats


class TestResourceDeltaStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        stats._LOCAL_DB = os.path.join(self.tmp.name, 'resource_delta.db')
        stats._table_ensured = False

    def tearDown(self):
        self.tmp.cleanup()
        stats._table_ensured = False

    def _insert_event(self, ts, resource, delta, source='Main', balance=None, instance='alas'):
        """直接插入指定时间的事件，用于测试时间过滤与分桶"""
        stats._ensure_table()
        with closing(sqlite3.connect(stats._LOCAL_DB)) as conn, conn:
            conn.execute(
                'INSERT INTO resource_delta_events '
                '(instance, ts, resource, delta, source, balance) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (instance, ts, resource, delta, source, balance),
            )
            conn.commit()

    def test_record_and_summary(self):
        self.assertTrue(
            stats.record_resource_delta('alas', 'Oil', 5000, 'Main', balance=15000)
        )
        self.assertTrue(
            stats.record_resource_delta('alas', 'Oil', -3000, 'Main', balance=12000)
        )
        self.assertTrue(
            stats.record_resource_delta('alas', 'Oil', -2000, 'Gacha', balance=10000)
        )
        self.assertTrue(
            stats.record_resource_delta('alas', 'Coin', -600, 'Gacha', balance=9400)
        )

        summary = {row['resource']: row for row in stats.get_delta_summary('alas')}
        self.assertEqual(2, len(summary))
        self.assertEqual(5000, summary['Oil']['increase'])
        self.assertEqual(5000, summary['Oil']['decrease'])
        self.assertEqual(0, summary['Oil']['net'])
        self.assertEqual(3, summary['Oil']['events'])
        self.assertEqual(0, summary['Coin']['increase'])
        self.assertEqual(600, summary['Coin']['decrease'])
        self.assertEqual(-600, summary['Coin']['net'])

    def test_summary_time_filter(self):
        now = datetime.now()
        past = now - timedelta(hours=3)
        self._insert_event(past.isoformat(), 'Oil', -1000, 'Main')
        self._insert_event(now.isoformat(), 'Oil', -2000, 'Main')

        # 默认：不限时间
        total = {row['resource']: row for row in stats.get_delta_summary('alas')}
        self.assertEqual(3000, total['Oil']['decrease'])
        # 起始时间之后只含最近一条
        recent = {
            row['resource']: row
            for row in stats.get_delta_summary(
                'alas', start_ts=(now - timedelta(hours=1)).isoformat()
            )
        }
        self.assertEqual(2000, recent['Oil']['decrease'])
        # 结束时间之前只含较早一条
        old = {
            row['resource']: row
            for row in stats.get_delta_summary(
                'alas', end_ts=(now - timedelta(hours=1)).isoformat()
            )
        }
        self.assertEqual(1000, old['Oil']['decrease'])

    def test_summary_isolated_by_instance(self):
        stats.record_resource_delta('alas', 'Oil', -1000, 'Main')
        stats.record_resource_delta('alas2', 'Oil', -2000, 'Main')
        summary = {row['resource']: row for row in stats.get_delta_summary('alas')}
        self.assertEqual(1000, summary['Oil']['decrease'])

    def test_timeline_day_buckets_merge_and_sort(self):
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        self._insert_event(now.isoformat(), 'Oil', -500, 'Main')
        self._insert_event(now.isoformat(), 'Oil', -300, 'Main')
        self._insert_event(yesterday.isoformat(), 'Oil', -700, 'Main')
        self._insert_event(yesterday.isoformat(), 'Coin', -600, 'Gacha')

        timeline = stats.get_delta_timeline('alas', bucket='day', days=30)
        self.assertEqual(2, len(timeline))
        # 桶按时间升序
        self.assertLess(timeline[0]['bucket'], timeline[1]['bucket'])
        today_row = timeline[-1]
        self.assertEqual(-800, today_row['Oil'])
        self.assertEqual('bucket' in today_row, True)
        yesterday_row = timeline[0]
        self.assertEqual(-700, yesterday_row['Oil'])
        self.assertEqual(-600, yesterday_row['Coin'])

    def test_timeline_week_and_month_buckets(self):
        self._insert_event('2026-09-02T10:00:00', 'Oil', -100, 'Main')
        # 同一周（周一为 2026-08-31）的另一条
        self._insert_event('2026-09-04T10:00:00', 'Oil', -50, 'Main')
        self._insert_event('2026-08-05T10:00:00', 'Oil', -200, 'Main')

        weekly = stats.get_delta_timeline('alas', bucket='week', days=365)
        by_bucket = {row['bucket']: row for row in weekly}
        # 2026-09-02 与 2026-09-04 同属周一为 2026-08-31 的一周，合并累计
        self.assertEqual(-150, by_bucket['2026-08-31']['Oil'])
        self.assertEqual(-200, by_bucket['2026-08-03']['Oil'])

        monthly = stats.get_delta_timeline('alas', bucket='month', days=365)
        by_month = {row['bucket']: row for row in monthly}
        self.assertEqual(-150, by_month['2026-09']['Oil'])
        self.assertEqual(-200, by_month['2026-08']['Oil'])

    def test_timeline_isolated_by_instance(self):
        now = datetime.now()
        self._insert_event(now.isoformat(), 'Oil', -100, 'Main')
        self._insert_event(now.isoformat(), 'Oil', -900, 'Main', instance='alas2')
        timeline = stats.get_delta_timeline('alas', bucket='day', days=1)
        self.assertEqual(1, len(timeline))
        self.assertEqual(-100, timeline[0]['Oil'])

    def test_ranking_only_consumption_desc(self):
        self._insert_event(datetime.now().isoformat(), 'Oil', -3000, 'Main')
        self._insert_event(datetime.now().isoformat(), 'Oil', -5000, 'Main')
        self._insert_event(datetime.now().isoformat(), 'Oil', -1000, 'GemsFarming')
        # 增加事件不参与排行
        self._insert_event(datetime.now().isoformat(), 'Oil', 10000, 'Reward')
        # 多资源分组
        self._insert_event(datetime.now().isoformat(), 'ActionPoint', -120, 'OpsiScheduling')

        ranking = stats.get_consumption_ranking('alas')
        oil_rows = [row for row in ranking if row['resource'] == 'Oil']
        self.assertEqual(2, len(oil_rows))
        # 同资源内按消耗降序；同任务的多条事件合并累计
        self.assertEqual('Main', oil_rows[0]['source'])
        self.assertEqual(8000, oil_rows[0]['consumed'])
        self.assertEqual(2, oil_rows[0]['times'])
        self.assertEqual('GemsFarming', oil_rows[1]['source'])
        self.assertEqual(1000, oil_rows[1]['consumed'])
        # 排行榜不包含纯增加的 source
        self.assertNotIn('Reward', {row['source'] for row in ranking})
        ap_rows = [row for row in ranking if row['resource'] == 'ActionPoint']
        self.assertEqual(120, ap_rows[0]['consumed'])

    def test_ranking_time_filter(self):
        now = datetime.now()
        past = now - timedelta(hours=3)
        self._insert_event(past.isoformat(), 'Oil', -1000, 'Main')
        self._insert_event(now.isoformat(), 'Oil', -2000, 'Gacha')
        ranking = stats.get_consumption_ranking(
            'alas', start_ts=(now - timedelta(hours=1)).isoformat()
        )
        self.assertEqual(1, len(ranking))
        self.assertEqual('Gacha', ranking[0]['source'])
        self.assertEqual(2000, ranking[0]['consumed'])

    def test_balance_roundtrip(self):
        stats.record_resource_delta('alas', 'Cube', -2, 'Gacha', balance=57)
        with closing(sqlite3.connect(stats._LOCAL_DB)) as conn:
            row = conn.execute(
                'SELECT balance FROM resource_delta_events WHERE resource = ?',
                ('Cube',),
            ).fetchone()
        self.assertEqual(57, row[0])


if __name__ == '__main__':
    unittest.main()
