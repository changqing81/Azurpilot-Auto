"""总览/切页加载提速优化的单元测试。

覆盖五项：
1. LogRes.groups 进程级缓存（dashboard.yaml 是打包定义，进程内只需读一次）
2. 总览任务列表刷新的配置 mtime 短路（文件未变不重做 load）
3. 总览版本号进程级缓存（不再每 60s 付一次 git 子进程）
4. 日志首显只发尾部 dom_max_chunks 条（前端反正按上限裁掉更早的）
5. 切实例时菜单结构不变则整块跳过重建
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from module.webui.utils import TaskHandler


class TestLogResGroupsCache(unittest.TestCase):
    def test_groups_read_from_disk_once_process_wide(self):
        from module.log_res import log_res as log_res_module
        from module.log_res.log_res import LogRes

        with patch.object(log_res_module, "_DASHBOARD_GROUPS", None):
            reads = []

            def fake_read(filepath):
                reads.append(filepath)
                return {"Dashboard": {"Oil": {"Value": 1}, "Coin": {"Value": 2}}}

            with patch("module.config.utils.read_file", fake_read), patch(
                "module.config.utils.filepath_argument", lambda name: f"{name}.yaml"
            ):
                g1 = LogRes(SimpleNamespace(data={})).groups
                g2 = LogRes(SimpleNamespace(data={})).groups

        self.assertEqual(len(reads), 1, "第二次构造实例不得再读 dashboard.yaml")
        self.assertIs(g1, g2, "进程级缓存应返回同一对象")
        self.assertIn("Oil", g1)


class TestOverviewTaskMtimeShortCircuit(unittest.TestCase):
    def _stub(self):
        from tests.test_webui_render_lock import _OverviewTaskStub

        stub = _OverviewTaskStub()
        self.load_calls = []
        stub.alas_config = SimpleNamespace(
            load=lambda: self.load_calls.append(1),
            get_next_task=lambda: None,
            pending_task=[],
            waiting_task=[],
        )
        stub.alas = SimpleNamespace(alive=True)
        return stub

    def _run(self, stub):
        from module.webui.app_dashboard import DashboardMixin

        with patch("module.webui.app_dashboard.clear"), patch(
            "module.webui.app_dashboard.use_scope"
        ), patch("module.webui.app_dashboard.put_column"), patch(
            "module.webui.app_dashboard.put_button"
        ), patch("module.webui.app_dashboard.put_text"), patch(
            "module.webui.app_dashboard.t", lambda key: key
        ):
            DashboardMixin.alas_update_overview_task(stub)

    def test_load_skipped_while_file_unchanged(self):
        stub = self._stub()
        mtimes = iter([100, 100, 200])
        stub._overview_config_mtime = lambda: next(mtimes)

        # 首跑：mtime 未知，必须 load
        self._run(stub)
        self.assertEqual(len(self.load_calls), 1)
        # 二跑：文件未变，跳过 load，但仍重算任务队列（快照比对通过则早退）
        self._run(stub)
        self.assertEqual(len(self.load_calls), 1, "mtime 未变时不得重复 load")
        # 三跑：文件变了，重新 load
        self._run(stub)
        self.assertEqual(len(self.load_calls), 2)


class TestLocalCommitCache(unittest.TestCase):
    def test_commit_queried_once_process_wide(self):
        import module.webui.app_overview as overview_module

        with patch.object(overview_module, "_LOCAL_COMMIT_CACHE", None):
            with patch.object(
                overview_module.updater, "get_commit", return_value=("abc1234",)
            ) as mock_get:
                v1 = overview_module._local_commit_version()
                v2 = overview_module._local_commit_version()
        self.assertEqual(v1, ("abc1234",))
        self.assertIs(v1, v2)
        self.assertEqual(mock_get.call_count, 1, "切回总览不得重复跑 git 子进程")


class TestLogTailFirstDisplay(unittest.TestCase):
    def _run_first_display(self, renderables_count):
        from module.webui.widgets import RichLog

        log = RichLog("log")
        pm = SimpleNamespace(
            renderables=list(range(renderables_count)),
            renderables_reduce_length=80,
        )
        with patch.object(RichLog, "render_cached", return_value="") as mock_render, \
                patch("module.webui.widgets.run_js"), \
                patch.object(RichLog, "reset"), \
                patch.object(RichLog, "extend"):
            g = log.put_log(pm)
            # 生成器第一个 yield 后才进入首显段：两次 next 触发首显渲染
            next(g)
            next(g)
            g.close()
        return mock_render.call_args[0][0]

    def test_first_display_sends_only_tail(self):
        tail = self._run_first_display(350)
        self.assertEqual(
            len(tail),
            200,
            "首显只发尾部 dom_max_chunks 条，前段发过去也会被前端裁掉",
        )
        self.assertEqual(tail[0], 150, "首显从倒数第 200 条开始")
        self.assertEqual(tail[-1], 349)

    def test_first_display_with_short_log(self):
        tail = self._run_first_display(50)
        self.assertEqual(len(tail), 50, "日志不足上限时全量显示")


class TestMenuStructSigSkip(unittest.TestCase):
    def _stub(self):
        from module.webui.lang import LANG

        rendered = []
        stub = SimpleNamespace(
            ALAS_MENU={"a": {"tasks": ["x"], "page": None}},
            ALAS_ARGS={"x": {}},
            _lang=LANG,
            _render_task_menu_items=lambda: rendered.append("items"),
            _render_config_search_control=lambda: rendered.append("search"),
            alas_overview=lambda: rendered.append("overview"),
        )
        return stub, rendered

    def _run(self, stub):
        from module.webui.app_task_config import TaskConfigMixin

        with patch("module.webui.app_task_config.use_scope"), patch(
            "module.webui.app_task_config.put_scope"
        ):
            TaskConfigMixin.alas_set_menu(stub)

    def test_switch_instance_keeps_menu_dom(self):
        stub, rendered = self._stub()

        self._run(stub)
        self.assertEqual(rendered, ["search", "items", "overview"])

        # 模拟切换到另一个实例：结构签名不变，菜单不重建
        stub.alas_name = "alas2"
        self._run(stub)
        self.assertEqual(
            rendered,
            ["search", "items", "overview", "overview"],
            "切实例只刷新概览，菜单 DOM 原样保留",
        )

    def test_lang_change_rebuilds_menu(self):
        import module.webui.lang as lang_module

        stub, rendered = self._stub()
        self._run(stub)
        self.assertEqual(rendered, ["search", "items", "overview"])

        with patch.object(lang_module, "LANG", "en-US"):
            self._run(stub)
        self.assertEqual(
            rendered,
            ["search", "items", "overview", "search", "items", "overview"],
            "语言变化必须重建菜单",
        )


if __name__ == "__main__":
    unittest.main()
