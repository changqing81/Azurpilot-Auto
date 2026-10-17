# -*- coding: utf-8 -*-
"""科研掉落统计视图。

覆盖四块逻辑：
1. 汇总周期 -> 取数窗口的纯函数；
2. 数据加载：collect 的取数参数与「自动期数」回填；
3. 渲染：口径 / 周期 / 期数按钮的高亮与出现条件；
4. 明细表：图标 URL、稀有度徽标、0 值淡化与空态。
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from module.webui.app_stat_research import (
    ResearchStatisticsMixin,
    _research_period_window,
)


class ResearchPeriodWindowTest(unittest.TestCase):
    """汇总周期 -> (start, end) 窗口。"""

    NOW = datetime(2026, 10, 17, 12, 30, 0)

    def test_month_window_starts_at_first_day(self):
        start, end = _research_period_window("month", now=self.NOW)
        self.assertEqual(start, datetime(2026, 10, 1, 0, 0, 0))
        self.assertEqual(end, self.NOW)

    def test_rolling_windows(self):
        self.assertEqual(
            _research_period_window("days30", now=self.NOW)[0],
            self.NOW - timedelta(days=30),
        )
        self.assertEqual(
            _research_period_window("days90", now=self.NOW)[0],
            self.NOW - timedelta(days=90),
        )

    def test_all_time_starts_from_2020(self):
        start, _ = _research_period_window("all", now=self.NOW)
        self.assertEqual((start.year, start.month, start.day), (2020, 1, 1))


def _collect_data(**overrides):
    """构造 collect() 形状的返回：一件已掉落 + 一件本期未掉的固定清单项。"""
    data = {
        "scope": "series",
        "series": 9,
        "available": [9, 8],
        "items": [
            {
                "name": "BlueprintTakahashi",
                "zh": "蓝图：高桥",
                "en": "Blueprint: Takahashi",
                "rarity": 4,
                "amount": 3,
                "count": 2,
                "avg": 1.5,
                "today": 1,
                "month": 3,
            },
            {
                "name": "Prototype_Carrier_Based_Ta_152_C_1_R14_T0",
                "zh": "试作型舰载机 Ta 152C",
                "en": "",
                "rarity": 5,
                "amount": 0,
                "count": 0,
                "avg": 0,
                "today": 0,
                "month": 0,
            },
        ],
        "series_items": ["BlueprintTakahashi"],
        "total": 3,
        "today": 1,
        "month": 3,
        "records": 2,
    }
    data.update(overrides)
    return data


class _ResearchHarness(ResearchStatisticsMixin):
    """最小化宿主：只提供视图用到的实例名与渲染状态。"""

    def __init__(self):
        self.alas_name = "alas"
        self._research_stats_scope = "series"
        self._research_stats_period = "days90"
        self._research_stats_series = 0


class LoadResearchStatsDataTest(unittest.TestCase):
    """数据加载：取数参数与自动期数回填。"""

    def _load(self, harness, data=None):
        import module.statistics.research_stats as research_stats

        mock_collect = MagicMock(return_value=data if data is not None else _collect_data())
        with patch.object(research_stats, "collect", mock_collect), patch(
            "module.webui.app_stat_research.t", side_effect=lambda key, **kw: key
        ):
            loaded = harness._load_research_stats_data()
        return loaded, mock_collect

    def test_uses_selected_period_window_and_remembers_series(self):
        harness = _ResearchHarness()
        loaded, mock_collect = self._load(harness)

        self.assertEqual(loaded["series"], 9)
        # 期数 0 = 自动取最新一期；collect 解析出 9 后应回填到视图状态
        self.assertEqual(harness._research_stats_series, 9)
        args, kwargs = mock_collect.call_args
        self.assertEqual(args[0], "alas")
        self.assertEqual(kwargs["scope"], "series")
        self.assertEqual(kwargs["series"], 0)
        start, end = kwargs["start"], kwargs["end"]
        self.assertEqual(end - start, timedelta(days=90))

    def test_no_instance_returns_none(self):
        harness = _ResearchHarness()
        harness.alas_name = None
        loaded, mock_collect = self._load(harness)
        self.assertIsNone(loaded)
        mock_collect.assert_not_called()


class OutputResearchControlsTest(unittest.TestCase):
    """控件：口径 / 周期 / 期数 三行的出现条件与高亮。"""

    def _render(self, harness, data):
        captured = []

        def _capture_buttons(buttons, **kwargs):
            captured.append(buttons)
            return MagicMock()

        with patch(
            "module.webui.app_stat_research.t", side_effect=lambda key, **kw: key
        ), patch(
            "module.webui.app_stat_research.put_buttons", side_effect=_capture_buttons
        ):
            harness._output_research_controls(data)
        return captured

    def test_series_scope_renders_three_rows(self):
        harness = _ResearchHarness()
        captured = self._render(harness, _collect_data())

        self.assertEqual(3, len(captured))
        scope_buttons = {btn["value"]: btn for btn in captured[0]}
        self.assertEqual(scope_buttons["series"]["color"], "primary")
        period_buttons = {btn["value"]: btn for btn in captured[1]}
        self.assertEqual(period_buttons["days90"]["color"], "primary")
        # 期数按钮与上游前端一致：固定列出名称表收录的全部期数（1~9），
        # 不因库里暂时没有该期记录而隐藏（新期刚开也要能先看收集清单）。
        series_buttons = [(btn["value"], btn["color"]) for btn in captured[2]]
        self.assertEqual(
            [(9, "primary")] + [(value, "secondary") for value in range(8, 0, -1)],
            series_buttons,
        )

    def test_consumable_scope_hides_series_row(self):
        harness = _ResearchHarness()
        data = _collect_data(scope="consumable", series=0, available=[])
        captured = self._render(harness, data)

        self.assertEqual(2, len(captured))
        scope_buttons = {btn["value"]: btn for btn in captured[0]}
        self.assertEqual(scope_buttons["consumable"]["color"], "primary")

    def test_series_row_shows_all_series_without_records(self):
        harness = _ResearchHarness()
        captured = self._render(harness, _collect_data(available=[]))
        self.assertEqual(3, len(captured))
        self.assertEqual(
            [9, 8, 7, 6, 5, 4, 3, 2, 1],
            [btn["value"] for btn in captured[2]],
        )


class BuildResearchTableTest(unittest.TestCase):
    """明细表：图标 URL、稀有度徽标、0 值淡化。"""

    def _html(self, harness, data):
        with patch("module.webui.app_stat_research.t", side_effect=lambda key, **kw: key):
            return harness._build_research_table_html(data)

    def test_rows_render_icons_badges_and_dimmed_zeros(self):
        harness = _ResearchHarness()
        html = self._html(harness, _collect_data())

        # 图标必须是**相对路径**：手机远控（P2P）挂在 /p2p/<peer>/ 子路径下，
        # 绝对路径 /static/... 会指向信号服务器根而裂图。
        self.assertIn(
            'src="static/assets/stats/research_items/BlueprintTakahashi.png"', html
        )
        self.assertNotIn('src="/static/', html)
        self.assertIn("蓝图：高桥", html)
        # 金图纸徽标（rarity=4 -> 金）与彩装徽标（rarity=5 -> 彩）
        self.assertIn("research-rarity", html)
        self.assertIn("金", html)
        self.assertIn("彩", html)
        # 本期未掉过的固定清单项：数量 0 淡显
        self.assertIn('<span style="opacity:.5">0</span>', html)
        # 平均值是小数时按 g 格式（1.5 不带多余小数）
        self.assertIn("1.5", html)

    def test_empty_items_returns_empty_state_but_keeps_controls(self):
        """某周期没有记录时：空态提示照常给，但口径/周期/期数控件必须保留，
        否则用户会被空态锁死在当前选择上。"""
        harness = _ResearchHarness()
        rendered = []
        buttons = []
        scope_context = MagicMock()
        with patch(
            "module.webui.app_stat_research.t", side_effect=lambda key, **kw: key
        ), patch("module.webui.app_stat_research.use_scope") as mock_scope, patch(
            "module.webui.app_stat_research.put_html",
            side_effect=lambda html, **kw: rendered.append(html) or MagicMock(),
        ), patch(
            "module.webui.app_stat_research.put_buttons",
            side_effect=lambda items, **kw: buttons.append(items) or MagicMock(),
        ), patch.object(
            harness,
            "_load_research_stats_data",
            return_value=_collect_data(items=[], available=[], records=0),
        ):
            mock_scope.return_value.__enter__ = MagicMock(return_value=scope_context)
            mock_scope.return_value.__exit__ = MagicMock(return_value=False)
            harness._render_research_stats()

        joined = "\n".join(rendered)
        self.assertIn("st-empty", joined)
        self.assertIn("Gui.Stat.ResearchEmptyTitle", joined)
        self.assertIn("Gui.Stat.ResearchEmptyDesc", joined)
        self.assertNotIn("simple-table", joined)
        # 三行控件（口径 / 周期 / 期数）在空态下仍然渲染
        self.assertEqual(3, len(buttons))


if __name__ == "__main__":
    unittest.main()
