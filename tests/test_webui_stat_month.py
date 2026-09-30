"""统计页「查看月份」共用状态的回归测试。

覆盖 2026-09-30 新增的历史月份：
- 选中月份解析（脏值归一为「本月」），键与「是否本月」的判断
- 可选月份 = cl1 库 ∪ 大世界掉落库 ∪ 当前月，去重、新→旧；单源失败不影响另一侧
- 选择器按钮：本月 + 历史月份（排除当前月）；无历史月份时只提示不弹窗
- 切月份只重绘体力图与「侵蚀一卡片 + 大世界收获」，其余统计模块不动
"""

import contextlib
import threading
import unittest
from datetime import datetime
from unittest.mock import patch

from module.webui.app_statistics_page import StatisticsPageMixin

NOW = datetime(2026, 9, 30, 21, 0)


class _PageHarness(StatisticsPageMixin):
    """只保留月份状态与重绘记录。"""

    def __init__(self):
        self.alas_name = "alas"
        self.page = "Stat"
        self._statistics_cache_key = ("alas", "zh-CN")
        self._page_lock = threading.RLock()
        self.rendered = []

    def _render_ap_chart(self, reuse_dataset=False):
        self.rendered.append(("ap_chart", reuse_dataset))

    def _render_opsi_month_view(self):
        self.rendered.append(("opsi_month_view", None))


class _StatMonthTestCase(unittest.TestCase):
    def setUp(self):
        self.patches = (
            patch(
                "module.webui.app_statistics_page.current_time",
                return_value=NOW,
            ),
            patch(
                "module.webui.app_statistics_page.t",
                side_effect=lambda key, **kwargs: key,
            ),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()


class TestStatMonthResolution(_StatMonthTestCase):
    def test_defaults_to_current_month(self):
        gui = _PageHarness()
        self.assertIsNone(gui._stat_selected_month())
        self.assertEqual((2026, 9), gui._stat_month_pair())
        self.assertEqual("2026-09", gui._stat_month_key())
        self.assertTrue(gui._stat_month_is_current())

    def test_selected_month_wins(self):
        gui = _PageHarness()
        gui._stat_view_month = (2026, 8)

        self.assertEqual((2026, 8), gui._stat_selected_month())
        self.assertEqual((2026, 8), gui._stat_month_pair())
        self.assertEqual("2026-08", gui._stat_month_key())
        self.assertFalse(gui._stat_month_is_current())

    def test_dirty_values_fall_back_to_current_month(self):
        """页面状态可能来自手动改动的会话或旧版本，脏值一律当「本月」。"""
        for dirty in ("2026-08", (2026, 13), (None, None), (2026,), 202608, None):
            with self.subTest(dirty=dirty):
                gui = _PageHarness()
                gui._stat_view_month = dirty
                self.assertIsNone(gui._stat_selected_month())
                self.assertEqual((2026, 9), gui._stat_month_pair())

    def test_string_digits_are_coerced(self):
        gui = _PageHarness()
        gui._stat_view_month = ("2026", "8")
        self.assertEqual((2026, 8), gui._stat_selected_month())


class TestStatAvailableMonths(_StatMonthTestCase):
    def _months(self, cl1, opsi, cl1_error=None, opsi_error=None):
        gui = _PageHarness()
        with (
            patch(
                "module.statistics.opsi_month.get_available_months",
                side_effect=cl1_error or (lambda instance: cl1),
            ),
            patch(
                "module.statistics.azurstats.AzurStats.get_opsi_drop_available_months",
                return_value=opsi,
                side_effect=opsi_error,
            ),
        ):
            return gui._stat_available_months()

    def test_unions_sources_and_sorts_desc(self):
        months = self._months(
            [(2026, 8), (2026, 7)], [(2026, 9), (2026, 8)]
        )
        self.assertEqual([(2026, 9), (2026, 8), (2026, 7)], months)

    def test_current_month_always_present(self):
        self.assertEqual([(2026, 9)], self._months([], []))

    def test_broken_cl1_source_keeps_opsi_months(self):
        months = self._months(
            [], [(2026, 7)], cl1_error=RuntimeError("db locked")
        )
        self.assertEqual([(2026, 9), (2026, 7)], months)

    def test_broken_opsi_source_keeps_cl1_months(self):
        months = self._months(
            [(2026, 7)], [], opsi_error=RuntimeError("db locked")
        )
        self.assertEqual([(2026, 9), (2026, 7)], months)


class TestStatMonthPicker(_StatMonthTestCase):
    def _open(self, history, selected=None):
        gui = _PageHarness()
        gui._stat_view_month = selected
        captured = {}
        with (
            patch.object(gui, "_stat_available_months", return_value=history),
            patch(
                "module.webui.app_statistics_page.put_buttons",
                side_effect=lambda buttons, onclick=None: captured.update(
                    buttons=buttons, onclick=onclick
                ),
            ),
            patch(
                "module.webui.app_statistics_page.popup",
                side_effect=lambda title, *a, **k: contextlib.nullcontext(),
            ) as popup_mock,
            patch(
                "module.webui.app_statistics_page.toast"
            ) as toast_mock,
        ):
            gui._open_stat_month_picker()
        captured["popup"] = popup_mock
        captured["toast"] = toast_mock
        return captured

    def test_current_month_is_first_option(self):
        captured = self._open([(2026, 9), (2026, 8)])
        buttons = captured["buttons"]
        self.assertEqual(
            "Gui.Stat.MeowLootCurrentMonthOption", buttons[0]["label"]
        )
        self.assertIsNone(buttons[0]["value"])
        self.assertEqual("primary", buttons[0]["color"])
        # 当前月不再单独列一遍
        self.assertEqual(
            [(2026, 8)], [b["value"] for b in buttons[1:]]
        )

    def test_selected_history_month_is_highlighted(self):
        captured = self._open([(2026, 9), (2026, 8)], selected=(2026, 8))
        buttons = captured["buttons"]
        self.assertEqual("secondary", buttons[0]["color"])
        self.assertEqual("primary", buttons[1]["color"])

    def test_no_history_only_toasts(self):
        captured = self._open([])
        captured["toast"].assert_called_once()
        self.assertEqual([], captured["popup"].call_args_list)


class TestStatMonthSwitch(_StatMonthTestCase):
    def test_switch_renders_only_month_dependent_sections(self):
        gui = _PageHarness()
        gui._set_stat_month((2026, 8))

        self.assertEqual((2026, 8), gui._stat_view_month)
        self.assertEqual(
            [("ap_chart", False), ("opsi_month_view", None)], gui.rendered
        )

    def test_back_to_current_month(self):
        gui = _PageHarness()
        gui._stat_view_month = (2026, 8)
        gui._set_stat_month(None)

        self.assertIsNone(gui._stat_view_month)
        self.assertEqual(
            [("ap_chart", False), ("opsi_month_view", None)], gui.rendered
        )

    def test_invalid_value_resets_to_current_month(self):
        gui = _PageHarness()
        gui._set_stat_month("2026-08")
        self.assertIsNone(gui._stat_view_month)

    def test_render_skipped_outside_statistics_page(self):
        gui = _PageHarness()
        gui.page = "Overview"
        gui._render_stat_month_sections()
        self.assertEqual([], gui.rendered)

    def test_render_skipped_before_page_mounted(self):
        gui = _PageHarness()
        gui._statistics_cache_key = None
        gui._render_stat_month_sections()
        self.assertEqual([], gui.rendered)


if __name__ == "__main__":
    unittest.main()
