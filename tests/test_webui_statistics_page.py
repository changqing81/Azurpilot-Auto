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

    def add(self, func, delay, pending_delete=False, group="fast"):
        self.added.append((func, delay, pending_delete))

    def get_task(self, name):
        return None


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

    # 舰队科技 / 心智计算器的面板入口（真实现由 FleetTechnologyMixin 提供）
    def _ft_state(self):
        return None

    def _ft_bind_callbacks(self):
        return None

    def _ft_render_tech_panel(self, force=False):
        self.rendered.append("tech")

    def _ft_render_mind_panel(self, force=False):
        self.rendered.append("mind")

    def _ft_render_scan_controls(self):
        self.rendered.append("scan_controls")

    def _ft_data_refresh(self):
        return None

    def run_fill_once(self):
        """跑一次后台填充任务：统计分区渲染已改为挂载后的首跑任务。"""
        for callback, _delay, _pending in list(self.task_handler.added):
            if getattr(callback, "__name__", "") == "_fill_statistics_sections":
                gen = callback()
                next(gen)   # 到第一个 yield：无工作
                next(gen)   # 执行一次填充
                return True
        return False


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
            # 分页式改版的页头 / 页签 / 科研占位是 HTML 字符串，走 put_html 输出。
            # 与 put_scope / put_button 一样，这里不需要真实会话。
            patch(
                "module.webui.app_statistics_page.put_html",
                return_value=_OutputStub(),
            ),
            # 舰队内容的宿主与点击桥 pin 也走真输出函数：同样需要无会话桩。
            patch(
                "module.webui.app_statistics_page.put_input",
                return_value=_OutputStub(),
            ),
            # lang.t() 支持占位符参数（内部会 .format()），页头的
            # 「数据截至 {time} · 实例 {instance}」就依赖这一点，所以桩要收下 kwargs。
            patch(
                "module.webui.app_statistics_page.t",
                side_effect=lambda key, *args, **kwargs: key,
            ),
            patch("module.webui.app_statistics_page.run_js"),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_reopening_unchanged_page_reuses_existing_render(self):
        self.gui.alas_set_stat()
        # 骨架先行：挂载时只排队填充，分区渲染在首跑任务里完成。
        self.assertEqual([], self.gui.rendered)
        self.assertTrue(self.gui.run_fill_once())
        self.assertEqual(
            ["ap", "opsi", "delta", "ship", "commission", "tech", "mind"],
            self.gui.rendered,
        )

        self.gui.rendered.clear()
        self.gui.task_handler.added.clear()
        self.gui.alas_set_stat()

        # 面板原样复用；只补画两个扫描开关所在工具栏（切页清掉了它们的轮询任务），
        # 不再排队填充。
        self.assertEqual(["scan_controls"], self.gui.rendered)
        self.assertFalse(self.gui._statistics_fill_queued)
        self.assertEqual(
            [
                ("_refresh_statistics_if_changed", 15, True),
                ("_ft_data_refresh", 5, True),
            ],
            [
                (callback.__name__, delay, pending_delete)
                for callback, delay, pending_delete in self.gui.task_handler.added
            ],
        )

    def test_local_data_change_marks_refresh_without_replacing_sections(self):
        self.gui.alas_set_stat()
        self.gui.run_fill_once()
        self.gui.rendered.clear()
        self.gui.signature = "v2"

        self.gui._refresh_statistics_if_changed()

        self.assertEqual([], self.gui.rendered)
        self.assertTrue(self.gui._statistics_refresh_pending)

        self.gui._refresh_statistics_page()
        # 刷新也走后台填充：点击路径本身不渲染。
        self.assertEqual([], self.gui.rendered)
        self.gui.run_fill_once()

        self.assertEqual(
            ["ap", "opsi", "delta", "ship", "commission", "tech", "mind"],
            self.gui.rendered,
        )
        self.assertEqual("v2", self.gui._statistics_source_signature)
        self.assertFalse(self.gui._statistics_refresh_pending)

    def test_switching_instance_replaces_cache_and_cleans_charts(self):
        self.gui.alas_set_stat()
        self.gui.run_fill_once()
        self.gui.rendered.clear()
        self.gui.alas_name = "alas2"

        self.gui.alas_set_stat()
        self.gui.run_fill_once()

        self.assertEqual(
            ["ap", "opsi", "delta", "ship", "commission", "tech", "mind"],
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


class TestFleetPanelsOnStatisticsPage(unittest.TestCase):
    """舰队科技 / 心智计算器以统计页页签的形式挂载（2026-10-16 入口迁移）。"""

    def setUp(self):
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch("module.webui.app_statistics_page.run_js"),
            patch(
                "module.webui.app_statistics_page.t",
                side_effect=lambda key, *args, **kwargs: key,
            ),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def test_tab_table_appends_the_two_fleet_panels(self):
        from module.webui.app_statistics_page import _STAT_PANEL_SCOPES, _STAT_TABS

        self.assertEqual(
            [
                ("opsi", "Gui.Stat.TabOpsi"),
                ("commission", "Gui.Stat.TabCommission"),
                ("research", "Gui.Stat.TabResearch"),
                ("resource", "Gui.Stat.TabResource"),
                ("fleet", "Gui.FleetTechnology.TabTech"),
                ("mind", "Gui.FleetTechnology.TabMind"),
            ],
            [(panel, key) for panel, key, _badge in _STAT_TABS],
        )
        self.assertEqual(("ft_tech",), _STAT_PANEL_SCOPES["fleet"])
        self.assertEqual(("ft_mind",), _STAT_PANEL_SCOPES["mind"])

    def test_mount_declares_fleet_scopes_tabs_and_bridge_pin(self):
        from module.webui import app_statistics_page as page

        scopes, inputs, htmls = [], [], []
        with patch.object(
            page,
            "put_scope",
            side_effect=lambda name, *args, **kwargs: scopes.append(name) or _OutputStub(),
        ), patch.object(
            page,
            "put_input",
            side_effect=lambda *args, **kwargs: inputs.append(kwargs.get("name")) or _OutputStub(),
        ), patch.object(
            page,
            "put_html",
            side_effect=lambda html, *args, **kwargs: htmls.append(html) or _OutputStub(),
        ), patch.object(page, "put_button", return_value=_OutputStub()):
            self.gui.alas_set_stat()

        self.assertIn("ft_tech", scopes)
        self.assertIn("ft_mind", scopes)
        self.assertIn("ft_bridge", scopes)
        self.assertEqual(["ft_bridge_pin"], inputs)
        tabs_html = next(html for html in htmls if "st-tabs" in html)
        for panel in ("fleet", "mind"):
            self.assertIn(f'data-panel="{panel}"', tabs_html)
        self.assertIn("Gui.FleetTechnology.TabTech", tabs_html)
        self.assertIn("Gui.FleetTechnology.TabMind", tabs_html)


class TabSwitchScriptTests(unittest.TestCase):
    """页签切换脚本：先量指示条、再切面板，收尾（resize / 指示条校正）延到下一帧。

    实测（2026-10-17 浏览器）：面板类名切换会让新面板首次参与布局，随后
    moveIndicator 读 offsetWidth 会同步强制整页布局 —— 科技列表 / 心智
    计算器这类大面板每次冷切换阻塞主线程约 0.5 秒。指示条几何只与页签自身
    有关，提前量即可把强制布局移出点击处理，收尾动作放到 rAF 里合并执行。
    """

    def _layout_script(self) -> str:
        scripts = []
        with patch(
            "module.webui.app_statistics_page.run_js",
            side_effect=lambda script, **kwargs: scripts.append(script),
        ), patch(
            "module.webui.app_statistics_page.use_scope",
            side_effect=lambda *_args, **_kwargs: nullcontext(),
        ), patch(
            "module.webui.app_statistics_page.put_scope", return_value=_OutputStub()
        ), patch(
            "module.webui.app_statistics_page.put_button", return_value=_OutputStub()
        ), patch(
            # 桥接 pin 的 put_input 必须打桩：无会话时会退回 PyWebIO 脚本模式挂起。
            "module.webui.app_statistics_page.put_input", return_value=_OutputStub()
        ), patch("module.webui.app_statistics_page.put_html", return_value=_OutputStub()):
            gui = _StatisticsHarness()
            gui.alas_set_stat()
        return next(script for script in scripts if "PANEL_ORDER" in script)

    def test_indicator_is_measured_before_panel_toggle(self):
        script = self._layout_script()
        self.assertLess(
            script.index("moveIndicator(target);"),
            script.index("panel.classList.toggle("),
        )

    def test_resize_is_deferred_and_coalesced(self):
        script = self._layout_script()
        self.assertIn("requestAnimationFrame(settle)", script)
        self.assertIn("if (!resizeQueued)", script)
        # 只允许 settle 里那一处同步派发（点击处理路径不得直接派发）。
        self.assertEqual(1, script.count('window.dispatchEvent(new Event("resize"));'))


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
