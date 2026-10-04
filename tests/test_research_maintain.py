"""科研「维护当天科研」回归：配置开关、照常运行+强制窗口调度与排程钳制。

语义（用户定义）：识别到今天有维护时照常运行科研；保证在维护前 60 分钟
强制运行一次，收光已完成项目并检查队列做满与否；未做满每 15 分钟再运行
一次，做满后无需再运行。
"""

import unittest
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

from module.config.config import AzurLaneConfig, name_to_function
from module.config.time_source import now as current_time
from module.config.utils import read_file
from module.research.research import (RESEARCH_MAINTAIN_LEAD_MINUTES,
                                      RESEARCH_MAINTAIN_RETRY_MINUTES,
                                      RewardResearch)


def make_config(**groups):
    """使用真实更新和绑定流程，全程在内存中操作配置。"""
    config = AzurLaneConfig('template')
    config.auto_update = False
    config.data = config.config_update({'Research': groups})
    config.bind('Research')
    config.task = name_to_function('Research')
    return config


def make_runner(config):
    """绕过 __init__ 构造仅带 config 的实例，UI/队列方法在用例里按需 patch。

    autospec=True 保证参数名写错时测试直接失败（Mock 盲区规则）。
    """
    runner = RewardResearch.__new__(RewardResearch)
    runner.config = config
    return runner


class TestMaintainFillConfig(unittest.TestCase):
    def test_maintain_fill_defaults_to_off(self):
        """新配置默认关闭，旧配置经 config_update 自动补齐。"""
        self.assertIs(make_config().Research_MaintainFill, False)
        self.assertIs(
            make_config(Research={'BatchMode': True, 'MaintainFill': True}).Research_MaintainFill,
            True)

    def test_maintain_fill_visible_in_webui(self):
        """开关必须对 GUI 可编辑：checkbox 类型且未隐藏。"""
        definition = read_file('module/config/argument/args.json')['Research']['Research']['MaintainFill']
        self.assertEqual(definition.get('type'), 'checkbox')
        self.assertIs(definition.get('value'), False)
        self.assertNotIn(definition.get('display'), ('hide', 'disabled'))

    def test_lead_and_retry_minutes(self):
        """强制运行点是维护前 60 分钟，未做满的重试间隔是 15 分钟。"""
        self.assertEqual(RESEARCH_MAINTAIN_LEAD_MINUTES, 60)
        self.assertEqual(RESEARCH_MAINTAIN_RETRY_MINUTES, 15)


