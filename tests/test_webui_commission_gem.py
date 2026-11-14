# -*- coding: utf-8 -*-
"""委托统计页的钻石委托成功率区块（跟随今日/本周/本月口径）。

覆盖六块：
1. 聚合纯函数：三档口径的边界（今日 / 本周跨月 / 历史月）、脏条目、成功率；
2. 数据层：一次读盘返回三档，与旧的单档接口结果一致；传入月数据可免读库；
3. 历史累计（全部月份）：跨月求和、脏条目与其它实例不计入、空库给零桶；
4. 收益节奏（日均/周均/月均）：跨度天数、单条记录、空库、与累计钻石一致；
4. 视图层：只显示当前口径、一个数字一格、💎 不出现、样式走 CSS 类；
5. 真实中文文案渲染：i18n 键缺失或被生成器重置时立即失败。
"""
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from module.statistics.cl1_database import (
    Cl1Database,
    _aggregate_gem_commission_stats,
)
from module.webui.app_stat_commission_gem import CommissionGemStatisticsMixin


def _entry(ts, duration, reward):
    return {"ts": ts, "duration": duration, "reward": reward}


def _bucket(count, success, reward):
    return {
        "count": count,
        "success": success,
        "reward": reward,
        "rate": round(success * 100 / count, 1) if count else 0.0,
    }


def _sample_stats():
    """三档数据刻意给成不同值，方便断言「页面只取当前口径那一档」。"""
    return {
        "today": {2: _bucket(5, 0, 0), 4: _bucket(2, 1, 25), 8: _bucket(0, 0, 0)},
        "week": {2: _bucket(14, 4, 63), 4: _bucket(5, 1, 25), 8: _bucket(1, 0, 0)},
        "month": {2: _bucket(18, 5, 74), 4: _bucket(6, 1, 25), 8: _bucket(1, 0, 0)},
    }


class AggregateGemCommissionStatsTest(unittest.TestCase):
    """聚合纯函数：now 由参数传入，用例里可以钉死。"""

    NOW = datetime(2026, 11, 4, 20, 0)  # 周三

    def _entries(self):
        return [
            _entry("2026-11-04T09:00:00", 2, 15),   # 今天，2h 成功
            _entry("2026-11-04T14:00:00", 2, 0),    # 今天，2h 失败
            _entry("2026-11-02T10:00:00", 4, 25),   # 本周一，4h 成功
            _entry("2026-10-30T10:00:00", 8, 60),   # 上月，不计入 11 月
        ]

    def test_three_buckets_split_by_duration_and_period(self):
        stats = _aggregate_gem_commission_stats(self._entries(), self.NOW, 2026, 11)
        self.assertEqual(stats["month"][2]["count"], 2)
        self.assertEqual(stats["month"][2]["success"], 1)
        self.assertEqual(stats["month"][2]["reward"], 15)
        self.assertEqual(stats["month"][2]["rate"], 50.0)
        self.assertEqual(stats["month"][4]["count"], 1)
        self.assertEqual(stats["month"][8]["count"], 0)
        self.assertEqual(stats["today"][2]["count"], 2)
        self.assertEqual(stats["today"][4]["count"], 0)
        self.assertEqual(stats["week"][2]["count"], 2)
        self.assertEqual(stats["week"][4]["count"], 1)

    def test_history_month_leaves_today_and_week_empty(self):
        stats = _aggregate_gem_commission_stats(self._entries(), self.NOW, 2026, 10)
        for period in ("today", "week"):
            for hour in (2, 4, 8):
                self.assertEqual(stats[period][hour]["count"], 0, (period, hour))
        self.assertEqual(stats["month"][8]["count"], 1)
        self.assertEqual(stats["month"][8]["reward"], 60)

    def test_dirty_entries_are_skipped(self):
        stats = _aggregate_gem_commission_stats(
            [
                {"duration": 2, "reward": 10},
                _entry("not-a-time", 2, 10),
                _entry("2026-11-04T09:00:00", 3, 10),
                _entry("2026-11-04T09:00:00", 2, 12),
                None,
            ],
            self.NOW,
            2026,
            11,
        )
        self.assertEqual(stats["month"][2]["count"], 1)
        self.assertEqual(stats["month"][2]["reward"], 12)

    def test_empty_entries_give_zero_buckets(self):
        stats = _aggregate_gem_commission_stats([], self.NOW, 2026, 11)
        for period in ("today", "week", "month"):
            for hour in (2, 4, 8):
                self.assertEqual(
                    stats[period][hour],
                    {"count": 0, "success": 0, "reward": 0, "rate": 0.0},
                )


