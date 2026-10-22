"""战役续接的凭据、地图保护与任务交接测试；不连接真实设备。"""

import copy
import json
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

from alas import AzurLaneAutoScript
from module.base.timer import Timer
from module.campaign.campaign_base import CampaignBase
from module.campaign.recovery import (CampaignRecovery, campaign_recovery_scope,
                                      capture_campaign_recovery)
from module.campaign.run import CampaignRun
from module.config.config import TaskEnd
from module.device.device import Device
from module.exception import CampaignEnd, GameNotRunningError, GameStuckError, ScriptError
from module.handler.assets import AUTO_SEARCH_MAP_OPTION_OFF, AUTO_SEARCH_MAP_OPTION_ON
from module.handler.auto_search import AutoSearchHandler
from module.handler.login import LoginHandler
from module.ui.page import Page


def make_runner():
    """只装配业务状态，设备方法用真实类签名约束。"""
    config = SimpleNamespace(
        config_name='recovery-test', task=SimpleNamespace(command='Main'),
        Campaign_ContinueAfterRestart=True, Campaign_UseClearMode=True,
        Campaign_UseAutoSearch=True, Campaign_Mode='normal', MAP_IS_ONE_TIME_STAGE=False,
        StopCondition_RunCount=0, modified={}, update=lambda: None,
        override=lambda **kwargs: None, task_switched=lambda: False,
        task_stop=lambda *args: (_ for _ in ()).throw(TaskEnd()),
        data={'Main': {'Campaign': {'Name': '12-4', 'ContinueAfterRestart': True},
                       'Scheduler': {'Enable': True}, 'Emotion': {'Mode': 'calculate'}},
              'Alas': {'Emulator': {'Serial': 'test-device'}},
              'General': {'PublicEmotion': {'Enable': False}}})
    campaign = object.__new__(CampaignBase)
    campaign.config = config
    campaign.map_is_clear_mode = campaign.map_is_auto_search = True
    campaign._recovery_map_active = True
    campaign._recovery_map_generation = 1
    campaign._auto_search_in_stage_timer = Timer(3, count=6)
    runner = object.__new__(CampaignRun)
    runner.config = config
    runner.campaign = campaign
    runner.device = create_autospec(Device, instance=True)
    runner.device.image = None
    runner.device.has_cached_image = True
    campaign.device = runner.device
    runner.name, runner.folder, runner.stage = 'campaign_12_4', 'campaign_main', '12-4'
    runner.run_count, runner.run_limit = 2, 0
    return runner


