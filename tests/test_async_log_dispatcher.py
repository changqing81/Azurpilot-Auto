"""``AsyncLogDispatcher`` 的回归测试：主线程日志入队不得被 WebUI 通道卡死。

背景（2026-10-14 事故）：worker 的 WebUI 日志通道是 ``multiprocessing.Manager``
队列代理，``q.put`` 是同步跨进程调用且无超时。Manager 侧变慢时主线程被永久
阻塞——3 分钟零日志、零文件写入、游戏停在主页，进程却仍存活且无异常。

修复后主线程只做纯内存入队，投递由独立守护线程完成。本文件覆盖：
入队不阻塞、投递不丢不重且保序、投递异常不杀线程、上限丢弃计数、stop 退出。
"""

import logging
import threading
import time
import unittest

import module.logger as logger_module
from module.logger import AsyncLogDispatcher


def wait_until(predicate, timeout=5.0, interval=0.01):
    """轮询等待条件成立，超时返回 False（避免用固定 sleep 造成偶发失败）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _GateSink:
    """可开关的投递闸门：闸门关闭时 ``__call__`` 会阻塞，模拟 WebUI 通道卡死。"""

    def __init__(self):
        self._open = threading.Event()
        self.entered = threading.Event()
        self.received = []
        self._lock = threading.Lock()

    def __call__(self, renderable):
        self.entered.set()
        # 上限 10 秒只是兜底，测试会在结束前 open()，避免守护线程永久卡住
        self._open.wait(10)
        with self._lock:
            self.received.append(renderable)

    def open(self):
        self._open.set()

    def close(self):
        self._open.clear()


class SubmitNeverBlocksTests(unittest.TestCase):
    """核心断言：投递端卡死时，主线程入队必须立即返回。"""

    def test_submit_returns_immediately_while_delivery_is_stuck(self):
        gate = _GateSink()
        dispatcher = AsyncLogDispatcher(gate, max_pending=1000)
        self.addCleanup(gate.open)
        self.addCleanup(dispatcher.stop)

        # 等投递线程真的进入阻塞，确保后续提交确实处在"通道卡死"状态下
        dispatcher.submit('warm-up')
        self.assertTrue(gate.entered.wait(5), '投递线程未进入 func')

        started = time.monotonic()
        for index in range(200):
            dispatcher.submit(f'line-{index}')
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0, '主线程入队被卡死的投递通道阻塞了')
        self.assertEqual(dispatcher.dropped, 0, '正常入队不应丢弃日志')

    def test_all_pending_items_are_delivered_after_channel_recovers(self):
        gate = _GateSink()
        dispatcher = AsyncLogDispatcher(gate, max_pending=1000)
        self.addCleanup(dispatcher.stop)

        dispatcher.submit('warm-up')
        self.assertTrue(gate.entered.wait(5))
        for index in range(50):
            dispatcher.submit(index)

        # 通道恢复后，阻塞期间堆积的日志应全部按序补投，一条不丢
        gate.open()
        self.assertTrue(
            wait_until(lambda: len(gate.received) == 51),
            f'仅投递了 {len(gate.received)}/51 条',
        )
        self.assertEqual(gate.received[0], 'warm-up')
        self.assertEqual(gate.received[1:], list(range(50)))
        self.assertEqual(dispatcher.dropped, 0)


class DeliverySemanticsTests(unittest.TestCase):

    def test_delivers_everything_in_order(self):
        gate = _GateSink()
        gate.open()
        dispatcher = AsyncLogDispatcher(gate, max_pending=1000)
        self.addCleanup(dispatcher.stop)

        for index in range(50):
            dispatcher.submit(index)

        self.assertTrue(wait_until(lambda: len(gate.received) == 50))
        self.assertEqual(gate.received, list(range(50)))

    def test_delivery_thread_survives_sink_exception(self):
        """单条投递抛异常不能杀死投递线程，否则后续日志会全部丢失。"""
        received = []
        calls = []

        def flaky(renderable):
            calls.append(renderable)
            if len(calls) == 1:
                raise RuntimeError('channel broken')
            received.append(renderable)

        dispatcher = AsyncLogDispatcher(flaky)
        self.addCleanup(dispatcher.stop)

        for item in ('a', 'b', 'c'):
            dispatcher.submit(item)

        self.assertTrue(wait_until(lambda: len(received) == 2))
        self.assertEqual(received, ['b', 'c'])

    def test_drops_oldest_and_counts_when_pending_exceeds_cap(self):
        gate = _GateSink()
        dispatcher = AsyncLogDispatcher(gate, max_pending=3)
        self.addCleanup(gate.open)
        self.addCleanup(dispatcher.stop)

        dispatcher.submit('blocker')
        self.assertTrue(gate.entered.wait(5), '投递线程未进入 func')

        for index in range(6):
            dispatcher.submit(index)

        self.assertEqual(dispatcher.dropped, 3, '超出上限应丢最旧的并计数')

    def test_stop_terminates_delivery_thread(self):
        dispatcher = AsyncLogDispatcher(lambda renderable: None)
        dispatcher.stop(timeout=2)
        self.assertFalse(dispatcher._thread.is_alive())


class SetFuncLoggerIntegrationTests(unittest.TestCase):
    """确认 set_func_logger 真的把日志接到投递线程上，而不是同步直投。"""

    def test_logger_info_goes_through_dispatcher(self):
        received = []
        done = threading.Event()

        def sink(renderable):
            received.append(renderable)
            done.set()

        saved_handlers = list(logger_module.logger.handlers)
        saved_dispatcher = logger_module._log_dispatcher
        saved_level = logger_module.logger.level
        try:
            logger_module.logger.setLevel(logging.INFO)
            logger_module.set_func_logger(sink)
            self.assertIsNotNone(logger_module._log_dispatcher)
            logger_module.logger.info('async-log-dispatcher-integration')

            self.assertTrue(done.wait(5), '日志未通过投递线程送达')
            self.assertGreaterEqual(len(received), 1)
        finally:
            logger_module.logger.handlers = saved_handlers
            logger_module.logger.setLevel(saved_level)
            if logger_module._log_dispatcher is not None:
                logger_module._log_dispatcher.stop()
            logger_module._log_dispatcher = saved_dispatcher


if __name__ == '__main__':
    unittest.main()
