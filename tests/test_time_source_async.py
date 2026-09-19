"""NTP 校时异步化的行为测试。

校时联网必须只发生在后台线程，读取路径（timestamp/now/status）
在未同步时立即返回本机时间，绝不阻塞调用方。
"""

import os
import time
import unittest
from unittest.mock import patch

from module.config.time_source import NTP_DISABLE_ENV, NetworkTimeSource


def _wait_thread(source, timeout=5) -> None:
    """等待后台校时线程结束（无论成功或失败）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        thread = source._sync_thread
        if thread is not None and not thread.is_alive():
            return
        time.sleep(0.01)


class TestNetworkTimeSourceAsync(unittest.TestCase):
    def setUp(self):
        # 确保测试环境未禁用 NTP
        env = {key: value for key, value in os.environ.items() if key != NTP_DISABLE_ENV}
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = NetworkTimeSource()

    def test_timestamp_does_not_query_network(self):
        """未同步时 timestamp 立即返回本机时间，不发起 NTP 查询。"""
        with patch.object(
            self.source, "_query_server",
            side_effect=AssertionError("读取路径不应联网"),
        ):
            before = time.time()
            value = self.source.timestamp()
            self.assertGreater(value, 0)
            # 若发生联网查询（最多 5 个服务器 × 1s 超时）不可能瞬时返回
            self.assertLess(time.time() - before, 0.5)

    def test_status_does_not_query_network(self):
        with patch.object(
            self.source, "_query_server",
            side_effect=AssertionError("读取路径不应联网"),
        ):
            data = self.source.status()
        self.assertFalse(data["synced"])
        self.assertIsNone(data["last_sync_elapsed"])

    def test_background_sync_applies_offset(self):
        """后台线程校时成功后，timestamp 切换到校准时间。"""
        with patch.object(self.source, "_query_server", return_value=123.456):
            self.assertTrue(self.source.refresh())
        _wait_thread(self.source)
        self.assertTrue(self.source.synced)
        self.assertIsNotNone(self.source.server)
        # 校准后的时间 ≈ 本机时间 + 偏移（后台写入的基准）
        expected_base = self.source.base_timestamp
        value = self.source.timestamp()
        self.assertAlmostEqual(value - expected_base, time.monotonic() - self.source.base_monotonic, places=3)

    def test_failed_sync_falls_back_to_local_time(self):
        with patch.object(
            self.source, "_query_server", side_effect=OSError("unreachable")
        ):
            self.assertFalse(self.source.refresh())
        _wait_thread(self.source)
        self.assertFalse(self.source.synced)
        # 失败后进入退避：读取仍返回本机时间且不抛异常
        self.assertGreater(self.source.timestamp(), 0)
        # 退避期内不再次调度同步线程
        thread_before = self.source._sync_thread
        self.source.timestamp()
        self.assertIs(thread_before, self.source._sync_thread)

    def test_force_resets_backoff(self):
        """force=True 清除失败退避，允许立即重新校时。"""
        self.source.retry_after_monotonic = time.monotonic() + 999
        with patch.object(self.source, "_query_server", return_value=1.0):
            self.assertTrue(self.source.refresh(force=True))
        _wait_thread(self.source)
        self.assertTrue(self.source.synced)


if __name__ == "__main__":
    unittest.main()