class CredentialTests(unittest.TestCase):
    def test_requires_opt_in_clear_mode_and_live_supported_map(self):
        for owner, name in (('config', 'Campaign_ContinueAfterRestart'),
                            ('config', 'Campaign_UseClearMode'), ('config', 'Campaign_UseAutoSearch'),
                            ('campaign', 'map_is_clear_mode'), ('campaign', 'map_is_auto_search'),
                            ('campaign', '_recovery_map_active')):
            runner = make_runner()
            target = runner.config if owner == 'config' else runner.campaign
            setattr(target, name, False)
            with self.subTest(name=name):
                self.assertIsNone(CampaignRecovery.capture(runner))
        self.assertIsNotNone(CampaignRecovery.capture(make_runner()))

    def test_rejects_special_maps_and_overridden_battle_entry(self):
        for folder in ('campaign_sos', 'war_archives_20240101', 'raid_20240101'):
            runner = make_runner()
            runner.folder = folder
            self.assertIsNone(CampaignRecovery.capture(runner))
        for key in ('MAP_IS_ONE_TIME_STAGE',):
            runner = make_runner()
            setattr(runner.config, key, True)
            self.assertIsNone(CampaignRecovery.capture(runner))
        class CustomCampaign(CampaignBase):
            def run(self):
                return True
        runner = make_runner()
        runner.campaign.__class__ = CustomCampaign
        self.assertIsNone(CampaignRecovery.capture(runner))

    def test_settings_are_copied_and_disabled_tasks_do_not_match(self):
        runner = make_runner()
        recovery = CampaignRecovery.capture(runner)
        self.assertFalse(recovery.ready)
        for group in ('Campaign', 'Emotion'):
            config = copy.deepcopy(runner.config)
            config.data['Main'][group]['Changed'] = True
            self.assertFalse(recovery.matches(config))
        config = copy.deepcopy(runner.config)
        config.data['Alas']['Emulator']['Serial'] = 'changed'
        self.assertFalse(recovery.matches(config))
        config = copy.deepcopy(runner.config)
        config.data['EmulatorSettings'] = {'Emulator': {'Serial': 'changed'}}
        self.assertFalse(recovery.matches(config))
        runner.config.data['Main']['Scheduler']['Enable'] = False
        self.assertFalse(recovery.matches(runner.config))
        self.assertFalse(recovery.matches(runner.config, task='Main'))

    def test_expiry_is_not_extended_by_repeated_capture(self):
        runner = make_runner()
        with patch('module.campaign.recovery.monotonic', return_value=100):
            recovery = CampaignRecovery.capture(runner)
        with patch('module.campaign.recovery.monotonic', return_value=699):
            second = CampaignRecovery.capture(runner, recovery)
            self.assertEqual(second.created, 100)
            self.assertTrue(second.matches(runner.config))
        with patch('module.campaign.recovery.monotonic', return_value=701):
            self.assertFalse(second.matches(runner.config))

    def test_attempt_limits_are_per_map_and_include_restart_failures(self):
        runner = make_runner()
        for field in ('attempts', 'restart_attempts'):
            recovery = CampaignRecovery.capture(runner)
            setattr(recovery, field, 3)
            self.assertIsNone(CampaignRecovery.capture(runner, recovery))
            runner.campaign._recovery_map_generation += 1
            self.assertEqual(CampaignRecovery.capture(runner, recovery).attempts, 0)

    def test_context_is_nested_and_cleared_after_exception(self):
        runner = make_runner()
        first, second = SimpleNamespace(), SimpleNamespace()
        with campaign_recovery_scope(first):
            with self.assertRaises(ValueError), campaign_recovery_scope(second):
                capture_campaign_recovery(runner, {'name': '12-4'})
                raise ValueError()
            capture_campaign_recovery(runner, {'name': '12-4'})
        self.assertIsNotNone(first._campaign_recovery)
        self.assertIsNotNone(second._campaign_recovery)
        first._campaign_recovery = None
        capture_campaign_recovery(runner, {})
        self.assertIsNone(first._campaign_recovery)

    def test_public_runner_captures_only_restart_errors(self):
        for error, captured in ((GameStuckError(), True), (ScriptError(), False)):
            runner, owner = make_runner(), SimpleNamespace()
            with patch.object(CampaignRun, '_run_campaign_loop', autospec=True, side_effect=error):
                with campaign_recovery_scope(owner), self.assertRaises(type(error)):
                    runner.run('12-4', total=3)
            self.assertEqual(hasattr(owner, '_campaign_recovery'), captured)
            if captured:
                self.assertEqual(owner._campaign_recovery.run_kwargs,
                                 dict(name='12-4', folder='campaign_main', mode='normal', total=3))

    def test_prepare_rebinds_devices_without_replacing_emotion(self):
        runner = make_runner()
        runner.campaign.emotion = object()
        emotion = runner.campaign.emotion
        recovery = CampaignRecovery.capture(runner)
        device = create_autospec(Device, instance=True)
        with patch.object(CampaignRecovery, 'find_state', autospec=True, return_value='map'):
            self.assertTrue(recovery.prepare(runner.config, device))
        self.assertIs(runner.device, device)
        self.assertIs(runner.campaign.device, device)
        self.assertIs(runner.campaign.emotion, emotion)
        self.assertFalse(recovery.ready)
        self.assertEqual(recovery.attempts, 1)

    def test_settings_changed_during_reload_abort_instead_of_running_old_map(self):
        runner = make_runner()
        recovery = CampaignRecovery.capture(runner)
        def update():
            runner.config.data['Main']['Campaign']['Name'] = '13-4'
        runner.config.update = update
        with patch.object(CampaignRun, 'run', autospec=True) as run:
            with self.assertRaises(TaskEnd):
                recovery.resume(runner.config, runner.device)
        run.assert_not_called()
        self.assertFalse(runner.campaign._recovery_map_active)

    def test_navigation_returns_map_without_withdraw_and_clears_graph(self):
        runner = make_runner()
        recovery = CampaignRecovery.capture(runner)
        with patch.object(CampaignBase, 'loop', autospec=True, return_value=iter([None])), \
                patch.object(CampaignBase, 'auto_search_recovery_state', autospec=True, return_value='map'), \
                patch.object(CampaignBase, 'withdraw', autospec=True) as withdraw, \
                patch.object(Page, 'clear_connection', wraps=Page.clear_connection) as clear:
            self.assertEqual(recovery.find_state(), 'map')
        withdraw.assert_not_called()
        self.assertGreaterEqual(clear.call_count, 1)
        self.assertTrue(all(page.parent is None for page in Page.iter_pages()))


