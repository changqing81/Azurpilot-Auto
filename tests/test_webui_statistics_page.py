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

    def _render_research_stats(self):
        self.rendered.append("research")

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
        """跑一次后台填充任务（取最近排队的一个并推进两步）。

        真实实现注册的是生成器对象（见 FillTaskWiringTests），替身时代
        可能拿到函数；两种都支持。取最近一个，避免重复推进已跑完的旧任务。
        """
        for callback, _delay, _pending in reversed(self.task_handler.added):
            if getattr(callback, "__name__", "") == "_fill_statistics_sections":
                gen = callback() if callable(callback) else callback
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
            # 懒加载：首屏只渲染默认页签（大世界 = ap/opsi/ship），其余面板等用户点开
            ["ap", "opsi", "ship"],
            self.gui.rendered,
        )

        self.gui.rendered.clear()
        self.gui.task_handler.added.clear()
        self.gui.alas_set_stat()

        # 面板原样复用，不再排队填充。
        # 懒加载：舰队面板还没被点开过，所以这次进入既不补画扫描工具栏、
        # 也不登记每 5 秒一次的配置刷新任务（那正是卡顿来源）。
        self.assertEqual([], self.gui.rendered)
        self.assertFalse(self.gui._statistics_fill_queued)
        # 懒加载：舰队面板未加载时不再登记 _ft_data_refresh（每 5 秒重建配置），
        # 只保留轻量的「数据源是否有变化」探测。
        self.assertEqual(
            [
                ("_refresh_statistics_if_changed", 15, True),
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
            # 懒加载：首屏只渲染默认页签（大世界 = ap/opsi/ship），其余面板等用户点开
            ["ap", "opsi", "ship"],
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
            # 懒加载：首屏只渲染默认页签（大世界 = ap/opsi/ship），其余面板等用户点开
            ["ap", "opsi", "ship"],
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
                # ft_toolbar 必须跟着 ft_tech 一起搬进「科技列表」面板：漏搬会让扫描
        # 工具栏留在页面最底部（真机截图里那条割裂白条）。
        self.assertEqual(("ft_tech", "ft_toolbar"), _STAT_PANEL_SCOPES["fleet"])
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
                # 两个桥：ft_bridge 转表格点击，st_bridge 转页签点击 + 面板高度回传
        self.assertEqual(["ft_bridge_pin", "st_bridge_pin"], inputs)
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


class FillTaskWiringTests(unittest.TestCase):
    """后台填充任务必须真能被 TaskHandler 驱动。

    2026-10-17 真机事故：``_queue_statistics_fill`` 最初把
    生成器**函数**传给 ``TaskHandler.add``，而 ``add`` 对普通可调用对象会套
    ``get_generator`` 包装——每个 tick 只是 yield 出一个新建的生成器、从不
    驱动它，分区永远不加载；用户第二次进入统计页时补画扫描开关抛
    ``AttributeError('_ft_report')``，整页打不开。旧用例用替身 ``callback()
    + next()`` 直接驱动生成器，恰好绕过 ``get_generator``，是典型 Mock 盲区。
    这里用真实 TaskHandler 跑一遍做回归锁。
    """

    def setUp(self):
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch("module.webui.app_statistics_page.put_scope", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_button", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_input", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_html", return_value=_OutputStub()),
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

    def test_fill_is_registered_as_generator_object(self):
        import inspect

        self.gui.alas_set_stat()

        fills = [
            callback
            for callback, _delay, _pending in self.gui.task_handler.added
            if getattr(callback, "__name__", "") == "_fill_statistics_sections"
        ]
        self.assertEqual(1, len(fills))
        self.assertTrue(
            inspect.isgenerator(fills[0]),
            "必须传生成器对象：传函数会被 TaskHandler.get_generator 包装后永不执行",
        )

    def test_fill_runs_once_under_real_task_handler(self):
        import time

        from module.webui.utils import TaskHandler

        gui = _StatisticsHarness()
        gui.task_handler = TaskHandler()
        try:
            gui.alas_set_stat()
            self.assertEqual([], gui.rendered)   # 骨架先行：挂载不渲染分区

            gui.task_handler.start()
            deadline = time.time() + 8
            while time.time() < deadline and len(gui.rendered) < 3:
                time.sleep(0.05)
        finally:
            gui.task_handler.stop()

        self.assertEqual(
            # 懒加载：首屏只渲染默认页签（大世界 = ap/opsi/ship），其余面板等用户点开
            ["ap", "opsi", "ship"],
            gui.rendered,
        )


class StatisticsLazyLoadingTests(unittest.TestCase):
    """懒加载：只渲染用户看过的页签，其余面板保持骨架不渲染。

    改造背景（2026-10-21）：统计页原本一次把 8 个分区全画完（含舰队科技 /
    心智计算器这类重面板），同步渲染实测 3.6 秒，期间点任何按钮都没响应；
    叠加每 5 秒一次的配置重建，界面表现为彻底卡死。现在改成按页签懒加载：
    首屏只画默认页签（大世界），点开哪个才渲染哪个，未点开的铺骨架占位。
    """

    def setUp(self):
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch("module.webui.app_statistics_page.put_scope", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_button", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_input", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_html", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.run_js"),
            patch("module.webui.app_statistics_page.pin_on_change"),
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

    def test_first_mount_only_renders_default_panel(self):
        """首屏只渲染默认页签（大世界），不得把 8 个分区全画一遍。"""
        self.gui.alas_set_stat()
        self.assertTrue(self.gui.run_fill_once())
        self.assertEqual(["ap", "opsi", "ship"], self.gui.rendered)

    def test_loaded_panels_state_tracks_default_only(self):
        self.gui.alas_set_stat()
        self.assertEqual({"opsi"}, self.gui._st_loaded_panels)

    def test_render_panel_loads_one_panel_and_marks_it(self):
        """点开某个页签只渲染该面板，并登记进已加载集合。"""
        self.gui.alas_set_stat()
        self.gui.run_fill_once()
        self.gui.rendered.clear()

        self.gui._render_statistics_panel("resource")
        self.assertEqual(["delta"], self.gui.rendered)
        self.assertIn("resource", self.gui._st_loaded_panels)
        # 大世界已加载，资源是新增的
        self.assertEqual({"opsi", "resource"}, self.gui._st_loaded_panels)

    def test_render_panel_is_idempotent(self):
        """重复点同一个页签不得重复渲染（否则内容会翻倍）。"""
        self.gui.alas_set_stat()
        self.gui.run_fill_once()
        self.gui.rendered.clear()

        self.gui._render_statistics_panel("resource")
        self.gui._render_statistics_panel("resource")
        self.gui._render_statistics_panel("resource")
        self.assertEqual(["delta"], self.gui.rendered)

    def test_render_panel_ignores_unknown_and_left_page(self):
        """未知面板、或已切离统计页时不渲染。"""
        self.gui.alas_set_stat()
        self.gui.rendered.clear()

        self.gui._render_statistics_panel("不存在的面板")
        self.assertEqual([], self.gui.rendered)

        self.gui.page = "Overview"
        self.gui._render_statistics_panel("resource")
        self.assertEqual([], self.gui.rendered)

    def test_refresh_only_rerenders_loaded_panels(self):
        """点「刷新」只刷新已加载面板，不把没看过的面板一起画出来。"""
        self.gui.alas_set_stat()
        self.gui.run_fill_once()
        self.gui._render_statistics_panel("resource")
        self.gui.rendered.clear()

        self.gui._refresh_statistics_page()
        self.gui.run_fill_once()
        # 只有已加载的 opsi + resource，不含 commission/research/fleet/mind
        self.assertEqual(["ap", "opsi", "ship", "delta"], self.gui.rendered)

    def test_bridge_panel_message_triggers_render(self):
        """点击桥收到 panel|<id> 时渲染对应面板。"""
        self.gui.alas_set_stat()
        self.gui.run_fill_once()
        self.gui.rendered.clear()

        self.gui._st_on_bridge("panel|research")
        self.assertEqual(["research"], self.gui.rendered)

    def test_bridge_height_message_only_records_height(self):
        """高度回传只记数，绝不能触发渲染（否则是无限循环）。"""
        self.gui.alas_set_stat()
        self.gui.run_fill_once()
        self.gui.rendered.clear()

        self.gui._st_on_bridge("height|opsi|912")
        self.assertEqual([], self.gui.rendered)
        self.assertEqual(912, self.gui._st_panel_heights["opsi"])

    def test_bridge_ignores_empty_and_garbage(self):
        self.gui.alas_set_stat()
        self.gui.rendered.clear()
        for value in (None, "", "garbage", "panel|", "height|x"):
            self.gui._st_on_bridge(value)
        self.assertEqual([], self.gui.rendered)

    def test_skeleton_heights_use_measured_then_estimate(self):
        """方案 C：骨架高度优先用上次实测值，没有实测用估计值。"""
        self.gui.alas_set_stat()
        heights = self.gui._stat_skeleton_heights()
        # 无实测值时用估计值
        self.assertEqual(900, heights["opsi"])
        self.assertEqual(1200, heights["fleet"])
        # 有实测值后以实测为准
        self.gui._st_panel_heights["opsi"] = 1024
        self.assertEqual(1024, self.gui._stat_skeleton_heights()["opsi"])

    def test_skeleton_height_has_floor(self):
        """实测值过小时兜底到最小高度，避免骨架塌成一条线。"""
        self.gui.alas_set_stat()
        self.gui._st_panel_heights["opsi"] = 5
        self.assertGreaterEqual(self.gui._stat_skeleton_heights()["opsi"], 240)


class StatisticsFleetToolbarScopeTests(unittest.TestCase):
    """ft_toolbar 必须搬进「科技列表」面板。

    真机事故（2026-10-21 截图）：扫描范围/扫描船坞那条工具栏留在页面最底部、
    与统计面板彻底分离，就是因为它不在 _STAT_PANEL_SCOPES 的搬迁清单里——
    PyWebIO 找不到 scope 时会在 ROOT 下新建孤儿容器。
    """

    def test_ft_toolbar_in_fleet_panel_scopes(self):
        from module.webui.app_statistics_page import _STAT_PANEL_SCOPES

        self.assertIn("ft_toolbar", _STAT_PANEL_SCOPES["fleet"])

    def test_every_scope_owned_by_exactly_one_panel(self):
        from module.webui.app_statistics_page import _STAT_PANEL_SCOPES

        seen = {}
        for panel_id, scopes in _STAT_PANEL_SCOPES.items():
            for scope in scopes:
                self.assertNotIn(
                    scope, seen,
                    f"scope {scope} 同时属于 {seen.get(scope)} 和 {panel_id}，会被搬两次",
                )
                seen[scope] = panel_id


class StatisticsSessionGuardTests(unittest.TestCase):
    """无会话时必须跳过 pin 注册，否则测试/脚本模式会永久挂住。

    实测（2026-10-21）：pywebio 的 get_session_implement() 在没有活动会话实现时
    会自动拉起 Script Mode 本地服务器并阻塞等待浏览器，整个测试进程卡死。
    """

    def test_has_webio_session_returns_false_without_session(self):
        from module.webui.app_statistics_page import _has_webio_session

        self.assertFalse(_has_webio_session())

    def test_bind_callbacks_skips_without_session(self):
        gui = _StatisticsHarness()
        with patch("module.webui.app_statistics_page.pin_on_change") as mock_pin:
            gui._st_bind_callbacks()
        mock_pin.assert_not_called()


class StatisticsBridgeRebindTests(unittest.TestCase):
    """桥接必须每次挂载都重绑。

    真机事故（2026-10-21）：切页会清空 statistics-content 并重建 st_bridge
    输入框，_st_callbacks_bound 却一直是 True，于是重挂载后不再绑定，
    pin 指向的是已被销毁的旧节点 —— 用户点页签完全没反应，只有默认页签
    （挂载时直接渲染的）有内容。日志证据：整份 GUI 日志里
    _ft_data_refresh 只被登记过 2 次，此后 16 次会话全部没有。
    """

    def setUp(self):
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch("module.webui.app_statistics_page.put_scope", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_button", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_input", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_html", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.run_js"),
            patch(
                "module.webui.app_statistics_page._has_webio_session",
                return_value=True,
            ),
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

    def test_mount_rebinds_bridge_after_remount(self):
        """重新挂载（换实例）后必须再次调用 pin_on_change。"""
        with patch(
            "module.webui.app_statistics_page.pin_on_change"
        ) as mock_pin:
            self.gui.alas_set_stat()
            self.assertEqual(1, mock_pin.call_count)

            # 换实例 → cache_key 变化 → 触发重新挂载
            self.gui.alas_name = "alas2"
            self.gui.alas_set_stat()

        self.assertEqual(
            2, mock_pin.call_count,
            "重新挂载后必须重绑 pin，否则页签点击永久失效",
        )

    def test_bind_is_skipped_without_session(self):
        """无会话时跳过绑定（不能触发 pywebio 的 Script Mode 回退而挂死）。"""
        with patch(
            "module.webui.app_statistics_page._has_webio_session",
            return_value=False,
        ), patch(
            "module.webui.app_statistics_page.pin_on_change"
        ) as mock_pin:
            self.gui._st_callbacks_bound = False
            self.gui._st_bind_callbacks()
        mock_pin.assert_not_called()


class StatisticsBridgeInputHiddenTests(unittest.TestCase):
    """点击桥的隐藏输入框必须有 CSS 隐藏。

    2026-10-21 真机截图：页面底部裸露出一行 height|mind|3603，
    就是 st_bridge 输入框（clear=True 清的是回调不是值，值会一直留着）。
    """

    def test_st_bridge_scope_is_hidden(self):
        from pathlib import Path

        css = (
            Path(__file__).resolve().parent.parent
            / "assets"
            / "gui"
            / "css"
            / "statistics-alas.css"
        ).read_text(encoding="utf-8")
        self.assertIn("#pywebio-scope-st_bridge", css)
        start = css.index("#pywebio-scope-st_bridge")
        block = css[start:start + 120]
        self.assertIn("display: none", block)


class StatisticsTabPersistenceTests(unittest.TestCase):
    """页签记忆与丢包重试：远控重连后不能把用户拽回默认页签。"""

    def test_script_uses_localstorage_and_retry(self):
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent
            / "module"
            / "webui"
            / "app_statistics_page.py"
        ).read_text(encoding="utf-8")

        # 页签记忆必须落 localStorage（window 状态在整页重载后会丢）
        self.assertIn("localStorage.setItem(STORAGE_KEY", source)
        self.assertIn("localStorage.getItem(STORAGE_KEY", source)
        self.assertNotIn("window[STORAGE_KEY] = id", source)

        # 丢消息重试：等不到内容要再请求
        self.assertIn("retryCount[panelId]", source)
        # 切回前台补发
        self.assertIn("visibilitychange", source)

class StatisticsPageLockOrderAndStateTests(unittest.TestCase):
    """回归：统计页后台填充的锁序与切页状态清理。

    事故现场（GUI 日志 2026-10-21 08:18~08:22）：后台填充先持 `_page_lock`
    再进 `@render_locked` 的分区渲染，与用户点「总览/主页」时的
    `render_lock → _page_lock` 互逆，两边互等即死锁 —— 整个界面点击无响应，
    且 `_statistics_fill_queued` 残留会让再次进入统计页永远不再填充分区。
    """

    def setUp(self):
        self.gui = _StatisticsHarness()
        self.patches = (
            patch(
                "module.webui.app_statistics_page.use_scope",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch("module.webui.app_statistics_page.put_scope", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_button", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_input", return_value=_OutputStub()),
            patch("module.webui.app_statistics_page.put_html", return_value=_OutputStub()),
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

    def _node(self, name):
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent
            / "module"
            / "webui"
            / "app_statistics_page.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node, ast
        self.fail(f"{name} 不存在")

    def test_fill_never_holds_page_lock_before_render_lock(self):
        """后台填充不得自行持有 _page_lock（锁序必须与用户回调一致）。"""
        node, ast = self._node("_fill_statistics_sections")
        attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        self.assertNotIn(
            "_page_lock",
            attrs,
            "后台填充不能自己拿 _page_lock：与用户回调的 render_lock → _page_lock 互逆会死锁",
        )
        called = {
            n.func.attr
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertIn("_render_statistics_sections_locked", called)

    def test_locked_entry_takes_render_lock_first(self):
        """统一入口必须由 @render_locked 包住，并在内部再取页面锁。"""
        node, ast = self._node("_render_statistics_sections_locked")
        decorators = [ast.unparse(d) for d in node.decorator_list]
        self.assertIn("render_locked", decorators)
        attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        self.assertIn("_page_lock", attrs)

    def test_reset_state_clears_fill_flags(self):
        self.gui._statistics_fill_queued = True
        self.gui._statistics_refresh_pending = True
        self.gui._reset_statistics_page_state()
        self.assertFalse(self.gui._statistics_fill_queued)
        self.assertFalse(self.gui._statistics_refresh_pending)

    def test_reenter_requeues_fill_when_sections_never_rendered(self):
        """填充任务在排队阶段被切页清掉后，再进统计页必须补排一次。"""
        self.gui.alas_set_stat()
        # 模拟「任务已随切页被清掉、分区从未渲染」：标志位被 reset 清空、任务消失
        self.gui._statistics_fill_queued = False
        self.gui.task_handler.added.clear()
        self.gui.alas_set_stat()
        fills = [
            callback
            for callback, _delay, _pending in self.gui.task_handler.added
            if getattr(callback, "__name__", "") == "_fill_statistics_sections"
        ]
        self.assertEqual(1, len(fills))


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
