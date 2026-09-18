"""岛屿计划统一调度的单元测试。

覆盖清单解析、到期判定、原子任务收敛、异常隔离与父任务延迟，
全部不依赖真实设备与 OCR。
"""
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from module.config.config import Function
from module.config.deep import deep_set
from module.exception import GameStuckError
from module.island.island_scheduling import IslandScheduling

NOW = datetime(2026, 9, 18, 12, 0, 0)


class FakeConfig:
    """仅提供岛屿计划调度所需的配置接口。"""

    def __init__(self, data, priority, interval=12):
        self.data = data
        self.IslandPlan_TaskPriority = priority
        self.IslandPlan_IntervalHours = interval
        self.modified = {}
        self.task = Function(data['IslandPlan'])
        self.Scheduler_NextRun = NOW
        self.delays = []
        self.bound = []
        self._disable_task_switch = False

    def save(self):
        for path, value in self.modified.items():
            deep_set(self.data, keys=path, value=value)
        self.modified.clear()
        return True

    def bind(self, func, func_list=None):
        if isinstance(func, Function):
            func = func.command
        self.bound.append(func)

    def task_delay(self, success=None, server_update=None, target=None, minute=None, task=None):
        self.delays.append({'minute': minute, 'task': task})


def make_config(priority='IslandFarm > IslandAirDrop', interval=12, next_runs=None):
    """构造只含岛屿任务的配置数据。"""
    next_runs = next_runs or {}
    data = {}
    for name in IslandScheduling.SUB_TASKS:
        data[name] = {
            'Scheduler': {
                'Command': name,
                'Enable': False,
                'NextRun': next_runs.get(name, NOW - timedelta(hours=1)),
            }
        }
    data['IslandPlan'] = {
        'Scheduler': {
            'Command': 'IslandPlan',
            'Enable': True,
            'NextRun': NOW - timedelta(hours=1),
        }
    }
    return FakeConfig(data=data, priority=priority, interval=interval)


def make_runner(config):
    """绕过 __init__ 构造调度器实例，避免真实设备与 OCR 初始化。"""
    runner = IslandScheduling.__new__(IslandScheduling)
    runner.config = config
    runner.device = Mock()
    runner.ui_ensure = Mock()
    return runner


class TestIslandTaskList(unittest.TestCase):
    """IslandPlan.TaskPriority 清单解析。"""

    def test_order_follows_priority_text(self):
        config = make_config(priority='IslandPearlSell > IslandFarm > IslandAirDrop')
        self.assertEqual(
            make_runner(config)._build_task_list(),
            ['IslandPearlSell', 'IslandFarm', 'IslandAirDrop'],
        )

    def test_unknown_and_duplicated_names_are_dropped(self):
        config = make_config(priority='IslandFarm > NotATask > IslandFarm > IslandAirDrop')
        self.assertEqual(
            make_runner(config)._build_task_list(),
            ['IslandFarm', 'IslandAirDrop'],
        )

    def test_empty_priority_yields_empty_list(self):
        config = make_config(priority='   ')
        self.assertEqual(make_runner(config)._build_task_list(), [])


