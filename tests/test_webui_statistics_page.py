import threading
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from module.webui.app_statistics_page import StatisticsPageMixin


class _OutputStub:
    def style(self, _value):
        return self


class _TaskHandlerStub:
    def __init__(self):
        self.added = []

    def add(self, func, delay, pending_delete=False):
        self.added.append((func, delay, pending_delete))


class _StatisticsHarness(StatisticsPageMixin):
    def __init__(self):
        self.alas_name = "alas"
        self.page = "Overview"
        self._page_lock = threading.Lock()
        self._statistics_cache_key = None
        self._statistics_source_signature = None
        self._statistics_refresh_pending = False
        self.signature = "v1"
        self.rendered = []
        self.cleaned = []
        self.task_handler = _TaskHandlerStub()

    def init_menu(self, name=None):
        self.page = name

    def set_title(self, _title):
        return None

    def cleanup_client_resources(self, *names):
        self.cleaned.append(names)

    def _get_statistics_source_signature(self):
        return self.signature

    def _render_ap_chart(self):
        self.rendered.append("ap")

    def _render_opsi_stats(self):
        self.rendered.append("opsi")

    def _render_resource_delta(self):
        self.rendered.append("delta")

    def _render_ship_exp(self):
        self.rendered.append("ship")

    def _render_commission_income(self):
        self.rendered.append("commission")


class TestStatisticsPageCache(unittest.TestCase):
    def setUp(self):
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch(
                "module.webui.app_statistics_page.put_scope",
                return_value=_OutputStub(),
            ),
            patch(
                "module.webui.app_statistics_page.put_button",
                return_value=_OutputStub(),
            ),
            patch("module.webui.app_statistics_page.t", side_effect=lambda key: key),
            patch("module.webui.app_statistics_page.run_js"),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_reopening_unchanged_page_reuses_existing_render(self):
        self.gui.alas_set_stat()
        self.assertEqual(
            ["ap", "opsi", "delta", "ship", "commission"],
            self.gui.rendered,
        )

        self.gui.rendered.clear()
        self.gui.alas_set_stat()

        self.assertEqual([], self.gui.rendered)
        self.assertEqual(2, len(self.gui.task_handler.added))
        for callback, delay, pending_delete in self.gui.task_handler.added:
            self.assertEqual("_refresh_statistics_if_changed", callback.__name__)
            self.assertEqual(15, delay)
            self.assertTrue(pending_delete)

    def test_local_data_change_marks_refresh_without_replacing_sections(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        self.gui.signature = "v2"

        self.gui._refresh_statistics_if_changed()

        self.assertEqual([], self.gui.rendered)
        self.assertTrue(self.gui._statistics_refresh_pending)

        self.gui._refresh_statistics_page()

        self.assertEqual(
            ["ap", "opsi", "delta", "ship", "commission"],
            self.gui.rendered,
        )
        self.assertEqual("v2", self.gui._statistics_source_signature)
        self.assertFalse(self.gui._statistics_refresh_pending)

    def test_switching_instance_replaces_cache_and_cleans_charts(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        self.gui.alas_name = "alas2"

        self.gui.alas_set_stat()

        self.assertEqual(
            ["ap", "opsi", "delta", "ship", "commission"],
            self.gui.rendered,
        )
        self.assertEqual(
            [
                (
                    "__apChartCleanups",
                    "__resourceDeltaChartCleanups",
                )
            ],
            self.gui.cleaned,
        )

    def test_background_check_does_not_render_after_navigation(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        self.gui.signature = "v2"
        self.gui.page = "Overview"

        self.gui._refresh_statistics_if_changed()

        self.assertEqual([], self.gui.rendered)


class TestDeltaTimelineTranslationPlaceholders(unittest.TestCase):
    """回归：t() 会无条件对翻译执行 .format()，含 {n} 占位符的翻译若直接取用会
    KeyError('n')（2026-09-15 时间轴上线当日事故：加载真实 i18n 后点粒度按钮即崩）。
    本测试加载真实语言包，锁定占位符必须以字面形式回传 JS 的行为。"""

    def test_render_delta_timeline_with_loaded_i18n(self):
        from module.webui import lang as webui_lang
        from module.webui.app_stat_delta import ResourceDeltaStatisticsMixin

        saved_dic, saved_lang = webui_lang.dic_lang, webui_lang.LANG
        webui_lang.dic_lang = {}
        webui_lang.LANG = "zh-CN"
        try:
            webui_lang.reload()
            # 事故现场复现：不带占位符取用必须仍然失败（t() 强制 format 的既定行为）
            with self.assertRaises(KeyError):
                webui_lang.t("Gui.Stat.DeltaTaskTimes")
            # 修复方式：把 n 传回字面 "{n}"，由 JS 端按节点替换
            self.assertIn(
                "{n}", webui_lang.t("Gui.Stat.DeltaTaskTimes", n="{n}")
            )
            self.assertIn("{n}", webui_lang.t("Gui.Stat.DeltaMore", n="{n}"))

            gui = type(
                "Gui", (ResourceDeltaStatisticsMixin,), {"alas_name": "alas"}
            )()
            timeline = [
                {
                    "source": "Event2",
                    "first_ts": "2026-09-15T21:15:52",
                    "last_ts": "2026-09-15T21:15:52",
                    "events": 1,
                    "resources": {
                        "Pt": {"increased": 90, "consumed": 0, "events": 1}
                    },
                }
            ]
            summary = [
                {
                    "resource": "Pt",
                    "increase": 90,
                    "decrease": 0,
                    "net": 90,
                    "events": 1,
                }
            ]

            with patch(
                "module.webui.app_stat_delta.put_html"
            ) as mock_html, patch(
                "module.webui.app_stat_delta.run_js"
            ) as mock_js:
                gui._render_delta_timeline(timeline, summary)

            mock_html.assert_called_once()
            mock_js.assert_called_once()
            js_code = mock_js.call_args[0][0]
            self.assertIn("运行 {n} 次", js_code)
            self.assertIn("+{n} 项", js_code)
            for placeholder in (
                "__TASKS__", "__CHART_ID__", "__TXT_GAIN__", "__TXT_LOSS__",
                "__TXT_TIMES__", "__TXT_MORE__",
            ):
                self.assertNotIn(placeholder, js_code)
        finally:
            webui_lang.dic_lang, webui_lang.LANG = saved_dic, saved_lang


if __name__ == "__main__":
    unittest.main()
