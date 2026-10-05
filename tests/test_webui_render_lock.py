"""渲染锁交互优先与后台渲染中断的单元测试。

背景：总览页的周期刷新任务（任务列表 / 仪表盘全量重建）持渲染锁可达
数秒，用户点击「统计」「任务设置」等回调也只能排队等锁——表现为
「总览加载中点哪里都没反应」。修复是两件事：

1. ``base._RenderLock``：可重入渲染锁，用户回调优先，后台任务让位
   （带防饿死硬闯）；
2. 后台长渲染循环里调 ``Base.render_should_yield()`` 分段中断，
   有用户等锁时立即放弃本轮剩余输出。
"""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from module.webui.base import (
    Base,
    _current_is_background_task,
    _RenderLock,
    render_locked,
)
from module.webui.utils import TaskHandler


def _wait_until(predicate, timeout=2.0, interval=0.01):
    """轮询等待条件成立，避免 Event 无法表达「锁内部状态」的观测点。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestRenderLockBasics(unittest.TestCase):
    def test_reentrant_acquire_same_thread(self):
        lock = _RenderLock()
        lock.acquire()
        lock.acquire()
        lock.release()
        lock.release()
        # 释放干净后其他线程可立即拿到
        got = threading.Event()
        threading.Thread(target=lambda: (lock.acquire(), got.set(), lock.release())).start()
        self.assertTrue(got.wait(2))

    def test_release_by_non_owner_raises(self):
        lock = _RenderLock()
        holder = threading.Thread(target=lock.acquire)
        holder.start()
        # 主线程从来不是 owner：无论子线程是否已拿到锁都必须报错，
        # 而不是把别人的锁放掉
        with self.assertRaises(RuntimeError):
            lock.release()
        holder.join(2)

    def test_mutual_exclusion(self):
        lock = _RenderLock()
        lock.acquire()
        second = threading.Event()
        threading.Thread(target=lambda: (lock.acquire(), second.set())).start()
        time.sleep(0.1)
        self.assertFalse(second.is_set())
        lock.release()
        self.assertTrue(second.wait(2))

    def test_context_manager_protocol(self):
        # app_manage 的菜单回调用 `with gui.render_lock:`：锁必须支持
        # 上下文管理器协议，with 内抛异常也要正常放锁
        lock = _RenderLock()
        with lock as acquired:
            self.assertIs(acquired, lock)
        got = threading.Event()
        threading.Thread(target=lambda: (lock.acquire(), got.set(), lock.release())).start()
        self.assertTrue(got.wait(2), "with 退出后锁必须已释放")

    def test_context_manager_releases_on_exception(self):
        lock = _RenderLock()
        with self.assertRaises(ValueError):
            with lock:
                raise ValueError("boom")
        got = threading.Event()
        threading.Thread(target=lambda: (lock.acquire(), got.set(), lock.release())).start()
        self.assertTrue(got.wait(2), "with 内抛异常后锁必须已释放")

    def test_context_manager_reentrant(self):
        lock = _RenderLock()
        with lock:
            with lock:
                pass
        got = threading.Event()
        threading.Thread(target=lambda: (lock.acquire(), got.set(), lock.release())).start()
        self.assertTrue(got.wait(2), "嵌套 with 退出后锁必须释放干净")


class TestInteractivePriority(unittest.TestCase):
    """核心语义：用户回调是下一个拿锁的，后台任务让位。"""

    def test_user_waits_jump_ahead_of_background(self):
        lock = _RenderLock()
        lock.acquire()  # 模拟后台任务持锁渲染

        user_got = threading.Event()
        bg_got = threading.Event()

        def user():
            lock.acquire()
            user_got.set()
            time.sleep(0.3)  # 用户持锁期间后台必须持续让位
            lock.release()

        def background():
            lock.acquire(is_background=True)
            bg_got.set()
            lock.release()

        t_user = threading.Thread(target=user)
        t_user.start()
        # 等 user 完成 waiter 注册（锁内部状态），而不是靠裸 sleep 赌时序
        self.assertTrue(_wait_until(lock.has_interactive_waiters))

        t_bg = threading.Thread(target=background)
        t_bg.start()

        lock.release()  # 原持锁者放锁
        self.assertTrue(user_got.wait(2), "用户等待者必须先于后台拿到锁")
        # 用户持锁期间后台拿不到（让位）
        self.assertFalse(bg_got.wait(0.2), "后台必须在用户持锁期间让位")
        # 用户释放、无新用户后，后台正常拿到
        self.assertTrue(bg_got.wait(3), "用户释放后后台应拿到锁")
        t_user.join(2)
        t_bg.join(2)

    def test_background_runs_freely_without_user(self):
        """没有用户等待时后台任务之间不互相让位。"""
        lock = _RenderLock()
        lock.acquire()
        bg_got = threading.Event()

        def background():
            lock.acquire(is_background=True)
            bg_got.set()
            lock.release()

        t_bg = threading.Thread(target=background)
        t_bg.start()
        lock.release()
        self.assertTrue(bg_got.wait(2))
        t_bg.join(2)

    def test_background_breaks_through_after_patience(self):
        """防饿死：让位耐心耗尽后硬闯一次。"""
        lock = _RenderLock()
        lock.BACKGROUND_PATIENCE = 0.2
        lock.acquire()  # 用户持锁不放（极端场景）

        bg_got = threading.Event()

        def background():
            lock.acquire(is_background=True)
            bg_got.set()
            lock.release()

        t_bg = threading.Thread(target=background)
        t_bg.start()
        self.assertTrue(bg_got.wait(3), "耐心耗尽后后台必须能硬闯拿锁")
        try:
            lock.release()
        except RuntimeError:
            pass  # 硬闯者已释放，主线程这个"用户"的释放不再合法
        t_bg.join(2)


class TestCallerIdentity(unittest.TestCase):
    """render_locked 按调用线程身份自动定优先级。"""

    def test_session_thread_is_interactive(self):
        self.assertFalse(_current_is_background_task(SimpleNamespace(task_handler=None)))
        gui = SimpleNamespace(task_handler=TaskHandler())
        self.assertFalse(_current_is_background_task(gui))
        # 模拟 TaskHandler.loop 执行任务期间设置的 thread-local
        gui.task_handler._task = SimpleNamespace(name="dummy")
        self.assertTrue(_current_is_background_task(gui))
        gui.task_handler._task = None

    def test_render_locked_passes_identity_to_lock(self):
        class _RecordingLock(_RenderLock):
            def __init__(self):
                super().__init__()
                self.background_flags = []

            def acquire(self, is_background: bool = False) -> None:
                self.background_flags.append(is_background)
                super().acquire(is_background)

        class _Gui:
            def __init__(self):
                self.render_lock = _RecordingLock()
                self.task_handler = TaskHandler()

            @render_locked
            def render(self):
                return "ok"

        gui = _Gui()
        self.assertEqual(gui.render(), "ok")
        self.assertEqual(gui.render_lock.background_flags, [False])

        gui.task_handler._task = SimpleNamespace(name="dummy")
        try:
            self.assertEqual(gui.render(), "ok")
            self.assertEqual(gui.render_lock.background_flags, [False, True])
        finally:
            gui.task_handler._task = None

    def test_render_locked_without_lock_still_works(self):
        class _Bare:
            @render_locked
            def render(self):
                return "bare"

        self.assertEqual(_Bare().render(), "bare")


class TestRenderShouldYield(unittest.TestCase):
    def test_true_only_while_user_is_waiting(self):
        lock = _RenderLock()
        holder = SimpleNamespace(render_lock=lock)
        self.assertFalse(Base.render_should_yield(holder))

        user_done = threading.Event()
        acquired = threading.Event()

        def user():
            lock.acquire()
            acquired.set()
            user_done.wait(2)
            lock.release()

        t = threading.Thread(target=user)
        t.start()
        self.assertTrue(acquired.wait(2))
        # 用户已拿到锁、无等待者 → 不中断
        self.assertFalse(Base.render_should_yield(holder))

        waiter = threading.Thread(target=lambda: lock.acquire())
        waiter.start()
        self.assertTrue(_wait_until(lock.has_interactive_waiters))
        # 有用户在等锁 → 持锁的长渲染必须让出
        self.assertTrue(Base.render_should_yield(holder))

        user_done.set()
        t.join(2)
        waiter.join(2)
        # 用户拿到锁后等待者清零
        self.assertTrue(_wait_until(lambda: not lock.has_interactive_waiters()))
        try:
            lock.release()
        except RuntimeError:
            pass

    def test_without_lock_returns_false(self):
        self.assertFalse(Base.render_should_yield(SimpleNamespace()))


class _OverviewTaskStub:
    """alas_update_overview_task 的最小替身：只保留锁与状态字段。"""

    def __init__(self):
        self.render_lock = _RenderLock()
        self.task_handler = TaskHandler()
        self.visible = True
        self.page = "Overview"
        self.alive = True
        self.pending_task = []
        self.waiting_task = []
        self._overview_snapshot = None
        self._should_yield = False
        # unbound 调用的替身不经过 DashboardMixin 的 MRO，
        # 钩子直接挂在实例上
        self._check_task_failure_notifications = lambda: None
        # mtime 短路：默认「看过同一 mtime」，mtime 语义测试自行覆盖
        from module.webui.app_dashboard import _OVERVIEW_MTIME_MISSING

        self._overview_config_mtime = lambda: 12345
        self._overview_config_mtime_seen = _OVERVIEW_MTIME_MISSING

    def render_should_yield(self):
        return self._should_yield


class TestOverviewTaskInterrupt(unittest.TestCase):
    """总览任务列表刷新：让位中断不得提交快照，否则列表断档。"""

    def _stub(self, should_yield):
        stub = _OverviewTaskStub()
        stub._should_yield = should_yield
        stub.alas_config = SimpleNamespace(
            load=lambda: None,
            get_next_task=lambda: None,
            pending_task=[],
            waiting_task=[],
        )
        stub.alas = SimpleNamespace(alive=True)
        return stub

    def test_yield_keeps_snapshot_uncommitted(self):
        from module.webui.app_dashboard import DashboardMixin

        stub = self._stub(should_yield=True)
        with patch("module.webui.app_dashboard.clear") as clear_mock, patch(
            "module.webui.app_dashboard.use_scope", MagicMock()
        ) as scope_mock:
            DashboardMixin.alas_update_overview_task(stub)
        self.assertIsNone(
            stub._overview_snapshot,
            "让位中断时快照必须保持旧值，否则下轮 diff 认为无变化、列表断档",
        )
        self.assertFalse(clear_mock.called, "让位中断时不得清空任务列表 scope")
        self.assertFalse(scope_mock.called)

    def test_normal_run_commits_snapshot(self):
        from module.webui.app_dashboard import DashboardMixin

        stub = self._stub(should_yield=False)
        with patch("module.webui.app_dashboard.use_scope"), patch(
            "module.webui.app_dashboard.clear"
        ) as clear_mock, patch("module.webui.app_dashboard.put_column"), patch(
            "module.webui.app_dashboard.put_button"
        ), patch("module.webui.app_dashboard.put_text"), patch(
            "module.webui.app_dashboard.t", lambda key: key
        ):
            DashboardMixin.alas_update_overview_task(stub)
        self.assertIsNotNone(stub._overview_snapshot)
        # 三个任务列表 scope（running/pending/waiting）都被重绘
        self.assertEqual(clear_mock.call_count, 3)


class TestDashboardInterrupt(unittest.TestCase):
    """仪表盘刷新：让位中断发生在任何状态写入之前。"""

    def _stub(self, should_yield):
        return SimpleNamespace(
            render_should_yield=lambda: should_yield,
            _log=SimpleNamespace(
                display_dashboard=True,
                first_display=True,
                last_display_time={},
                dashboard_arg_group=["Oil", "Coin"],
            ),
            alas_config=SimpleNamespace(data={}),
        )

    def test_update_dashboard_yield_before_state_write(self):
        from module.webui.app_dashboard import DashboardMixin

        stub = self._stub(should_yield=True)
        fake_log_res = SimpleNamespace(
            group=lambda name: {"Value": "100", "Record": None}
        )
        with patch("module.webui.app_dashboard.LogRes", lambda config: fake_log_res):
            DashboardMixin._update_dashboard(stub)
        # 中断检查在循环顶部、状态写入之前
        self.assertTrue(stub._log.first_display, "中断不得消耗 first_display")
        self.assertEqual(stub._log.last_display_time, {}, "中断不得污染增量缓存")

    def test_update_dashboard_yield_skips_scope_clear(self):
        from module.webui.app_dashboard import DashboardMixin

        stub = SimpleNamespace(
            visible=True,
            page="Overview",
            render_should_yield=lambda: True,
            _log=SimpleNamespace(display_dashboard=False),
        )
        entered = []
        import contextlib

        @contextlib.contextmanager
        def _spy_use_scope(*args, **kwargs):
            entered.append((args, kwargs))
            yield

        with patch("module.webui.app_dashboard.use_scope", _spy_use_scope):
            DashboardMixin.alas_update_dashboard(stub, _clear=True)
        self.assertEqual(
            entered,
            [],
            "用户在等锁时连 dashboard 容器清空都不应发生",
        )


if __name__ == "__main__":
    unittest.main()
