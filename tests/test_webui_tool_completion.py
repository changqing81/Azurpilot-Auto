"""内部工具及手动舰队扫描的完成事件和进程退出回归测试。"""

import os
import pickle
import queue
import unittest
from unittest.mock import PropertyMock, create_autospec, patch

from alas import AzurLaneAutoScript
from module.config.config import AzurLaneConfig
from module.webui import tool_completion
from module.webui.process_manager import ProcessManager
from module.webui.tool_completion import ToolCompletion


class ToolCompletionTests(unittest.TestCase):
    def setUp(self):
        self.events = queue.Queue()
        # autospec 保留真实构造函数和 run/cross_get 调用签名，避免 Mock 接受错误参数。
        self.factory_patch = patch('alas.AzurLaneAutoScript', autospec=AzurLaneAutoScript)
        self.factory = self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        self.runner = self.factory.return_value
        self.runner.run.return_value = True
        self.runner._last_task_error = None
        self.runner.config = create_autospec(AzurLaneConfig, instance=True)

    def test_tool_success_includes_elapsed_and_real_run_signature(self):
        with patch.object(tool_completion, 'time', autospec=True) as timer:
            timer.monotonic.side_effect = [10.0, 12.5]
            self.assertIs(tool_completion.run_internal_tool('ap', 'Benchmark', self.events), True)
        self.runner.run.assert_called_once_with('benchmark', skip_first_screenshot=True)
        self.factory.assert_called_once_with(config_name='ap')
        event = self.events.get_nowait()
        self.assertEqual(event, ToolCompletion('ap', 'Benchmark', True, 2.5))
        self.assertTrue(self.events.empty())
        self.assertEqual(pickle.loads(pickle.dumps(event)), event)

    def test_returned_failure_is_not_reported_as_success(self):
        for result in (False, 'recoverable'):
            with self.subTest(result=result):
                self.runner.run.return_value = result
                self.runner._last_task_error = 'GameStuckError'
                self.assertEqual(tool_completion.run_internal_tool('ap', 'Benchmark', self.events), result)
                event = self.events.get_nowait()
                self.assertFalse(event.success)
                self.assertIn('GameStuckError', event.reason)
                self.assertTrue(self.events.empty())

    def test_exceptions_and_early_exit_preserve_original_error(self):
        for exc in (TimeoutError('扫描超时'), RuntimeError('工具失败'), SystemExit(1), KeyboardInterrupt()):
            with self.subTest(exc=type(exc).__name__):
                self.runner.run.side_effect = exc
                with self.assertRaises(type(exc)) as caught:
                    tool_completion.run_internal_tool('ap', 'FleetScan', self.events)
                self.assertIs(caught.exception, exc)
                event = self.events.get_nowait()
                self.assertFalse(event.success)
                self.assertIn(type(exc).__name__, event.reason)
                self.assertEqual(event.summary, '')
                self.runner.config.cross_get.assert_not_called()
                self.assertTrue(self.events.empty())

    def test_constructor_failure_also_reports_once(self):
        self.factory.side_effect = ValueError('实例初始化失败')
        with self.assertRaises(ValueError):
            tool_completion.run_internal_tool('ap', 'Benchmark', self.events)
        self.assertIn('实例初始化失败', self.events.get_nowait().reason)
        self.assertTrue(self.events.empty())

    def test_queue_failure_preserves_business_result_and_exception(self):
        with patch.object(self.events, 'put_nowait', autospec=True, side_effect=RuntimeError):
            self.assertTrue(tool_completion.run_internal_tool('ap', 'Benchmark', self.events))
            original = TimeoutError('原始业务异常')
            self.runner.run.side_effect = original
            with self.assertRaises(TimeoutError) as caught:
                tool_completion.run_internal_tool('ap', 'Benchmark', self.events)
            self.assertIs(caught.exception, original)

    def test_successful_scan_contains_current_fleets_and_names(self):
        self.runner.config.cross_get.return_value = {
            'vanguard': {'1': [{'name': '拉菲', 'level': 125}]},
            'main': {'1': [{'name': '企业', 'level': 125}]},
            'submarine': {'2': [{'name': '大青花鱼', 'level': 120}]},
        }
        tool_completion.run_internal_tool('ap', 'FleetScan', self.events)
        event = self.events.get_nowait()
        self.assertTrue(event.success)
        for text in ('共扫描到 3 艘舰船', '舰队 1 前排', '舰队 1 主力', '舰队 2 潜艇', '拉菲', '企业', '大青花鱼'):
            self.assertIn(text, event.summary)
        self.runner.config.cross_get.assert_called_once_with('FleetInfo.FleetInfo.Result', default={})

    def test_empty_scan_and_summary_failure_do_not_change_success(self):
        self.runner.config.cross_get.return_value = {}
        tool_completion.run_internal_tool('ap', 'FleetScan', self.events)
        self.assertIn('0 艘舰船', self.events.get_nowait().summary)
        self.runner.config.cross_get.side_effect = ValueError('摘要错误')
        self.assertTrue(tool_completion.run_internal_tool('ap', 'FleetScan', self.events))
        event = self.events.get_nowait()
        self.assertTrue(event.success)
        self.assertIn('摘要读取失败', event.summary)

    def test_notification_titles_and_contents(self):
        events = (
            ToolCompletion('ap', 'Benchmark', True, 2.5),
            ToolCompletion('ap', 'FleetScan', True, 3, summary='共扫描到 3 艘舰船'),
            ToolCompletion('ap', 'FleetScan', False, 4, reason='扫描超时'),
        )
        with patch.object(tool_completion, 'notify_custom_complete', autospec=True) as notify:
            for event in events:
                tool_completion.dispatch_tool_completion(event)
        self.assertEqual(notify.call_count, 3)
        self.assertEqual(notify.call_args_list[0].args[1], '[AP 内部工具完成] 性能测试')
        self.assertIn('耗时：2.5 秒', notify.call_args_list[0].args[2])
        self.assertEqual(notify.call_args_list[1].args[1], '[舰队扫描完成] 成功')
        self.assertIn('共扫描到 3 艘舰船', notify.call_args_list[1].args[2])
        self.assertEqual(notify.call_args_list[2].args[1], '[舰队扫描完成] 失败')
        self.assertIn('失败原因：扫描超时', notify.call_args_list[2].args[2])

    def test_real_process_entry_reports_tool_but_not_scheduler(self):
        """执行真实 GUI 分发入口，验证单次工具通知与自动调度静默的边界。"""
        with (
            patch.dict(os.environ, {'DEMO': '0'}),
            patch('module.webui.process_manager.set_file_logger', autospec=True),
            patch('module.webui.process_manager.set_func_logger', autospec=True),
            patch('module.webui.process_manager.State.electron', False),
            patch('sys.argv', ['gui.py']),
        ):
            ProcessManager.run_process('ap', 'Benchmark', self.events)
            self.assertEqual(self.events.get_nowait().task, 'Benchmark')
            self.assertTrue(self.events.empty())
            ProcessManager.run_process('ap', 'alas', self.events)
            self.runner.loop.assert_called_once_with()
            self.assertTrue(self.events.empty())

    def test_both_notification_paths_isolate_send_errors(self):
        with patch.object(tool_completion, 'notify_custom_complete', autospec=True, side_effect=RuntimeError):
            for task in ('Benchmark', 'FleetScan'):
                tool_completion.dispatch_tool_completion(ToolCompletion('ap', task, True, 1))


