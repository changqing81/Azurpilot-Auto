import asyncio
import json
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pywebio.session as pywebio_session
from pywebio.output import put_text
from pywebio.session import local
from pywebio.session.threadbased import ThreadBasedSession
from rich.text import Text
from starlette.websockets import WebSocketState

from module.webui.app_home import HomeMixin
from module.webui.app_shell import AppShellMixin
from module.webui.app_task_config import TaskConfigMixin
from module.webui.base import Frame
from module.webui.fastapi import (
    SafeWebSocketConnection,
    WEBSOCKET_MAX_PENDING_MESSAGES,
)
from module.webui.utils import Task, TaskHandler
from module.webui.widgets import RichLog


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.messages = []
        self.close_count = 0
        self.concurrent_sends = 0
        self.max_concurrent_sends = 0

    async def send_json(self, message) -> None:
        self.concurrent_sends += 1
        self.max_concurrent_sends = max(
            self.max_concurrent_sends, self.concurrent_sends
        )
        await asyncio.sleep(0)
        self.messages.append(message)
        self.concurrent_sends -= 1

    async def close(self) -> None:
        self.close_count += 1
        self.application_state = WebSocketState.DISCONNECTED


class _BlockedWebSocket(_RecordingWebSocket):
    async def send_json(self, message) -> None:
        await asyncio.Event().wait()


class TestSafeWebSocketConnection(unittest.TestCase):
    def test_messages_share_one_ordered_sender_task(self):
        run_async_test(self._assert_messages_share_one_ordered_sender_task())

    async def _assert_messages_share_one_ordered_sender_task(self):
        websocket = _RecordingWebSocket()
        connection = SafeWebSocketConnection(
            websocket, asyncio.get_running_loop()
        )

        for index in range(100):
            connection.write_message({"index": index})

        sender_task = connection._sender_task
        self.assertIsNotNone(sender_task)
        self.assertFalse(sender_task.done())

        await sender_task
        self.assertEqual(
            list(range(100)),
            [message["index"] for message in websocket.messages],
        )
        self.assertEqual(1, websocket.max_concurrent_sends)

    def test_slow_client_backlog_is_bounded(self):
        run_async_test(self._assert_slow_client_backlog_is_bounded())

    async def _assert_slow_client_backlog_is_bounded(self):
        websocket = _BlockedWebSocket()
        connection = SafeWebSocketConnection(
            websocket, asyncio.get_running_loop()
        )

        for index in range(WEBSOCKET_MAX_PENDING_MESSAGES + 1):
            connection.write_message({"index": index})

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertLessEqual(
            len(connection._pending_messages),
            WEBSOCKET_MAX_PENDING_MESSAGES,
        )
        self.assertEqual(1, websocket.close_count)
        self.assertTrue(connection.closed())
        self.assertEqual(0, len(connection._pending_messages))

        connection.write_message({"index": "after-close-1"})
        connection.write_message({"index": "after-close-2"})
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(0, len(connection._pending_messages))
        self.assertEqual(1, websocket.close_count)
        self.assertTrue(connection.closed())


class TestTaskHandlerScheduling(unittest.TestCase):
    def test_overdue_task_does_not_run_in_catch_up_burst(self):
        calls = 0
        first_call = threading.Event()

        def run_once():
            nonlocal calls
            calls += 1
            first_call.set()

        handler = TaskHandler()
        task = Task(
            iter_task(run_once),
            delay=0.2,
            next_run=time.time() - 60,
        )
        handler.add_task(task)
        handler.start()
        try:
            self.assertTrue(first_call.wait(timeout=1))
            time.sleep(0.05)
            self.assertEqual(1, calls)
        finally:
            handler.stop()

    def test_wake_task_interrupts_scheduler_wait(self):
        called = threading.Event()
        handler = TaskHandler()
        task = Task(
            iter_task(called.set),
            delay=60,
            next_run=time.time() + 60,
            name="deferred",
        )
        handler.add_task(task)
        handler.start()
        try:
            self.assertTrue(handler.wake_task("deferred"))
            self.assertTrue(called.wait(timeout=1))
        finally:
            handler.stop()

    def test_wake_during_execution_is_not_lost(self):
        first_call_started = threading.Event()
        release_first_call = threading.Event()
        second_call = threading.Event()
        calls = 0

        def run_task():
            nonlocal calls
            calls += 1
            if calls == 1:
                first_call_started.set()
                release_first_call.wait(timeout=1)
            else:
                second_call.set()

        handler = TaskHandler()
        handler.add_task(Task(iter_task(run_task), delay=60, name="running"))
        handler.start()
        try:
            self.assertTrue(first_call_started.wait(timeout=1))
            self.assertTrue(handler.wake_task("running"))
            release_first_call.set()
            self.assertTrue(second_call.wait(timeout=1))
        finally:
            handler.stop()