class _FixedDatetime(datetime):
    """把 datetime.now() 钉死的子类；其余构造函数行为保持不变。"""

    FIXED = None

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED


class GemStatsByPeriodsDbTest(unittest.TestCase):
    """数据层：一次读盘给三档，旧的单档接口结果不变。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Cl1Database(db_path=Path(self._tmp.name) / "cl1.sqlite3")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_month(self, month_key, entries):
        data = self.database.get_stats("alas", month_key)
        data["gem_commission_entries"] = entries
        self.database.save_stats("alas", month_key, data)

    def test_cross_month_week_pulls_previous_month(self):
        _FixedDatetime.FIXED = datetime(2026, 12, 1, 10, 0)
        self._write_month("2026-12", [_entry("2026-12-01T09:00:00", 2, 16)])
        self._write_month("2026-11", [_entry("2026-11-30T09:00:00", 4, 25)])
        with patch("module.statistics.cl1_database.datetime", _FixedDatetime):
            stats = self.database.get_gem_commission_stats_by_periods("alas", 2026, 12)
        self.assertEqual(stats["month"][2]["count"], 1)
        self.assertEqual(stats["month"][4]["count"], 0)
        self.assertEqual(stats["week"][4]["count"], 1)
        self.assertEqual(stats["week"][2]["count"], 1)
        self.assertEqual(stats["today"][2]["count"], 1)

    def test_single_period_api_matches_three_period_api(self):
        self._write_month(
            "2026-12",
            [_entry("2026-12-01T09:00:00", 2, 16), _entry("2026-12-01T10:00:00", 2, 0)],
        )
        _FixedDatetime.FIXED = datetime(2026, 12, 1, 12, 0)
        with patch("module.statistics.cl1_database.datetime", _FixedDatetime):
            all_periods = self.database.get_gem_commission_stats_by_periods("alas", 2026, 12)
            for period in ("today", "week", "month"):
                self.assertEqual(
                    self.database.get_gem_commission_stats("alas", period, 2026, 12),
                    all_periods[period],
                )

    def test_month_data_parameter_skips_second_read(self):
        """传入已读月数据时不再读库——页面加载的总读库次数靠它压到 1 次。"""
        month_data = self.database.get_stats("alas", "2026-12")
        month_data["gem_commission_entries"] = [_entry("2026-12-01T09:00:00", 2, 16)]
        with patch.object(
            self.database, "get_stats", side_effect=AssertionError("不该再读库")
        ):
            stats = self.database.get_gem_commission_stats_by_periods(
                "alas", 2026, 12, month_data=month_data
            )
        self.assertEqual(stats["month"][2]["count"], 1)


class GemBlockHtmlTest(unittest.TestCase):
    """视图层：只显示当前口径、一个数字一格、样式走 CSS 类。"""

    def _build(self, gem_stats, period="month", is_current_month=True, month_suffix=""):
        harness = CommissionGemStatisticsMixin.__new__(CommissionGemStatisticsMixin)
        with patch(
            "module.webui.app_stat_commission_gem.t", side_effect=lambda key, **kw: key
        ):
            return harness._build_commission_gem_html(
                {
                    "gem_stats": gem_stats,
                    "period": period,
                    "is_current_month": is_current_month,
                    "month_suffix": month_suffix,
                }
            )

    def test_follows_selected_period(self):
        """选今日只显示今日，选本周只显示本周——三档不同屏。"""
        html_today = self._build(_sample_stats(), period="day")
        # 今日口径：2h 委托 5 次、4h 委托 2 次；本月的 18 次不该出现
        self.assertIn(">5</td>", html_today)
        self.assertIn(">2</td>", html_today)
        self.assertNotIn(">18</td>", html_today)
        self.assertNotIn("27.8%", html_today)

        html_month = self._build(_sample_stats(), period="month")
        self.assertIn(">18</td>", html_month)
        self.assertIn("27.8%", html_month)
        # 今日口径的成功率 50.0%（4h 1/2）不该出现在本月视图
        self.assertNotIn("50.0%", html_month)

    def test_day_week_month_pick_different_buckets(self):
        stats = _sample_stats()
        day = self._build(stats, period="day")
        week = self._build(stats, period="week")
        month = self._build(stats, period="month")
        # 成功率各不相同，说明取的是不同桶
        self.assertIn("50.0%", day)     # 今日 4h = 1/2
        self.assertIn("20.0%", week)    # 本周 4h = 1/5
        self.assertIn("16.7%", month)   # 本月 4h = 1/6

    def test_five_columns_one_number_per_cell(self):
        html = self._build(_sample_stats())
        # 五列：时长 / 成功率 / 成功次数 / 委托次数 / 获得钻石
        self.assertEqual(html.count("<th "), 5)
        # 3 档时长 + 合计 1 行 = 4 个数据行，加表头共 5 个 tr
        self.assertEqual(html.count("<tr"), 5)
        # 不再有 "5/18" 这类合写
        self.assertNotIn("5/18", html)
        self.assertNotIn("0/5", html)
        # 一个数字一格
        self.assertIn(">5</td>", html)
        self.assertIn(">18</td>", html)
        self.assertIn(">74</td>", html)

    def test_period_badge_shows_current_scope(self):
        for period, key in (
            ("day", "Gui.Stat.CommissionIncomeDay"),
            ("week", "Gui.Stat.CommissionIncomeWeek"),
            ("month", "Gui.Stat.CommissionIncomeMonth"),
        ):
            html = self._build(_sample_stats(), period=period)
            self.assertIn('class="gem-period-badge"', html)
            self.assertIn(key, html, period)

    def test_styles_go_through_css_classes(self):
        """远控下每字节都要经 WS 分片传输：视觉样式必须走 CSS 类，不能内联。"""
        html = self._build(_sample_stats())
        styles = re.findall(r'style="([^"]*)"', html)
        for style in styles:
            self.assertTrue(
                style.startswith("width: ") and style.endswith(";") and len(style) < 20
                or "!important" in style,
                f"发现应挪进 CSS 的内联样式: {style}",
            )
        for bad in ("padding", "margin", "font", "color", "background", "border", "opacity"):
            self.assertNotIn(bad, " ".join(s for s in styles if "!important" not in s), bad)
        for cls in (
            "commission-gem-section",
            "gem-scroll",
            "commission-gem-table",
            "gem-row",
            "gem-rate-cell",
            "gem-num-cell",
            "gem-duration-cell",
        ):
            self.assertIn(cls, html, f"缺少类名: {cls}")

    def test_no_diamond_emoji_anywhere(self):
        html = self._build(_sample_stats())
        self.assertNotIn("U0001F48E", html)

    def test_no_data_shows_hint(self):
        html = self._build(None)
        self.assertIn("Gui.Stat.CommissionGemNoData", html)

    def test_malformed_bucket_falls_back_to_placeholder(self):
        html = self._build({"month": {2: "broken", 4: {}, 8: {}}})
        self.assertIn("&#8212;", html)


class GemTotalsDbTest(unittest.TestCase):
    """数据层：历史累计跨全部月份，不受口径与所选年月影响。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Cl1Database(db_path=Path(self._tmp.name) / "cl1.sqlite3")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_month(self, month_key, entries):
        data = self.database.get_stats("alas", month_key)
        data["gem_commission_entries"] = entries
        self.database.save_stats("alas", month_key, data)

    def test_sums_across_every_month(self):
        self._write_month(
            "2026-09",
            [_entry("2026-09-03T18:41:36", 2, 0), _entry("2026-09-04T18:41:36", 4, 34)],
        )
        self._write_month("2026-10", [_entry("2026-10-01T08:04:57", 2, 20)])
        self._write_month("2026-11", [_entry("2026-11-01T04:39:54", 8, 60)])
        totals = self.database.get_gem_commission_totals("alas")
        self.assertEqual(totals[2], _bucket(2, 1, 20))
        self.assertEqual(totals[4], _bucket(1, 1, 34))
        self.assertEqual(totals[8], _bucket(1, 1, 60))

    def test_dirty_entries_are_skipped(self):
        self._write_month(
            "2026-11",
            [
                {"duration": 2, "reward": 10},          # 缺 ts
                _entry("not-a-time", 2, 10),            # ts 不像 ISO 时间
                _entry("2026-11-04T09:00:00", 3, 10),   # 非法时长
                _entry("2026-11-04T09:00:00", 2, 12),
            ],
        )
        totals = self.database.get_gem_commission_totals("alas")
        # 只统计 ts 形如 ISO 时间且时长属于 2/4/8 的条目
        self.assertEqual(totals[2]["count"], 1)
        self.assertEqual(totals[2]["reward"], 12)
        self.assertEqual(totals[4]["count"], 0)
        self.assertEqual(totals[8]["count"], 0)

    def test_other_instance_is_not_mixed_in(self):
        data = self.database.get_stats("其他实例", "2026-11")
        data["gem_commission_entries"] = [_entry("2026-11-01T09:00:00", 2, 99)]
        self.database.save_stats("其他实例", "2026-11", data)
        self._write_month("2026-11", [_entry("2026-11-02T09:00:00", 2, 10)])
        totals = self.database.get_gem_commission_totals("alas")
        self.assertEqual(totals[2], _bucket(1, 1, 10))

    def test_empty_database_gives_zero_buckets(self):
        totals = self.database.get_gem_commission_totals("alas")
        for hour in (2, 4, 8):
            self.assertEqual(totals[hour], _bucket(0, 0, 0))


