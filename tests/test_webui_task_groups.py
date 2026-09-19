"""WebUI 任务分组并行的行为测试。

TaskHandler 按任务组（fast/slow）各占一个调度线程：组内串行、
组间并行，慢任务不得阻塞高频 UI 任务。

注意：add() 传入的是**已实例化的 generator 对象或普通函数**
（与生产代码一致），generator function 会被 get_generator
当作普通 callable 双重包装。
"""

import threading
import time
import unittest

from module.webui.utils import TaskHandler


class TestTaskGroupParallelism(unittest.TestCase):
    def test_slow_task_does_not_block_fast_group(self):
        """slow 组任务阻塞期间，fast 组任务仍按期反复执行。"""
        handler = TaskHandler()
        fast_runs = []
        slow_started = threading.Event()
        slow_release = threading.Event()

        def slow_task():
            slow_started.set()
            slow_release.wait(timeout=5)

        def fast_task():
            fast_runs.append(time.time())

        handler.add(fast_task, 0.02)
        handler.add(slow_task, 100, group="slow")
        handler.start()
        try:
            self.assertTrue(slow_started.wait(timeout=5), "slow 任务未开始执行")
            time.sleep(0.2)
        finally:
            slow_release.set()
            handler.stop()
        # 若仍是单线程串行调度，fast 任务会被阻塞的 slow 任务卡死，仅执行 1 次
        self.assertGreaterEqual(len(fast_runs), 3)

    def test_wake_running_task_sets_wake_requested(self):
        """对正在执行的任务 wake 时置 wake_requested，而非覆盖 next_run。"""
        handler = TaskHandler()
        release = threading.Event()
        started = threading.Event()

        def target():
            th = yield
            started.set()
            release.wait(timeout=5)

        handler.add(target(), 10)
        handler.start()
        try:
            self.assertTrue(started.wait(timeout=5), "任务未开始执行")
            task = handler.get_task("target")
            next_run_before = task.next_run
            self.assertTrue(handler.wake_task("target"))
            self.assertTrue(task.wake_requested)
            # 正在执行的任务不动 next_run（由执行后的重新计时处理）
            self.assertEqual(task.next_run, next_run_before)
        finally:
            release.set()
            handler.stop()

    def test_wake_waiting_task_advances_next_run(self):
        """对等待中的任务 wake 时直接把 next_run 提前到当前时间。"""
        handler = TaskHandler()

        def target():
            yield

        handler.add(target(), 100)
        task = handler.get_task("target")
        handler.wake_task("target")
        try:
            self.assertLessEqual(task.next_run, time.time())
            self.assertFalse(task.wake_requested)
        finally:
            handler.stop()

    def test_stop_from_task_thread_succeeds(self):
        """任务线程内调用 stop（如 generator 内自我移除场景）不应 join 自身死锁。"""
        handler = TaskHandler()
        started = threading.Event()
        stopped = {}

        def target():
            th = yield
            started.set()
            stopped["result"] = th.stop()
            yield

        handler.add(target(), 10)
        handler.start()
        self.assertTrue(started.wait(timeout=5))
        deadline = time.time() + 5
        while "result" not in stopped and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(stopped.get("result"))
        self.assertFalse(handler._alive)


if __name__ == "__main__":
    unittest.main()