class TestRichLogRendering(unittest.TestCase):
    def test_batch_render_preserves_all_lines(self):
        log = RichLog("log")
        html = log.render_many((Text("第一行"), Text("第二行")))

        self.assertIn("第一行", html)
        self.assertIn("第二行", html)

    def test_batch_render_empty_iterable_returns_empty_string(self):
        log = RichLog("log")

        self.assertEqual("", log.render_many([]))

    def test_extend_appends_and_trims_oldest_nodes(self):
        """前端日志 DOM 必须有上限：只 append 不回收会把 WebView2 撑到 GB 级。

        裁剪必须按"块"整删：rich 输出的 HTML 里 span 之间夹杂裸文本节点，
        若按元素节点逐个裁剪（children() + remove()），夹在中间的文本节点
        会残留且永不回收，内存照样无限增长。
        """
        log = RichLog("log")
        log.keep_bottom = False

        with patch("module.webui.widgets.run_js", autospec=True) as run_js:
            log.extend("<pre>一行日志</pre>")

        js = run_js.call_args[0][0]
        self.assertIn('document.querySelector("#pywebio-scope-log>div")', js)
        # 内容注入：整块 innerHTML，块级分组后整删
        self.assertIn("chunk.innerHTML = text", js)
        self.assertIn("box.appendChild(chunk)", js)
        self.assertIn("firstElementChild", js)
        self.assertIn("removeChild", js)
        self.assertIn(str(RichLog.dom_max_chunks), js)
        self.assertEqual(run_js.call_args[1]["text"], "<pre>一行日志</pre>")

    def test_extend_does_not_emit_js_for_empty_text(self):
        log = RichLog("log")
        log.keep_bottom = False

        with patch("module.webui.widgets.run_js", autospec=True) as run_js:
            log.extend("")

        run_js.assert_not_called()

    def test_extend_keeps_scrolling_when_keep_bottom(self):
        log = RichLog("log")
        log.keep_bottom = True

        with patch("module.webui.widgets.run_js", autospec=True) as run_js:
            log.extend("<pre>x</pre>")

        self.assertEqual(run_js.call_count, 2)
        self.assertIn("scrollTop", run_js.call_args_list[1][0][0])

    def test_dom_limit_leaves_room_for_full_reset_render(self):
        # reset() 全量首显（400 条）只占 1 块，chunk 上限必须远大于 1，
        # 保证首显后仍有足够的向上回溯空间且不会被立刻裁掉。
        self.assertGreaterEqual(RichLog.dom_max_chunks, 100)


