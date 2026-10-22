"""条件挂起的真实队列回归：任务保留身份，旧标记失效，解除后自动恢复。"""

import copy
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from module.config.config import AzurLaneConfig, Function, TaskEnd, name_to_function
from module.config.deep import deep_set
from module.os.tasks.scheduling_state import (
    _RUNTIME_STATES, get_smart_scheduling_state, smart_scheduling_session,
)

TASK = 'OpsiPreventActionPointOverflow'
SMART = 'OpsiScheduling'
NOW = datetime(2026, 10, 22, 12)


class MemoryConfig(AzurLaneConfig):
    """保留真实选任务、延迟和切换实现，仅将磁盘读写替换为内存更新。"""

    SCHEDULER_PRIORITY = f'{TASK} > Main2 > {SMART} > OpsiAshBeacon'

    def __init__(self, smart_enabled=True, flag=False, name='conditional_wait_test'):
        self.bound = {}
        self.modified = {}
        self.auto_update = False
        self.config_name = name
        self.data = {
            task: {'Scheduler': {'Enable': True, 'Command': task,
                                 'NextRun': NOW - timedelta(minutes=1)}}
            for task in (TASK, SMART, 'OpsiAshBeacon')
        }
        self.data[SMART]['Scheduler']['Enable'] = smart_enabled
        self.data[TASK]['Storage'] = {'Storage': {'GuardTempClosed': flag}}
        self.task = Function(self.data[SMART])
        self.pending_task = []
        self.waiting_task = []

    def load(self):
        """使用测试直接修改的内存配置，模拟下一轮热重载。"""

    def update(self):
        """沿用真实 task_delay 的修改结果，仅省去文件 IO。"""
        for path, value in self.modified.items():
            deep_set(self.data, keys=path, value=value)
        self.modified.clear()


class StateTestCase(unittest.TestCase):
    """隔离进程内状态与时钟，避免测试之间共享调度生命周期。"""

    def setUp(self):
        for patcher in (
            patch.dict(_RUNTIME_STATES, clear=True),
            patch('module.config.config.current_time', return_value=NOW),
            patch('module.os.tasks.scheduling_state.current_time', return_value=NOW),
            patch.object(AzurLaneConfig, 'is_hoarding_task', False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_waiting(self, config):
        """核对真实队列中只有一份启用的防溢出任务，并处于等待状态。"""
        config.get_next_task()
        tasks = config.pending_task + config.waiting_task
        matches = [task for task in tasks if task.command == TASK]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0].enable)
        self.assertIn(TASK, [task.command for task in config.waiting_task])
        self.assertNotIn(TASK, [task.command for task in config.pending_task])

    def assert_resumed(self, config):
        """互斥解除后无需修改 NextRun，最高优先级防溢出立即可被选中。"""
        self.assertEqual(config.get_next().command, TASK)
        self.assertEqual(sum(task.command == TASK for task in
                             config.pending_task + config.waiting_task), 1)