class GemAllTimeRowHtmlTest(unittest.TestCase):
    """视图层：历史累计行 = 全部月份，且不随上方口径变化。"""

    TOTALS = {2: _bucket(254, 82, 1252), 4: _bucket(96, 19, 595), 8: _bucket(24, 6, 391)}

    def _build(self, gem_stats, gem_totals, period="month"):
        harness = CommissionGemStatisticsMixin.__new__(CommissionGemStatisticsMixin)
        with patch(
            "module.webui.app_stat_commission_gem.t", side_effect=lambda key, **kw: key
        ):
            return harness._build_commission_gem_html(
                {
                    "gem_stats": gem_stats,
                    "gem_totals": gem_totals,
                    "period": period,
                    "is_current_month": True,
                    "month_suffix": "",
                }
            )

    def test_alltime_row_shows_cross_month_sums(self):
        html = self._build(_sample_stats(), self.TOTALS)
        self.assertIn("gem-row-alltime", html)
        self.assertIn("Gui.Stat.CommissionGemRowAllTime", html)
        self.assertIn("Gui.Stat.CommissionGemAllTimeScope", html)
        # 374 次 / 107 成功 / 2238 钻石 / 28.6%
        self.assertIn(">374</td>", html)
        self.assertIn(">107</td>", html)
        self.assertIn(">2238</td>", html)
        self.assertIn("28.6%", html)
        # 钻石数用类名强调，不靠内联样式
        self.assertIn("gem-reward-strong", html)

    def test_alltime_row_ignores_selected_period(self):
        """今日口径下累计行仍是全部月份的数 —— 这就是「总成功率」的意义。"""
        html = self._build(_sample_stats(), self.TOTALS, period="day")
        self.assertIn("gem-row-alltime", html)
        self.assertIn(">2238</td>", html)

    def test_alltime_row_absent_without_totals(self):
        for totals in (None, {}, {2: _bucket(0, 0, 0), 4: _bucket(0, 0, 0), 8: _bucket(0, 0, 0)}):
            html = self._build(_sample_stats(), totals)
            self.assertNotIn("gem-row-alltime", html, totals)

    def test_alltime_row_keeps_five_columns(self):
        html = self._build(_sample_stats(), self.TOTALS)
        # 表头仍是 5 列；数据行 = 3 档 + 合计 + 历史累计
        self.assertEqual(html.count("<th "), 5)
        self.assertEqual(html.count("<tr"), 6)

    def test_styles_stay_in_css_classes(self):
        html = self._build(_sample_stats(), self.TOTALS)
        for bad in ("color:", "background:", "font-size:"):
            self.assertNotIn(bad, html, bad)


