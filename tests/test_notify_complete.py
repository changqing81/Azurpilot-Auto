"""完成通知复用错误通道、限频合并和异常隔离的回归测试。"""

import json
import queue
import threading
import unittest
from unittest.mock import create_autospec, patch

import yaml

from module.notify import complete
from module.notify.complete import CompletionDispatcher, CompletionMessage


class StopWorker(BaseException):
    """仅供测试结束常驻发送循环，不模拟业务异常。"""


class CompletionNotifyTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = CompletionDispatcher()
        self.message = CompletionMessage('ap', '[AP 内部工具完成] 性能测试', '执行状态：成功')

    @staticmethod
    def config_text(raw):
        return json.dumps({'Alas': {'Error': {'OnePushConfig': raw}}})

    def test_disabled_channels_skip_without_errors(self):
        """用户未配置或关闭错误通道时，不访问 onepush，也不输出失败警告。"""
        for raw in (None, '', ' ', 'provider: null', '---', '{}'):
            with (
                self.subTest(raw=raw),
                patch.object(complete, 'atomic_read_text', return_value=self.config_text(raw)),
                patch('module.notify.notify.handle_notify', autospec=True) as send,
                patch.object(complete.logger, 'warning') as warning,
            ):
                self.dispatcher._send_batch([self.message])
                send.assert_not_called()
                warning.assert_not_called()
        with patch.object(complete, 'atomic_read_text', return_value=''):
            self.assertIsNone(complete._load_push_config('ap'))

    def test_uses_existing_error_channel_and_content(self):
        """发送参数由现有错误配置提供，不引入第二套推送配置。"""
        raw = 'provider: custom\nurl: https://example.invalid/push\nmethod: post'
        with (
            patch.object(complete, 'atomic_read_text', return_value=self.config_text(raw)),
            patch('module.notify.notify.handle_notify', autospec=True, return_value=True) as send,
        ):
            self.dispatcher._send_batch([self.message])
        self.assertEqual(yaml.safe_load(send.call_args.args[0]), yaml.safe_load(raw))
        self.assertEqual(send.call_args.kwargs['title'], self.message.title)
        self.assertIn(self.message.content, send.call_args.kwargs['content'])

    def test_same_channel_merges_and_keeps_each_result(self):
        """同通道多实例可合并，单项标题和成功失败状态必须保留。"""
        failed = CompletionMessage('other', '[舰队扫描完成] 失败', '执行状态：失败；扫描超时')
        with (
            patch.object(complete, '_load_push_config', return_value='provider: custom'),
            patch('module.notify.notify.handle_notify', autospec=True, return_value=True) as send,
        ):
            self.dispatcher._send_batch([self.message, failed])
        send.assert_called_once()
        self.assertIn('2 项', send.call_args.kwargs['title'])
        for message in (self.message, failed):
            self.assertIn(message.title, send.call_args.kwargs['content'])
            self.assertIn(message.content, send.call_args.kwargs['content'])

    def test_different_channels_are_separate_and_rate_limited(self):
        """限频覆盖不同实例，凭据不因消息合并而混用。"""
        clock = [100.0]
        other = CompletionMessage('other', '[舰队扫描完成] 成功', '共扫描到 1 艘舰船')
        with (
            patch.object(complete, '_load_push_config', side_effect=['channel-a', 'channel-b']),
            patch.object(complete, 'time', autospec=True) as timer,
            patch('module.notify.notify.handle_notify', autospec=True, return_value=True) as send,
        ):
            timer.monotonic.side_effect = lambda: clock[0]
            timer.sleep.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
            self.dispatcher._send_batch([self.message, other])
        self.assertEqual([call.args[0] for call in send.call_args_list], ['channel-a', 'channel-b'])
        timer.sleep.assert_called_once_with(10.0)
        self.assertNotIn(other.content, send.call_args_list[0].kwargs['content'])

    def test_network_exception_does_not_stop_next_notification(self):
        """超时异常和返回 False 都不会终止后续通知处理。"""
        self.dispatcher.SEND_INTERVAL = 0
        with (
            patch.object(complete, '_load_push_config', return_value='provider: custom'),
            patch('module.notify.notify.handle_notify', autospec=True,
                  side_effect=[TimeoutError('网络超时'), False, True]) as send,
        ):
            for _ in range(3):
                self.dispatcher._send_batch([self.message])
        self.assertEqual(send.call_count, 3)

    def test_bad_config_does_not_affect_other_instances(self):
        with (
            patch.object(complete, '_load_push_config', side_effect=[ValueError('配置损坏'), 'channel-b']),
            patch('module.notify.notify.handle_notify', autospec=True) as send,
        ):
            self.dispatcher._send_batch([self.message, self.message])
        send.assert_called_once()

    def test_worker_collects_messages_during_merge_window(self):
        """直接驱动队列边界，验证五秒聚合窗口而不实际等待五秒。"""
        inbox = create_autospec(queue.Queue, instance=True)
        inbox.get.side_effect = [self.message, self.message, queue.Empty(), StopWorker()]
        self.dispatcher._queue = inbox
        with (
            patch.object(complete, 'time', autospec=True) as timer,
            patch.object(CompletionDispatcher, '_send_batch', autospec=True) as send,
        ):
            timer.monotonic.return_value = 100
            with self.assertRaises(StopWorker):
                self.dispatcher._run()
        send.assert_called_once_with(self.dispatcher, [self.message, self.message])
        self.assertEqual(inbox.get.call_args_list[1].kwargs['timeout'], 5.0)

    def test_concurrent_submit_starts_only_one_background_worker(self):
        """网络发送阻塞期间，多线程调用仍只入队并复用同一个发送线程。"""
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def held_worker(dispatcher):
            calls.append(threading.get_ident())
            entered.set()
            release.wait(5)

        results = []
        try:
            with patch.object(CompletionDispatcher, '_run', autospec=True, side_effect=held_worker):
                threads = [threading.Thread(target=lambda: results.append(self.dispatcher.submit(self.message)))
                           for _ in range(16)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())
                self.assertTrue(entered.is_set())
                self.assertEqual(len(calls), 1)
                self.assertEqual(results, [True] * 16)
                self.assertEqual(self.dispatcher._queue.qsize(), 16)
        finally:
            release.set()
            if self.dispatcher._thread is not None:
                self.dispatcher._thread.join(timeout=2)

    def test_start_and_queue_failures_are_isolated(self):
        """线程启动失败、队列满和日志失败不得向调用方抛出。"""
        with (
            patch.object(complete.threading.Thread, 'start', autospec=True, side_effect=RuntimeError),
            patch.object(complete.logger, 'warning', side_effect=RuntimeError),
        ):
            self.assertFalse(self.dispatcher.submit(self.message))
        self.dispatcher._thread = create_autospec(threading.Thread, instance=True)
        self.dispatcher._thread.is_alive.return_value = True
        self.dispatcher._queue = queue.Queue(maxsize=1)
        self.dispatcher._queue.put_nowait(self.message)
        self.assertFalse(self.dispatcher.submit(self.message))

    def test_public_entry_isolates_dispatcher_failure(self):
        with patch.object(complete._dispatcher, 'submit', autospec=True, side_effect=RuntimeError):
            self.assertFalse(complete.notify_custom_complete('ap', '标题', '正文'))


if __name__ == '__main__':
    unittest.main()
