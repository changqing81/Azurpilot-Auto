"""低耗任务换船候选扫描的回归测试。

覆盖 module/campaign/gems_farming.py 的候选遍历：

- find_custom_candidates（后排/前排共用）：按候选名优先级逐个匹配，
  最高优先级的候选名命中即返回；首页命中的是次级名字时仍会翻页，
  给更高优先级的船一次机会（不丢"指定舰船"的效果）；倒序页命中即返回；
  两轮都没有命中时回退到首页的次级候选，回退前先做单点坐标校验。
- find_candidates（前排模板列表路径）：命中一个模板即返回，
  整个模板列表只扫描一次。

测试用缓存感知的假扫描器统计"真实扫描次数"，与 ShipScanner 的帧缓存同构：
同一张截图上的多次匹配只算一次 OCR，切排序会换帧、因此多一次。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

import module.campaign.gems_farming as gems_farming
from module.base.button import Button
from module.campaign.gems_farming import GemsFarming
from module.retire.scanner import Ship

CV_NAMES = ['bogue', 'ranger', 'langley']
DD_NAMES = ['cassin', 'downes', 'z20']


def make_frame():
    """生成一个新的截图对象，模拟 device.screenshot() 换帧。"""
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def set_hits(templates, hit):
    """设置当前页面上可见（能匹配到）的候选名，其余不可见。"""
    hit = (hit or '').upper()
    for name, template in templates.items():
        template.hit = (name == hit)


class FakeScanner:
    """缓存感知的假扫描器：同一张截图只记一次真实扫描。"""

    def __init__(self, ship_count=3):
        self.ships = [
            Ship(level=1, emotion=120, rarity='common', fleet=0, status='free',
                 button=Button(area=(0, 0, 10, 10), color=(0, 0, 0),
                               button=(0, 0, 10, 10), name=f'CARD_{index}'))
            for index in range(ship_count)
        ]
        self.ocr_calls = 0
        self._cached_image = None

    def scan(self, image, cached=False, output=True):
        if image is not self._cached_image:
            self._cached_image = image
            self.ocr_calls += 1
        return list(self.ships)


class FakeTemplate:
    """命中与否可控的假模板，同时记录被匹配的次数。"""

    def __init__(self, hit):
        self.hit = hit
        self.matches = 0

    def match(self, image, similarity=0.9):
        self.matches += 1
        return self.hit


def make_farmer():
    """构造只具备扫描相关依赖的 GemsFarming（绕过 __init__，不依赖设备与配置）。"""
    farmer = GemsFarming.__new__(GemsFarming)
    farmer.config = SimpleNamespace(
        GemsFarming_CommonCV='custom',
        GemsFarming_CommonCVFilter='bogue > ranger > langley',
        GemsFarming_CommonDDFilter='cassin > downes > z20',
        COMMON_CV_FILTER='bogue > ranger > langley',
    )
    farmer.device = SimpleNamespace(image=make_frame())
    farmer.image_crop = lambda area, copy=False: np.zeros((10, 10, 3), dtype=np.uint8)
    farmer.dock_sort_method_dsc_set = Mock()
    return farmer


def sort_switcher(farmer, templates, flip_value, flip_hit, restore_hit):
    """模拟切排序：换帧，并按目标排序设置该页可见的候选名。

    Args:
        flip_value: 第一次切换时传入的排序值（翻页方向）。
        flip_hit: 翻页后该页可见的候选名，None 表示该页没有目标船。
        restore_hit: 恢复原排序后该页可见的候选名。
    """
    def switch(descending, *args, **kwargs):
        farmer.device.image = make_frame()
        set_hits(templates, flip_hit if descending == flip_value else restore_hit)

    return switch


def make_templates(names, hit=None):
    """按候选名生成模板字典，hit 为当前页面命中的名字。"""
    templates = {name.upper(): FakeTemplate(False) for name in names}
    set_hits(templates, hit)
    return templates


class FindCustomCandidatesTest(unittest.TestCase):
    def run_search(self, farmer, scanner, templates, names, ship_type='cv'):
        with patch.object(GemsFarming, 'get_common_ship_filter',
                          autospec=True, return_value=list(names)), \
                patch.object(gems_farming, f'TEMPLATE_COMMON_{ship_type.upper()}', templates):
            return farmer.find_custom_candidates(scanner, ship_type=ship_type)

    def test_top_name_hit_returns_without_reverse(self):
        """最高优先级的候选名命中即返回：不翻页，只扫描一次。"""
        farmer, scanner = make_farmer(), FakeScanner()
        templates = make_templates(CV_NAMES, hit='bogue')

        candidates = self.run_search(farmer, scanner, templates, CV_NAMES)

        self.assertTrue(candidates)
        self.assertEqual(scanner.ocr_calls, 1)
        farmer.dock_sort_method_dsc_set.assert_not_called()

    def test_lower_name_hit_still_finds_top_name_after_flip(self):
        """首页只命中次级名字时仍会翻页，把最高优先级的船找回来。"""
        farmer, scanner = make_farmer(), FakeScanner()
        templates = make_templates(CV_NAMES, hit='ranger')
        # 翻到倒序页后最高优先级的船才出现
        farmer.dock_sort_method_dsc_set.side_effect = sort_switcher(
            farmer, templates, flip_value=True, flip_hit='bogue', restore_hit='ranger')

        candidates = self.run_search(farmer, scanner, templates, CV_NAMES)

        self.assertTrue(candidates)
        self.assertEqual([ship.level for ship in candidates],
                         [ship.level for ship in scanner.ships])
        farmer.dock_sort_method_dsc_set.assert_called_once_with(True)  # 后排翻页方向
        self.assertEqual(scanner.ocr_calls, 2)

    def test_fallback_keeps_page_one_choice_when_reverse_empty(self):
        """倒序页也没有目标船时，回退到首页的次级候选，且不多付整页 OCR。"""
        farmer, scanner = make_farmer(), FakeScanner()
        templates = make_templates(CV_NAMES, hit='ranger')
        farmer.dock_sort_method_dsc_set.side_effect = sort_switcher(
            farmer, templates, flip_value=True, flip_hit=None, restore_hit='ranger')

        candidates = self.run_search(farmer, scanner, templates, CV_NAMES)

        self.assertTrue(candidates)
        # 翻页一次 + 恢复排序一次
        self.assertEqual(farmer.dock_sort_method_dsc_set.call_args_list, [call(True), call(False)])
        # 坐标单点校验通过，无需第三次整页扫描
        self.assertEqual(scanner.ocr_calls, 2)

    def test_fallback_rescans_when_coordinate_check_fails(self):
        """坐标校验失败时重新整页识别（多一次扫描），不拿旧坐标冒险。"""
        farmer, scanner = make_farmer(), FakeScanner()
        templates = make_templates(CV_NAMES, hit='ranger')
        farmer.dock_sort_method_dsc_set.side_effect = sort_switcher(
            farmer, templates, flip_value=True, flip_hit=None, restore_hit='ranger')

        with patch.object(GemsFarming, 'match_ship_to_template',
                          autospec=True, return_value=False):
            candidates = self.run_search(farmer, scanner, templates, CV_NAMES)

        self.assertTrue(candidates)
        self.assertEqual(scanner.ocr_calls, 3)

    def test_all_miss_returns_empty(self):
        """两个方向都没有命中时返回空列表，交给调用方兜底。"""
        farmer, scanner = make_farmer(), FakeScanner()
        templates = make_templates(CV_NAMES)
        farmer.dock_sort_method_dsc_set.side_effect = sort_switcher(
            farmer, templates, flip_value=True, flip_hit=None, restore_hit=None)

        candidates = self.run_search(farmer, scanner, templates, CV_NAMES)

        self.assertEqual(candidates, [])
        farmer.dock_sort_method_dsc_set.assert_called_once_with(True)
        self.assertEqual(scanner.ocr_calls, 2)

    def test_vanguard_priority_and_flip_direction(self):
        """前排（先锋）走同一函数：优先级语义一致，翻页方向与后排相反。"""
        farmer, scanner = make_farmer(), FakeScanner()
        templates = make_templates(DD_NAMES, hit='downes')
        # dd 的翻页值是 not True = False
        farmer.dock_sort_method_dsc_set.side_effect = sort_switcher(
            farmer, templates, flip_value=False, flip_hit=None, restore_hit='downes')

        candidates = self.run_search(farmer, scanner, templates, DD_NAMES, ship_type='dd')

        self.assertTrue(candidates)
        self.assertEqual(farmer.dock_sort_method_dsc_set.call_args_list, [call(False), call(True)])
        self.assertEqual(scanner.ocr_calls, 2)

    def test_vanguard_top_name_hit_scans_once(self):
        """前排最高优先级命中即返回，只扫描一次。"""
        farmer, scanner = make_farmer(), FakeScanner()
        templates = make_templates(DD_NAMES, hit='cassin')

        candidates = self.run_search(farmer, scanner, templates, DD_NAMES, ship_type='dd')

        self.assertTrue(candidates)
        self.assertEqual(scanner.ocr_calls, 1)
        farmer.dock_sort_method_dsc_set.assert_not_called()


class FindCandidatesTest(unittest.TestCase):
    def test_list_template_stops_at_first_hit(self):
        """模板列表逐个匹配，命中一个模板即返回，剩余模板不再匹配。"""
        farmer, scanner = make_farmer(), FakeScanner()
        first, second, third = FakeTemplate(False), FakeTemplate(True), FakeTemplate(False)

        candidates = farmer.find_candidates([first, second, third], scanner)

        self.assertTrue(candidates)
        self.assertEqual(scanner.ocr_calls, 1)  # 整个模板列表只扫描一次
        self.assertEqual(third.matches, 0)

    def test_single_template_returns_empty_when_miss(self):
        """单个模板未命中时返回空列表，不抛异常。"""
        farmer, scanner = make_farmer(), FakeScanner()

        self.assertEqual(farmer.find_candidates(FakeTemplate(False), scanner), [])
        self.assertEqual(scanner.ocr_calls, 1)


if __name__ == '__main__':
    unittest.main()
