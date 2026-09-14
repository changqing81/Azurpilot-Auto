"""WebRTC 信令自动重连测试。

背景：信令 WS 一断（heartbeat=30，localshare 弱网/服务重启），旧实现线程直接退出，
要等 keep_ssh_alive 轮询（原 60 秒）才重启，期间远控完全不可用；现改为线程内
指数退避自动重连（2s→30s 封顶），成功连上过信令的轮次会重置退避。
"""

import asyncio
import unittest
from unittest.mock import patch

from module.webui.fake_pil_module import remove_fake_pil_module

remove_fake_pil_module()

from module.webui.remote_access import (  # noqa: E402
    RemoteSignalError,
    WebRTCRemoteAccessProvider,
)


class TestSignalReconnect(unittest.IsolatedAsyncioTestCase):
    def _provider(self):
        return WebRTCRemoteAccessProvider(object.__new__(object))

    async def _run(self, provider, script):
        """驱动重连循环：script 是每次 _run_signal_loop 被调用时执行的函数。"""
        calls = []
        sleeps = []

        async def fake_signal_loop():
            calls.append(True)
            script(len(calls))

        async def fake_sleep(delay):
            sleeps.append(delay)

        with patch.object(provider, '_run_signal_loop', fake_signal_loop), \
                patch('module.webui.remote_access.asyncio.sleep', fake_sleep):
            await provider._run_signal_loop_with_reconnect()
        return calls, sleeps

    async def test_retries_with_backoff_until_stop(self):
        provider = self._provider()

        def script(call):
            if call == 1:
                raise RemoteSignalError('first drop')
            if call == 2:
                raise RemoteSignalError('second drop')
            # 第三次：模拟 stop_event 被置位后的正常返回
            provider.stop_event.set()

        calls, sleeps = await self._run(provider, script)
        self.assertEqual(len(calls), 3)
        # 连续失败：退避按 2s → 4s 翻倍；stop 后正常返回不再 sleep
        self.assertEqual(sleeps, [2, 4])
        self.assertEqual(provider.info.error, 'second drop')
        self.assertEqual(provider.info.connection_state, 'signaling')

    async def test_backoff_resets_after_successful_session(self):
        provider = self._provider()

        def script(call):
            if call >= 4:
                provider.stop_event.set()
                return
            # 模拟"成功连上信令（推进到 waiting_peer）随后断开"
            provider.info.connection_state = 'waiting_peer'
            raise RemoteSignalError('dropped')

        calls, sleeps = await self._run(provider, script)
        self.assertEqual(len(calls), 4)
        # 每轮都成功连上过信令 → 退避每次重置为 2s，不单调翻倍
        self.assertEqual(sleeps, [2, 2, 2])

    async def test_normal_close_reconnects_and_stop_exits(self):
        provider = self._provider()

        def script(call):
            if call == 1:
                return  # 服务端优雅关闭，stop_event 未置位 → 应重连
            provider.stop_event.set()

        calls, sleeps = await self._run(provider, script)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [2])


if __name__ == '__main__':
    unittest.main()
