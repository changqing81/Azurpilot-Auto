"""远控断线治理的回归测试。

背景（2026-09-21）：远控环境下页面每几秒整页刷新一次，根因有三类：
  1. 前端有两条独立的 `location.reload()` 刷新链（alas-utils.js 的
     on_session_close 与 app.py 注入的"远控断线看门狗"），看门狗一收到 ws open
     就把重试计数清零，抖动链路上"连续 5 次就停手"的上限形同虚设 → 无限刷新。
  2. 信令一断 `_run_signal_loop` 就在 finally 里关掉所有 peer 连接，已连上的
     页面 WebSocket 随之断开 → 整页刷新；其实 datachannel 不依赖信令。
  3. WebRTC 线程启动时 `_wait_for_ssh_info` 在 SSH 进程还没 spawn 的窗口里
     直接判失败，只能等 keep_ssh_alive 的下一轮（10 秒）重启并关掉所有 peer。

本文件只做"防回退"级别的断言，不依赖真实的 aiortc / aiohttp / 浏览器。
"""

import asyncio
import re
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from module.webui.fake_pil_module import remove_fake_pil_module

remove_fake_pil_module()

from module.webui import remote_access as ra  # noqa: E402
from module.webui.remote_access import WebRTCRemoteAccessProvider  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alive_thread() -> threading.Thread:
    thread = threading.Thread(target=lambda: time.sleep(5), daemon=True)
    thread.start()
    return thread


class FakeSSHProvider:
    """只实现 _wait_for_ssh_info 需要的属性。"""

    def __init__(self, thread_alive: bool, notfound: bool = False):
        self.notfound = notfound
        self.thread = _alive_thread() if thread_alive else None
        self.info = SimpleNamespace(address=None)
        # 模拟"线程已拉起但 ssh 进程还没 spawn 出来"的窗口
        self.alive = thread_alive and False

    def is_alive(self) -> bool:
        return self.alive


class FakePeerConnection:
    def __init__(self, state: str = "connected"):
        self.connectionState = state
        self.close_called = False

    async def close(self) -> None:
        self.close_called = True
        self.connectionState = "closed"


class TestSshReadyRace(unittest.TestCase):
    def test_waits_while_ssh_thread_is_still_starting(self):
        """SSH 线程活着但进程尚未 spawn（is_alive() 仍为 False）时不能判失败。"""
        ssh = FakeSSHProvider(thread_alive=True)
        provider = WebRTCRemoteAccessProvider(ssh)

        def deliver_address():
            time.sleep(0.2)
            ssh.info.address = "https://example.invalid/p2p"

        threading.Thread(target=deliver_address, daemon=True).start()

        with patch.object(ra, "P2P_SETUP_TIMEOUT", 3):
            started = time.time()
            ready = provider._wait_for_ssh_info()
        self.assertTrue(ready)
        # 旧实现会在这里立刻返回 False（远早于地址到达）
        self.assertGreaterEqual(time.time() - started, 0.15)

    def test_fails_fast_when_ssh_thread_exited(self):
        provider = WebRTCRemoteAccessProvider(FakeSSHProvider(thread_alive=False))
        with patch.object(ra, "P2P_SETUP_TIMEOUT", 5):
            started = time.time()
            ready = provider._wait_for_ssh_info()
        self.assertFalse(ready)
        self.assertLess(time.time() - started, 1)

    def test_fails_fast_when_ssh_not_found(self):
        ssh = FakeSSHProvider(thread_alive=True, notfound=True)
        ssh.info.address = None
        provider = WebRTCRemoteAccessProvider(ssh)
        ssh.thread.join(0.05)  # 线程仍活着，但 notfound 已经明确
        with patch.object(ra, "P2P_SETUP_TIMEOUT", 5):
            started = time.time()
            ready = provider._wait_for_ssh_info()
        self.assertFalse(ready)
        self.assertLess(time.time() - started, 1)


