
"""换船跳过心情识别的回归测试。

覆盖长卿 2026-10-25 反馈的两个问题：

1. 船坞卡片的情绪状态图标坐标偏移 25 像素，框到了立绘空白，
   导致所有舰船 emotion_status 恒为 unknown、心情 OCR 恒为噪声值。
2. 开启「忽略心情警告弹窗」后，换船仍按心情下限过滤候选舰船，
   使得 1 级突击者（真实心情 50，被 OCR 误读成 7）被误杀，
   寻船失败后继续沿用旧旗舰。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import module.campaign.gems_farming as gems_farming
from module.campaign.gems_farming import GemsFarming
from module.retire.dock import CARD_EMOTION_STATUS_GRIDS, CARD_GRIDS
from module.retire.scanner import Ship


class ShipEmotionKeyTest(unittest.TestCase):
    """Ship.emotion_key 必须能安全处理 None（心情扫描被关闭时）。"""

    def test_none_emotion_sorts_as_zero(self):
        ships = [
            Ship(level=1, emotion=None, button=None),
            Ship(level=1, emotion=50, button=None),
            Ship(level=1, emotion=0, button=None),
        ]
        ships.sort(key=lambda s: s.emotion_key, reverse=True)
        # 0 与 None 都映射到排序键 0，稳定排序下顺序保持输入顺序；
        # 断言只需保证心情 50 在最前、且比较过程中没有抛 TypeError
        self.assertEqual(ships[0].emotion, 50)
        self.assertEqual({s.emotion for s in ships[1:]}, {0, None})

    def test_max_works_with_none(self):
        ships = [Ship(level=1, emotion=None, button=None),
                 Ship(level=2, emotion=None, button=None)]
        # 不应抛 TypeError
        target = max(ships, key=lambda s: (s.level, s.emotion_key))
        self.assertEqual(target.level, 2)


class EmotionStatusGridTest(unittest.TestCase):
    """情绪状态图标区域必须落在卡片右上角的表情图标上。"""

    def test_status_area_matches_expression_icon(self):
        # 图标实测卡内坐标 (114, 31)-(135, 53)
        area = CARD_EMOTION_STATUS_GRIDS.buttons[0].area
        card = CARD_GRIDS.buttons[0].area
        x1, y1 = int(area[0] - card[0]), int(area[1] - card[1])
        x2, y2 = int(area[2] - card[0]), int(area[3] - card[1])
        # 上下各留 1~2 像素容差，必须覆盖 31~53 这段 y 区间
        self.assertLessEqual(y1, 32, '状态区域上沿不能低于表情图标顶部')
        self.assertGreaterEqual(y2, 52, '状态区域下沿不能高于表情图标底部')
        self.assertGreaterEqual(x1, 108)
        self.assertLessEqual(x2, 138)


def make_farmer(ignore_emotion):
    """构造只具备选船依赖的 GemsFarming。"""
    farmer = GemsFarming.__new__(GemsFarming)
    farmer.config = SimpleNamespace(
        GemsFarming_IgnoreEmotionWarning=ignore_emotion,
        GemsFarming_CommonCV='any',
        GemsFarming_CommonCVFilter='bogue > ranger > langley',
        GemsFarming_AllowHighFlagshipLevel=False,
        GemsFarming_UseEmotionFirst=False,
        GemsFarming_ChangeFlagship='ship',
        SERVER='cn',
        COMMON_CV_FILTER='bogue > ranger > langley',
        Fleet_FleetOrder='fleet1_standby_fleet2_all',
        Fleet_Fleet2=1,
    )
    farmer.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
    farmer.campaign = SimpleNamespace(_map_battle=10)
    farmer.dock_favourite_set = Mock()
    farmer.dock_sort_method_dsc_set = Mock()
    farmer.dock_filter_set = Mock()
    farmer._ship_change_confirm = Mock()
    farmer.set_emotion = Mock()
    farmer.get_emotion = Mock(return_value=119)
    return farmer


class IgnoreEmotionSkipsScanTest(unittest.TestCase):
    """开启忽略心情警告时，扫描器不应限制心情下限、也不应开启心情子扫描器。"""

    def run_cv_search(self, ignore_emotion, ships):
        farmer = make_farmer(ignore_emotion)
        captured = {}

        class FakeScanner:
            def __init__(self, **kwargs):
                captured['level'] = kwargs.get('level')
                captured['emotion'] = kwargs.get('emotion')
                captured['disabled'] = []

            def disable(self, *args):
                captured['disabled'].extend(args)

            def scan(self, image, cached=False, output=True):
                return list(ships)

            def set_limitation(self, **kwargs):
                pass

        with patch.object(gems_farming, 'ShipScanner', FakeScanner), \
                patch.object(GemsFarming, 'find_custom_candidates',
                             autospec=True, return_value=list(ships)):
            farmer.get_common_rarity_cv(lv=31)
        return captured

    def test_ignore_emotion_drops_emotion_lower_bound(self):
        ships = [Ship(level=1, emotion=None, button=None)]
        captured = self.run_cv_search(True, ships)
        # 心情下限必须是 0，否则候选会被 satisfy_limitation 全部刷掉
        self.assertEqual(captured['emotion'], (0, 150))

    def test_ignore_emotion_disables_emotion_subscanner(self):
        ships = [Ship(level=1, emotion=None, button=None)]
        captured = self.run_cv_search(True, ships)
        self.assertIn('emotion', captured['disabled'],
                      '跳过心情识别时必须关闭心情子扫描器，避免无谓 OCR')

    def test_keep_emotion_uses_lower_bound(self):
        ships = [Ship(level=1, emotion=50, button=None)]
        captured = self.run_cv_search(False, ships)
        # 未开启忽略时仍按 emotion_lower_bound = 4 + 10*2 = 24 过滤
        self.assertEqual(captured['emotion'], (24, 150))
        self.assertNotIn('emotion', captured['disabled'])


class FlagshipChangeWithNoneEmotionTest(unittest.TestCase):
    """心情为 None 时换船不应写情绪记录（不把 0 当成红脸）。"""

    def test_none_emotion_skips_set_emotion(self):
        farmer = GemsFarming.__new__(GemsFarming)
        farmer.config = SimpleNamespace(
            GemsFarming_ChangeVanguard='disabled',
            GemsFarming_AllowHighFlagshipLevel=False,
        )
        farmer.set_emotion = Mock()
        farmer.get_emotion = Mock(return_value=119)
        farmer._ship_change_confirm = Mock()

        ship = Ship(level=1, emotion=None, button='BTN')
        GemsFarming.flagship_change_with_emotion(farmer, [ship])

        farmer.set_emotion.assert_not_called()
        farmer._ship_change_confirm.assert_called_once_with('BTN')


if __name__ == '__main__':
    unittest.main()