class TestRunBranches(unittest.TestCase):
    """run() 的维护分支：照常运行（钳强制点）、窗口内强制运行、正常流程。"""

    @staticmethod
    def make_runner(**groups):
        groups.setdefault('BatchMode', True)
        groups.setdefault('MaintainFill', True)
        return make_runner(make_config(Research=groups))

    @staticmethod
    def patch_game(times=(timedelta(hours=3), None, None, None, None), slot=0):
        """顶掉 run() 会触碰的 UI/队列方法；times 为 _batch_snapshot 的 5 槽显示时长。

        返回 ExitStack，供 with 使用，退出时按序还原。
        """
        patchers = [
            patch.object(RewardResearch, 'ui_ensure', autospec=True),
            patch.object(RewardResearch, 'queue_enter', autospec=True),
            patch.object(RewardResearch, 'queue_receive', autospec=True),
            patch.object(RewardResearch, 'queue_quit', autospec=True),
            patch.object(RewardResearch, 'handle_pending_t_research', autospec=True,
                         return_value=True),
            patch.object(RewardResearch, 'receive_6th_research', autospec=True),
            patch.object(RewardResearch, 'research_fill_queue', autospec=True),
            patch.object(RewardResearch, 'get_research_ended', autospec=True,
                         return_value=current_time()),
            patch.object(RewardResearch, 'get_queue_slot', autospec=True, return_value=slot),
            patch.object(RewardResearch, '_batch_ready', autospec=True, return_value=True),
            patch.object(RewardResearch, '_batch_snapshot', autospec=True,
                         return_value=(list(times), None)),
        ]
        stack = ExitStack()
        for patcher in patchers:
            stack.enter_context(patcher)
        return stack

    def test_normal_run_clamped_to_forced_point(self):
        """照常运行阶段：Σ 排程越过强制运行点时钳到维护前 60 分钟。"""
        runner = self.make_runner()
        maintain = current_time() + timedelta(hours=3)
        with patch('module.research.research.query_maintain_today', autospec=True,
                   return_value=(maintain, '今天有维护', True)), \
                self.patch_game(times=(timedelta(hours=3), None, None, None, None)):
            runner.run()
        lead = (maintain - timedelta(minutes=RESEARCH_MAINTAIN_LEAD_MINUTES)).replace(microsecond=0)
        self.assertEqual(runner.config.Scheduler_NextRun, lead)

    def test_normal_run_keeps_early_schedule(self):
        """照常运行阶段：Σ 排程远早于强制运行点时不钳制。"""
        runner = self.make_runner()
        maintain = current_time() + timedelta(hours=3)
        with patch('module.research.research.query_maintain_today', autospec=True,
                   return_value=(maintain, '今天有维护', True)), \
                self.patch_game(times=(timedelta(minutes=10), None, None, None, None)):
            runner.run()
        expected = (current_time() + timedelta(minutes=13)).replace(microsecond=0)
        self.assertEqual(runner.config.Scheduler_NextRun, expected)

    def test_window_retry_when_not_full(self):
        """强制运行阶段队列未做满：15 分钟后再运行一次。"""
        runner = self.make_runner()
        maintain = current_time() + timedelta(minutes=30)
        with patch('module.research.research.query_maintain_today', autospec=True,
                   return_value=(maintain, '今天有维护', True)), \
                self.patch_game(slot=2):
            runner.run()
        expected = (current_time() + timedelta(minutes=RESEARCH_MAINTAIN_RETRY_MINUTES))
        self.assertAlmostEqual(runner.config.Scheduler_NextRun, expected,
                               delta=timedelta(seconds=5))

    def test_window_full_clamped_to_maintenance(self):
        """强制运行阶段队列做满：排程不早于维护开始。"""
        runner = self.make_runner()
        maintain = current_time() + timedelta(minutes=50)
        with patch('module.research.research.query_maintain_today', autospec=True,
                   return_value=(maintain, '今天有维护', True)), \
                self.patch_game(times=(timedelta(minutes=30), None, None, None, None), slot=0):
            runner.run()
        self.assertEqual(runner.config.Scheduler_NextRun, maintain.replace(microsecond=0))

    def test_window_full_keeps_late_schedule(self):
        """强制运行阶段队列做满且 Σ 排程晚于维护开始：不钳制。"""
        runner = self.make_runner()
        maintain = current_time() + timedelta(minutes=30)
        with patch('module.research.research.query_maintain_today', autospec=True,
                   return_value=(maintain, '今天有维护', True)), \
                self.patch_game(times=(timedelta(hours=3), None, None, None, None), slot=0):
            runner.run()
        self.assertGreater(runner.config.Scheduler_NextRun, maintain)

    def test_maintenance_already_started_runs_normally(self):
        """今天的维护已经开始：不再算维护模式，走正常批量流程。"""
        runner = self.make_runner()
        maintain = current_time() - timedelta(minutes=30)
        with patch('module.research.research.query_maintain_today', autospec=True,
                   return_value=(maintain, '维护已经过去', True)), \
                patch.object(RewardResearch, 'ui_ensure', autospec=True), \
                patch.object(RewardResearch, '_run_batch', autospec=True) as fake_batch:
            runner.run()
        fake_batch.assert_called_once()

    def test_no_maintenance_today_runs_normally(self):
        """维护不是今天（或没有公告）：走正常批量流程。"""
        runner = self.make_runner()
        with patch('module.research.research.query_maintain_today', autospec=True,
                   return_value=(None, '下次维护不是今天', True)), \
                patch.object(RewardResearch, 'ui_ensure', autospec=True), \
                patch.object(RewardResearch, '_run_batch', autospec=True) as fake_batch:
            runner.run()
        fake_batch.assert_called_once()

    def test_switch_off_skips_query(self):
        """「维护当天科研」关闭：不查公告，直接走正常批量流程。"""
        runner = self.make_runner(MaintainFill=False)
        with patch('module.research.research.query_maintain_today', autospec=True) as fake_query, \
                patch.object(RewardResearch, 'ui_ensure', autospec=True), \
                patch.object(RewardResearch, '_run_batch', autospec=True) as fake_batch:
            runner.run()
        fake_query.assert_not_called()
        fake_batch.assert_called_once()

    def test_batch_mode_off_skips_query(self):
        """批量模式关闭：维护开关不生效，不查公告，走普通模式流程。"""
        runner = self.make_runner(BatchMode=False)
        with patch('module.research.research.query_maintain_today', autospec=True) as fake_query, \
                patch.object(RewardResearch, 'ui_ensure', autospec=True) as fake_ui, \
                patch.object(RewardResearch, 'queue_enter', autospec=True), \
                patch.object(RewardResearch, 'queue_receive', autospec=True), \
                patch.object(RewardResearch, 'get_research_ended', autospec=True,
                             return_value=current_time()), \
                patch.object(RewardResearch, 'queue_quit', autospec=True), \
                patch.object(RewardResearch, 'handle_pending_t_research', autospec=True,
                             return_value=False), \
                patch.object(RewardResearch, 'get_queue_slot', autospec=True, return_value=5):
            runner.run()
        fake_query.assert_not_called()
        fake_ui.assert_called_once()


