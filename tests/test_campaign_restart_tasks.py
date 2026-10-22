"""每日活动进度与本地紧急委托外层流程的续接回归。"""

import unittest
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from module.campaign.campaign_base import CampaignBase
from module.campaign.gems_farming import GemsFarming
from module.campaign.recovery import CampaignRecovery, campaign_recovery_scope
from module.campaign.run import CampaignRun
from module.config.config import TaskEnd
from module.event.campaign_abcd import CampaignABCD
from module.exception import CampaignEnd, GameStuckError, ScriptEnd
from tests.test_campaign_restart_recovery import make_runner


class EventRecoveryTests(unittest.TestCase):
    def test_daily_progress_resumes_interrupted_stage_before_remaining_stages(self):
        for across_reset in (False, True):
            with self.subTest(across_reset=across_reset):
                runner = object.__new__(CampaignABCD)
                runner.__dict__.update(make_runner().__dict__)
                config = runner.config
                config.task.command = 'EventA'
                config.data['EventA'] = config.data.pop('Main')
                config.Campaign_Event = 'event_test'
                config.EventDaily_StageFilter = 'a1 > a2 > a3'
                config.EventDaily_LastStage = 0
                config.Scheduler_NextRun = datetime(2026, 10, 22, 12)
                config.Scheduler_ServerUpdate = '00:00'
                config.multi_set = nullcontext
                delays = []
                config.task_delay = lambda **kwargs: delays.append(kwargs)
                trace = []
                failed = False
                def execute(obj, name, folder, mode, total, resume):
                    nonlocal failed
                    trace.append((name, resume, total))
                    if not resume:
                        obj.name, obj.folder, obj.stage = name, folder, name
                        obj.run_count = 0
                        obj.campaign._recovery_map_generation += 1
                        obj.campaign._recovery_map_active = True
                    if name == 'a2' and not failed:
                        failed = True
                        raise GameStuckError()
                    obj.run_count = 1
                owner = SimpleNamespace()
                reset = datetime(2026, 10, 22)
                with patch('module.event.campaign_abcd.os.listdir', return_value=['a1.py', 'a2.py', 'a3.py']), \
                        patch('module.event.campaign_abcd.get_server_last_update', side_effect=lambda _: reset), \
                        patch.object(CampaignABCD, 'handle_stage_name', autospec=True,
                                     side_effect=lambda obj, name, folder: (str(name).lower(), folder)), \
                        patch.object(CampaignRun, '_run_campaign_loop', autospec=True, side_effect=execute):
                    with campaign_recovery_scope(owner), self.assertRaises(GameStuckError):
                        runner.run()
                    self.assertEqual(config.EventDaily_LastStage, 'a1')
                    recovery = owner._campaign_recovery
                    self.assertEqual(recovery.run_kwargs['name'], 'a2')
                    if across_reset:
                        reset = datetime(2026, 10, 23)
                    with patch.object(CampaignRecovery, 'prepare', autospec=True, return_value=True):
                        recovery.resume(config, runner.device)
                self.assertEqual(trace, [('a1', False, 1), ('a2', False, 1),
                                         ('a2', True, 1), ('a3', False, 1)])
                self.assertEqual(config.EventDaily_LastStage, 'a3')
                self.assertEqual(delays[-1], {'server_update': True})

    def test_base_resume_keeps_map_and_does_not_repeat_entry(self):
        campaign = make_runner().campaign
        campaign._resume_after_restart = True
        campaign._restart_resume_guard = True
        campaign.battle_count = 4
        campaign.ENTRANCE = '测试关卡'
        with patch.object(CampaignBase, 'map_get_info', autospec=True) as info, \
                patch.object(CampaignBase, 'enter_map', autospec=True) as enter, \
                patch.object(CampaignBase, 'auto_search_execute_a_battle', autospec=True,
                             side_effect=CampaignEnd):
            self.assertTrue(campaign.run())
        self.assertEqual(campaign.battle_count, 4)
        self.assertFalse(campaign._recovery_map_active)
        self.assertFalse(campaign._restart_resume_guard)
        info.assert_not_called()
        enter.assert_not_called()

    def test_base_resume_stop_withdraws_without_executing_battle(self):
        campaign = make_runner().campaign
        campaign._resume_after_restart = campaign._restart_stop_requested = True
        campaign.ENTRANCE = '测试关卡'
        with patch.object(CampaignBase, 'withdraw_after_restart', autospec=True, side_effect=CampaignEnd) as withdraw, \
                patch.object(CampaignBase, 'auto_search_execute_a_battle', autospec=True) as battle:
            with self.assertRaises(ScriptEnd):
                campaign.run()
        withdraw.assert_called_once_with(campaign)
        battle.assert_not_called()


class FarmingRecoveryTests(unittest.TestCase):
    def test_resume_skips_initial_memory_check_but_keeps_final_save(self):
        runner = object.__new__(GemsFarming)
        runner._resume_campaign_run = True
        with patch.object(GemsFarming, '_init_fleet_memory', autospec=True) as initial, \
                patch.object(GemsFarming, '_save_fleet_memory', autospec=True) as save, \
                patch.object(GemsFarming, '_run', autospec=True) as run:
            runner.run('2-4')
        initial.assert_not_called()
        save.assert_called_once_with(runner)
        run.assert_called_once_with(runner, name='2-4', folder='campaign_main', mode='normal',
                                    total=0, initial_check=False)

    def test_local_outer_loop_does_not_collide_with_campaign_inner_loop(self):
        runner = object.__new__(GemsFarming)
        runner.__dict__.update(make_runner().__dict__)
        runner._resume_campaign_run = True
        runner._trigger_lv32 = True
        runner.config.GemsFarming_AllowHighFlagshipLevel = False
        with patch.object(GemsFarming, 'change_flagship', new=False), \
                patch.object(CampaignRun, 'run', autospec=True, side_effect=TaskEnd) as run:
            with self.assertRaises(TaskEnd):
                runner._run('2-4')
        self.assertTrue(runner._trigger_lv32)
        run.assert_called_once_with(runner, name='2-4', folder='campaign_main', mode='normal', total=0)


if __name__ == '__main__':
    unittest.main()
