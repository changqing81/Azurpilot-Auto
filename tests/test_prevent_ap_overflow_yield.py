"""防止行动力溢出与智能调度（OpsiScheduling）互斥的单元测试。

行为约定：智能调度正常运行时防行动力溢出不许插队抢占，即使它的优先级
被配置得比智能调度更高；智能调度挂起（延迟到未来，不在待处理队列里）、
未启用，或队列里排在前面的是其他任务时，防溢出的原有逻辑保持不变。

判据取「去掉本任务后待处理队列的队首」——即调度器的真实选任务依据，
而不是智能调度自己的 NextRun：被抢占后它的 NextRun 仍停在过去。
"""
import unittest
from types import MethodType, SimpleNamespace

from module.campaign.os_run import OSCampaignRun
from module.os.tasks.prevent_action_point_overflow import OpsiPreventActionPointOverflow

TASK = 'OpsiPreventActionPointOverflow'
SCHEDULING = 'OpsiScheduling'


class FakeConfig:
    """最小化配置替身：只提供待处理队列与延迟写入记录。"""

    def __init__(self, pending=()):
        self.pending_task = []
        self._pending_commands = list(pending)
        self.schedule_delays = []
        self.cross_set_calls = []

    def get_next_task(self):
        """与真实实现同构：计算并写入 pending_task（已按优先级排序）。"""
        self.pending_task = [
            SimpleNamespace(command=command) for command in self._pending_commands
        ]

    def task_delay(self, minute=None, task=None, **kwargs):
        self.schedule_delays.append((minute, task))

    def cross_set(self, keys, value):
        self.cross_set_calls.append((keys, value))


def make_runner(config, campaign=None, load_raises=False):
    """构造只绑定入口方法的 OSCampaignRun 替身。"""
    runner = SimpleNamespace()
    runner.config = config

    def load_campaign():
        if load_raises:
            raise RuntimeError('load campaign failed')
        return campaign

    runner.load_campaign = load_campaign
    runner.opsi_prevent_action_point_overflow = MethodType(
        OSCampaignRun.opsi_prevent_action_point_overflow, runner
    )
    return runner


class SmartSchedulingTakesTurnTests(unittest.TestCase):
    """队列判据：去掉本任务后队首是智能调度时才让出。"""

    def test_true_when_smart_scheduling_is_runner_up(self):
        """智能调度紧随本任务之后（正在运行或被本任务抢占）时让出。"""
        config = FakeConfig(pending=[TASK, SCHEDULING, 'ThreeOilLowCost'])
        self.assertTrue(
            OpsiPreventActionPointOverflow.smart_scheduling_takes_turn(config)
        )

    def test_false_when_smart_scheduling_suspended(self):
        """智能调度挂起（不在待处理队列）时互斥解除。"""
        config = FakeConfig(pending=[TASK, 'ThreeOilLowCost'])
        self.assertFalse(
            OpsiPreventActionPointOverflow.smart_scheduling_takes_turn(config)
        )

    def test_false_when_smart_scheduling_disabled(self):
        """智能调度未启用时不在队列里，互斥解除（与挂起同一条判据）。"""
        config = FakeConfig(pending=[TASK])
        self.assertFalse(
            OpsiPreventActionPointOverflow.smart_scheduling_takes_turn(config)
        )

    def test_false_when_another_task_would_run_next(self):
        """队首是主线等任务时不让出：防溢出仍可抢在它们前面烧行动力。"""
        config = FakeConfig(pending=[TASK, 'Main2', SCHEDULING])
        self.assertFalse(
            OpsiPreventActionPointOverflow.smart_scheduling_takes_turn(config)
        )

    def test_false_when_queue_has_only_self(self):
        """队列里只剩本任务时（无其他待处理任务）不让出。"""
        config = FakeConfig(pending=[TASK])
        self.assertFalse(
            OpsiPreventActionPointOverflow.smart_scheduling_takes_turn(config)
        )


class YieldTurnTests(unittest.TestCase):
    """让出动作只写 NextRun，绝不写 Scheduler.Enable。"""

    def test_yield_writes_next_run_only(self):
        config = FakeConfig(pending=[TASK, SCHEDULING])
        OpsiPreventActionPointOverflow.yield_turn_to_smart_scheduling(config)
        self.assertEqual(
            config.schedule_delays,
            [(OpsiPreventActionPointOverflow.SMART_SCHEDULING_YIELD_MINUTES, TASK)],
        )
        self.assertEqual(config.cross_set_calls, [])


class EntryWiringTests(unittest.TestCase):
    """入口接线：让出时完全不碰游戏，正常时保持原流程。"""

    def test_entry_yields_before_loading_campaign(self):
        config = FakeConfig(pending=[TASK, SCHEDULING])
        loaded = []
        runner = make_runner(config)
        original = runner.load_campaign

        def record_load():
            loaded.append(True)
            return original()

        runner.load_campaign = record_load
        runner.opsi_prevent_action_point_overflow()

        self.assertEqual(loaded, [])
        self.assertEqual(
            config.schedule_delays,
            [(OpsiPreventActionPointOverflow.SMART_SCHEDULING_YIELD_MINUTES, TASK)],
        )

    def test_entry_runs_task_when_smart_scheduling_not_next(self):
        config = FakeConfig(pending=[TASK, 'ThreeOilLowCost'])
        calls = []
        campaign = SimpleNamespace(
            os_prevent_action_point_overflow=lambda: calls.append(True)
        )
        runner = make_runner(config, campaign=campaign)

        runner.opsi_prevent_action_point_overflow()

        self.assertEqual(calls, [True])
        self.assertEqual(config.schedule_delays, [])


if __name__ == '__main__':
    unittest.main()
