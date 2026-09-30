"""体力图表视图（分时/天视图/月视图）的装配回归测试。

覆盖 2026-09-20 恢复视图切换时定下的行为：
- 视图取值必须收敛到白名单，非法值回退分时曲线
- 已下线的"日变化表格 / 增减柱状图"不得被采纳（含历史配置里的残留值）
- 日/月聚合的 OHLC 口径
- 按钮组只渲染三个视图，并与标题同排（标题在左、切换在右）
- 切视图复用已读数据集，不重复读数据源

另覆盖 2026-09-30 加的历史月份：
- 标题行多一个月份胶囊（显示统计页当前查看的月份，点开共用月份选择器）
- 数据集缓存带月份校验，切了月份就不许复用上个月的快照
"""

import unittest
from datetime import datetime
from unittest.mock import patch

from module.webui.app_stat_action_point import ActionPointStatisticsMixin
from module.webui.app_statistics_page import StatisticsPageMixin


def _points():
    def point(year, month, day, hour, minute, ap, source="-"):
        return {
            "dt": datetime(year, month, day, hour, minute),
            "ap": ap,
            "source": source,
        }

    return [
        point(2026, 9, 18, 23, 30, 100, "cl1"),
        point(2026, 9, 19, 8, 0, 120, "meow"),
        point(2026, 9, 19, 20, 0, 90, "-"),
        point(2026, 9, 20, 9, 0, 150, "cl1"),
        point(2026, 9, 20, 15, 0, 130, "meow"),
    ]


class _ChartHarness(ActionPointStatisticsMixin, StatisticsPageMixin):
    """只保留图表装配所需状态的测试替身。

    带上 ``StatisticsPageMixin`` 是为了拿到真实的查看月份解析
    （``_stat_month_key`` 等）——月份状态本就是统计页级别的共用状态。
    """

    def __init__(self, view="line"):
        self.alas_name = "alas"
        self._ap_chart_view = view
        self.rendered = []

    def _render_ap_chart(self, reuse_dataset=False):
        self.rendered.append((getattr(self, "_ap_chart_view", None), reuse_dataset))