class ConditionalWaitTests(StateTestCase):
    def test_old_guard_flag_never_disables_task(self):
        """历史 Status 残留无需人工清理，也不再影响启用查询。"""
        config = MemoryConfig(smart_enabled=False, flag=True)
        self.assertTrue(Function(config.data[TASK]).enable)
        self.assertTrue(config.is_task_enabled(TASK))
        self.assert_resumed(config)

    def test_user_disabled_task_stays_disabled(self):
        """任务常驻约束不覆盖用户主动关闭任务的意愿。"""
        config = MemoryConfig(flag=True)
        config.data[TASK]['Scheduler']['Enable'] = False
        self.assertFalse(config.is_task_enabled(TASK))
        self.assertFalse(Function(config.data[TASK]).enable)
        config.get_next_task()
        self.assertNotIn(TASK, [task.command for task in
                               config.pending_task + config.waiting_task])

    def test_ready_waits_even_when_another_task_precedes_scheduling(self):
        """防溢出最高优先级、主线插在智能调度之前，互斥仍生效。"""
        config = MemoryConfig()
        config.data['Main2'] = {'Scheduler': {
            'Command': 'Main2', 'Enable': True, 'NextRun': NOW - timedelta(minutes=1),
        }}
        self.assert_waiting(config)
        self.assertEqual(config.get_next().command, 'Main2')
        self.assertIn('OpsiAshBeacon', [task.command for task in config.pending_task])

    def test_running_then_disabled_restores_original_schedule(self):
        """开启、运行、关闭后下一轮恢复，整个过程不覆盖原始排程。"""
        config = MemoryConfig()
        original = copy.deepcopy(config.data[TASK])
        with smart_scheduling_session(config):
            self.assertEqual(get_smart_scheduling_state(config), 'running')
            self.assert_waiting(config)
            config.data[SMART]['Scheduler']['Enable'] = False
            self.assertEqual(get_smart_scheduling_state(config), 'disabled')
            self.assert_resumed(config)
        self.assertEqual(config.data[TASK], original)

    def test_completion_releases_even_with_overdue_next_run(self):
        """运行已经结束时，旧的到期时间不能伪装成仍在运行。"""
        config = MemoryConfig()
        with smart_scheduling_session(config):
            self.assert_waiting(config)
        self.assertEqual(get_smart_scheduling_state(config), 'finished')
        self.assert_resumed(config)

    def test_resource_wait_releases_and_refresh_reactivates(self):
        """使用真实 task_delay 模拟资源不足，刷新到期后重新参与互斥。"""
        config = MemoryConfig()
        with self.assertRaises(TaskEnd), smart_scheduling_session(config):
            config.task_delay(minute=60, task=SMART)
            raise TaskEnd
        self.assertEqual(get_smart_scheduling_state(config), 'waiting')
        self.assert_resumed(config)
        later = NOW + timedelta(hours=2)
        with (patch('module.config.config.current_time', return_value=later),
              patch('module.os.tasks.scheduling_state.current_time', return_value=later)):
            self.assertEqual(get_smart_scheduling_state(config), 'ready')
            self.assert_waiting(config)

    def test_error_and_config_reconstruction_do_not_keep_waiting(self):
        """报错后即使开关开启、NextRun 到期，重建配置也不会再次永久拦截。"""
        config = MemoryConfig()
        with self.assertRaises(RuntimeError), smart_scheduling_session(config):
            raise RuntimeError('模拟智能调度异常')
        rebuilt = MemoryConfig()
        self.assertEqual(get_smart_scheduling_state(rebuilt), 'failed')
        self.assert_resumed(rebuilt)
        with smart_scheduling_session(rebuilt):
            self.assert_waiting(rebuilt)

    def test_task_switch_preserves_session_intent(self):
        """原生 TaskEnd 切换不等于崩溃，临时让出后仍可继续智能调度。"""
        config = MemoryConfig()
        with self.assertRaises(TaskEnd), smart_scheduling_session(config):
            raise TaskEnd
        rebuilt = MemoryConfig()
        self.assertEqual(get_smart_scheduling_state(rebuilt), 'interrupted')
        self.assert_waiting(rebuilt)

    def test_process_restart_ignores_persisted_guard_flag(self):
        """新进程只按开关和排程重建状态，等待刷新时不会被旧标记锁住。"""
        config = MemoryConfig(flag=True)
        config.task_delay(minute=60, task=SMART)
        _RUNTIME_STATES.clear()
        self.assert_resumed(config)

    def test_instances_and_nested_sessions_are_isolated(self):
        """不同实例不串状态，同一实例嵌套入口不会提前释放外层会话。"""
        config = MemoryConfig()
        with smart_scheduling_session(config):
            with smart_scheduling_session(config):
                self.assertEqual(get_smart_scheduling_state(config), 'running')
            self.assertEqual(get_smart_scheduling_state(config), 'running')
            other = MemoryConfig(name='other_instance')
            self.assertEqual(get_smart_scheduling_state(other), 'ready')
        self.assertEqual(get_smart_scheduling_state(other), 'ready')

    def test_repeated_polling_never_selects_waiting_overflow(self):
        """连续轮询只会选中智能调度，防溢出不会裸返回造成忙循环。"""
        config = MemoryConfig()
        original = copy.deepcopy(config.data)
        with smart_scheduling_session(config):
            for _ in range(20):
                self.assertEqual(config.get_next().command, SMART)
                self.assert_waiting(config)
        self.assertEqual(config.data, original)

    def test_proxy_task_switch_does_not_yield_to_high_priority_overflow(self):
        """侵蚀1代跑沿用外层切换身份，防溢出等待不会打断智能调度。"""
        config = MemoryConfig()
        config._task_switch_owner = config.task
        config.task = name_to_function('OpsiHazard1Leveling')
        with smart_scheduling_session(config):
            config.check_task_switch()
            config.data[SMART]['Scheduler']['Enable'] = False
            with self.assertRaises(TaskEnd):
                config.check_task_switch()
        self.assert_resumed(config)

    def test_existing_future_overflow_schedule_is_preserved(self):
        """只解除互斥等待，不抢跑行动力不足时正常计算出的未来排程。"""
        config = MemoryConfig()
        config.task_delay(minute=90, task=TASK)
        with smart_scheduling_session(config):
            self.assert_waiting(config)
        config.data[SMART]['Scheduler']['Enable'] = False
        config.get_next_task()
        task = next(task for task in config.waiting_task if task.command == TASK)
        self.assertEqual(task.next_run, NOW + timedelta(minutes=90))


if __name__ == '__main__':
    unittest.main()
