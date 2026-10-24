import unittest
from unittest.mock import Mock, patch

from alas import AzurLaneAutoScript
from module.exception import (
    GameStuckError,
    get_task_from_exception,
    mark_task_on_exception,
)


def _make_script():
    """构造绕过 __init__ 的调度器实例，仅挂载推送所需的最小属性。"""
    script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
    script.config_name = 'test'
    script.__dict__['config'] = Mock()
    return script


class TestExceptionTaskMark(unittest.TestCase):
    """异常对象上的子任务标记：写入、读取、降级。"""

    def test_mark_and_read_back(self):
        exc = GameStuckError('游戏卡住')
        mark_task_on_exception(exc, 'OpsiHazard1Leveling')
        self.assertEqual('OpsiHazard1Leveling', get_task_from_exception(exc))

    def test_unmarked_exception_returns_none(self):
        self.assertIsNone(get_task_from_exception(GameStuckError('x')))

    def test_mark_ignores_invalid_task_name(self):
        exc = GameStuckError('x')
        # 非字符串 / 空串都不写入，且不抛异常
        mark_task_on_exception(exc, None)
        mark_task_on_exception(exc, '')
        mark_task_on_exception(exc, 123)
        self.assertIsNone(get_task_from_exception(exc))


class TestErrorNotifyTaskTag(unittest.TestCase):
    """错误推送正文注入【当前任务名】前缀及降级行为。"""

    def test_content_gets_task_prefix(self):
        script = _make_script()
        with patch('alas.handle_notify') as send:
            script._error_notify(
                'commission',
                title='t',
                content='<test> 游戏卡住 - 将自动重启游戏',
                exc=GameStuckError('x'),
            )
        # command 转驼峰后走 i18n：Commission → 委托
        self.assertIn('【委托】', send.call_args.kwargs['content'])
        # 原始正文保持在前缀之后，不被破坏
        self.assertTrue(
            send.call_args.kwargs['content'].endswith(
                '<test> 游戏卡住 - 将自动重启游戏'))

    def test_marked_sub_task_wins_over_command(self):
        script = _make_script()
        exc = GameStuckError('x')
        # 智能调度代跑侵蚀1：command 是父任务，标记是子任务
        mark_task_on_exception(exc, 'OpsiHazard1Leveling')
        with patch('alas.handle_notify') as send:
            script._error_notify(
                'opsi_scheduling',
                title='t',
                content='原始错误',
                exc=exc,
            )
        # 应报子任务"侵蚀1练级"，而不是父任务"智能调度"
        self.assertIn('【侵蚀1练级】', send.call_args.kwargs['content'])
        self.assertNotIn('【智能调度】', send.call_args.kwargs['content'])

    def test_unknown_task_falls_back_to_english(self):
        script = _make_script()
        with patch('alas.handle_notify') as send:
            script._error_notify(
                'some_unknown_task',
                title='t',
                content='原始错误',
                exc=GameStuckError('x'),
            )
        # i18n 查不到时降级为英文驼峰任务名，推送仍要发出
        self.assertIn('【SomeUnknownTask】', send.call_args.kwargs['content'])

    def test_prefix_failure_never_breaks_push(self):
        script = _make_script()
        with patch('alas.handle_notify') as send, \
                patch.object(script, '_error_task_display',
                             side_effect=RuntimeError('boom')):
            script._error_notify('commission', title='t', content='原始错误')
        # 拼接前缀链路炸了也必须原样发出推送
        send.assert_called_once()
        self.assertEqual('原始错误', send.call_args.kwargs['content'])

    def test_display_helper_survives_bad_command(self):
        script = _make_script()
        # command 为 None 等异常输入时，任务名获取降级不抛异常
        self.assertTrue(script._error_task_display(None))


class TestAggregatorContextMark(unittest.TestCase):
    """聚合调度上下文切换器：异常冒泡时子任务名穿透到异常对象。"""

    def test_opsi_context_marks_exception_with_sub_task(self):
        from module.os.tasks.scheduling import OpsiScheduling

        runner = OpsiScheduling.__new__(OpsiScheduling)
        runner.config = Mock()
        # 代跑任务对象直接用 Mock 顶替，不读真实配置
        runner._make_opsi_task_function = Mock(return_value=Mock())

        def boom():
            raise GameStuckError('子任务卡住')

        with self.assertRaises(GameStuckError) as ctx:
            runner._run_with_opsi_task_context(
                'OpsiHazard1Leveling', boom)
        # finally 已把 config.task 恢复，但异常对象上仍带子任务标记
        self.assertEqual(
            'OpsiHazard1Leveling', get_task_from_exception(ctx.exception))

    def test_island_context_marks_exception_with_sub_task(self):
        from module.island import island_scheduling

        runner = island_scheduling.IslandScheduling.__new__(
            island_scheduling.IslandScheduling)
        runner.config = Mock()
        # 代跑任务对象直接用 Mock 顶替，不读真实配置
        runner._make_task_function = Mock(return_value=Mock())

        def boom(*args, **kwargs):
            raise GameStuckError('岛屿子任务卡住')

        # 拦截 importlib.import_module，避免真实导入岛屿子模块拖慢测试；
        # 直接抛异常以验证标记逻辑
        with patch.object(
                island_scheduling.importlib, 'import_module', side_effect=boom), \
                self.assertRaises(GameStuckError) as ctx:
            runner._run_with_island_task_context(
                'IslandDaily', 'module.island.island_daily', 'IslandDaily')
        self.assertEqual(
            'IslandDaily', get_task_from_exception(ctx.exception))


if __name__ == '__main__':
    unittest.main()