class TestInitialRendering(unittest.TestCase):
    def test_shell_is_sent_before_localstorage_roundtrip(self):
        events = []

        class StopAfterLocalStorage(Exception):
            pass

        def read_localstorage(keys):
            events.append(("localstorage", keys))
            raise StopAfterLocalStorage

        gui = SimpleNamespace(
            theme="default",
            is_mobile=False,
            mount_shell=lambda: events.append("shell"),
            init_wallpaper=lambda: None,
        )
        with (
            patch("module.webui.app_home.set_env"),
            patch("module.webui.app_home.load_webui_styles"),
            patch("module.webui.app_home.is_oobe_needed", return_value=False),
            # 可见性监听在 mount_shell 之后、localStorage 读取之前执行，
            # 其中的 pywebio 调用（put_input/pin_on_change/run_js）会触发
            # Script Mode：pywebio 启动 tornado 服务器并阻塞等待浏览器
            # WebSocket 连接，导致整个测试进程挂死，必须一并 patch 掉。
            patch("module.webui.app_home.put_input"),
            patch("module.webui.app_home.pin_on_change"),
            patch("module.webui.app_home.run_js"),
            patch(
                "module.webui.app_home.get_localstorage_values",
                side_effect=read_localstorage,
            ),
            self.assertRaises(StopAfterLocalStorage),
        ):
            HomeMixin.run(gui)

        self.assertEqual(
            [
                "shell",
                ("localstorage", ("clarity_notice_shown", "aside", "alas_last_page")),
            ],
            events,
        )

    def test_aside_date_does_not_trigger_network_time_refresh(self):
        gui = SimpleNamespace(
            af_flag=False,
            refresh_aside_labels=lambda: None,
            refresh_aside_instances=lambda force=False: None,
        )
        with (
            patch("module.webui.app_shell.put_scope"),
            patch(
                "module.webui.app_shell.current_time",
                side_effect=AssertionError("首屏不应读取网络时间"),
            ),
        ):
            AppShellMixin.set_aside.__wrapped__(gui)

    def test_initial_loading_js_has_disconnect_watchdog(self):
        """远控断线看门狗必须注入首屏 JS（断线免手动刷新兜底）。"""
        from module.webui.app import INITIAL_LOADING_JS

        self.assertIn('alas_watchdog_reload', INITIAL_LOADING_JS)

    def test_custom_background_encoding_is_cached(self):
        """背景 data URI 按 mtime+size 缓存，文件未变不重复编码。"""
        import tempfile
        from pathlib import Path
        from module.webui import app_home

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "custom_background.jpg"
            p.write_bytes(b"\xff\xd8fake")
            first = app_home._encode_custom_background(p)
            second = app_home._encode_custom_background(p)
            self.assertIs(first, second)   # 命中缓存，同一对象
            p.write_bytes(b"\xff\xd8changed")   # 内容变化 → 缓存失效
            third = app_home._encode_custom_background(p)
            self.assertIsNot(first, third)

    def test_css_file_read_is_cached(self):
        """CSS 内容按 mtime+size 缓存，文件未变不重复读盘。"""
        import tempfile
        from pathlib import Path
        from module.webui import utils

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "demo.css"
            p.write_text("body{}", encoding="utf-8")
            first = utils._read_css_cached(str(p))
            second = utils._read_css_cached(str(p))
            self.assertIs(first, second)
            p.write_text("html{}", encoding="utf-8")
            self.assertEqual(utils._read_css_cached(str(p)), "html{}")


