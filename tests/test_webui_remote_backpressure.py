"""P2P 隧道发送背压测试。

背景：一条 datachannel 同时承载 HTTP 响应、pywebio 的 UI WebSocket、SSE 与实时预览，
且 aiortc 的 channel.send() 只入队不阻塞。导出几十 MB 的日志压缩包时，若不加节流，
SCTP 发送缓冲会被堆满，UI 消息排在后面 → 用户看到"整个界面卡死"。
"""

import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from module.webui.fake_pil_module import remove_fake_pil_module

remove_fake_pil_module()

from module.webui import remote_access
from module.webui.remote_access import (
    P2P_SEND_DRAIN_TIMEOUT,
    P2P_SEND_HIGH_WATER,
    P2P_SEND_LOW_WATER,
    WebRTCTunnel,
)


class FakeChannel:
    """最小 datachannel 替身，模拟 aiortc 的背压接口。"""

    def __init__(self, buffered=0, ready="open"):
        self.bufferedAmount = buffered
        self.bufferedAmountLowThreshold = 0
        self.readyState = ready
        self.sent = []
        self._listeners = {}

    def send(self, data):
        self.sent.append(data)

    def on(self, event, func):
        self._listeners.setdefault(event, []).append(func)
        return self

    def remove_listener(self, event, func):
        handlers = self._listeners.get(event, [])
        if func in handlers:
            handlers.remove(func)

    def emit_low(self):
        for func in list(self._listeners.get("bufferedamountlow", [])):
            func()


class TestWaitSendCapacity(unittest.IsolatedAsyncioTestCase):
    def _tunnel(self, **kwargs):
        channel = FakeChannel(**kwargs)
        tunnel = WebRTCTunnel("127.0.0.1", 1000, channel)
        return tunnel, channel

    async def test_sets_low_water_threshold_on_init(self):
        _, channel = self._tunnel()
        self.assertEqual(channel.bufferedAmountLowThreshold, P2P_SEND_LOW_WATER)

    async def test_no_wait_when_buffer_below_high_water(self):
        tunnel, channel = self._tunnel(buffered=P2P_SEND_HIGH_WATER)
        await asyncio.wait_for(tunnel._wait_send_capacity(), timeout=1)
        # 未超水位就不该注册监听，避免无谓开销
        self.assertEqual(channel._listeners.get("bufferedamountlow", []), [])

    async def test_waits_until_bufferedamountlow(self):
        tunnel, channel = self._tunnel(buffered=P2P_SEND_HIGH_WATER + 1)
        task = asyncio.create_task(tunnel._wait_send_capacity())
        await asyncio.sleep(0.05)
        self.assertFalse(task.done(), "缓冲超水位时应暂停发送")

        channel.bufferedAmount = P2P_SEND_LOW_WATER
        channel.emit_low()
        await asyncio.wait_for(task, timeout=1)
        self.assertTrue(task.done())
        # 事件用完即摘，避免监听器泄漏
        self.assertEqual(channel._listeners.get("bufferedamountlow", []), [])

    async def test_timeout_logs_and_continues(self):
        """链路异常时不能让请求永久挂住。"""
        tunnel, _ = self._tunnel(buffered=P2P_SEND_HIGH_WATER + 1)
        with (
            patch.object(remote_access, "P2P_SEND_DRAIN_TIMEOUT", 0.05),
            patch.object(remote_access.logger, "warning") as warn,
        ):
            await asyncio.wait_for(tunnel._wait_send_capacity(), timeout=1)
        warn.assert_called_once()
        self.assertLess(P2P_SEND_DRAIN_TIMEOUT, 60)

    async def test_returns_immediately_when_channel_not_open(self):
        tunnel, channel = self._tunnel(buffered=P2P_SEND_HIGH_WATER + 1, ready="closed")
        await asyncio.wait_for(tunnel._wait_send_capacity(), timeout=1)
        self.assertEqual(channel._listeners.get("bufferedamountlow", []), [])


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    def iter_chunked(self, size):
        async def generator():
            for chunk in self._chunks:
                yield chunk

        return generator()


class _FakeResponse:
    def __init__(self, chunks):
        self.status = 200
        self.reason = "OK"
        self.headers = {"Content-Type": "application/zip"}
        self.content = _FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def request(self, *args, **kwargs):
        return _FakeResponse(self._chunks)


def _fake_aiohttp(chunks):
    module = types.ModuleType("aiohttp")
    module.ClientSession = lambda *a, **k: _FakeSession(chunks)
    return module


class TestHttpRequestBackpressure(unittest.IsolatedAsyncioTestCase):
    async def test_checks_capacity_before_every_chunk_and_before_end(self):
        """每个数据块发送前都要检查通道余量，否则大响应仍会堵住 UI。"""
        chunks = [b"a" * 10, b"b" * 10, b"c" * 10]
        channel = FakeChannel()
        tunnel = WebRTCTunnel("127.0.0.1", 8080, channel)

        calls = []

        async def fake_wait():
            calls.append(1)

        tunnel._wait_send_capacity = fake_wait

        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(chunks)}):
            await tunnel._http_request({"id": "req-1", "method": "GET", "path": "/api/log/error"})

        # 3 个数据块 + 1 次收尾 = 4 次
        self.assertEqual(len(calls), len(chunks) + 1)

    async def test_emits_start_chunks_and_end(self):
        chunks = [b"x" * 5, b"y" * 5]
        channel = FakeChannel()
        tunnel = WebRTCTunnel("127.0.0.1", 8080, channel)

        with patch.dict(sys.modules, {"aiohttp": _fake_aiohttp(chunks)}):
            await tunnel._http_request({"id": "req-2", "method": "GET", "path": "/x"})

        import json

        types_sent = [json.loads(item)["type"] for item in channel.sent]
        self.assertEqual(
            types_sent,
            ["http.response.start", "http.response.chunk", "http.response.chunk", "http.response.end"],
        )
        self.assertEqual(json.loads(channel.sent[1])["id"], "req-2")


if __name__ == "__main__":
    unittest.main()
