"""两类低耗任务的共享选船回归，使用内存舰船，不读取实例或连接设备。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from module.campaign import ambush_1_1, fleet_selection, gems_farming
from module.campaign.ambush_1_1 import Ambush11
from module.campaign.fleet_selection import FleetSelectionMixin
from module.campaign.gems_farming import GemsFarming
from module.campaign.run import CampaignRun
from module.exception import ScriptError
from module.retire.scanner import Ship


TASKS = (GemsFarming, Ambush11)

# 各任务类的方法分别定义在自己的模块里（fleet_selection.py 的 mixin 已无人继承），
# patch 模板表必须落在实现所在模块，否则 patch 无效、真 cv2 匹配会被触发。
TASK_MODULE = {GemsFarming: gems_farming, Ambush11: ambush_1_1}


def patch_templates(task, kind, templates):
    """按任务实现模块 patch TEMPLATE_COMMON_CV / TEMPLATE_COMMON_DD。"""
    return patch.object(TASK_MODULE[task], f'TEMPLATE_COMMON_{kind}', templates)


def patch_ship_scanner(task, ships):
    """按任务实现模块 patch ShipScanner 为内存 Scanner。"""
    return patch.object(TASK_MODULE[task], 'ShipScanner',
                        side_effect=lambda **limits: Scanner(ships, **limits))


def runner(task, **settings):
    instance = object.__new__(task)
    values = dict(
        Fleet_FleetOrder='fleet1_all_fleet2_standby', Fleet_Fleet1=1, Fleet_Fleet2=2,
        GemsFarming_ChangeFlagship='ship', GemsFarming_ChangeVanguard='ship',
        GemsFarming_CommonCV='any', GemsFarming_CommonDD='any',
        GemsFarming_CommonCVFilter='bogue > langley', GemsFarming_CommonDDFilter='cassin > downes',
        COMMON_CV_FILTER='bogue > langley', COMMON_DD_FILTER='cassin > downes',
        GemsFarming_AllowHighFlagshipLevel=False, GemsFarming_AllowLowVanguardLevel=False,
        GemsFarming_UseEmotionFirst=False, GemsFarming_VanguardLevelMin=1,
        GemsFarming_VanguardLevelMax=125, SERVER='cn',
    )
    values.update(settings)
    instance.config = SimpleNamespace(**values)
    instance.campaign = SimpleNamespace(_map_battle=2, config=SimpleNamespace(
        Campaign_Mode='normal', LV32_TRIGGERED=False, GEMS_EMOTION_TRIGGERED=False))
    instance.device = SimpleNamespace(image=object(), screenshot=Mock(), click=Mock())
    instance.hard_mode = False
    instance.dock_favourite_set = Mock()
    instance.dock_sort_method_dsc_set = Mock()
    instance.dock_filter_set = Mock()
    instance.image_crop = Mock(side_effect=lambda button, **kwargs: button)
    instance.get_common_ship_filter = Mock(return_value=['bogue', 'langley'])
    return instance


class Scanner:
    """仅替换图像采集，范围判断使用生产 Ship.satisfy_limitation。"""

    def __init__(self, ships, **limits):
        self.ships = ships
        self.limits = limits

    def disable(self, name):
        self.limits.pop(name, None)

    def set_limitation(self, **limits):
        self.limits.update(limits)

    def scan(self, image, output=True):
        return [ship for ship in self.ships if ship.satisfy_limitation(self.limits)]


def template(*names):
    return SimpleNamespace(match=lambda image, similarity: image in names)


def ship(name, level=1, emotion=100, fleet=0):
    return Ship(button=name, level=level, emotion=emotion, fleet=fleet, status='free')


class CandidateSelectionTest(unittest.TestCase):
    def test_backline_orders_emotion_then_level_then_filter_priority(self):
        ships = [ship('unknown'), ship('bogue-low-emotion', emotion=99),
                 ship('langley', level=10), ship('bogue-high-level', level=11),
                 ship('bogue', level=10), ship('langley-low-level', level=9)]
        templates = {'BOGUE': template('bogue', 'bogue-low-emotion', 'bogue-high-level'),
                     'LANGLEY': template('langley', 'langley-low-level')}
        for task in TASKS:
            with self.subTest(task=task.__name__), patch_templates(task, 'CV', templates):
                found = runner(task).find_all_backline_candidates(Scanner(ships), ['bogue', 'langley'])
            self.assertEqual(['langley-low-level', 'bogue', 'langley', 'bogue-high-level', 'bogue-low-emotion'],
                             [item.button for item in found])

    def test_vanguard_preserves_priority_and_scan_order_without_level_sort(self):
        ships = [ship('downes', level=1), ship('cassin', level=70), ship('both', level=100),
                 ship('downes-happy', emotion=110)]
        templates = {'CASSIN': template('cassin', 'both'),
                     'DOWNES': template('downes', 'downes-happy', 'both')}
        for task in TASKS:
            with self.subTest(task=task.__name__), patch_templates(task, 'DD', templates):
                found = runner(task).find_all_vanguard_candidates(Scanner(ships), ['cassin', 'downes'])
            self.assertEqual(['downes-happy', 'cassin', 'both', 'downes'], [item.button for item in found])

    def test_custom_filter_prefers_first_ship_on_reversed_page(self):
        for task in TASKS:
            instance = runner(task, GemsFarming_CommonCV='custom')
            first, second = ship('first'), ship('second')
            # 首屏只有第二优先级，反向后出现第一优先级，必须优先选第一艘。
            instance.find_candidates = Mock(side_effect=[[], [second], [first]])
            self.assertEqual([first], instance.find_custom_candidates(Mock(), 'cv'))
            instance.dock_sort_method_dsc_set.assert_called_once_with(True)

    def test_custom_filter_restores_sort_before_using_cached_candidate(self):
        for task in TASKS:
            for ship_type, names in [('cv', ['bogue', 'langley']), ('dd', ['cassin', 'downes'])]:
                instance = runner(task, GemsFarming_CommonCV='custom', GemsFarming_CommonDD='custom')
                instance.get_common_ship_filter.return_value = names
                candidate = ship('second')
                instance.find_candidates = Mock(side_effect=[[], [candidate], [], [], [candidate]])
                # fallback 校验用模块级模板表做单点匹配；桩成永远命中的模板避免触发 cv2。
                with patch_templates(task, ship_type.upper(),
                                     {name.upper(): template(*names) for name in names}):
                    self.assertEqual([candidate], instance.find_custom_candidates(Mock(), ship_type))
                descending = ship_type == 'dd'
                if task is Ambush11:
                    # ambush 实现：倒序后命中次级候补时经其 919 行恢复排序
                    # （带 wait_loading=False），与 gems 的 878 行细节不同。
                    self.assertEqual([call(not descending),
                                      call(descending, wait_loading=False)],
                                     instance.dock_sort_method_dsc_set.call_args_list)
                else:
                    self.assertEqual([call(not descending), call(descending)],
                                     instance.dock_sort_method_dsc_set.call_args_list)

    def test_template_list_retains_first_matching_template_preference(self):
        scanner = Scanner([ship('second'), ship('first')])
        for task in TASKS:
            found = runner(task).find_candidates([template('first'), template('second')], scanner)
            self.assertEqual(['first'], [item.button for item in found])
            self.assertEqual([], runner(task).find_custom_candidates(scanner, 'invalid'))
            with self.assertRaises(ScriptError):
                task.get_templates('invalid')


class LevelPolicyTest(unittest.TestCase):
    def test_cv_low_level_boundaries_emotion_and_current_fleet(self):
        ships = [ship('level-zero', level=0, fleet=1), ship('min', level=1, fleet=1),
                 ship('max', level=31, fleet=1), ship('too-high', level=32, fleet=1),
                 ship('tired', level=10, emotion=7, fleet=1), ship('other-fleet', fleet=2)]
        for task in TASKS:
            instance = runner(task)
            with patch_ship_scanner(task, ships):
                selected = instance.get_common_rarity_cv()
            self.assertEqual(['min', 'max'], [item.button for item in selected])

    def test_cv_high_level_defaults_differ_by_server_and_include_idle_ships(self):
        for task in TASKS:
            for server, level in [('cn', 100), ('en', 70), ('jp', 70)]:
                with self.subTest(task=task.__name__, server=server):
                    instance = runner(task, SERVER=server, GemsFarming_AllowHighFlagshipLevel=True)
                    instance.find_custom_candidates = Mock(return_value=[])
                    ships = [ship('below', level=level-1), ship('match', level=level), ship('above', level=level+1)]
                    with patch_ship_scanner(task, ships):
                        selected = instance.get_common_rarity_cv()
                    self.assertEqual(['match'], [item.button for item in selected])

    def test_cv_empty_custom_filter_falls_back_to_eligible_idle_ship(self):
        for task in TASKS:
            instance = runner(task, GemsFarming_CommonCV='custom')
            instance.find_custom_candidates = Mock(return_value=[])
            ships = [ship('idle', emotion=0), ship('other-fleet', emotion=0, fleet=2)]
            with patch_ship_scanner(task, ships):
                selected = instance.get_common_rarity_cv(emotion=0)
            self.assertEqual(['idle'], [item.button for item in selected])

    def test_emotion_first_cv_returns_one_candidate_and_falls_back_when_unmatched(self):
        for task in TASKS:
            instance = runner(task, GemsFarming_UseEmotionFirst=True)
            winner, current = ship('winner'), ship('current', fleet=1)
            instance.find_all_backline_candidates = Mock(return_value=[winner, current])
            with patch_ship_scanner(task, [current]):
                self.assertEqual([winner], instance.get_common_rarity_cv())
                instance.find_all_backline_candidates.side_effect = [[], []]
                self.assertEqual([current], instance.get_common_rarity_cv())
            self.assertIn(call(True), instance.dock_sort_method_dsc_set.call_args_list)
            self.assertEqual(call(False), instance.dock_sort_method_dsc_set.call_args_list[-1])

    def test_default_dd_policies_and_explicit_ranges_remain_task_specific(self):
        cases = [
            (GemsFarming, {}, False, (100, 100)),
            (GemsFarming, {'SERVER': 'en'}, False, (70, 70)),
            (GemsFarming, {'GemsFarming_AllowLowVanguardLevel': True}, False, (30, 100)),
            (GemsFarming, {'GemsFarming_AllowLowVanguardLevel': True}, True, (70, 100)),
            (GemsFarming, {'GemsFarming_CommonDD': 'DDG'}, False, (125, 125)),
            (Ambush11, {}, False, (1, 28)),
            (Ambush11, {'SERVER': 'en'}, False, (1, 28)),
            (Ambush11, {'GemsFarming_CommonDD': 'DDG'}, False, (1, 28)),
            (Ambush11, {'GemsFarming_AllowLowVanguardLevel': True}, True, (1, 28)),
        ]
        for task in TASKS:
            cases.append((task, {'GemsFarming_VanguardLevelMin': 15, 'GemsFarming_VanguardLevelMax': 40}, True, (15, 40)))
        for task, settings, hard, bounds in cases:
            with self.subTest(task=task.__name__, settings=settings, hard=hard):
                instance = runner(task, **settings)
                instance.hard_mode = hard
                ships = [ship(str(level), level=level) for level in sorted(set([bounds[0]-1, *bounds, bounds[1]+1]))]
                module = gems_farming if task is GemsFarming else ambush_1_1
                with patch.object(module, 'ShipScanner', side_effect=lambda **limits: Scanner(ships, **limits)) as factory:
                    selected = instance.get_common_rarity_dd()
                self.assertEqual(bounds, factory.call_args.kwargs['level'])
                self.assertEqual(sorted(set(bounds)), [item.level for item in selected])

    def test_invalid_dd_setting_keeps_distinct_task_fallback(self):
        with self.assertRaises(ScriptError):
            runner(GemsFarming, GemsFarming_CommonDD='invalid').get_common_rarity_dd()
        instance = runner(Ambush11, GemsFarming_CommonDD='invalid')
        instance.find_candidates = Mock(return_value=[])
        # 伏击保留原先的阵营兜底，最后由共享模板校验报告未知设置。
        with patch.object(ambush_1_1, 'ShipScanner', return_value=Scanner([])):
            with self.assertRaises(ScriptError):
                instance.get_common_rarity_dd()
        instance.dock_filter_set.assert_called_once_with(
            index='dd', rarity='common', faction=['eagle', 'iron'], extra='can_limit_break')


class FleetInteractionTest(unittest.TestCase):
    def test_mro_preserves_campaign_super_and_short_circuits_task_limits(self):
        for task in TASKS:
            instance = runner(task)
            # 两任务各自实现 triggered_stop_condition（fleet_selection.py 的
            # FleetSelectionMixin 已无人继承），并经 super() 调用 CampaignRun。
            self.assertIn('triggered_stop_condition', vars(task))
            with patch.object(CampaignRun, 'triggered_stop_condition', autospec=True, return_value=False) as parent:
                self.assertFalse(instance.triggered_stop_condition(oil_check=False))
                parent.assert_called_once_with(instance, oil_check=False)
                parent.reset_mock()
                instance.campaign.config.LV32_TRIGGERED = True
                self.assertTrue(instance.triggered_stop_condition())
                self.assertTrue(instance._trigger_lv32)
                parent.assert_not_called()
            instance = runner(task, GemsFarming_AllowHighFlagshipLevel=True)
            instance.campaign.config.LV32_TRIGGERED = True
            with patch.object(CampaignRun, 'triggered_stop_condition', return_value=False) as parent:
                self.assertFalse(instance.triggered_stop_condition())
                parent.assert_called_once_with(oil_check=True)
                instance.campaign.config.GEMS_EMOTION_TRIGGERED = True
                self.assertTrue(instance.triggered_stop_condition())
                self.assertTrue(instance._trigger_emotion)

    def test_ship_change_preserves_equipment_order_and_execute_result(self):
        for task in TASKS:
            for position in ('flagship', 'vanguard'):
                with self.subTest(task=task.__name__, position=position):
                    instance = runner(task, GemsFarming_ChangeFlagship='ship_equip', GemsFarming_ChangeVanguard='ship_equip')
                    trace = Mock()
                    for method in ('_fleet_detail_enter', '_ship_detail_enter', 'clear_all_equip', '_fleet_back', 'apply_equip_code'):
                        setattr(instance, method, getattr(trace, method))
                    execute = f'{position}_change_execute'
                    setattr(instance, execute, getattr(trace, execute))
                    getattr(trace, execute).return_value = False
                    instance.fleet_detail_enter_flagship = 'flagship'
                    instance.fleet_detail_enter = 'vanguard'
                    self.assertFalse(getattr(instance, f'{position}_change')())
                    self.assertEqual([call._fleet_detail_enter(1), call._ship_detail_enter(position),
                                      call.clear_all_equip(), call._fleet_back(), getattr(call, execute)(),
                                      call._ship_detail_enter(position), call.apply_equip_code(), call._fleet_back()],
                                     trace.mock_calls)

    def test_hard_mode_uses_correct_fleet_buttons(self):
        from module.equipment import assets as equip_assets
        for task in TASKS:
            for order, suffix in [('fleet1_all_fleet2_standby', '1'), ('fleet1_standby_fleet2_all', '2')]:
                instance = runner(task, Fleet_FleetOrder=order)
                instance.campaign.config.Campaign_Mode = 'hard'
                instance.hard_mode_override()
                self.assertTrue(instance.hard_mode)
                self.assertIs(instance.fleet_detail_enter_flagship,
                              getattr(equip_assets, f'FLEET_DETAIL_ENTER_FLAGSHIP_HARD_{suffix}'))
                # 困难模式的进入函数切到各任务自己的 *_hard 实现
                self.assertIs(instance._fleet_detail_enter.__func__,
                              task._fleet_detail_enter_hard)

    def test_dock_enter_handles_tip_and_ready_state(self):
        for task in TASKS:
            instance = runner(task)
            instance.page_fleet_check_button = object()
            instance.loop = Mock(return_value=iter(range(1)))
            instance.appear = Mock(side_effect=[False, False])
            instance.handle_game_tips = Mock(return_value=True)
            self.assertFalse(instance.dock_enter('slot'))
            instance.device.click.assert_not_called()
            instance.loop = Mock(return_value=iter(range(3)))
            instance.appear = Mock(side_effect=[False, True, True])
            self.assertTrue(instance.dock_enter('slot'))
            instance.device.click.assert_called_once_with('slot')

    def test_hard_fleet_entry_reenters_stage_after_retirement(self):
        for task in TASKS:
            instance = runner(task)
            instance.stage = 'campaign_2_1'
            instance.campaign.ENTRANCE = object()
            instance.campaign.ensure_campaign_ui = Mock()
            instance.campaign.handle_map_mode_switch = Mock(return_value=True)
            instance.ui_click = Mock()
            instance.appear = Mock(side_effect=[False, True])
            instance.appear_then_click = Mock(side_effect=[False, False, False, False, True, False, False, False])
            instance.handle_retirement = Mock(side_effect=[True, False, False])
            instance._fleet_detail_enter_hard(1)
            # 两份实现都只按 stage 重新导航，不传 mode
            instance.campaign.ensure_campaign_ui.assert_called_once_with('campaign_2_1')
            self.assertEqual(3, instance.device.screenshot.call_count)
            self.assertEqual(2, instance.appear_then_click.call_args_list.count(call(instance.campaign.ENTRANCE, interval=2)))


if __name__ == '__main__':
    unittest.main()