class TestTaskConfigRendering(unittest.TestCase):
    def test_subconfig_fields_are_sent_as_one_nested_output(self):
        commands = []
        closed = threading.Event()
        previous_session_classes = pywebio_session._active_session_cls.copy()

        def collect_commands(session):
            batch = session.get_task_commands()
            commands.extend(batch if isinstance(batch, list) else [batch])

        def render_subconfig():
            gui = object.__new__(TaskConfigMixin)
            local.gui = gui
            gui.alas_name = "test"
            gui.alas_config = SimpleNamespace(
                read_file=lambda _: {
                    "Alas": {"Emulator": {"PackageName": "cn"}},
                    "Synthetic": {"Group": {"First": "a", "Second": "b"}},
                }
            )
            gui.ALAS_ARGS = {
                "Synthetic": {
                    "Group": {
                        "First": {"type": "input", "value": "a"},
                        "Second": {"type": "input", "value": "b"},
                    }
                }
            }
            gui.init_menu = lambda name, skip_clear=False: None
            gui.set_title = lambda text: None
            gui._bind_config_watcher = lambda path: None
            gui._build_navigator = lambda task, group: put_text(group[0])
            gui.alas_set_group("Synthetic")

        pywebio_session._active_session_cls[:] = [ThreadBasedSession]
        try:
            with patch(
                "module.webui.app_task_config.t",
                side_effect=lambda key, *args, **kwargs: (
                    "" if key.endswith(".help") else key
                ),
            ):
                ThreadBasedSession(
                    render_subconfig,
                    session_info=SimpleNamespace(),
                    on_task_command=collect_commands,
                    on_session_close=closed.set,
                )
                self.assertTrue(closed.wait(timeout=2))
        finally:
            pywebio_session._active_session_cls[:] = previous_session_classes

        output_commands = [
            command for command in commands if command.get("command") == "output"
        ]
        self.assertEqual(1, len(output_commands))
        self.assertEqual("scope", output_commands[0]["spec"]["type"])
        self.assertEqual(
            "pywebio-scope-_groups",
            output_commands[0]["spec"]["dom_id"],
        )

    def test_empty_group_is_not_rendered_as_shell_or_navigator(self):
        """空分组（Storage / 任务状态）不产出分组壳，也不进右侧导航。

        懒渲染曾照单全收 ALAS_ARGS 的每个分组：右侧会多出一个「任务状态」
        导航按钮，另外还有一个点开后把自己清空、分组凭空消失的空壳。
        渲染口径必须与全量渲染一致——不出控件的分组整个跳过。
        """
        commands = []
        navigator_groups = []
        closed = threading.Event()
        previous_session_classes = pywebio_session._active_session_cls.copy()

        def collect_commands(session):
            batch = session.get_task_commands()
            commands.extend(batch if isinstance(batch, list) else [batch])

        def render_config():
            gui = object.__new__(TaskConfigMixin)
            local.gui = gui
            gui.alas_name = "test"
            # 阈值归零强制走懒渲染分支（大任务页才会自然命中）
            gui.CONFIG_LAZY_GROUP_THRESHOLD = 0
            gui.alas_config = SimpleNamespace(
                read_file=lambda _: {
                    "Alas": {"Emulator": {"PackageName": "cn"}},
                    "Synthetic": {"Group": {"First": "a"}},
                }
            )
            gui.ALAS_ARGS = {
                "Synthetic": {
                    "Group": {"First": {"type": "input", "value": "a"}},
                    # 每个任务尾部都挂着一个空 storage 分组
                    "Storage": {
                        "Storage": {
                            "type": "storage",
                            "value": {},
                            "valuetype": "ignore",
                            "display": "disabled",
                        }
                    },
                }
            }
            gui.init_menu = lambda name, skip_clear=False: None
            gui.set_title = lambda text: None
            gui._bind_config_watcher = lambda path: None

            def fake_navigator(task, group):
                navigator_groups.append(group[0])
                return put_text(group[0])

            gui._build_navigator = fake_navigator
            gui.alas_set_group("Synthetic")

        pywebio_session._active_session_cls[:] = [ThreadBasedSession]
        try:
            with patch(
                "module.webui.app_task_config.t",
                side_effect=lambda key, *args, **kwargs: (
                    "" if key.endswith(".help") else key
                ),
            ):
                ThreadBasedSession(
                    render_config,
                    session_info=SimpleNamespace(),
                    on_task_command=collect_commands,
                    on_session_close=closed.set,
                )
                self.assertTrue(closed.wait(timeout=2))
        finally:
            pywebio_session._active_session_cls[:] = previous_session_classes

        self.assertEqual(["Group"], navigator_groups)
        output_commands = [
            command for command in commands if command.get("command") == "output"
        ]
        self.assertEqual(1, len(output_commands))
        spec = output_commands[0]["spec"]
        dom_ids: list = []

        def collect_dom_ids(node):
            if isinstance(node, dict):
                if node.get("dom_id"):
                    dom_ids.append(node["dom_id"])
                for value in node.values():
                    collect_dom_ids(value)
            elif isinstance(node, list):
                for value in node:
                    collect_dom_ids(value)

        collect_dom_ids(spec)
        self.assertIn("pywebio-scope-group_Group", dom_ids)
        # 空分组既无 scope 也无导航按钮（导航/壳里都会出现分组名 "Storage"）
        self.assertNotIn("pywebio-scope-group_Storage", dom_ids)
        self.assertNotIn("Storage", json.dumps(spec, ensure_ascii=False))