class TestPeerRegistry(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = WebRTCRemoteAccessProvider(FakeSSHProvider(thread_alive=False))

    def test_prune_keeps_connected_peers(self):
        """信令重连结束只回收失效连接，在线页面必须留着。"""
        live = FakePeerConnection("connected")
        dead = FakePeerConnection("failed")
        self.provider._peer_connections.update({live, dead})
        self.provider._peer_connections_by_viewer["viewer-live"] = live
        self.provider._peer_connections_by_viewer["viewer-dead"] = dead

        self.provider._prune_peer_connections()

        self.assertIn(live, self.provider._peer_connections)
        self.assertEqual(self.provider._peer_connections_by_viewer, {"viewer-live": live})
        self.assertNotIn(dead, self.provider._peer_connections)

    async def test_disconnected_gets_grace_window_before_rebuild(self):
        pc = FakePeerConnection("disconnected")
        self.provider._peer_connections.add(pc)
        self.provider._peer_connections_by_viewer["viewer-1"] = pc

        with patch.object(ra, "P2P_DISCONNECTED_GRACE", 0.01):
            self.provider._schedule_peer_grace(pc, "viewer-1")

            async def wait_until_closed():
                for _ in range(100):
                    if pc.close_called:
                        return True
                    await asyncio.sleep(0.01)
                return False

            self.assertTrue(await asyncio.wait_for(wait_until_closed(), timeout=3))
        self.assertNotIn(pc, self.provider._peer_connections)
        self.assertEqual(self.provider._peer_connections_by_viewer, {})

    async def test_recovered_connection_keeps_grace_task_cancelled(self):
        pc = FakePeerConnection("disconnected")
        self.provider._peer_connections.add(pc)

        with patch.object(ra, "P2P_DISCONNECTED_GRACE", 0.2):
            self.provider._schedule_peer_grace(pc, None)
            pc.connectionState = "connected"
            self.provider._cancel_peer_grace(pc)
            await asyncio.sleep(0.3)

        self.assertFalse(pc.close_called)
        self.assertIn(pc, self.provider._peer_connections)

    async def test_close_all_peers_on_thread_exit(self):
        first = FakePeerConnection("connected")
        second = FakePeerConnection("disconnected")
        self.provider._peer_connections.update({first, second})
        self.provider._peer_connections_by_viewer["v1"] = first

        await self.provider._close_all_peers()

        self.assertTrue(first.close_called and second.close_called)
        self.assertEqual(self.provider._peer_connections, set())
        self.assertEqual(self.provider._peer_connections_by_viewer, {})


class TestFrontendReloadGuard(unittest.TestCase):
    """前端刷新链只能有一条计数入口，且不能在 ws open 时清零。"""

    @classmethod
    def setUpClass(cls):
        cls.app_source = (PROJECT_ROOT / "module/webui/app.py").read_text(encoding="utf-8")
        cls.js = re.search(
            r'INITIAL_LOADING_JS = """(.*?)"""', cls.app_source, re.S
        ).group(1)
        cls.utils_js = (PROJECT_ROOT / "assets/gui/js/alas-utils.js").read_text(
            encoding="utf-8"
        )

    def test_watchdog_does_not_reset_counter_on_open(self):
        # open 回调里只允许记录"本页首个连上时刻"与收起提示条，不允许清掉重试计数
        open_handler = re.search(
            r"ws\.addEventListener\('open', function \(\) \{(.*?)\n        \}\);",
            self.js,
            re.S,
        ).group(1)
        self.assertNotIn("removeItem(KEY)", open_handler)

    def test_watchdog_stops_fast_retry_after_cap(self):
        self.assertIn("MAX_RELOADS", self.js)
        self.assertIn("SLOW_RETRY_MS", self.js)
        # 超过上限后必须转向慢重试 + 提示条，而不是无脑 setTimeout(reload)
        schedule = re.search(r"function scheduleReload\(\) \{(.*?)\n    \}", self.js, re.S).group(1)
        self.assertIn("setBanner(", schedule)
        self.assertIn("SLOW_RETRY_MS", schedule)

    def test_watchdog_waits_for_page_own_reconnect_loop(self):
        """只要页面自己还在尝试建连（pywebio 重连循环），就绝不能刷新。"""
        self.assertIn("ATTEMPT_MS", self.js)
        self.assertIn("MAX_DOWN_MS", self.js)
        watch = re.search(r"function watchConnection\(\) \{(.*?)\n    \}", self.js, re.S).group(1)
        # 已连上直接返回，不做任何刷新
        self.assertIn("anyOpen()", watch)
        # 兜底刷新只允许出现在两个"没救了"的分支里，且必须在等待分支之前
        reload_at = watch.index("scheduleReload()")
        wait_at = watch.index("armWatch()")
        self.assertLess(reload_at, wait_at)
        self.assertIn("downSince", watch)
        # 每次新建 WebSocket 都要刷新"最近建连尝试"时间戳
        self.assertIn("lastAttemptTs = Date.now();", self.js)

    def test_auto_reload_can_be_disabled_persistently(self):
        # window.reload = 0 之外，还要支持 localStorage 里的持久开关
        self.assertIn("alas_auto_reload", self.js)
        self.assertIn("autoReloadDisabled", self.js)

    def test_server_enables_pywebio_session_reconnect(self):
        """reconnect_timeout 必须真的传到 PyWebIO：这是"不刷新就能恢复"的前提。"""
        from pywebio.platform.adaptor import ws as ws_adaptor

        from module.webui.fastapi import WEBSOCKET_RECONNECT_TIMEOUT, asgi_app

        asgi_app({"index": lambda: None})
        self.assertGreater(WEBSOCKET_RECONNECT_TIMEOUT, 0)
        self.assertGreaterEqual(
            ws_adaptor._state.expire_second, WEBSOCKET_RECONNECT_TIMEOUT
        )

    def test_watchdog_exposes_shared_entry(self):
        self.assertIn("window.__alasReconnect", self.js)

    def test_alas_utils_reuses_shared_entry(self):
        self.assertIn("window.__alasReconnect", self.utils_js)
        # 兜底路径（未安装看门狗，如新前端）仍然要能刷新
        self.assertIn("location.reload()", self.utils_js)


if __name__ == "__main__":
    unittest.main()