class TestIslandDueCheck(unittest.TestCase):
    """子模块到期判定只看 NextRun。"""

    def setUp(self):
        patcher = patch('module.island.island_scheduling.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_past_and_exact_now_are_due(self):
        config = make_config(next_runs={
            'IslandFarm': NOW - timedelta(seconds=1),
            'IslandAirDrop': NOW,
        })
        runner = make_runner(config)
        self.assertTrue(runner._is_sub_task_due('IslandFarm'))
        self.assertTrue(runner._is_sub_task_due('IslandAirDrop'))

    def test_future_is_not_due(self):
        config = make_config(next_runs={'IslandFarm': NOW + timedelta(seconds=1)})
        self.assertFalse(make_runner(config)._is_sub_task_due('IslandFarm'))

    def test_enable_flag_is_ignored(self):
        # 原子任务的 Enable 会在本轮被收敛为 False，判定不能再依赖它。
        config = make_config()
        self.assertEqual(config.data['IslandFarm']['Scheduler']['Enable'], False)
        self.assertTrue(make_runner(config)._is_sub_task_due('IslandFarm'))


class TestIslandAggregation(unittest.TestCase):
    """一轮运行的编排行为。"""

    def setUp(self):
        patcher = patch('module.island.island_scheduling.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_converge_disables_atomic_tasks(self):
        config = make_config()
        for name in IslandScheduling.SUB_TASKS:
            config.data[name]['Scheduler']['Enable'] = True
        make_runner(config)._converge_atomic_tasks()
        for name in IslandScheduling.SUB_TASKS:
            self.assertFalse(config.data[name]['Scheduler']['Enable'], name)

    def test_converge_keeps_parent_task_enabled(self):
        config = make_config()
        config.data['IslandFarm']['Scheduler']['Enable'] = True
        make_runner(config)._converge_atomic_tasks()
        self.assertTrue(config.data['IslandPlan']['Scheduler']['Enable'])

    def test_run_executes_due_tasks_in_order_and_skips_future(self):
        config = make_config(
            priority='IslandFarm > IslandAirDrop > IslandPearlSell',
            next_runs={'IslandPearlSell': NOW + timedelta(days=3)},
        )
        runner = make_runner(config)
        executed = []
        runner._run_sub_task = lambda name: executed.append(name) or True

        runner.run()

        self.assertEqual(executed, ['IslandFarm', 'IslandAirDrop'])
        runner.ui_ensure.assert_called_once()

    def test_run_skips_island_visit_when_nothing_is_due(self):
        config = make_config(
            priority='IslandFarm',
            next_runs={'IslandFarm': NOW + timedelta(hours=1)},
        )
        runner = make_runner(config)
        runner._run_sub_task = Mock()

        runner.run()

        runner._run_sub_task.assert_not_called()
        runner.ui_ensure.assert_not_called()

    def test_delay_uses_interval_hours(self):
        config = make_config(priority='IslandFarm', interval=12)
        runner = make_runner(config)
        runner._run_sub_task = Mock(return_value=True)

        runner.run()

        self.assertEqual(config.delays, [{'minute': 720, 'task': None}])


class TestIslandExceptionIsolation(unittest.TestCase):
    """子模块异常传播边界。"""

    def setUp(self):
        patcher = patch('module.island.island_scheduling.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    @contextmanager
    def _runner_with_failures(self, failing):
        config = make_config(priority='IslandFarm > IslandAirDrop > IslandPearlSell')
        runner = make_runner(config)
        executed = []

        def fake_context(task_name, module_path, class_name):
            executed.append(task_name)
            if task_name in failing:
                raise failing[task_name]

        runner._run_with_island_task_context = fake_context
        yield runner, executed

    def test_normal_exception_keeps_round_running(self):
        with self._runner_with_failures({'IslandAirDrop': ValueError('识别失败')}) as (runner, executed):
            runner.run()
        self.assertEqual(executed, ['IslandFarm', 'IslandAirDrop', 'IslandPearlSell'])

    def test_fatal_exception_interrupts_round(self):
        with self._runner_with_failures({'IslandAirDrop': GameStuckError('卡死')}) as (runner, executed):
            with self.assertRaises(GameStuckError):
                runner.run()
        self.assertEqual(executed, ['IslandFarm', 'IslandAirDrop'])

    def test_run_sub_task_returns_false_on_failure(self):
        with self._runner_with_failures({'IslandFarm': ValueError('识别失败')}) as (runner, _):
            self.assertFalse(runner._run_sub_task('IslandFarm'))
        with self._runner_with_failures({}) as (runner, _):
            self.assertTrue(runner._run_sub_task('IslandFarm'))


class TestIslandTaskContext(unittest.TestCase):
    """子任务身份切换与恢复。"""

    def setUp(self):
        patcher = patch('module.island.island_scheduling.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_context(self, runner, task_name, func):
        module_path, class_name = IslandScheduling.SUB_TASKS[task_name]
        with patch('module.island.island_scheduling.importlib.import_module') as import_module:
            sub_task_cls = Mock()
            sub_task_cls.return_value.run = func
            import_module.return_value = type('FakeModule', (), {class_name: sub_task_cls})
            runner._run_with_island_task_context(task_name, module_path, class_name)
            return sub_task_cls

    def test_context_switches_and_restores(self):
        config = make_config()
        runner = make_runner(config)
        parent_task = config.task
        seen = {}

        def func():
            seen['bound'] = list(config.bound)
            seen['disable_switch'] = config._disable_task_switch
            seen['task'] = config.task.command
            seen['owner'] = config._task_switch_owner

        self._run_context(runner, 'IslandFarm', func)

        self.assertEqual(seen['task'], 'IslandFarm')
        self.assertEqual(seen['owner'], parent_task)
        self.assertEqual(seen['bound'][-1], 'IslandFarm')
        self.assertTrue(seen['disable_switch'])

        self.assertEqual(config.task, parent_task)
        self.assertFalse(config._disable_task_switch)
        self.assertFalse(hasattr(config, '_task_switch_owner'))
        self.assertFalse(hasattr(config, '_bind_task_override'))

    def test_context_restores_on_exception(self):
        config = make_config()
        runner = make_runner(config)
        parent_task = config.task

        def func():
            raise ValueError('boom')

        with self.assertRaises(ValueError):
            self._run_context(runner, 'IslandFarm', func)

        self.assertEqual(config.task, parent_task)
        self.assertFalse(config._disable_task_switch)
        self.assertEqual(config.bound[-1], 'IslandPlan')

    def test_sub_task_delay_writes_its_own_next_run(self):
        config = make_config()
        runner = make_runner(config)

        def func():
            # 子任务身份下 task_delay 必须落在子任务自己身上
            config.task_delay(minute=6 * 60)

        self._run_context(runner, 'IslandFarm', func)
        self.assertEqual(config.delays, [{'minute': 360, 'task': None}])


if __name__ == '__main__':
    unittest.main()