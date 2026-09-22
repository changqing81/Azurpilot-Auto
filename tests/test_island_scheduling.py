"""岛屿计划统一调度的单元测试。

覆盖开关筛选、可选顺序、到期判定、原子任务收敛、异常隔离、父任务延迟与旧配置迁移，
全部不依赖真实设备与 OCR。
"""
import re
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from module.config.config import Function
from module.config.deep import deep_get, deep_set
from module.config.redirect_utils.utils import (
    ISLAND_PLAN_INTERVAL_DEFAULT,
    ISLAND_PLAN_INTERVAL_MAX,
    ISLAND_PLAN_INTERVAL_MIN,
    ISLAND_PLAN_SUB_TASKS,
    clamp_island_plan_interval,
    island_plan_task_priority_redirect,
)
from module.exception import GameStuckError
from module.config.utils import filepath_args, read_file
from module.island.island_scheduling import IslandScheduling
from module.ui.page import page_island
from module.ui.ui import UI

NOW = datetime(2026, 9, 18, 12, 0, 0)

# 旧版 TaskPriority 文本框的默认值，用于迁移用例
OLD_DEFAULT_PRIORITY = ' > '.join(IslandScheduling.SUB_TASKS)


class FakeConfig:
    """仅提供岛屿计划调度所需的配置接口。"""

    def __init__(self, data, interval=12, task_order=''):
        self.data = data
        self.IslandPlan_IntervalHours = interval
        self.IslandPlan_TaskOrder = task_order
        self.modified = {}
        self.task = Function(data['IslandPlan'])
        self.Scheduler_NextRun = NOW
        self.delays = []
        self.bound = []
        self._disable_task_switch = False

    def __getattr__(self, item):
        # 真实配置里 `IslandPlan_EnableFarm` 这类属性由 bind() 从 data 取出；
        # 配置里确实没有该字段时属性不存在，与真实行为一致（读方按开启兜底）。
        if item.startswith('IslandPlan_'):
            field = item[len('IslandPlan_'):]
            value = deep_get(self.data, keys=f'IslandPlan.IslandPlan.{field}')
            if value is None:
                raise AttributeError(item)
            return value
        raise AttributeError(item)

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
        self.delays.append({'minute': minute, 'target': target, 'task': task})


def make_config(enabled=None, interval=12, task_order='', next_runs=None,
                respect_sub_task_times=True):
    """构造只含岛屿任务的配置数据。

    Args:
        enabled: 打开的开关对应的原子任务名；`None` 表示全部打开。
        interval: `IslandPlan.IntervalHours` 的原始值。
        task_order: `IslandPlan.TaskOrder` 的原始文本。
        next_runs: 各子任务的 `Scheduler.NextRun`。
        respect_sub_task_times: `IslandPlan.RespectSubTaskTimes` 的值。
    """
    enabled = set(IslandScheduling.SUB_TASKS) if enabled is None else set(enabled)
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
        },
        'IslandPlan': {
            'RespectSubTaskTimes': respect_sub_task_times,
            **{
                IslandScheduling._enable_arg(name): name in enabled
                for name in IslandScheduling.SUB_TASKS
            },
        },
    }
    return FakeConfig(data=data, interval=interval, task_order=task_order)


def make_runner(config):
    """绕过 __init__ 构造调度器实例，避免真实设备与 OCR 初始化。"""
    runner = IslandScheduling.__new__(IslandScheduling)
    runner.config = config
    runner.device = Mock()
    runner.ui_goto = Mock()
    return runner


