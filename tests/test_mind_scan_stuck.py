"""真实故障徽标及真实排序策略、位移合并、计时器下的有限退出。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.exception import GameStuckError, MindCalculatorScanError
from module.retire.mind_scan import MindCalculatorScan
from module.retire.scanner import FleetScanner
from module.runtime.mind_calculator import calculate
from module.runtime.mind_recognition import (ScanMerger, ScanPolicy, _has_name_ellipsis, _match_name, _name_image,
                                           fleet_status, grid_rows, recognize_cards)
from tests.test_mind_scan import FIXTURES, cards_for_levels


class BadgeRegressionTests(unittest.TestCase):
    def test_real_zuifeng_badge_and_all_first_page_fleets(self):
        image = Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB')
        pixels = np.array(image)
        from module.retire.dock import CARD_GRIDS
        rows = grid_rows(pixels)
        x = round(float(CARD_GRIDS.origin[0] + 4 * CARD_GRIDS.delta[0]))
        region = pixels[rows[1] + 117:rows[1] + 162, x:x + 35]
        reader = FleetScanner()
        # 原绿通道模板确实漏掉原帧的 1；补读不降低模板相似度。
        self.assertEqual(reader._match(reader.pre_process(region)), 0)
        self.assertEqual(fleet_status(region, reader), 1)
        cards = recognize_cards(image)
        self.assertEqual([card.fleet for card in cards],
                         [2, 2, 2, 1, 1, 1, 2, 2, 2, 1, 1, 1, 1, 1, 1, 2, 2, 2, 5, 5, 3])
        self.assertEqual(cards[11].ship['name'], '瑞凤')
        self.assertEqual(cards[11].ship['level'], 125)
        policy = ScanPolicy(95, 125, True)
        self.assertEqual(policy.sort_issue([[card, card.y] for card in cards]), '')
        cards[11].fleet = 0
        self.assertIn('普通舰之后', policy.sort_issue([[card, card.y] for card in cards]))

    def test_white_fallback_does_not_turn_blue_artwork_into_fleets(self):
        pixels = np.array(Image.open(FIXTURES / 'mind_fleet_blue.png').convert('RGB'))
        reader = FleetScanner()
        for y in range(0, len(pixels), 45):
            self.assertEqual(fleet_status(pixels[y:y + 45], reader), 0)

    def test_real_six_badge_uses_strongest_template_in_both_sampling_phases(self):
        pixels = np.array(Image.open(FIXTURES / 'mind_fleet_six.png').convert('RGB'))
        reader = FleetScanner()
        regions = [pixels[y:y + 45] for y in (0, 45)]
        self.assertEqual([reader._match(reader.pre_process(region)) for region in regions], [6, 1])
        self.assertEqual([fleet_status(region, reader) for region in regions], [6, 6])

    def test_ambiguous_number_and_conflicting_channels_remain_unknown(self):
        from module.runtime.mind_recognition import _fleet_number
        reader = FleetScanner()
        with patch('module.runtime.mind_recognition.cv2.matchTemplate', return_value=np.array([[.85]], np.float32)):
            self.assertEqual(_fleet_number(np.zeros((45, 35, 3), np.uint8), reader), -1)
        for votes in ((1, 6), (-1, 6), (6, -1)):
            with patch('module.runtime.mind_recognition._fleet_number', side_effect=votes):
                self.assertEqual(fleet_status(np.zeros((45, 35, 3), np.uint8), reader), -1)

    def test_cumulative_uncertainty_only_recovers_with_new_slot_evidence(self):
        image = Image.new('RGB', (1280, 720), (80, 80, 80))
        cards = cards_for_levels([94] * 7 + [93] * 7 + [92] * 7)
        cards[0].fleet = -1
        cards[0].ship['review'] = True
        merger, policy = ScanMerger(), ScanPolicy(95, 125, True)
        merger.add(image, cards)
        self.assertFalse(policy.can_stop(cards, merger.slots))
        fresh = copy.deepcopy(cards)
        fresh[0].fleet = 0
        merger.add(image, fresh)
        self.assertTrue(policy.can_stop(fresh, merger.slots))
        self.assertTrue(merger.slots[0][0].ship['review'])


class ScanEvidenceCleanupTests(unittest.TestCase):
    def test_success_cleanup_only_removes_previous_generated_files_of_this_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_root = Path(temporary)
            root = log_root / 'mind_scan' / 'testpilot'
            root.mkdir(parents=True)
            old = root / '1791971000000000000.png'
            current = root / '1791972000000000000.png'
            other = log_root / 'mind_scan' / 'other' / old.name
            other.parent.mkdir()
            protected = [current, current.with_suffix('.json'), other, root / 'manual.png', log_root / 'alas.txt']
            for path in [old, old.with_suffix('.json'), *protected]:
                path.write_text('保留测试内容', encoding='utf-8')
            scanner = MindCalculatorScan.__new__(MindCalculatorScan)
            scanner.config = SimpleNamespace(config_name='testpilot')
            scanner._scan_evidence_paths = {current.resolve()}
            with patch('module.retire.mind_scan.logger.log_file', str(log_root / 'alas.txt')):
                scanner._prune_scan_evidence()
            self.assertFalse(old.exists())
            self.assertFalse(old.with_suffix('.json').exists())
            self.assertTrue(all(path.exists() for path in protected))

    def test_authorized_diagnostics_preserve_all_rounds(self):
        scanner = MindCalculatorScan.__new__(MindCalculatorScan)
        with patch('module.retire.mind_scan.Path.is_file', return_value=True), \
                patch('module.retire.mind_scan.Path.iterdir') as files:
            scanner._prune_scan_evidence()
        files.assert_not_called()

    def test_cleanup_refuses_an_instance_path_outside_the_scan_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_root = Path(temporary) / 'log'
            log_root.mkdir()
            protected = Path(temporary) / 'protected'
            protected.mkdir()
            image = protected / '1791971000000000000.png'
            image.write_text('不能删除', encoding='utf-8')
            scanner = MindCalculatorScan.__new__(MindCalculatorScan)
            scanner.config = SimpleNamespace(config_name='../../protected')
            with patch('module.retire.mind_scan.logger.log_file', str(log_root / 'alas.txt')):
                scanner._prune_scan_evidence()
            self.assertTrue(image.exists())


class NamePhaseRegressionTests(unittest.TestCase):
    def test_real_purple_header_phases_do_not_compete_with_the_broader_blue_mask(self):
        from module.runtime.mind_recognition import rarity_masks
        pixels = np.array(Image.open(FIXTURES / 'mind_rarity_phase.png').convert('RGB'))
        regions = [pixels[y:y+5] for y in range(0, len(pixels), 5)]
        old = [max(rarity_masks(region), key=lambda key: rarity_masks(region)[key].sum()) for region in regions]
        self.assertEqual(old, ['SR', 'R', 'SR', 'R'])
        ocr = Mock()
        ocr.ocr_for_single_lines.side_effect = lambda images: ['尚未确认的测试舰船'] * len(images)
        for region, expected in zip(regions, ['SR', 'SR', 'SR', 'R']):
            image = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
            image[75:80, 93:231] = region
            card = recognize_cards(Image.fromarray(image), row_origins=[75], name_ocr=ocr)[0]
            self.assertEqual(card.ship['rarity'], expected)
            self.assertEqual(card.ship['base_rarity'], '')
            self.assertTrue(card.ship['review'])
            self.assertEqual(calculate([card.ship])['mind'], 0)

    def test_real_ryujo_phases_and_verified_retrofit_punctuation(self):
        pixels = np.array(Image.open(FIXTURES / 'mind_ryujo_phase.png').convert('RGB'))
        for index in range(2):
            image = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
            image[235:265, 83:235] = pixels[index * 30:(index + 1) * 30]
            card = recognize_cards(Image.fromarray(image), row_origins=[75, 302, 529])[0]
            self.assertEqual(card.ship['name'], '龙骧')
            self.assertFalse(card.ship['review'])
        for text in ('华甲改', '华甲·改', '华甲.改'):
            info, _ = _match_name([text], 'SSR')
            self.assertEqual((info['name'], info['base_name'], info['base_rarity']), ('华甲.改', '华甲', 'SR'))
        pixels = np.array(Image.open(FIXTURES / 'mind_retrofit_punctuation.png').convert('RGB'))
        for index in range(2):
            image = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
            image[235:265, 83:235] = pixels[index * 30:(index + 1) * 30]
            card = recognize_cards(Image.fromarray(image), row_origins=[75, 302, 529])[0]
            self.assertEqual((card.ship['name'], card.ship['base_rarity']), ('华甲.改', 'SR'))
            self.assertFalse(card.ship['review'])
        self.assertIsNone(_match_name(['龙骥', '龙凤'], 'SR')[0])
        self.assertIsNone(_match_name(['华甲改', '华甲'], 'SSR')[0])

    def test_real_single_character_name_requires_two_exact_scaled_reads(self):
        pixels = np.array(Image.open(FIXTURES / 'mind_shizuku_phase.png').convert('RGB'))
        for index in range(3):
            image = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
            image[462:492, 742:894] = pixels[index * 30:(index + 1) * 30]
            card = recognize_cards(Image.fromarray(image), row_origins=[75, 302, 529])[11]
            self.assertEqual(card.ship['name'], '雫')
            self.assertFalse(card.ship['review'])
            if index in (1, 2):
                self.assertIn('乘', card.name_reads)
                self.assertGreaterEqual(card.name_reads.count('雫'), 2)

    def test_single_character_retry_does_not_accept_one_vote_or_conflict(self):
        image = Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB')
        for reads in (['雫', '乘'], ['雫', '雪风'], ['雫', '乘'] * 6):
            with self.subTest(reads=reads):
                ocr = Mock()
                ocr.ocr_for_single_lines.side_effect = [['乘'] + ['伦敦.改'] * 20, ['Φ', '乘'], reads]
                card = recognize_cards(image, name_ocr=ocr)[0]
                self.assertTrue(card.ship['review'])
                self.assertNotEqual(card.ship['name'], '雫')

    def test_real_short_name_border_noise_requires_two_matching_color_reads(self):
        pixels = np.array(Image.open(FIXTURES / 'mind_matsubream_phase.png').convert('RGB'))
        for index in range(2):
            image = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
            image[462:492, 412:564] = pixels[index * 30:(index + 1) * 30]
            card = recognize_cards(Image.fromarray(image), row_origins=[75, 302, 529])[9]
            self.assertEqual(card.ship['name'], '松鲷')
            self.assertFalse(card.ship['review'])
            if index == 1:
                self.assertIn('松国', card.name_reads)
                self.assertGreaterEqual(card.name_reads.count('松鲷'), 2)

    def test_unresolved_literal_suffix_cannot_be_silently_dropped(self):
        ocr = Mock()
        ocr.ocr_for_single_lines.side_effect = [
            ['约克城I'] + ['伦敦.改'] * 20, ['约克城', '约克城I'], ['约克城', '约克城']]
        image = Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB')
        card = recognize_cards(image, name_ocr=ocr)[0]
        self.assertTrue(card.ship['review'])
        self.assertNotEqual(card.ship['name'], '约克城')
        self.assertIn('罗马后缀未确认', card.ship['source'])

    def test_real_incomplete_second_type_suffix_never_becomes_the_base_ship(self):
        pixels = np.array(Image.open(FIXTURES / 'mind_yorktown_phase.png').convert('RGB'))
        for index in range(2):
            # 将真实名称条放回固定网格，隔离采样相位，经过真实整页识别路径。
            image = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
            image[235:265, 83:235] = pixels[index * 30:(index + 1) * 30]
            card = recognize_cards(Image.fromarray(image), row_origins=[75, 302, 529])[0]
            self.assertEqual(card.ship['name'], '约克城II')
            self.assertFalse(card.ship['review'])
            if index == 0:
                self.assertIn('约克城I', card.name_reads)
                self.assertGreaterEqual(sum(text.replace('Ⅱ', 'II') == '约克城II' for text in card.name_reads), 2)

    def test_real_name_sampling_phases_preserve_identity_and_visual_ellipsis(self):
        from module.ocr.al_ocr import AlOcr, OcrSettings
        pixels = np.array(Image.open(FIXTURES / 'mind_names_stuck.png').convert('RGB'))
        labels = json.loads((FIXTURES / 'mind_names_stuck.json').read_text(encoding='utf-8'))
        ocr = AlOcr(name='ppocr_v6', settings=OcrSettings('onnx', 'cpu', False, 'standard'), save_debug_images=False)
        for index, label in enumerate(labels):
            region = pixels[index * 30:(index + 1) * 30]
            ellipsis = _has_name_ellipsis(region)
            self.assertEqual(ellipsis, label['ellipsis'])
            raw = ocr.ocr_for_single_lines([_name_image(region)])[0]
            self.assertEqual(_match_name([raw], 'UR', ellipsis=ellipsis)[0]['name'], label['name'], raw)

    def test_missing_ellipsis_without_image_evidence_remains_review(self):
        self.assertIsNone(_match_name(['乌尔里希·冯.'], 'UR')[0])
        self.assertEqual(_match_name(['乌尔里希·冯.'], 'UR', ellipsis=True)[0]['name'], '乌尔里希·冯·胡滕')
        # 原舰/META 共用截断前缀时，图像省略号也不能替用户选择身份。
        self.assertIsNone(_match_name(['安德烈亚·多利'], 'SR', ellipsis=True)[0])

    def test_verified_white_phoenix_and_edge_punctuation_do_not_guess_other_names(self):
        self.assertEqual(_match_name(['自凤'], 'UR')[0]['name'], '白凤')
        self.assertEqual(_match_name(['、大青花鱼', "大青花鱼'"], 'SSR')[0]['name'], '大青花鱼')
        self.assertIsNone(_match_name(['自凤II'], 'UR')[0])

    def test_complete_real_names_do_not_supply_false_ellipsis_evidence(self):
        pixels = np.array(Image.open(FIXTURES / 'mind_names.png').convert('RGB'))
        for index in range(len(pixels) // 30):
            self.assertFalse(_has_name_ellipsis(pixels[index * 30:(index + 1) * 30]), index)


class ScanLoopRegressionTests(unittest.TestCase):
    def setUp(self):
        from tests.test_mind_calculator import RecognitionTests
        self.scanner = MindCalculatorScan.__new__(MindCalculatorScan)
        self.scanner.config = SimpleNamespace(Emulator_ControlMethod='MaaTouch', config_name='testpilot')
        self.scanner.device = Mock()
        self.scanner.appear = Mock(return_value=True)
        self.scanner.handle_popup_confirm = Mock(return_value=False)
        diagnostic = self.enterContext(tempfile.TemporaryDirectory())
        self.scanner._save_scan_evidence = Mock(return_value=str(Path(diagnostic) / 'evidence.png'))
        self.scanner._policy = ScanPolicy(95, 125, True)
        self.position, self.now = 0, 1.
        self.bottom = 2043
        self.captures, self.reads = 0, []
        offsets = list(range(0, self.bottom + 1, 227))
        self.images = dict(zip(offsets, RecognitionTests.scroll_frames(offsets)))
        self.page_calls = {}
        self.kind = ''
        self.stalled = False
        self.show_bottom = True
        def screenshot():
            self.now += .25
            self.captures += 1
            self.scanner.device.image = self.images[self.position]
        def drag(start, end, **kwargs):
            if not self.stalled:
                self.position = min(self.bottom, max(0, self.position + start[1] - end[1]))
        self.scanner.device.screenshot.side_effect = screenshot
        self.scanner.device.drag.side_effect = drag
        def thumb(_):
            start = 475 if self.show_bottom and self.position == self.bottom else 2
            return (np.arange(565) >= start) & (np.arange(565) < start + 90)
        scrollbar = SimpleNamespace(appear=lambda _: True, total=565, match_color=thumb)
        self.enterContext(patch('module.base.timer.time', side_effect=lambda: self.now))
        self.enterContext(patch('module.retire.mind_scan.DOCK_SCROLL', scrollbar))
        self.enterContext(patch('module.retire.mind_scan.grid_rows', return_value=[65, 292, 519]))
        self.enterContext(patch('module.retire.mind_scan.recognize_cards', side_effect=self.recognize))

    def recognize(self, *args, **kwargs):
        page = self.position // 681
        self.reads.append(self.position)
        self.page_calls[page] = self.page_calls.get(page, 0) + 1
        levels = ([125] * 21 if page == 0 else [110] * 7 + [100] * 7 + [95] * 7 if page == 1
                  else [94 - page] * 21)
        cards = cards_for_levels(levels, fleet=int(page == 0))
        for index, card in enumerate(cards):
            card.ship.update(name=f'测试舰{page}-{index}', base_rarity='SR', excluded=False)
        if page == 0:
            if self.kind == 'corrected_order' and self.page_calls[page] == 1 or self.kind == 'order':
                cards[11].fleet = 0
            if self.kind == 'name':
                cards[11].ship.update(name='纳希莫夫海军上', review=True)
                cards[11].quality = 2
            if self.kind == 'level':
                cards[11].level_reliable = False
                cards[11].ship.update(level=0, review=True)
            if self.kind == 'fleet':
                cards[11].fleet = -1
        return cards

    def scan(self):
        return self.scanner._scan_pages(Mock(), Mock())

    def test_false_ordinary_is_rechecked_then_real_policy_stops_after_three_low_rows(self):
        self.kind = 'corrected_order'
        ships = self.scan()
        self.assertEqual(self.reads, [0, 0, 681, 1362])
        self.assertLess(self.position, self.bottom)
        self.assertEqual(len(ships), 42)
        self.assertFalse(any(ship['review'] for ship in ships))
        self.assertTrue(self.scanner._policy.sort_valid)

    def test_local_name_failure_continues_and_preserves_safe_early_stop(self):
        self.kind = 'name'
        ships = self.scan()
        self.assertEqual(self.reads, [0, 0, 0, 681, 1362])
        self.assertEqual(sum(ship['review'] for ship in ships), 1)
        self.assertIn('待人工核对', ships[11]['source'])
        self.assertIn('累计第 2 行第 5 列', ships[11]['source'])
        self.assertEqual(ships[11]['name'], '纳希莫夫海军上')
        self.assertTrue(self.scanner._policy.sort_valid)

    def test_persistent_sort_level_or_fleet_doubt_continues_to_real_bottom(self):
        for kind in ('order', 'level', 'fleet'):
            with self.subTest(kind=kind):
                self.setUp()
                self.kind = kind
                ships = self.scan()
                self.assertEqual(self.reads, [0, 0, 0, 681, 1362, 2043])
                self.assertEqual(self.position, self.bottom)
                self.assertTrue(any(ship['review'] for ship in ships))
                self.assertFalse(self.scanner._policy.sort_valid)
                if kind == 'level':
                    unknown = [ship for ship in ships if ship['level'] == 0]
                    self.assertEqual(len(unknown), 1)
                    self.assertEqual(calculate(unknown)['mind'], 0)

    def test_continuously_unstable_frames_have_a_deadline(self):
        self.scanner._stable_pixels = Mock(side_effect=lambda _: np.full((2, 2), self.captures % 2 * 255, np.uint8))
        with self.assertRaisesRegex(MindCalculatorScanError, '30 秒'):
            self.scan()
        self.assertLessEqual(self.captures, 121)
        self.assertEqual(self.reads, [])

    def test_lost_page_and_repeated_popups_have_the_same_deadline(self):
        for popup in (False, True):
            with self.subTest(popup=popup):
                self.setUp()
                self.scanner.appear.return_value = False
                self.scanner.handle_popup_confirm.return_value = popup
                with self.assertRaisesRegex(MindCalculatorScanError, '页面已丢失'):
                    self.scan()
                self.assertLessEqual(self.captures, 121)
                self.assertEqual(self.reads, [])

    def test_zero_motion_is_bounded_without_claiming_bottom(self):
        self.stalled = True
        with self.assertRaises((MindCalculatorScanError, GameStuckError)):
            self.scan()
        self.assertLess(self.now, 32)
        self.assertEqual(self.reads, [0])

    def test_near_bottom_without_endpoint_does_not_save_partial_result(self):
        self.show_bottom = False
        self.kind = 'level'
        with self.assertRaises((MindCalculatorScanError, GameStuckError)):
            self.scan()
        self.assertEqual(self.position, self.bottom)
        self.assertLess(self.now, 65)


if __name__ == '__main__':
    unittest.main()