class GemPaceDbTest(unittest.TestCase):
    """数据层：日均 / 周均 / 月均按覆盖天数折算。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Cl1Database(db_path=Path(self._tmp.name) / "cl1.sqlite3")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_month(self, month_key, entries):
        data = self.database.get_stats("alas", month_key)
        data["gem_commission_entries"] = entries
        self.database.save_stats("alas", month_key, data)

    def test_natural_week_and_month_averages(self):
        # 9/3(第36周) 30 钻；9/7 与 9/12 同属第37周，合计 70 钻；全部落在 9 月
        self._write_month(
            "2026-09",
            [
                _entry("2026-09-03T08:00:00", 2, 30),
                _entry("2026-09-07T08:00:00", 4, 40),
                _entry("2026-09-12T08:00:00", 8, 0),
                _entry("2026-09-12T20:00:00", 2, 30),
            ],
        )
        pace = self.database.get_gem_commission_pace("alas")
        self.assertEqual(pace["days"], 10)
        self.assertEqual(pace["weeks"], 2)
        self.assertEqual(pace["months"], 1)
        self.assertEqual(pace["total"], 100)
        self.assertEqual(pace["count"], 4)
        # 日均 = 总钻 / 跨度天数
        self.assertEqual(pace["avg_day"], 10.0)
        # 周均 = (30 + 70) / 2 个自然周
        self.assertEqual(pace["avg_week"], 50.0)
        # 月均 = 100 / 1 个自然月（不是 10 × 30）
        self.assertEqual(pace["avg_month"], 100.0)
        self.assertEqual(pace["first_date"], "2026-09-03")
        self.assertEqual(pace["last_date"], "2026-09-12")

    def test_averages_are_per_natural_month_not_extrapolated(self):
        # 9 月 100 钻、10 月 40 钻 -> 月均 70，而不是「总钻 / 天数 × 30」
        self._write_month("2026-09", [_entry("2026-09-03T08:00:00", 2, 100)])
        self._write_month("2026-10", [_entry("2026-10-20T08:00:00", 2, 40)])
        pace = self.database.get_gem_commission_pace("alas")
        self.assertEqual(pace["months"], 2)
        self.assertEqual(pace["avg_month"], 70.0)
        # 周均同理按自然周：W36=100、W43=40 => 70
        self.assertEqual(pace["weeks"], 2)
        self.assertEqual(pace["avg_week"], 70.0)

    def test_single_record_counts_as_one_day(self):
        self._write_month("2026-09", [_entry("2026-09-03T08:00:00", 2, 25)])
        pace = self.database.get_gem_commission_pace("alas")
        self.assertEqual(pace["days"], 1)
        self.assertEqual(pace["weeks"], 1)
        self.assertEqual(pace["months"], 1)
        self.assertEqual(pace["avg_day"], 25.0)
        self.assertEqual(pace["avg_week"], 25.0)
        self.assertEqual(pace["avg_month"], 25.0)

    def test_empty_database_returns_none(self):
        self.assertIsNone(self.database.get_gem_commission_pace("alas"))

    def test_dirty_entries_do_not_extend_span(self):
        self._write_month(
            "2026-09",
            [
                _entry("2026-09-10T08:00:00", 2, 20),
                _entry("2026-09-11T08:00:00", 3, 99),      # 非法时长
                {"duration": 2, "reward": 99},             # 缺 ts
                _entry("not-a-time", 2, 99),               # ts 形状不对
            ],
        )
        pace = self.database.get_gem_commission_pace("alas")
        self.assertEqual(pace["days"], 1)
        self.assertEqual(pace["weeks"], 1)
        self.assertEqual(pace["months"], 1)
        self.assertEqual(pace["total"], 20)
        self.assertEqual(pace["count"], 1)

    def test_total_matches_totals_api(self):
        self._write_month(
            "2026-10",
            [_entry("2026-10-01T08:00:00", 2, 15), _entry("2026-10-02T08:00:00", 4, 25)],
        )
        totals = self.database.get_gem_commission_totals("alas")
        pace = self.database.get_gem_commission_pace("alas")
        self.assertEqual(pace["total"], sum(bucket["reward"] for bucket in totals.values()))


class GemPaceRowHtmlTest(unittest.TestCase):
    """视图层：表格里的「平均」行。"""

    PACE = {
        "days": 10,
        "weeks": 2,
        "months": 1,
        "total": 100,
        "count": 4,
        "avg_day": 10.0,
        "avg_week": 50.0,
        "avg_month": 100.0,
        "first_date": "2026-09-03",
        "last_date": "2026-09-12",
    }

    def _build(self, gem_stats, gem_totals, gem_pace):
        harness = CommissionGemStatisticsMixin.__new__(CommissionGemStatisticsMixin)
        with patch(
            "module.webui.app_stat_commission_gem.t", side_effect=lambda key, **kw: key
        ):
            return harness._build_commission_gem_html(
                {
                    "gem_stats": gem_stats,
                    "gem_totals": gem_totals,
                    "gem_pace": gem_pace,
                    "period": "month",
                    "is_current_month": True,
                    "month_suffix": "",
                }
            )

    def test_pace_row_renders_three_averages(self):
        html = self._build(_sample_stats(), GemAllTimeRowHtmlTest.TOTALS, self.PACE)
        self.assertIn("gem-row-pace", html)
        self.assertIn("Gui.Stat.CommissionGemRowPace", html)
        self.assertIn("Gui.Stat.CommissionGemPaceDay", html)
        self.assertIn("Gui.Stat.CommissionGemPaceWeek", html)
        self.assertIn("Gui.Stat.CommissionGemPaceMonth", html)
        # 数值：+10.0 / +50.0 / +100.0（自然周、自然月平均）
        self.assertIn("+10.0", html)
        self.assertIn("+50.0", html)
        self.assertIn("+100.0", html)
        # 后四列合并
        self.assertIn('colspan="4"', html)

    def test_pace_row_absent_without_data(self):
        for pace in (None, {}, {"days": 0}):
            html = self._build(_sample_stats(), GemAllTimeRowHtmlTest.TOTALS, pace)
            self.assertNotIn("gem-row-pace", html, pace)

    def test_pace_row_keeps_five_columns(self):
        html = self._build(_sample_stats(), GemAllTimeRowHtmlTest.TOTALS, self.PACE)
        self.assertEqual(html.count("<th "), 5)
        # 3 档 + 合计 + 历史累计 + 平均 = 6 个数据行
        self.assertEqual(html.count("<tr"), 7)

    def test_pace_uses_thousands_separator(self):
        pace = dict(self.PACE, avg_month=1234.5)
        html = self._build(_sample_stats(), GemAllTimeRowHtmlTest.TOTALS, pace)
        self.assertIn("+1,234.5", html)

    def test_pace_scope_shows_all_three_denominators(self):
        """副标题要同时给出天/周/月分母，否则用户只能猜月均是怎么平均的。"""
        html = self._build(_sample_stats(), GemAllTimeRowHtmlTest.TOTALS, self.PACE)
        self.assertIn("Gui.Stat.CommissionGemPaceScope", html)

    def test_pace_styles_stay_in_css_classes(self):
        html = self._build(_sample_stats(), GemAllTimeRowHtmlTest.TOTALS, self.PACE)
        for bad in ("color:", "background:", "font-size:"):
            self.assertNotIn(bad, html, bad)


class GemBlockRealI18nTest(unittest.TestCase):
    """真实中文文案：键缺失或被生成器重置成 key 时这里会挂。"""

    def setUp(self):
        import module.webui.lang as wl

        # 加载真实翻译会写全局 dic_lang，必须还原：
        # 其他用例依赖「t() 未加载时返回 key 原文」来断言文案键名。
        self._wl = wl
        self._saved_lang = wl.LANG
        self._saved_dic = dict(wl.dic_lang)

    def tearDown(self):
        self._wl.LANG = self._saved_lang
        self._wl.dic_lang.clear()
        self._wl.dic_lang.update(self._saved_dic)

    def test_zh_cn_labels_render(self):
        harness = CommissionGemStatisticsMixin.__new__(CommissionGemStatisticsMixin)
        self._wl.LANG = "zh-CN"
        self._wl.reload()
        html = harness._build_commission_gem_html(
            {
                "gem_stats": _sample_stats(),
                "gem_totals": GemAllTimeRowHtmlTest.TOTALS,
                "gem_pace": GemPaceRowHtmlTest.PACE,
                "period": "month",
                "is_current_month": True,
                "month_suffix": "",
            }
        )
        for label in (
            "钻石委托成功率", "委托时长", "成功率", "成功次数", "委托次数",
            "获得钻石", "合计", "本月", "历史累计", "全部月份",
            "平均", "日均", "周均", "月均",
        ):
            self.assertIn(label, html, f"文案缺失: {label}")
        self.assertNotIn("Gui.Stat.CommissionGem", html)
        self.assertNotIn("U0001F48E", html)


if __name__ == "__main__":
    unittest.main()