class TestIslandTaskList(unittest.TestCase):
    """开关筛选与可选顺序。"""

    def test_all_switches_on_uses_builtin_order(self):
        runner = make_runner(make_config())
        self.assertEqual(runner._build_task_list(), list(IslandScheduling.SUB_TASKS))

    def test_disabled_switches_are_skipped(self):
        config = make_config(enabled=['IslandFarm', 'IslandAirDrop'])
        self.assertEqual(
            make_runner(config)._build_task_list(),
            ['IslandAirDrop', 'IslandFarm'],
        )

    def test_all_switches_off_yields_empty_list(self):
        self.assertEqual(make_runner(make_config(enabled=[]))._build_task_list(), [])

    def test_missing_switches_fall_back_to_enabled(self):
        # 旧配置缺字段时不能整轮不跑
        config = make_config()
        del config.data['IslandPlan']['IslandPlan']
        self.assertEqual(
            make_runner(config)._build_task_list(),
            list(IslandScheduling.SUB_TASKS),
        )

    def test_task_order_reorders_enabled_modules(self):
        config = make_config(task_order='IslandPearlSell > IslandFarm')
        task_list = make_runner(config)._build_task_list()
        self.assertEqual(task_list[:2], ['IslandPearlSell', 'IslandFarm'])
        # 未列出的已启用模块排在后面，且顺序仍是内置顺序
        rest = [name for name in IslandScheduling.SUB_TASKS
                if name not in ('IslandPearlSell', 'IslandFarm')]
        self.assertEqual(task_list[2:], rest)

    def test_task_order_cannot_enable_disabled_module(self):
        config = make_config(enabled=['IslandFarm'], task_order='IslandPearlSell > IslandFarm')
        self.assertEqual(make_runner(config)._build_task_list(), ['IslandFarm'])

    def test_task_order_drops_unknown_and_duplicated_names(self):
        config = make_config(task_order='IslandFarm > NotATask > IslandFarm > IslandAirDrop')
        task_list = make_runner(config)._build_task_list()
        self.assertEqual(task_list[:2], ['IslandFarm', 'IslandAirDrop'])
        self.assertEqual(len(task_list), len(IslandScheduling.SUB_TASKS))

    def test_blank_task_order_uses_builtin_order(self):
        self.assertEqual(
            make_runner(make_config(task_order='   '))._build_task_list(),
            list(IslandScheduling.SUB_TASKS),
        )
        self.assertEqual(
            make_runner(make_config(task_order=None))._build_task_list(),
            list(IslandScheduling.SUB_TASKS),
        )