class CompletionQueueTests(unittest.TestCase):
    def test_dead_worker_queue_is_drained_without_rendering_completion(self):
        """子进程已退出时仍消费完成事件，且事件不会变成可见日志。"""
        manager = object.__new__(ProcessManager)
        manager._renderable_queue = queue.Queue()
        manager.renderables = []
        manager.renderables_max_length = 400
        event = ToolCompletion('ap', 'FleetScan', True, 1)
        manager._renderable_queue.put('结束前日志')
        manager._renderable_queue.put(event)
        manager._renderable_queue.put('结束日志')
        with (
            patch.object(ProcessManager, 'alive', new_callable=PropertyMock, return_value=False),
            patch('module.webui.process_manager.dispatch_tool_completion', autospec=True) as dispatch,
        ):
            manager._thread_log_queue_handler()
        dispatch.assert_called_once_with(event)
        self.assertEqual(manager.renderables, ['结束前日志', '结束日志'])
        self.assertTrue(manager._renderable_queue.empty())

    def test_notification_failure_does_not_stop_log_consumption(self):
        manager = object.__new__(ProcessManager)
        manager._renderable_queue = queue.Queue()
        manager.renderables = []
        manager.renderables_max_length = 400
        manager._renderable_queue.put(ToolCompletion('ap', 'Benchmark', True, 1))
        manager._renderable_queue.put('后续日志')
        with (
            patch.object(ProcessManager, 'alive', new_callable=PropertyMock, return_value=False),
            patch('module.webui.process_manager.dispatch_tool_completion', autospec=True, side_effect=RuntimeError),
        ):
            manager._thread_log_queue_handler()
        self.assertEqual(manager.renderables, ['后续日志'])



if __name__ == '__main__':
    unittest.main()