class MapProtectionTests(unittest.TestCase):
    def setUp(self):
        self.campaign = make_runner().campaign
        self.clock = 100.0
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.clock))
        self.loading = self.enterContext(patch.object(CampaignBase, 'is_combat_loading', autospec=True, return_value=False))
        self.executing = self.enterContext(patch.object(CampaignBase, 'is_combat_executing', autospec=True, return_value=False))
        self.color = self.enterContext(patch.object(CampaignBase, 'match_template_color', autospec=True,
                                                   side_effect=lambda obj, button, **kw: button is AUTO_SEARCH_MAP_OPTION_OFF))
        self.click = self.enterContext(patch.object(AutoSearchHandler, 'handle_auto_search_map_option',
                                                   autospec=True, return_value=True))
        self.campaign.prepare_auto_search_recovery('map')

    def test_two_seconds_are_observed_without_sleep(self):
        self.assertFalse(self.campaign.handle_auto_search_map_option())
        self.clock += 1.99
        self.assertFalse(self.campaign.handle_auto_search_map_option())
        self.click.assert_not_called()
        self.clock += 0.02
        self.assertTrue(self.campaign.handle_auto_search_map_option())
        self.click.assert_called_once_with(self.campaign)

    def test_loading_executing_on_and_ambiguous_states_never_click(self):
        self.clock += 3
        for mode in ('loading', 'executing', 'on', 'unknown'):
            with self.subTest(mode=mode):
                self.loading.return_value = mode == 'loading'
                self.executing.return_value = mode == 'executing'
                self.color.side_effect = lambda obj, button, **kw: mode == 'on' and button is AUTO_SEARCH_MAP_OPTION_ON
                self.assertFalse(self.campaign.handle_auto_search_map_option())
        self.click.assert_not_called()

    def test_timeout_is_bounded_and_normal_maps_keep_original_handler(self):
        self.clock += 31
        for _ in range(60):
            self.campaign._restart_resume_timer.add_count()
        with self.assertRaises(GameStuckError):
            self.campaign.handle_auto_search_map_option()
        self.campaign._restart_resume_guard = False
        self.assertTrue(self.campaign.handle_auto_search_map_option())

    def test_non_map_states_are_not_recovery_entries(self):
        self.assertFalse(self.campaign.prepare_auto_search_recovery('combat'))
        self.loading.return_value = True
        self.assertIsNone(self.campaign.auto_search_recovery_state())
        self.color.assert_not_called()

    def test_stopping_map_never_enables_auto_search(self):
        self.color.side_effect = lambda obj, button, **kw: button is AUTO_SEARCH_MAP_OPTION_OFF
        with patch.object(CampaignBase, 'loop', autospec=True, return_value=iter([None, None])), \
                patch.object(CampaignBase, 'is_in_stage', autospec=True, side_effect=[False, True]), \
                patch.object(CampaignBase, 'handle_popup_confirm', autospec=True, return_value=False), \
                patch.object(CampaignBase, 'appear_then_click', autospec=True, return_value=True) as click:
            with self.assertRaises(CampaignEnd):
                self.campaign.withdraw_after_restart()
        self.assertEqual(click.call_count, 1)
        self.assertNotIn(click.call_args.args[1], (AUTO_SEARCH_MAP_OPTION_ON, AUTO_SEARCH_MAP_OPTION_OFF))