class TestBatchScheduleClamp(unittest.TestCase):
    """_batch_schedule 按维护阶段钳制排程。"""

    @staticmethod
    def make_runner():
        return make_runner(make_config(Research={'BatchMode': True, 'MaintainFill': True}))

    @staticmethod
    def patch_snapshot(times):
        return patch.object(RewardResearch, '_batch_snapshot', autospec=True,
                            return_value=(times, None))

    def test_normal_stage_clamps_to_lead(self):
        """照常阶段：Σ 排程越过强制运行点时钳到维护前 60 分钟。"""
        runner = self.make_runner()
        maintain = current_time() + timedelta(hours=2)
        with self.patch_snapshot([timedelta(hours=3), None, None, None, None]):
            runner._batch_schedule(maintain=maintain)
        lead = (maintain - timedelta(minutes=RESEARCH_MAINTAIN_LEAD_MINUTES)).replace(microsecond=0)
        self.assertEqual(runner.config.Scheduler_NextRun, lead)

    def test_normal_stage_empty_queue_waits_for_lead(self):
        """照常阶段队列读空：仍排到强制运行点，保证维护前醒一次。"""
        runner = self.make_runner()
        maintain = current_time() + timedelta(hours=2)
        with self.patch_snapshot([None, None, None, None, None]):
            runner._batch_schedule(maintain=maintain)
        lead = (maintain - timedelta(minutes=RESEARCH_MAINTAIN_LEAD_MINUTES)).replace(microsecond=0)
        self.assertEqual(runner.config.Scheduler_NextRun, lead)

    def test_maintain_stage_clamps_to_maintenance(self):
        """强制运行阶段：Σ 排程早于维护开始时钳到维护开始。"""
        runner = self.make_runner()
        maintain = current_time() + timedelta(minutes=50)
        with self.patch_snapshot([timedelta(minutes=30), None, None, None, None]):
            runner._batch_schedule(maintain=maintain)
        self.assertEqual(runner.config.Scheduler_NextRun, maintain.replace(microsecond=0))

    def test_maintain_stage_empty_queue_retries(self):
        """强制运行阶段队列读空：15 分钟后重试。"""
        runner = self.make_runner()
        maintain = current_time() + timedelta(minutes=30)
        with self.patch_snapshot([None, None, None, None, None]):
            runner._batch_schedule(maintain=maintain)
        expected = current_time() + timedelta(minutes=RESEARCH_MAINTAIN_RETRY_MINUTES)
        self.assertAlmostEqual(runner.config.Scheduler_NextRun, expected,
                               delta=timedelta(seconds=5))

    def test_no_maintain_keeps_original_schedule(self):
        """不传 maintain 时行为与原版一致（Σ+3min，读空排服务器刷新）。"""
        runner = self.make_runner()
        with self.patch_snapshot([timedelta(hours=3), None, None, None, None]):
            runner._batch_schedule()
        expected = current_time() + timedelta(hours=3, minutes=3)
        self.assertAlmostEqual(runner.config.Scheduler_NextRun, expected,
                               delta=timedelta(seconds=5))


if __name__ == '__main__':
    unittest.main()