class TestIslandInterval(unittest.TestCase):
    """运行间隔自由填写，越界收敛到 [1, 24] 小时。"""

    def setUp(self):
        patcher = patch('module.island.island_scheduling.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _interval(self, raw):
        return make_runner(make_config(interval=raw))._get_interval_hours()

    def test_valid_values_pass_through(self):
        self.assertEqual(self._interval(12), 12.0)
        self.assertEqual(self._interval(1), 1.0)
        self.assertEqual(self._interval(24), 24.0)
        self.assertEqual(self._interval(6.5), 6.5)
        self.assertEqual(self._interval('8'), 8.0)

    def test_above_max_is_clamped(self):
        self.assertEqual(self._interval(25), 24.0)
        self.assertEqual(self._interval(240), 24.0)

    def test_below_min_is_clamped(self):
        self.assertEqual(self._interval(0), 1.0)
        self.assertEqual(self._interval(-3), 1.0)

    def test_invalid_values_fall_back_to_default(self):
        default = float(IslandScheduling.DEFAULT_INTERVAL_HOURS)
        for raw in ('abc', '', None, 'nan'):
            with self.subTest(raw=raw):
                self.assertEqual(self._interval(raw), default)

    def test_delay_uses_clamped_interval(self):
        for raw, hours in ((30, 24), (0, 1), (6, 6)):
            with self.subTest(raw=raw):
                config = make_config(enabled=['IslandFarm'], interval=raw)
                make_runner(config)._delay_next_run()
                self.assertEqual(config.delays[0]['target'], NOW + timedelta(hours=hours))


class TestIslandIntervalClamp(unittest.TestCase):
    """配置层的 clamp_island_plan_interval（落盘与保存时用）。"""

    def test_valid_values_pass_through(self):
        self.assertEqual(clamp_island_plan_interval(12), 12)
        self.assertEqual(clamp_island_plan_interval('8'), 8)
        self.assertEqual(clamp_island_plan_interval(6.5), 6.5)

    def test_out_of_range_is_clamped_to_bound(self):
        self.assertEqual(clamp_island_plan_interval(99), ISLAND_PLAN_INTERVAL_MAX)
        self.assertEqual(clamp_island_plan_interval(25), ISLAND_PLAN_INTERVAL_MAX)
        self.assertEqual(clamp_island_plan_interval(0), ISLAND_PLAN_INTERVAL_MIN)
        self.assertEqual(clamp_island_plan_interval(-3), ISLAND_PLAN_INTERVAL_MIN)

    def test_dirty_values_fall_back_to_default(self):
        for raw in ('', None, 'abc', 'nan', [], {}):
            with self.subTest(raw=raw):
                self.assertEqual(
                    clamp_island_plan_interval(raw), ISLAND_PLAN_INTERVAL_DEFAULT
                )

    def test_scheduler_constants_share_config_layer_bounds(self):
        # config 层不许 import module.island（循环依赖），两边只能靠这个断言绑住
        self.assertEqual(IslandScheduling.MIN_INTERVAL_HOURS, ISLAND_PLAN_INTERVAL_MIN)
        self.assertEqual(IslandScheduling.MAX_INTERVAL_HOURS, ISLAND_PLAN_INTERVAL_MAX)
        self.assertEqual(IslandScheduling.DEFAULT_INTERVAL_HOURS, ISLAND_PLAN_INTERVAL_DEFAULT)


class TestIslandBuiltinOrder(unittest.TestCase):
    """内置执行顺序：经营模块排最后，GUI 预填的顺序必须与 SUB_TASKS 一致。"""

    def test_business_runs_last(self):
        # 经营模块最耗时（分批逛商店），整轮被打断时放在最后损失最小
        self.assertEqual(list(IslandScheduling.SUB_TASKS)[-1], 'IslandBusiness')
        self.assertEqual(ISLAND_PLAN_SUB_TASKS[-1], 'Business')

    def test_gui_default_order_matches_sub_tasks(self):
        default = deep_get(
            read_file(filepath_args()), keys='IslandPlan.IslandPlan.TaskOrder.value'
        )
        self.assertTrue(default, 'TaskOrder 应当预填内置顺序而不是留空')
        task_list = make_runner(make_config(task_order=default))._build_task_list()
        self.assertEqual(task_list, list(IslandScheduling.SUB_TASKS))


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
            enabled=['IslandFarm', 'IslandAirDrop', 'IslandPearlSell'],
            task_order='IslandFarm > IslandAirDrop > IslandPearlSell',
            next_runs={'IslandPearlSell': NOW + timedelta(days=3)},
        )
        runner = make_runner(config)
        executed = []
        runner._run_sub_task = lambda name: executed.append(name) or True

        runner.run()

        self.assertEqual(executed, ['IslandFarm', 'IslandAirDrop'])
        runner.ui_goto.assert_called_once()

    def test_run_skips_island_visit_when_nothing_is_due(self):
        config = make_config(
            enabled=['IslandFarm'],
            next_runs={'IslandFarm': NOW + timedelta(hours=1)},
        )
        runner = make_runner(config)
        runner._run_sub_task = Mock()

        runner.run()

        runner._run_sub_task.assert_not_called()
        runner.ui_goto.assert_not_called()

    def test_run_skips_island_visit_when_all_switches_off(self):
        config = make_config(enabled=[])
        runner = make_runner(config)
        runner._run_sub_task = Mock()

        runner.run()

        runner._run_sub_task.assert_not_called()
        runner.ui_goto.assert_not_called()

    def test_delay_uses_interval_hours(self):
        config = make_config(enabled=['IslandFarm'], interval=12)
        runner = make_runner(config)
        runner._run_sub_task = Mock(return_value=True)

        runner.run()

        self.assertEqual(config.delays[0]['target'], NOW + timedelta(hours=12))