class TaskHandoffTests(unittest.TestCase):
    def script(self, runner):
        script = object.__new__(AzurLaneAutoScript)
        script.config = runner.config
        script.device = runner.device
        script.recovery = SimpleNamespace(pending=None, handle_pending=lambda: False, task_scope=nullcontext)
        script._channel_float_done = False
        return script

    def test_resume_skips_entry_and_preserves_completed_run_count(self):
        runner = make_runner()
        runner._resume_map = runner._resume_campaign_run = True
        with patch.object(CampaignRun, 'load_campaign', autospec=True) as load, \
                patch.object(CampaignRun, 'triggered_stop_condition', autospec=True, return_value=False), \
                patch.object(CampaignRun, 'after_campaign_run', autospec=True) as after, \
                patch.object(CampaignBase, 'event_time_limit_triggered', autospec=True, return_value=False), \
                patch.object(CampaignBase, 'run', autospec=True) as run, \
                patch.object(CampaignBase, 'withdraw', autospec=True) as withdraw, \
                patch.object(CampaignBase, 'ensure_campaign_ui', autospec=True) as ensure, \
                patch.object(CampaignBase, 'ensure_auto_search_exit', autospec=True):
            runner.run('12-4', total=3)
        self.assertEqual(runner.run_count, 3)
        self.assertTrue(runner.campaign._resume_after_restart)
        run.assert_called_once_with(runner.campaign)
        after.assert_called_once_with(runner)
        load.assert_not_called()
        withdraw.assert_not_called()
        ensure.assert_not_called()

    def test_scheduler_requires_completed_restart_and_matching_task(self):
        for ready, command, changed, should_resume in (
                (False, 'main', False, False), (True, 'main', False, True),
                (True, 'main', True, False), (True, 'main2', False, False)):
            runner = make_runner()
            script = self.script(runner)
            recovery = CampaignRecovery.capture(runner)
            recovery.ready = ready
            script._campaign_recovery = recovery
            if changed:
                runner.config.data['Main']['Campaign']['Name'] = '13-4'
            with patch.object(AzurLaneAutoScript, command, autospec=True) as normal, \
                    patch.object(AzurLaneAutoScript, 'handle_channel_float', autospec=True) as channel, \
                    patch.object(CampaignRecovery, 'resume', autospec=True) as resume:
                self.assertTrue(script.run(command))
            self.assertEqual(resume.call_count, int(should_resume))
            self.assertEqual(normal.call_count, int(not should_resume))
            self.assertEqual(channel.call_count, int(not should_resume))
            self.assertIsNone(script._campaign_recovery)

    def test_restart_marks_ready_only_after_success_and_rebinds_device(self):
        runner = make_runner()
        script = self.script(runner)
        recovery = CampaignRecovery.capture(runner)
        script._campaign_recovery = recovery
        script.device = create_autospec(Device, instance=True)
        with patch.object(AzurLaneAutoScript, 'delay_due_restart', autospec=True, return_value=False), \
                patch.object(AzurLaneAutoScript, 'delay_next_restart', autospec=True), \
                patch.object(LoginHandler, 'app_restart', autospec=True, side_effect=GameNotRunningError):
            with self.assertRaises(GameNotRunningError):
                script.restart()
        self.assertFalse(recovery.ready)
        self.assertEqual(recovery.restart_attempts, 1)
        with patch.object(AzurLaneAutoScript, 'delay_due_restart', autospec=True, return_value=False), \
                patch.object(AzurLaneAutoScript, 'delay_next_restart', autospec=True), \
                patch.object(LoginHandler, 'app_restart', autospec=True):
            script.restart()
        self.assertTrue(recovery.ready)
        self.assertIs(runner.campaign.device, script.device)

    def test_config_defaults_and_special_tasks_are_hidden(self):
        data = json.loads(Path('module/config/argument/args.json').read_text(encoding='utf-8'))
        self.assertFalse(data['Main']['Campaign']['ContinueAfterRestart']['value'])
        for task in ('Ambush11', 'Raid', 'RaidScuttle', 'RaidDaily', 'Coalition',
                     'CoalitionScuttle', 'CoalitionSp', 'WarArchives', 'OperationHandover'):
            if 'ContinueAfterRestart' in data[task].get('Campaign', {}):
                entry = data[task]['Campaign']['ContinueAfterRestart']
                self.assertFalse(entry['value'])
                self.assertEqual(entry['display'], 'hide')


if __name__ == '__main__':
    unittest.main()
