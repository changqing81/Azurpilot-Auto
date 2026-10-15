"""科技独立扫描、稳定身份和持久化快捷操作的针对性回归。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from PIL import Image

from module.config.deep import deep_get, deep_set
from module.runtime import fleet_tech as tech
from module.runtime.fleet_tech_identity import FleetTechIdentity, surface_icon
from module.runtime.mind_recognition import Card, ScanMerger, aircraft_icon, color_rows, recognize_cards

FIXTURES = Path(__file__).parent / 'fixtures'


def observed(gid, level, **extra):
    return dict(group_type=gid, name='测试舰', level=level, review=False, **extra)


def merge(record, ships, **extra):
    params = dict(min_level=98, max_level=125, reached_bottom=False, filters_clear=True,
                  physical_count=2, expected_count=2, updated_at='fixture')
    params.update(extra)
    return tech.merge_scan(record, ships, **params)


class FleetTechIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.identity = FleetTechIdentity()

    def test_same_name_requires_positive_type_evidence_and_same_type_is_manual(self):
        for name, surface_gid, aircraft_gid in [('加贺', 30507, 30702), ('天城', 30405, 30715)]:
            with self.subTest(name=name):
                self.assertIsNone(self.identity.match([name] * 3, 'SSR')[0])
                self.assertEqual(self.identity.match([name] * 3, surface=True)[0]['group_type'], surface_gid)
                self.assertEqual(self.identity.match([name] * 3, aviation=True)[0]['group_type'], aircraft_gid)
        for name in ['约克', '新月']:
            self.assertIsNone(self.identity.match([name] * 3, 'SSR', fuzzy=True)[0])
            self.assertIsNone(self.identity.match(['x' + name] * 3, 'SSR', fuzzy=True)[0])
        self.assertIsNone(self.identity.match(['x天城'] * 3, 'SSR', fuzzy=True)[0])
        self.assertIsNone(self.identity.match(['虎责'] * 3, 'SR', fuzzy=True)[0])
        self.assertEqual(self.identity.match(['约克.改'] * 2)[0]['group_type'], 20307)

    def test_special_suffixes_and_no_technology_are_valid_identities(self):
        for reads, expected in [(['克利夫兰μ兵装', '克利夫兰(μ兵装)'], 10224), (['小克利夫兰'] * 2, 10220),
                                (['拉菲II'] * 2, 10151), (['海伦娜·META'] * 2, 970201)]:
            with self.subTest(reads=reads):
                info, _ = self.identity.match(reads)
                self.assertIsNotNone(info)
                self.assertEqual(info['group_type'], expected)
        self.assertEqual(self.identity.match(['贝尔法斯特.改'] * 2)[0]['group_type'], 20212)
        self.assertEqual(self.identity.match(['勇敢·改'] * 2)[0]['group_type'], 20116)
        self.assertEqual(self.identity.match(['扶桑.改'] * 2, aviation=True)[0]['group_type'], 30501)
        self.assertEqual(self.identity.match(['小猎兔犬'] * 2)[0]['group_type'], 20106)
        self.assertEqual(self.identity.match(['小天鹅.改'] * 2)[0]['group_type'], 20110)
        self.assertEqual(self.identity.match(['小云仙'] * 2)[0]['group_type'], 30321)
        self.assertEqual(self.identity.match(['亚利桑那·MET..'] * 2, fuzzy=True)[0]['group_type'], 970503)
        self.assertEqual(self.identity.match(['特装型布里MKI..'] * 2, ellipsis=True, fuzzy=True)[0]['group_type'], 10002)
        self.assertEqual(self.identity.match(['大青花鱼(μ兵..'] * 2, ellipsis=True, fuzzy=True)[0]['group_type'], 10805)
        self.assertIsNone(self.identity.match(['约克城I'] * 2, fuzzy=True)[0])
        self.assertIsNone(self.identity.match(['约克城III'] * 2, fuzzy=True)[0])
        self.assertEqual(self.identity.match(['拉菲I', '拉菲II', '拉菲II'])[0]['group_type'], 10151)
        self.assertEqual(self.identity.match(['特装型布里MKIII'] * 2)[0]['name'], '特装型布里MKIII')

    def test_native_type_icons_are_independent_positive_evidence(self):
        kaga = np.array(Image.open(FIXTURES / 'fleet_tech_kaga.png').convert('RGB'))
        amagi = np.array(Image.open(FIXTURES / 'fleet_tech_amagi.png').convert('RGB'))
        self.assertTrue(surface_icon(kaga, 93, 77))
        self.assertFalse(aircraft_icon(kaga, 93, 77))
        self.assertTrue(aircraft_icon(kaga, 258, 77))
        self.assertFalse(surface_icon(kaga, 258, 77))
        self.assertTrue(surface_icon(amagi, 258, 75))
        self.assertTrue(aircraft_icon(amagi, 93, 75))

    def test_native_live_suffixes_remodel_types_and_pixel_phases(self):
        from module.ocr.al_ocr import AlOcr, OcrSettings
        names = AlOcr(name='ppocr_v6', settings=OcrSettings('onnx', 'cpu', False, 'standard'), save_debug_images=False)
        levels = AlOcr(name='azur_lane', settings=OcrSettings('onnx', 'cpu', False, 'alocr_en_v2_6'), save_debug_images=False)
        cases = [('types', [75, 302], {30405: 120, 30507: 120, 30702: 120}),
                 ('suffixes', [74, 528], {970102: 125, 10326: 120}),
                 ('hybrid', [78], {30501: 120, 30502: 120, 30503: 120, 30504: 120}),
                 ('small', [67, 294], {10805: 99, 30321: 99}),
                 ('cjk', [524], {30606: 120}), ('bulin', [69, 523], {10001: 100, 10002: 99}),
                 ('cjk_phase', [297], {50106: 105}), ('cjk_phase_alt', [532], {30606: 120})]
        for name, rows, expected in cases:
            with self.subTest(fixture=name):
                cards = recognize_cards(Image.open(FIXTURES / f'fleet_tech_live_{name}.png'),
                                        row_origins=rows, name_ocr=names, level_ocr=levels, identity=self.identity)
                self.assertFalse([card.ship for card in cards if card.ship['review']])
                self.assertEqual({card.ship['group_type']: card.ship['level'] for card in cards}, expected)

    def test_group_merger_keeps_same_names_and_all_special_ships(self):
        merger = ScanMerger(group_identity=True)
        ships = [observed(30507, 120), observed(30702, 120), observed(30702, 105), observed(10220, 105), observed(10224, 99)]
        merger.slots = [[Card(93, 80, i, ship, 6, True), 80] for i, ship in enumerate(ships)]
        result = merger.ships(98, 125)
        self.assertEqual({ship['group_type'] for ship in result}, {30507, 30702, 10220, 10224})
        self.assertEqual(next(ship['level'] for ship in result if ship['group_type'] == 30702), 120)

    def test_short_name_card_art_dots_are_not_a_truncated_special_version(self):
        from module.ocr.al_ocr import AlOcr, OcrSettings
        from module.runtime.mind_recognition import _has_name_ellipsis
        image = Image.open(FIXTURES / 'fleet_tech_live_ellipsis.png').convert('RGB')
        self.assertFalse(_has_name_ellipsis(np.array(image)[691:721, 83:235]))
        names = AlOcr(name='ppocr_v6', settings=OcrSettings('onnx', 'cpu', False, 'standard'), save_debug_images=False)
        cards = recognize_cards(image, row_origins=[531], name_ocr=names, identity=self.identity)
        self.assertEqual(len(cards), 1)
        self.assertEqual((cards[0].ship['group_type'], cards[0].ship['level']), (30402, 120))
        self.assertFalse(cards[0].ship['review'])

    def test_same_physical_card_conflicting_group_cannot_replace_identity(self):
        image = Image.open(FIXTURES / 'fleet_tech_kaga.png').convert('RGB')
        merger = ScanMerger(group_identity=True)
        first, second = observed(30507, 120), observed(30702, 120)
        for ship in [first, second]:
            ship['source'] = 'fixture'
        merger.add(image, [Card(93, 77, 0, first, 6, True)])
        merger.add(image, [Card(93, 77, 0, second, 6, True)])
        result = merger.ships()
        self.assertTrue(result[0]['review'])
        self.assertEqual(result[0]['candidates'], [30507, 30702])

    def test_page_coverage_rejects_missing_middle_cards_and_partial_nonbottom_pages(self):
        from module.retire.fleet_tech_scan import FleetTechScan
        scanner = object.__new__(FleetTechScan)
        cards = [Card(93, y, col, observed(10102, 100), 6, True) for y in (80, 307, 534) for col in range(7)]
        scanner._check_scan_page(cards, False)
        scanner._check_scan_page(cards[:-4], True)
        for incomplete, bottom in [(cards[:-4], False), (cards[:8] + cards[9:], True)]:
            with self.assertRaises(ValueError): scanner._check_scan_page(incomplete, bottom)

    def test_interrupted_or_failed_scan_does_not_enter_record_transaction(self):
        from module.retire.fleet_tech_scan import FleetTechScan
        from module.retire.assets import DOCK_SEARCH_CLOSED
        from module.exception import MindCalculatorScanError
        from module.config.config import TaskEnd
        from module.ui.page import page_dock, page_main
        scanner = object.__new__(FleetTechScan)
        scanner.config = SimpleNamespace(FleetTech_MinLevel=98, FleetTech_MaxLevel=125)
        scanner.device = SimpleNamespace(image=None)
        scanner.ui_ensure = scanner._close_search = scanner.dock_reset = scanner.dock_sort_method_dsc_set = lambda *args: None
        scanner.ui_ensure = Mock()
        scanner.appear = lambda button: button is DOCK_SEARCH_CLOSED
        scroll = SimpleNamespace(set_top=lambda _: None, appear=lambda _: True, at_top=lambda _: True, length=1, total=2)
        with patch('module.retire.fleet_tech_scan.DOCK_SCROLL', scroll), \
                patch('module.retire.enhancement.OCR_DOCK_AMOUNT.ocr', return_value=(100, 0, 100)), \
                patch('module.ocr.al_ocr.AlOcr'), patch('module.retire.fleet_tech_scan.config_transaction') as transaction:
            for error in [TaskEnd('cancelled'), MindCalculatorScanError('OCR failed')]:
                scanner.ui_ensure.reset_mock()
                with patch.object(scanner, '_scan_pages', side_effect=error), self.assertRaises(type(error)):
                    scanner.run()
                transaction.assert_not_called()
                self.assertEqual(scanner.ui_ensure.call_args_list, [call(page_main), call(page_dock)])

    def test_real_ocr_saves_distinct_kaga_amagi_and_keeps_same_type_ambiguities(self):
        from module.ocr.al_ocr import AlOcr, OcrSettings
        settings = OcrSettings('onnx', 'cpu', False, 'standard')
        names = AlOcr(name='ppocr_v6', settings=settings, save_debug_images=False)
        levels = AlOcr(name='azur_lane', settings=settings, save_debug_images=False)
        for label, expected in [('kaga', {30507, 30702}), ('amagi', {30405, 30715, 30407}), ('york', {20307})]:
            with self.subTest(label=label):
                image = Image.open(FIXTURES / f'fleet_tech_{label}.png').convert('RGB')
                cards = recognize_cards(image, name_ocr=names, level_ocr=levels, row_origins=color_rows(np.array(image)), identity=self.identity)
                result = merge({}, [card.ship for card in cards])
                self.assertTrue(expected <= set(map(int, result['current'])))
                if label == 'york':
                    self.assertNotIn('40311', result['current'])
                    self.assertTrue(result['review_ships'])
        image = Image.open(FIXTURES / 'fleet_tech_mu.png').convert('RGB')
        cards = recognize_cards(image, name_ocr=names, level_ocr=levels, identity=self.identity)
        result = merge({}, [card.ship for card in cards])
        self.assertTrue(any(self.identity.ships[int(gid)]['variant'] == 'mu' for gid in result['current']))
        self.assertIn('80105', result['current'])
        # 实际日志中的 80px 采样曾读成“天胆”；有限复读应保持与默认采样相同的完整身份集合。
        shifted = recognize_cards(image, name_ocr=names, level_ocr=levels, row_origins=(80, 307, 534), identity=self.identity)
        repeated = merge({}, [card.ship for card in shifted])
        self.assertEqual(result['last_scan']['fingerprint'], repeated['last_scan']['fingerprint'])


if __name__ == '__main__':
    unittest.main()
