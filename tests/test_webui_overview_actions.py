"""主页启动/停止按钮异步化的单元测试。

stop_by_user 会在生命周期锁内终止 worker 并执行关游戏收尾，全程可达数十秒；
同步调用会把 WebUI 会话线程占住，按钮刷新与日志泵全部冻结（用户实测
「点了关闭要再跑一会儿、日志区落后两分钟」）。这里锁定三件事：
启动/停止调用立即返回、按钮状态乐观翻转、操作完成后标记自动清理。

被测对象是零依赖的 ``module.webui.instance_action``——不要在这里导入
app_overview：它的 import 链带 WebUI 全家桶副作用，会污染同进程的
pywebio 会话注册表（static/remote 断连守卫测试会集体报 script mode）。
"""

import threading
import time
import unittest
from types import SimpleNamespace

from module.webui.instance_action import alas_ui_state, spawn_instance_action


def _make_harness(alive: bool = False):
    return SimpleNamespace(
        alas=SimpleNamespace(
            alive=alive, _ui_pending=None, _ui_action_running=False
        )
    )


class TestSpawnInstanceAction(unittest.TestCase):
    def setUp(self):
        self.harness = _make_harness()

    def test_action_runs_in_background_and_returns_immediately(self):
        release = threading.Event()
        started = threading.Event()

        def slow_action():
            started.set()
            release.wait(timeout=10)

        # 同步执行会在这里阻塞到 release 超时；异步执行立即返回
        self.assertTrue(spawn_instance_action(self.harness.alas, slow_action, "stop"))
        self.assertTrue(started.wait(timeout=5))
        # 操作还在等 release，进行中标记必须仍在
        self.assertTrue(self.harness.alas._ui_action_running)
        self.assertEqual("stop", self.harness.alas._ui_pending)
        release.set()
        for _ in range(200):
            if not self.harness.alas._ui_action_running:
                break
            time.sleep(0.02)
        self.assertFalse(self.harness.alas._ui_action_running)
        self.assertIsNone(self.harness.alas._ui_pending)

    def test_reentrant_click_is_ignored_while_running(self):
        release = threading.Event()

        def slow_action():
            release.wait(timeout=10)

        self.assertTrue(
            spawn_instance_action(self.harness.alas, slow_action, "stop")
        )
        # 操作进行中，第二次点击直接忽略（不排队、不覆盖 pending）
        self.assertFalse(
            spawn_instance_action(self.harness.alas, lambda: None, "start")
        )
        self.assertEqual("stop", self.harness.alas._ui_pending)
        release.set()

    def test_exception_still_clears_pending_flags(self):
        def failing_action():
            raise RuntimeError("boom")

        self.assertTrue(
            spawn_instance_action(self.harness.alas, failing_action, "start")
        )
        for _ in range(200):
            if not self.harness.alas._ui_action_running:
                break
            time.sleep(0.02)
        self.assertFalse(self.harness.alas._ui_action_running)
        self.assertIsNone(self.harness.alas._ui_pending)


class TestAlasUiState(unittest.TestCase):
    def setUp(self):
        self.harness = _make_harness()

    def test_pending_stop_shows_start_immediately(self):
        self.harness.alas.alive = True
        self.harness.alas._ui_pending = "stop"
        # 停止进行中：进程尚未退出，按钮先翻到「启动」
        self.assertFalse(alas_ui_state(self.harness.alas))

    def test_pending_start_shows_stop_immediately(self):
        self.harness.alas.alive = False
        self.harness.alas._ui_pending = "start"
        # 启动进行中：worker 尚未登记存活，按钮先翻到「停止」
        self.assertTrue(alas_ui_state(self.harness.alas))

    def test_falls_back_to_alive_without_pending(self):
        self.harness.alas.alive = True
        self.assertTrue(alas_ui_state(self.harness.alas))
        self.harness.alas.alive = False
        self.assertFalse(alas_ui_state(self.harness.alas))


if __name__ == "__main__":
    unittest.main()