class TestIslandNextRunAlignment(unittest.TestCase):
    """父任务下一轮必须对齐「远期」子模块 NextRun，且不被近期待重检拖成高频进岛。

    子模块把下次时间指到固定时刻（18:00 采集 / 次日 03:00 订单·任务·补给），
    只按固定间隔进岛的话，这些时刻落在两轮之间，当天那次必然被跳过
    —— 2026-09-21 实测：每日采集 02:03 设 18:00，下一轮 14:23 未到期，
    18:00 那次采集永远丢了。

    但餐馆家族 `task_delay(minute=0)` 的立刻回访、几十分钟后的重检每轮都会出现，
    不能当作提前进岛的理由 —— 2026-09-22 实测：配 12 小时被压成 30 分钟，
    一天跑了 18 轮。只有超过 `EARLY_TRIGGER_MIN_MINUTES`（60 分钟）的才算数。
    """

    def setUp(self):
        patcher = patch('module.island.island_scheduling.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_earliest_far_next_run_wins(self):
        config = make_config(
            enabled=['IslandDailyGather'],
            next_runs={'IslandDailyGather': NOW + timedelta(hours=2)},
        )
        make_runner(config)._delay_next_run()
        self.assertEqual(
            config.delays[0]['target'], NOW + timedelta(hours=2, minutes=5)
        )

    def test_far_future_next_run_falls_back_to_interval(self):
        config = make_config(
            enabled=['IslandDailyGather'],
            next_runs={'IslandDailyGather': NOW + timedelta(hours=30)},
        )
        make_runner(config)._delay_next_run()
        self.assertEqual(config.delays[0]['target'], NOW + timedelta(hours=12))

    def test_immediate_recheck_is_ignored(self):
        # 子模块 minute=0 的"立刻再看"不能把下一轮拉回现在
        config = make_config(
            enabled=['IslandDailyGather'],
            next_runs={'IslandDailyGather': NOW + timedelta(minutes=1)},
        )
        make_runner(config)._delay_next_run()
        self.assertEqual(config.delays[0]['target'], NOW + timedelta(hours=12))

    def test_near_future_below_threshold_is_ignored(self):
        # 59 分钟后才到期的回访属于"近期待重检"，本轮不去接它
        config = make_config(
            enabled=['IslandDailyGather'],
            next_runs={'IslandDailyGather': NOW + timedelta(minutes=59)},
        )
        make_runner(config)._delay_next_run()
        self.assertEqual(config.delays[0]['target'], NOW + timedelta(hours=12))

    def test_threshold_boundary_is_ignored(self):
        # 阈值语义是"超过 60 分钟"，正好 60 分钟不算
        config = make_config(
            enabled=['IslandDailyGather'],
            next_runs={'IslandDailyGather': NOW + timedelta(minutes=60)},
        )
        make_runner(config)._delay_next_run()
        self.assertEqual(config.delays[0]['target'], NOW + timedelta(hours=12))

    def test_next_run_beyond_threshold_uses_reset_buffer(self):
        config = make_config(
            enabled=['IslandDailyGather'],
            next_runs={'IslandDailyGather': NOW + timedelta(minutes=61)},
        )
        make_runner(config)._delay_next_run()
        self.assertEqual(
            config.delays[0]['target'], NOW + timedelta(minutes=66)
        )

    def test_overdue_next_run_is_ignored(self):
        # 本轮刚跑完、已过期的 NextRun 不能把下一轮拉回现在
        config = make_config(
            enabled=['IslandDailyGather'],
            next_runs={'IslandDailyGather': NOW - timedelta(hours=1)},
        )
        make_runner(config)._delay_next_run()
        self.assertEqual(config.delays[0]['target'], NOW + timedelta(hours=12))

    def test_switch_off_uses_fixed_interval(self):
        # 关掉「尊重子模块到期时间」= 纯固定间隔，远期 NextRun 也不再提前进岛
        config = make_config(
            enabled=['IslandDailyGather'],
            next_runs={'IslandDailyGather': NOW + timedelta(hours=2)},
            respect_sub_task_times=False,
        )
        make_runner(config)._delay_next_run()
        self.assertEqual(config.delays[0]['target'], NOW + timedelta(hours=12))

    def test_missing_switch_defaults_to_respecting_sub_task_times(self):
        # 旧配置缺字段时保持既有行为，且不能整轮不跑
        config = make_config(
            enabled=['IslandDailyGather'],
            next_runs={'IslandDailyGather': NOW + timedelta(hours=2)},
        )
        del config.data['IslandPlan']['IslandPlan']['RespectSubTaskTimes']
        self.assertTrue(make_runner(config)._respect_sub_task_times())
        make_runner(config)._delay_next_run()
        self.assertEqual(
            config.delays[0]['target'], NOW + timedelta(hours=2, minutes=5)
        )

    def test_shop_refill_noise_does_not_shrink_interval(self):
        # 复刻 2026-09-22 真机日志的那一轮：餐馆家族刚写了 minute=0 的立刻回访，
        # 每日订单 5 分钟后重检，啾咖啡 20 分钟后回收 —— 三个都是噪声，
        # 结果必须是固定间隔 12 小时，而不是被压到 30 分钟。
        config = make_config(
            next_runs={
                'IslandJuuEatery': NOW + timedelta(seconds=1),
                'IslandJuuCoffee': NOW + timedelta(seconds=1),
                'IslandRestaurant': NOW + timedelta(seconds=1),
                'IslandDailyOrder': NOW + timedelta(minutes=5),
                'IslandTeahouse': NOW + timedelta(minutes=20),
            },
        )
        make_runner(config)._delay_next_run()
        self.assertEqual(config.delays[0]['target'], NOW + timedelta(hours=12))


class TestIslandNavigationSignature(unittest.TestCase):
    """进岛导航必须用真实存在的方法与参数。

    普通 Mock 会把签名错误一起吞掉（`runner.ui_goto = Mock()` 接受任何参数），
    真机上就是这么炸的：`ui_ensure(page_island, get_ship=False)` —— `get_ship`
    是 `ui_goto` 的参数，`ui_ensure` 的签名里没有。这里用 autospec 让 Mock
    校验真实签名，同类错误以后会在单测阶段就暴露。
    """

    def setUp(self):
        patcher = patch('module.island.island_scheduling.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_run_navigates_with_ui_goto_and_get_ship_false(self):
        config = make_config(enabled=['IslandFarm'])
        runner = make_runner(config)
        runner._run_sub_task = Mock(return_value=True)
        del runner.ui_goto  # 换成 autospec 的 Mock，按真实签名校验

        with patch.object(UI, 'ui_goto', autospec=True, return_value=False) as goto:
            runner.run()

        goto.assert_called_once()
        args, kwargs = goto.call_args
        self.assertIs(args[1], page_island)
        self.assertFalse(kwargs.get('get_ship', True), '岛屿内导航必须 get_ship=False')


class TestIslandSubTaskMapping(unittest.TestCase):
    """SUB_TASKS 里的 (模块路径, 类名) 必须真实存在。

    代跑走的是 `importlib.import_module()` + `getattr()`；映射写错会被「把
    import_module 打桩」的那些用例掩盖过去，只能在真机上炸出 ImportError /
    AttributeError。这里不真导入（全部导入会拖慢整套用例近一分钟），改为查文件
    是否存在 + 源码里有没有对应的 class 定义。
    """

    ROOT = Path(__file__).resolve().parents[1]

    def test_sub_task_modules_and_classes_exist(self):
        for name, (module_path, class_name) in IslandScheduling.SUB_TASKS.items():
            with self.subTest(task=name):
                file = self.ROOT / (module_path.replace('.', '/') + '.py')
                self.assertTrue(file.is_file(), f'{module_path} 不存在')
                source = file.read_text(encoding='utf-8')
                self.assertIsNotNone(
                    re.search(rf'^class {class_name}\b', source, re.M),
                    f'{module_path} 里没有 class {class_name}',
                )


class TestIslandExceptionIsolation(unittest.TestCase):
    """子模块异常传播边界。"""

    def setUp(self):
        patcher = patch('module.island.island_scheduling.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    @contextmanager
    def _runner_with_failures(self, failing):
        config = make_config(
            enabled=['IslandFarm', 'IslandAirDrop', 'IslandPearlSell'],
            task_order='IslandFarm > IslandAirDrop > IslandPearlSell',
        )
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

    def test_fatal_exception_still_schedules_next_run(self):
        # 2026-09-22 实测：中断时若跳过排期，NextRun 停在已过期的旧值，
        # 调度器下一个空闲槽立刻重跑（间隔只有 9.7 分钟）。
        with self._runner_with_failures({'IslandAirDrop': GameStuckError('卡死')}) as (runner, _):
            with self.assertRaises(GameStuckError):
                runner.run()
        self.assertEqual(
            runner.config.delays[-1]['target'], NOW + timedelta(hours=12)
        )

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
        self.assertEqual(config.delays[0]['minute'], 360)


class TestIslandSwitchArgNaming(unittest.TestCase):
    """开关名与原子任务名的对应关系。"""

    def test_enable_arg_is_derived_from_task_name(self):
        self.assertEqual(IslandScheduling._enable_arg('IslandFarm'), 'EnableFarm')
        self.assertEqual(IslandScheduling._enable_arg('IslandJuuCoffee'), 'EnableJuuCoffee')
        self.assertEqual(IslandScheduling._enable_arg('IslandMineForest'), 'EnableMineForest')

    def test_migration_list_matches_sub_tasks(self):
        # 迁移表定义在 config 层，必须与调度器的 SUB_TASKS 完全一致，
        # 否则旧清单会漏迁移或多迁移开关。
        self.assertEqual(
            ISLAND_PLAN_SUB_TASKS,
            [name[len('Island'):] for name in IslandScheduling.SUB_TASKS],
        )


class TestIslandPriorityMigration(unittest.TestCase):
    """旧 TaskPriority 文本框 → 16 个开关。"""

    def _mapping(self, text):
        values = island_plan_task_priority_redirect(text)
        return dict(zip(ISLAND_PLAN_SUB_TASKS, values))

    def test_full_old_default_enables_everything(self):
        mapping = self._mapping(OLD_DEFAULT_PRIORITY)
        self.assertTrue(all(mapping.values()))
        self.assertEqual(len(mapping), 16)

    def test_removed_module_stays_off(self):
        mapping = self._mapping('IslandFarm > IslandAirDrop')
        self.assertTrue(mapping['Farm'])
        self.assertTrue(mapping['AirDrop'])
        self.assertFalse(mapping['PearlSell'])
        self.assertEqual(sum(mapping.values()), 2)

    def test_empty_and_non_string_are_all_off(self):
        for value in ('', '   ', None, 123):
            with self.subTest(value=value):
                self.assertFalse(any(island_plan_task_priority_redirect(value)))

    def test_no_false_positive_between_similar_names(self):
        # IslandFarm 不能被 IslandFishery 之类名字误命中
        mapping = self._mapping('IslandFishery')
        self.assertFalse(any(mapping.values()))


class TestIslandSwitchesConfigUpdate(unittest.TestCase):
    """迁移在真实 ConfigUpdater 链路里生效。"""

    @classmethod
    def setUpClass(cls):
        from module.config.config_updater import ConfigUpdater
        cls.updater = ConfigUpdater()

    def _update(self, plan_args):
        old = {'IslandPlan': {'IslandPlan': plan_args}}
        return self.updater.config_update(old)['IslandPlan']['IslandPlan']

    def test_old_priority_is_migrated_and_removed(self):
        new = self._update({
            'Season': 'autumn',
            'IntervalHours': 6,
            'TaskPriority': 'IslandFarm > IslandAirDrop',
        })
        self.assertNotIn('TaskPriority', new)
        self.assertEqual(new['IntervalHours'], 6)
        self.assertEqual(new['Season'], 'autumn')
        self.assertTrue(new['EnableFarm'])
        self.assertTrue(new['EnableAirDrop'])
        self.assertFalse(new['EnablePearlSell'])

    def test_fresh_config_gets_all_switches_on_and_builtin_order(self):
        new = self._update({})
        enables = {k: v for k, v in new.items() if k.startswith('Enable')}
        self.assertEqual(len(enables), 16)
        self.assertTrue(all(enables.values()))
        # TaskOrder 预填内置顺序（不是留空），且经营模块在最后
        self.assertTrue(new['TaskOrder'].startswith('IslandAirDrop'))
        self.assertTrue(new['TaskOrder'].endswith('IslandBusiness'))
        self.assertEqual(new['IntervalHours'], 12)

    def test_out_of_range_interval_is_clamped_on_load(self):
        # 手改 JSON / 导入配置留下的越界值要在加载时就改掉，而不是拖到运行时
        self.assertEqual(self._update({'IntervalHours': 99})['IntervalHours'], 24)
        self.assertEqual(self._update({'IntervalHours': 0})['IntervalHours'], 1)
        self.assertEqual(self._update({'IntervalHours': 'abc'})['IntervalHours'], 12)

    def test_valid_interval_is_kept(self):
        for raw, expected in ((6, 6), (24, 24), ('8', 8)):
            with self.subTest(raw=raw):
                self.assertEqual(self._update({'IntervalHours': raw})['IntervalHours'], expected)

    def test_save_callback_rewrites_out_of_range_interval(self):
        # 界面保存时立刻回写收敛值，输入框才会当场显示 24
        for raw, expected in ((99, 24), ('99', 24), (0, 1), (240, 24)):
            with self.subTest(raw=raw):
                self.assertEqual(
                    list(self.updater.save_callback('IslandPlan.IslandPlan.IntervalHours', raw)),
                    [('IslandPlan.IslandPlan.IntervalHours', expected)],
                )

    def test_save_callback_keeps_valid_interval(self):
        self.assertEqual(
            list(self.updater.save_callback('IslandPlan.IslandPlan.IntervalHours', 8)), []
        )

    def test_cleared_task_order_falls_back_to_default(self):
        # 不再保留空值：清空后按默认值恢复成预填的内置顺序
        new = self._update({'TaskOrder': ''})
        self.assertTrue(new['TaskOrder'].startswith('IslandAirDrop'))
        self.assertTrue(new['TaskOrder'].endswith('IslandBusiness'))


if __name__ == '__main__':
    unittest.main()