class _ApChartTestCase(unittest.TestCase):
    def setUp(self):
        self.patches = (
            patch(
                "module.webui.app_stat_action_point.t",
                side_effect=lambda key, **kwargs: key,
            ),
            patch(
                "module.webui.app_stat_action_point.current_time",
                return_value=datetime(2026, 9, 20, 16, 0),
            ),
            # 月份解析走统计页那份实现，两处都要钉住时钟才是确定性的
            patch(
                "module.webui.app_statistics_page.current_time",
                return_value=datetime(2026, 9, 20, 16, 0),
            ),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def _series(self, view):
        return _ChartHarness(view)._build_ap_chart_series(_points())


class TestApChartViewWhitelist(_ApChartTestCase):
    def test_unknown_view_falls_back_to_line(self):
        data = self._series("nonsense")
        self.assertEqual("line", data["current_view"])
        self.assertEqual("Gui.Stat.ViewTitleLine", data["view_title"])
        self.assertEqual([], data["opens"])

    def test_all_documented_views_are_supported(self):
        self.assertEqual(
            ("line", "day", "month"),
            ActionPointStatisticsMixin.AP_CHART_VIEWS,
        )

    def test_switch_ignores_unknown_view_and_skips_same_view(self):
        gui = _ChartHarness("line")
        gui._switch_ap_chart_view("bogus")
        self.assertEqual([], gui.rendered)
        self.assertEqual("line", gui._ap_chart_view)

        gui._switch_ap_chart_view("line")
        self.assertEqual([], gui.rendered)

        gui._switch_ap_chart_view("day")
        self.assertEqual([("day", True)], gui.rendered)
        self.assertEqual("day", gui._ap_chart_view)

    def test_removed_views_are_rejected(self):
        """日变化表格与增减柱状图已下线，残留配置值不得被采纳。"""
        gui = _ChartHarness("table")
        gui._switch_ap_chart_view("bar")
        self.assertEqual([], gui.rendered)
        self.assertEqual("table", gui._ap_chart_view)

        data = gui._build_ap_chart_series(_points())
        self.assertEqual("line", data["current_view"])


class TestApChartAggregation(_ApChartTestCase):
    def test_month_view_aggregates_daily_ohlc(self):
        data = self._series("month")
        self.assertEqual(["09-18", "09-19", "09-20"], data["labels"])
        self.assertEqual([100, 120, 150], data["opens"])
        self.assertEqual([100, 90, 130], data["closes"])
        self.assertEqual([100, 120, 150], data["highs"])
        self.assertEqual([100, 90, 130], data["lows"])
        self.assertEqual([1, 2, 2], data["counts"])
        self.assertEqual("Gui.Stat.ViewTitleMonth", data["view_title"])

    def test_day_view_uses_latest_available_date(self):
        data = self._series("day")
        self.assertEqual(["09:00", "15:00"], data["labels"])
        self.assertEqual([150, 130], data["closes"])
        self.assertEqual("Gui.Stat.CandlesCount", data["data_points_text"])

    def test_line_view_keeps_raw_points(self):
        data = self._series("line")
        self.assertEqual([100, 120, 90, 150, 130], data["ap_list"])
        self.assertEqual(5, len(data["labels"]))


class _OutputStub:
    """记录输出调用的入参与附加样式。"""

    def __init__(self, captured, **payload):
        self.captured = captured
        self.captured.update(payload)

    def style(self, css):
        self.captured["style"] = css
        return self


class TestApChartViewSwitcher(_ApChartTestCase):
    def _render(self, view="month", title="按月", view_month=None):
        captured = {"button_groups": [], "onclicks": []}

        def fake_put_buttons(buttons, onclick=None, **kwargs):
            captured["button_groups"].append(buttons)
            captured["onclicks"].append(onclick)
            # 视图按钮组是最后一次调用，保持 captured["buttons"] 的旧语义
            return _OutputStub(captured, buttons=buttons)

        harness = _ChartHarness(view)
        if view_month is not None:
            harness._stat_view_month = view_month
        with (
            patch(
                "module.webui.app_stat_action_point.put_buttons",
                side_effect=fake_put_buttons,
            ),
            patch(
                "module.webui.app_stat_action_point.put_html",
                side_effect=lambda html: _OutputStub(captured, title_html=html),
            ),
            patch(
                "module.webui.app_stat_action_point.put_row",
                side_effect=lambda items, size=None: _OutputStub(
                    captured, row_items=items, row_size=size
                ),
            ),
        ):
            harness._render_ap_chart_view_switcher(title, view)
        return captured

    def test_switcher_renders_three_buttons(self):
        captured = self._render()
        self.assertEqual(
            ["line", "day", "month"],
            [button["value"] for button in captured["buttons"]],
        )
        colors = {b["value"]: b["color"] for b in captured["buttons"]}
        self.assertEqual("primary", colors["month"])
        self.assertEqual("off", colors["line"])

    def test_title_row_keeps_title_left_and_switcher_right(self):
        """对齐 statistics-v2 的卡片头部：标题在左、月份胶囊与粒度切换在右。"""
        captured = self._render()
        self.assertEqual("auto 1fr auto auto", captured["row_size"])
        self.assertIn("按月", captured["title_html"])
        self.assertIn("align-items:center", captured["style"])

    def test_month_capsule_shows_view_month_and_opens_picker(self):
        """月份胶囊显示当前查看月份，回调指向统计页共用的月份选择器。"""
        captured = self._render()
        month_buttons = captured["button_groups"][0]
        self.assertEqual(1, len(month_buttons))
        self.assertEqual("2026-09", month_buttons[0]["label"])
        self.assertEqual("month", month_buttons[0]["value"])

        harness = _ChartHarness("line")
        opened = []
        harness._open_stat_month_picker = lambda: opened.append(1)
        harness._open_ap_month_picker("month")
        self.assertEqual([1], opened)

    def test_month_capsule_follows_selected_history_month(self):
        captured = self._render(view_month=(2026, 8))
        month_buttons = captured["button_groups"][0]
        self.assertEqual("2026-08", month_buttons[0]["label"])


class TestApChartDatasetReuse(_ApChartTestCase):
    """切视图只换聚合口径，原始快照复用，避免重复读三个数据源。"""

    def _harness_with_counter(self):
        gui = _ChartHarness("line")
        calls = []

        def fake_timelines():
            calls.append(1)
            return (
                [{"ts": "2026-09-20T09:00:00", "ap_total": 150, "source": "cl1"}],
                [],
                [],
            )

        gui._load_ap_chart_timelines = fake_timelines
        return gui, calls

    def test_reuse_skips_reloading_data_sources(self):
        gui, calls = self._harness_with_counter()

        first = gui._load_ap_chart_dataset()
        second = gui._load_ap_chart_dataset(reuse=True)

        self.assertEqual(1, len(calls))
        self.assertIs(first, second)

    def test_without_cache_reuse_still_loads(self):
        gui, calls = self._harness_with_counter()

        gui._load_ap_chart_dataset(reuse=True)

        self.assertEqual(1, len(calls))

    def test_month_change_invalidates_cached_dataset(self):
        """切了查看月份就不许复用上个月的快照。"""
        gui, calls = self._harness_with_counter()

        gui._load_ap_chart_dataset()
        gui._stat_view_month = (2026, 8)
        gui._load_ap_chart_dataset(reuse=True)

        self.assertEqual(2, len(calls))

    def test_same_month_keeps_reusing_dataset(self):
        gui, calls = self._harness_with_counter()

        gui._load_ap_chart_dataset()
        gui._stat_view_month = (2026, 9)
        gui._load_ap_chart_dataset(reuse=True)

        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
