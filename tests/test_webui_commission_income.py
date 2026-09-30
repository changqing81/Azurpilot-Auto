# -*- coding: utf-8 -*-
"""委托收益统计的历史月份查看。

覆盖三块逻辑：
1. 年月步进与口径收敛纯函数（历史月份强制整月口径）；
2. 数据加载跟随所选月份（汇总 + 明细同源）；
3. 月份选择控件的禁用规则（未来月禁用、历史月禁用今日/本周）。
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from module.webui.app_stat_commission import (
    CommissionIncomeStatisticsMixin,
    _effective_commission_period,
    _shift_commission_month,
)


def _entry(ts, gem=1):
    return {"ts": ts, "items": {"Gem": gem}, "commission_count": 1, "screenshots": []}


class ShiftCommissionMonthTest(unittest.TestCase):
    def test_single_month_step(self):
        self.assertEqual(_shift_commission_month(2026, 9, 1), (2026, 10))
        self.assertEqual(_shift_commission_month(2026, 9, -1), (2026, 8))

    def test_cross_year_step(self):
        self.assertEqual(_shift_commission_month(2026, 1, -1), (2025, 12))
        self.assertEqual(_shift_commission_month(2025, 12, 1), (2026, 1))

    def test_multi_year_step(self):
        self.assertEqual(_shift_commission_month(2026, 9, -12), (2025, 9))
        self.assertEqual(_shift_commission_month(2026, 9, -120), (2016, 9))


class EffectiveCommissionPeriodTest(unittest.TestCase):
    NOW = datetime(2026, 9, 15, 12, 0, 0)

    def test_current_month_keeps_user_period(self):
        for period in ("day", "week", "month"):
            self.assertEqual(
                _effective_commission_period(period, 2026, 9, now=self.NOW),
                period,
            )

    def test_history_month_forces_month_period(self):
        # 今日/本周锚定当前日期，历史月份下恒为空，必须收敛为整月口径
        for period in ("day", "week"):
            self.assertEqual(
                _effective_commission_period(period, 2026, 8, now=self.NOW),
                "month",
            )

    def test_future_month_also_forces_month_period(self):
        self.assertEqual(
            _effective_commission_period("day", 2027, 1, now=self.NOW), "month"
        )


class _CommissionHarness(CommissionIncomeStatisticsMixin):
    """最小化宿主：只提供统计视图用到的时间状态。"""

    def __init__(self, year=None, month=None):
        now = datetime.now()
        self.alas_name = "alas"
        self._commission_income_period = "day"
        self._commission_income_year = year if year is not None else now.year
        self._commission_income_month = month if month is not None else now.month
        self._commission_recent_page = 0


class LoadCommissionIncomeDataTest(unittest.TestCase):
    """数据加载：口径收敛与明细跟随所选月份。"""

    def _load(self, harness):
        mock_db = MagicMock()
        mock_db.get_commission_income.return_value = [
            _entry("2026-08-03T10:00:00"),
            _entry("2026-08-01T09:00:00"),
        ]
        # summary 聚合走 commission_income_stats 模块级绑定的 db 引用，
        # 与 webui 局部导入的 db 一并 patch，避免测试摸到真实 SQLite
        with patch("module.webui.app_stat_commission.t", side_effect=lambda key, **kw: key), \
                patch("module.statistics.cl1_database.db", new=mock_db), \
                patch("module.statistics.commission_income_stats.cl1_db", new=mock_db):
            return harness._load_commission_income_data(), mock_db

    def test_history_month_forces_month_period_and_suffix(self):
        harness = _CommissionHarness(year=2026, month=8)
        data, mock_db = self._load(harness)

        self.assertEqual(data["period"], "month")
        self.assertFalse(data["is_current_month"])
        self.assertEqual(data["month_suffix"], " · 2026-08")
        # 明细来自所选月份且按时间倒序（load 一次 + summary 聚合一次，共 2 次同参调用）
        self.assertEqual(mock_db.get_commission_income.call_count, 2)
        mock_db.get_commission_income.assert_called_with("alas", 2026, 8)
        self.assertEqual(
            [entry["ts"] for entry in data["recent"]],
            ["2026-08-03T10:00:00", "2026-08-01T09:00:00"],
        )

    def test_current_month_keeps_period_and_no_suffix(self):
        harness = _CommissionHarness()  # 默认当前年月
        data, mock_db = self._load(harness)

        self.assertEqual(data["period"], "day")
        self.assertTrue(data["is_current_month"])
        self.assertEqual(data["month_suffix"], "")
        mock_db.get_commission_income.assert_called_with(
            "alas", data["selected_year"], data["selected_month"]
        )


class OutputCommissionIncomeTest(unittest.TestCase):
    """渲染：历史月下禁用今日/本周，月份控件 ▶ 到当前月禁用。"""

    def _render(self, harness, income_data):
        captured = []

        def _capture_buttons(buttons, **kwargs):
            captured.append(buttons)
            return MagicMock()

        scope_context = MagicMock()
        with patch("module.webui.app_stat_commission.t", side_effect=lambda key, **kw: key), \
                patch("module.webui.app_stat_commission.use_scope") as mock_scope, \
                patch("module.webui.app_stat_commission.put_html", return_value=MagicMock()), \
                patch("module.webui.app_stat_commission.put_buttons", side_effect=_capture_buttons):
            mock_scope.return_value.__enter__ = MagicMock(return_value=scope_context)
            mock_scope.return_value.__exit__ = MagicMock(return_value=False)
            harness._output_commission_income(
                "<summary/>", "<table/>", "<recent/>", income_data
            )
        return captured

    @staticmethod
    def _income_data(year, month, is_current, period="month"):
        return {
            "period": period,
            "selected_year": year,
            "selected_month": month,
            "is_current_month": is_current,
            "month_suffix": "" if is_current else f" · {year}-{month:02d}",
            "summary": {"detail_rows": [], "total_commissions": 0},
            "recent": [],
            "item_name_map": {},
            "item_icon_map": {},
            "datetime": datetime,
            "item_meta": {},
            "item_name_lookup": {},
            "tracked_items": ["Gem"],
        }

    def test_history_month_disables_day_and_week(self):
        captured = self._render(
            _CommissionHarness(year=2026, month=8), self._income_data(2026, 8, False)
        )
        period_buttons = {btn["value"]: btn for btn in captured[0]}
        self.assertTrue(period_buttons["day"]["disabled"])
        self.assertTrue(period_buttons["week"]["disabled"])
        self.assertFalse(period_buttons["month"].get("disabled", False))

        # 月份控件：◀ ▶ 均可用（历史月还能往前/往后翻）
        month_buttons = {btn["value"]: btn for btn in captured[1]}
        self.assertFalse(month_buttons["prev"].get("disabled", False))
        self.assertFalse(month_buttons["next"].get("disabled", False))
        self.assertEqual(month_buttons["picker"]["label"], "2026-08")

    def test_current_month_enables_day_week_and_disables_next(self):
        captured = self._render(
            _CommissionHarness(), self._income_data(2026, 9, True)
        )
        period_buttons = {btn["value"]: btn for btn in captured[0]}
        self.assertFalse(period_buttons["day"].get("disabled", False))
        self.assertFalse(period_buttons["week"].get("disabled", False))

        month_buttons = {btn["value"]: btn for btn in captured[1]}
        self.assertTrue(month_buttons["next"]["disabled"])

    def test_month_switch_prev_disabled_at_floor(self):
        # 2020-01 是翻页下限，◀ 禁用防止翻进无意义年代
        captured = self._render(
            _CommissionHarness(year=2020, month=1), self._income_data(2020, 1, False)
        )
        month_buttons = {btn["value"]: btn for btn in captured[1]}
        self.assertTrue(month_buttons["prev"]["disabled"])
        self.assertFalse(month_buttons["next"].get("disabled", False))


class OpenCommissionMonthPickerTest(unittest.TestCase):
    """选择面板：年份合并当前年，未来月份禁用。"""

    def _open(self, harness, months_with_data, now):
        popups = []

        def _capture_popup(title, content, **kwargs):
            popups.append((title, content))
            return MagicMock()

        rendered = []

        def _capture_buttons(buttons, **kwargs):
            rendered.append(buttons)
            return MagicMock()

        with patch("module.webui.app_stat_commission.t", side_effect=lambda key, **kw: key), \
                patch("module.webui.app_stat_commission.datetime") as mock_dt, \
                patch("module.webui.app_stat_commission.popup", side_effect=_capture_popup), \
                patch("module.webui.app_stat_commission.close_popup"), \
                patch("module.webui.app_stat_commission.put_buttons", side_effect=_capture_buttons), \
                patch("module.statistics.cl1_database.db") as mock_db:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            mock_db.list_commission_months.return_value = months_with_data
            harness._open_commission_month_picker()
        return popups, rendered

    def test_future_months_disabled_and_years_merged(self):
        now = datetime(2026, 9, 15)
        harness = _CommissionHarness(year=2026, month=8)
        popups, rendered = self._open(
            harness, ["2026-08", "2026-09"], now
        )

        self.assertEqual(len(popups), 1)
        # 年份行：有数据的 2026 合并当前年，无重复
        self.assertEqual([btn["label"] for btn in rendered[0]], ["2026"])
        # 月份网格 12 个：所选 8 月高亮，10-12 月禁用
        self.assertEqual(len(rendered[1]), 12)
        by_label = {btn["label"]: btn for btn in rendered[1]}
        self.assertEqual(by_label["8"]["color"], "primary")
        self.assertFalse(by_label["8"]["disabled"])
        for future in ("10", "11", "12"):
            self.assertTrue(by_label[future]["disabled"], future)

    def test_years_include_history_years(self):
        now = datetime(2026, 9, 15)
        harness = _CommissionHarness()
        _, rendered = self._open(
            harness, ["2024-03", "2025-11", "2026-01"], now
        )
        # 有数据的年份倒序排列
        self.assertEqual(
            [btn["label"] for btn in rendered[0]], ["2026", "2025", "2024"]
        )


class ListCommissionMonthsTest(unittest.TestCase):
    """数据库层：只列出确实有委托条目的月份。"""

    def test_filters_empty_months(self):
        import tempfile
        from pathlib import Path

        from module.statistics.cl1_database import Cl1Database

        with tempfile.TemporaryDirectory() as tmp:
            database = Cl1Database(db_path=Path(tmp) / "cl1.sqlite3")
            # 当月走 add 接口写入
            database.add_commission_income(
                "alas", {"Gem": 30}, commission_count=1
            )
            # 历史月手工构造：2026-08 有条目，2026-07 空月份
            for month_key, entries in (
                ("2026-08", [_entry("2026-08-01T09:00:00")]),
                ("2026-07", []),
            ):
                data = database.get_stats("alas", month_key)
                data["commission_income_entries"] = entries
                database.save_stats("alas", month_key, data)

            self.assertEqual(
                database.list_commission_months("alas"),
                # 当月（add 接口写入）与 2026-08 有条目，空的 2026-07 被过滤
                sorted({datetime.now().strftime("%Y-%m"), "2026-08"}),
            )


if __name__ == "__main__":
    unittest.main()
