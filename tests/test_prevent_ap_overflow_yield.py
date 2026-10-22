"""执行入口条件拦截：不碰游戏、不改排程，解除互斥后执行原任务。"""

import copy
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

    def test_entry_waits_before_loading_campaign(self):
        """等待阶段保持任务在队列中，且不初始化游戏。"""
        config = MemoryConfig()
        runner = self.make_runner(config)
        original = copy.deepcopy(config.data)
        with patch.object(OSCampaignRun, 'load_campaign', autospec=True) as load:
            runner.opsi_prevent_action_point_overflow()
        load.assert_not_called()
        self.assert_waiting(config)
        self.assertEqual(config.data, original)
        self.assertEqual(config.modified, {})

    def test_entry_resumes_immediately_after_disable(self):
        """完整验收：开启待命、关闭后下一次入口直接执行原任务。"""
        config = MemoryConfig()
        runner = self.make_runner(config)
        campaign = OperationSiren.__new__(OperationSiren)
        with (patch.object(OSCampaignRun, 'load_campaign', autospec=True,
                           return_value=campaign) as load,
              patch.object(OperationSiren, 'os_prevent_action_point_overflow',
                           autospec=True) as run):
            runner.opsi_prevent_action_point_overflow()
            load.assert_not_called()
            run.assert_not_called()
            config.data[SMART]['Scheduler']['Enable'] = False
            self.assert_resumed(config)
            runner.opsi_prevent_action_point_overflow()
        load.assert_called_once_with(runner)
        run.assert_called_once_with(campaign)

    def test_direct_task_entry_is_also_intercepted(self):
        """直接调用业务入口也不能绕过互斥。"""
        config = MemoryConfig()
        runner = OpsiPreventActionPointOverflow.__new__(OpsiPreventActionPointOverflow)
        runner.config = config
        with patch.object(OpsiPreventActionPointOverflow,
                          '_get_current_action_point_for_overflow', autospec=True) as read_ap:
            runner.os_prevent_action_point_overflow()
        read_ap.assert_not_called()
        self.assert_waiting(config)

    def test_failure_releases_entry_with_switch_still_enabled(self):
        """失败后开关仍开启也不阻止防溢出恢复。"""
        config = MemoryConfig()
        runner = self.make_runner(config)
        with self.assertRaises(RuntimeError), OpsiScheduling.scheduling_session(config):
            raise RuntimeError('智能调度失败')
        campaign = OperationSiren.__new__(OperationSiren)
        with (patch.object(OSCampaignRun, 'load_campaign', autospec=True,
                           return_value=campaign),
              patch.object(OperationSiren, 'os_prevent_action_point_overflow',
                           autospec=True) as run):
            runner.opsi_prevent_action_point_overflow()
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
