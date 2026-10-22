"""大世界入口生命周期回归：不再持久化挂起标记，异常与关闭自动释放。"""

from unittest.mock import patch

from module.campaign.os_run import OSCampaignRun
from module.config.config import TaskEnd, name_to_function
from module.os.operation_siren import OperationSiren
from module.os.tasks.scheduling import OpsiScheduling
from tests.test_config_guard_suspend import MemoryConfig, SMART, StateTestCase, TASK


class GuardLifecycleTests(StateTestCase):
    def make_runner(self, config):
        """跳过设备初始化，但执行真实战役入口与生命周期包装。"""
        runner = OSCampaignRun.__new__(OSCampaignRun)
        runner.config = config
        return runner

    def test_initialization_and_run_are_tracked_without_flag_writes(self):
        """初始化已进入互斥，正常退出不覆盖防溢出的 NextRun。"""
        config = MemoryConfig(flag=True)
        runner = self.make_runner(config)
        original = config.data[TASK]['Scheduler']['NextRun']

        def initialize(_runner, cls=OperationSiren):
            self.assertEqual(OpsiScheduling.get_scheduling_state(config), 'running')
            self.assert_waiting(config)
            return OperationSiren.__new__(OperationSiren)

        with (patch.object(OSCampaignRun, 'load_campaign', autospec=True,
                           side_effect=initialize),
              patch.object(OperationSiren, 'update_prevent_action_point_overflow_schedule',
                           autospec=True) as update):
            result = runner._run_opsi_task_with_ap_overflow_guard(lambda campaign: '完成')
        self.assertEqual(result, '完成')
        update.assert_not_called()
        self.assertEqual(config.modified, {})
        self.assertTrue(config.data[TASK]['Storage']['Storage']['GuardTempClosed'])
        self.assertEqual(config.data[TASK]['Scheduler']['NextRun'], original)
        self.assert_resumed(config)

    def test_initialization_failure_releases_wait(self):
        """初始化异常也计入失败状态，不能仅保护已经进入的智能调度循环。"""
        config = MemoryConfig()
        runner = self.make_runner(config)
        with patch.object(OSCampaignRun, 'load_campaign', autospec=True,
                          side_effect=RuntimeError('初始化失败')):
            with self.assertRaises(RuntimeError):
                runner._run_opsi_task_with_ap_overflow_guard(lambda campaign: None)
        self.assertEqual(OpsiScheduling.get_scheduling_state(config), 'failed')
        self.assert_resumed(config)

    def test_business_failure_releases_wait(self):
        """运行异常继续抛给顶层，但立即解除防溢出的条件挂起。"""
        config = MemoryConfig()
        runner = self.make_runner(config)

        def fail(campaign):
            raise ValueError('执行失败')

        with patch.object(OSCampaignRun, 'load_campaign', autospec=True):
            with self.assertRaises(ValueError):
                runner._run_opsi_task_with_ap_overflow_guard(fail)
        self.assert_resumed(config)

    def test_gui_disable_during_real_task_switch_releases_wait(self):
        """使用真实 check_task_switch，模拟 GUI 关闭后的热重载与 TaskEnd。"""
        config = MemoryConfig()
        runner = self.make_runner(config)

        def disable(campaign):
            config.data[SMART]['Scheduler']['Enable'] = False
            config.check_task_switch()

        with patch.object(OSCampaignRun, 'load_campaign', autospec=True):
            with self.assertRaises(TaskEnd):
                runner._run_opsi_task_with_ap_overflow_guard(disable)
        self.assert_resumed(config)

    def test_direct_smart_entry_tracks_exceptions(self):
        """绕过战役入口直接运行也有状态保护。"""
        config = MemoryConfig()
        runner = OpsiScheduling.__new__(OpsiScheduling)
        runner.config = config
        with (patch.object(OpsiScheduling, '_delay_smart_scheduling_for_opsi_explore',
                           autospec=True, return_value=False),
              patch.object(OpsiScheduling, 'is_smart_scheduling_enabled',
                           autospec=True, return_value=True),
              patch.object(OpsiScheduling, 'run_smart_scheduling_once',
                           autospec=True, side_effect=RuntimeError('执行失败'))):
            with self.assertRaises(RuntimeError):
                runner.run_smart_scheduling()
        self.assert_resumed(config)

    def test_other_opsi_tasks_keep_ap_schedule_update(self):
        """普通大世界任务仍更新行动力排程，不再临时禁用防溢出。"""
        config = MemoryConfig(smart_enabled=False)
        config.task = name_to_function('OpsiDaily')
        runner = self.make_runner(config)
        campaign = OperationSiren.__new__(OperationSiren)
        with (patch.object(OSCampaignRun, 'load_campaign', autospec=True,
                           return_value=campaign),
              patch.object(OperationSiren, 'update_prevent_action_point_overflow_schedule',
                           autospec=True) as update):
            runner._run_opsi_task_with_ap_overflow_guard(lambda campaign: None)
        update.assert_called_once_with(campaign)
        self.assertEqual(config.modified, {})

    def test_user_disabled_overflow_is_never_reenabled(self):
        """多次运行大世界任务也不能覆盖用户关闭意愿。"""
        config = MemoryConfig(smart_enabled=False, flag=True)
        config.data[TASK]['Scheduler']['Enable'] = False
        config.task = name_to_function('OpsiDaily')
        runner = self.make_runner(config)
        campaign = OperationSiren.__new__(OperationSiren)
        with (patch.object(OSCampaignRun, 'load_campaign', autospec=True,
                           return_value=campaign),
              patch.object(OperationSiren, 'update_prevent_action_point_overflow_schedule',
                           autospec=True) as update):
            for _ in range(3):
                runner._run_opsi_task_with_ap_overflow_guard(lambda campaign: None)
        update.assert_not_called()
        self.assertFalse(config.is_task_enabled(TASK))
        self.assertEqual(config.modified, {})