class PageScopeDecoratorTests(unittest.TestCase):
    """页面渲染方法必须把 content scope 的装饰器留在自己身上。

    事故（dev `48a1e5469` 引入，用户实测发现）：把 `_spawn_instance_action` /
    `_alas_ui_state` 两个 helper 插进了 `@render_locked` +
    `@use_scope("content", clear=True)` 与 `alas_overview` **之间** —— 装饰器挂到
    helper 上、页面函数裸奔：总览内容既不进 content scope 也不清空内容区，
    于是整页渲染进「菜单」scope（调度器/统计界面看着像二级菜单），骨架屏也
    永久残留。

    这里锁住那条被违反的不变量：**凡是 `init_menu(..., skip_clear=True)` 的页面，
    自己必须已经声明过 `use_scope("content", clear=True)`**（skip_clear 的语义
    就是「调用方已清空」）。
    """

    @staticmethod
    def _decorator_source(node) -> list:
        import ast

        return [ast.unparse(d) for d in node.decorator_list]

    @staticmethod
    def _own_calls(node, attr_name: str) -> list:
        """收集函数自身（不含嵌套函数/lambda）里对 ``attr_name`` 的调用。"""
        import ast

        found = []
        stack = list(node.body)
        while stack:
            item = stack.pop()
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and item.func.attr == attr_name
            ):
                found.append(item)
            stack.extend(ast.iter_child_nodes(item))
        return found

    @classmethod
    def _calls_init_menu_with_skip_clear(cls, node) -> bool:
        import ast

        for call in cls._own_calls(node, "init_menu"):
            for keyword in call.keywords:
                if keyword.arg == "skip_clear" and isinstance(keyword.value, ast.Constant):
                    if keyword.value.value is True:
                        return True
        return False

    def test_skip_clear_pages_declare_content_scope(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "module"
        checked = []
        offenders = []
        for path in sorted(root.rglob("*.py")):
            # 个别模块带 BOM，utf-8-sig 兜住
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not self._calls_init_menu_with_skip_clear(node):
                    continue
                decorators = self._decorator_source(node)
                checked.append(node.name)
                if not any(
                    "use_scope" in d and "content" in d for d in decorators
                ):
                    offenders.append(f"{path.name}:{node.lineno} {node.name} {decorators}")
        # 至少覆盖到总览 / 配置页 / 设置 / 工具 / 更新 / 管理
        self.assertIn("alas_overview", checked)
        self.assertIn("alas_set_group", checked)
        self.assertGreaterEqual(len(checked), 7)
        self.assertEqual(offenders, [])

    def test_alas_overview_keeps_its_content_scope_decorators(self):
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent
            / "module"
            / "webui"
            / "app_overview.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "alas_overview":
                decorators = self._decorator_source(node)
                self.assertIn("render_locked", decorators)
                self.assertTrue(
                    any("use_scope" in d and "content" in d for d in decorators),
                    decorators,
                )
                return
        self.fail("alas_overview 不存在")

    def test_skeleton_never_wipes_content_scope(self):
        """骨架屏只 append 占位节点并由观察者自动撤掉，不得清空内容区 DOM。

        早期实现 `content.innerHTML = 骨架` 会把 pywebio 自己的 scope 容器一起抹掉，
        之后往这些 scope 输出时客户端只会在 ROOT 下新建孤儿容器（内容渲染到别处）。
        """
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent / "module" / "webui" / "base.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("content.innerHTML = '<div class=\"alas-skeleton\">", source)
        self.assertIn("alas-page-skeleton", source)
        self.assertIn("MutationObserver", source)


class PageRestoreTests(unittest.TestCase):
    """远控兜底刷新（整页重载）后回到离开时的页面，而不是一律回总览。

    `init_menu` 把页面名写进 localStorage（`Frame.LAST_PAGE_KEY`），新会话在
    `app_home.run()` 里按它恢复；页面名 → 渲染函数的映射就是 `Frame.page_renderer`。
    """

    def _gui(self):
        calls = []

        def rec(name):
            def _call(*args, **kwargs):
                calls.append(name)
                return name

            return _call

        gui = SimpleNamespace(
            ALAS_MENU={
                "Farm": {"menu": "collapse", "tasks": ["GemsFarming"]},
                "Tools": {"page": "tool", "tasks": ["Meowfficer"]},
            },
            alas_overview=rec("Overview"),
            alas_set_stat=rec("Stat"),
            show_home=rec("HomePage"),
            dev_set_menu=rec("Develop"),
            dev_setting=rec("Setting"),
            dev_utils=rec("Utils"),
            dev_update=rec("Update"),
            dev_remote=rec("Remote"),
            fleet_scan_page=rec("FleetScan"),
            fleet_info_page=rec("FleetInfo"),
            alas_set_group=rec("Group"),
            alas_daemon_overview=rec("Daemon"),
        )
        return gui, calls

    def test_key_is_stable(self):
        """localStorage 键名与 app.py 的读取保持一致。"""
        self.assertEqual(Frame.LAST_PAGE_KEY, "alas_last_page")

    def test_named_pages_map_to_renderers(self):
        from module.webui.base import Frame

        for page in ("Overview", "Stat", "HomePage", "Setting", "Utils",
                     "Update", "Remote", "FleetScan", "FleetInfo", "Develop"):
            with self.subTest(page=page):
                gui, calls = self._gui()
                renderer = Frame.page_renderer(gui, page)
                self.assertIsNotNone(renderer, page)
                renderer()
                self.assertEqual(calls, [page])

    def test_task_page_follows_menu_page_type(self):
        from module.webui.base import Frame

        gui, calls = self._gui()
        Frame.page_renderer(gui, "GemsFarming")()
        self.assertEqual(calls, ["Group"])
        gui, calls = self._gui()
        Frame.page_renderer(gui, "Meowfficer")()
        self.assertEqual(calls, ["Daemon"])

    def test_unknown_page_and_empty_name_return_none(self):
        from module.webui.base import Frame

        gui, _ = self._gui()
        self.assertIsNone(Frame.page_renderer(gui, "NotAPage"))
        self.assertIsNone(Frame.page_renderer(gui, ""))
        self.assertIsNone(Frame.page_renderer(gui, None))

    def test_init_menu_persists_page_name(self):
        """导航时必须把页面名写进 localStorage（搭在既有 JS 批次里，零额外往返）。"""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent / "module" / "webui" / "base.py"
        ).read_text(encoding="utf-8")
        self.assertIn("self.LAST_PAGE_KEY", source)
        self.assertIn("localStorage.setItem(", source)


class TestWebUIImports(unittest.TestCase):
    def test_entry_does_not_eagerly_import_image_stack(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import module.webui.app; "
                    "print(int('numpy' in sys.modules), "
                    "int('cv2' in sys.modules), "
                    "int('module.statistics.azurstats' in sys.modules))"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual("0 0 0", result.stdout.strip().splitlines()[-1])


def iter_task(func):
    """创建符合 Task 协议的简单生成器。"""
    yield
    while True:
        func()
        yield


def run_async_test(coroutine):
    """复用当前事件循环，避免替换 WebUI 已配置的默认循环。"""
    created = False
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        created = True
    try:
        loop.run_until_complete(coroutine)
    finally:
        if created:
            loop.close()


if __name__ == "__main__":
    unittest.main()
