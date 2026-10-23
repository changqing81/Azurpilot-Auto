"""执行入口不再二次拦截：互斥只在调度队列里判定，且只在智能调度运行中生效。"""

import copy
import unittest
from unittest.mock import patch

from module.campaign.os_run import OSCampaignRun
from module.os.operation_siren import OperationSiren
from module.os.tasks.prevent_action_point_overflow import OpsiPreventActionPointOverflow
from module.os.tasks.scheduling import OpsiScheduling
from tests.test_config_guard_suspend import MemoryConfig, SMART, StateTestCase


class ConditionalEntryTests(StateTestCase):
    def make_runner(self, config):
        """只绕过设备构造，入口方法保持真实实现。"""
        runner = OSCampaignRun.__new__(OSCampaignRun)
        runner.config = config
        return runner

    def run_entry(self, config):
        """执行真实入口，返回 (load_campaign, os_prevent_action_point_overflow) 两个 mock。"""
        runner = self.make_runner(config)
        campaign = OperationSiren.__new__(OperationSiren)
        with (patch.object(OSCampaignRun, 'load_campaign', autospec=True,
                           return_value=campaign) as load,
              patch.object(OperationSiren, 'os_prevent_action_point_overflow',
                           autospec=True) as run):
            runner.opsi_prevent_action_point_overflow()
        return load, run, campaign

    def test_entry_runs_even_when_scheduling_is_ready(self):
        """智能调度只是排队到期时，防溢出正常执行，不被入口拦下。"""
        config = MemoryConfig()
        self.assertEqual(OpsiScheduling.get_scheduling_state(config), 'ready')
        original = copy.deepcopy(config.data)

        load, run, campaign = self.run_entry(config)
        load.assert_called_once()
        run.assert_called_once_with(campaign)
        # 入口不写任何持久化状态
        self.assertEqual(config.data, original)
        self.assertEqual(config.modified, {})

    def test_entry_is_not_gated_before_loading_campaign(self):
        """入口不再在 load_campaign 之前做二次拦截，也不会裸返回造成忙循环。"""
        config = MemoryConfig()
        load, run, campaign = self.run_entry(config)
        load.assert_called_once()
        run.assert_called_once_with(campaign)

    def test_direct_task_entry_is_not_intercepted(self):
        """直接调用业务入口同样交给调度队列互斥，入口本身不拦。"""
        config = MemoryConfig()
        runner = OpsiPreventActionPointOverflow.__new__(OpsiPreventActionPointOverflow)
        runner.config = config
        with (patch.object(OpsiPreventActionPointOverflow,
                           '_get_prevent_action_point_overflow_thresholds',
                           autospec=True, return_value=(200, 50)),
              patch.object(OpsiPreventActionPointOverflow,
                           '_get_current_action_point_for_overflow',
                           autospec=True, return_value=100) as read_ap,
              patch.object(OpsiPreventActionPointOverflow,
                           'update_prevent_action_point_overflow_schedule',
                           autospec=True) as reschedule,
              patch.object(OpsiPreventActionPointOverflow, 'config', create=True)):
            with self.assertRaises(Exception):
                runner.os_prevent_action_point_overflow()
        read_ap.assert_called_once()

    def test_failure_releases_entry_with_switch_still_enabled(self):
        """失败后开关仍开启也不阻止防溢出恢复。"""
        config = MemoryConfig()
        with self.assertRaises(RuntimeError), OpsiScheduling.scheduling_session(config):
            raise RuntimeError('智能调度失败')
        load, run, campaign = self.run_entry(config)
        run.assert_called_once_with(campaign)

    def test_normal_ap_delay_still_changes_only_next_run(self):
        """防溢出正常排程与互斥等待分离，不覆盖用户关闭状态。"""
        config = MemoryConfig(smart_enabled=False)
        config.data['OpsiPreventActionPointOverflow']['Scheduler']['Enable'] = False
        runner = OpsiPreventActionPointOverflow.__new__(OpsiPreventActionPointOverflow)
        runner.config = config
        with patch.object(OpsiPreventActionPointOverflow,
                          '_get_prevent_action_point_overflow_thresholds',
                          autospec=True, return_value=(200, 50)):
            runner.update_prevent_action_point_overflow_schedule(current_ap=200)
        self.assertFalse(config.is_task_enabled('OpsiPreventActionPointOverflow'))

    def test_running_scheduling_still_parks_overflow_in_queue(self):
        """智能调度正在运行时，队列把防溢出放进等待队列（互斥仍生效）。"""
        config = MemoryConfig()
        with OpsiScheduling.scheduling_session(config):
            self.assertEqual(OpsiScheduling.get_scheduling_state(config), 'running')
            self.assert_waiting(config)


class TaskTargetTests(unittest.TestCase):
    """执行任务下拉只剩侵蚀1练级与短猫相接，非法值统一回退。"""

    def make_runner(self, task_value):
        runner = OpsiPreventActionPointOverflow.__new__(OpsiPreventActionPointOverflow)
        runner.config = unittest.mock.Mock()
        runner.config.cross_get = lambda keys, default=None: task_value
        return runner

    def test_hazard1_and_meow_are_accepted(self):
        for value in ('OpsiHazard1Leveling', 'OpsiMeowfficerFarming'):
            self.assertEqual(self.make_runner(value)._get_prevent_action_point_overflow_task(), value)

    def test_legacy_scheduling_value_falls_back_to_hazard1(self):
        """存量配置残留的智能调度被从下拉移除后，回退到侵蚀1练级。"""
        self.assertEqual(
            self.make_runner('OpsiScheduling')._get_prevent_action_point_overflow_task(),
            'OpsiHazard1Leveling',
        )

    def test_unknown_value_falls_back_to_hazard1(self):
        self.assertEqual(
            self.make_runner('NotATask')._get_prevent_action_point_overflow_task(),
            'OpsiHazard1Leveling',
        )


if __name__ == '__main__':
    unittest.main()
