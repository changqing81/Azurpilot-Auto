
"""船坞空槽位跳过 OCR 的回归测试。

背景（长卿 2026-10-25 反馈）：换船反复报「没有普通稀有度航母」。
排查发现船坞卡片网格固定 14 格，筛选后剩下的格子是暗色底纹，
原实现把它们一并送进 LevelOcr，pre_process 找不到 'L' 时返回 1x1
占位图，OCR 输出空串、int('') -> 0。于是日志里「空位」和「1 级船」
无法区分，更糟的是空槽位会被当成「低血量遮罩」提亮，放大噪点。

预期行为：
- 空槽位判为 0，且不送进 OCR（省推理、不产生噪声值）
- 有卡的格子照常识别
- 返回值长度与按钮数一致（情绪/舰队等子扫描器依赖下标对齐）
- 低血量遮罩（整块压暗但保留舰船轮廓）不能被误判成空槽位
"""

import unittest
from unittest.mock import patch

import cv2
import numpy as np

import module.retire.scanner as scanner_module
from module.combat.level import COLOR_MASKED, COLOR_WHITE, LevelOcr


def make_empty_card():
    """构造空槽位图像：整体偏暗、无文字、无成片遮罩色。"""
    return np.full((22, 61, 3), 70, dtype=np.uint8)


def make_filled_card():
    """构造有卡图像：仿照 Lv.XX 的白字压在深蓝底上。"""
    image = np.full((22, 61, 3), 40, dtype=np.uint8)
    cv2.putText(image, 'Lv.56', (2, 17), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return image


def make_masked_card():
    """构造低血量遮罩卡：整块压暗到 COLOR_MASKED 附近，但保留轮廓。"""
    base = np.full((22, 61, 3), 255, dtype=np.uint8)
    cv2.putText(base, 'Lv.56', (2, 17), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (255, 255, 255), 1, cv2.LINE_AA)
    scaled = (base.astype(np.float32) * (COLOR_MASKED[0] / COLOR_WHITE[0]))
    return scaled.astype(np.uint8)


class IsEmptyCardTest(unittest.TestCase):
    def test_empty_card_detected(self):
        self.assertTrue(LevelOcr.is_empty_card(make_empty_card()))

    def test_filled_card_not_empty(self):
        self.assertFalse(LevelOcr.is_empty_card(make_filled_card()))

    def test_masked_card_not_empty(self):
        """低血量遮罩亮度接近空槽位，必须靠遮罩色像素区分，不能误判。"""
        self.assertFalse(LevelOcr.is_empty_card(make_masked_card()))


class StubLevelOcr:
    """记录被送检的按钮数，并按按钮序号返回固定等级。"""

    def __init__(self, *args, **kwargs):
        self.buttons = list(args[0]) if args else []
        self.SHOW_LOG = False
        self.ocr_calls = []

    @staticmethod
    def is_empty_card(image):
        return LevelOcr.is_empty_card(image)

    def ocr(self, image, direct_ocr=False):
        self.ocr_calls.append(len(self.buttons) if not direct_ocr else len(image))
        # 送检几个就回几个，值取序号+1，便于断言下标对齐
        return [i + 1 for i in range(len(self.ocr_calls[-1:]) and
                                   (len(self.buttons) if not direct_ocr else len(image)))]


class LevelScannerSkipEmptyTest(unittest.TestCase):
    def build(self, regions):
        """用给定区域构造 LevelScanner，并替换掉 OCR 实现。"""
        stub = StubLevelOcr([])
        with patch.object(scanner_module, 'LevelOcr', lambda *a, **k: stub):
            scanner = scanner_module.LevelScanner()
        scanner.ocr_model = stub
        # 用单排网格：把按钮替换成 1xN，area 直接由 regions 决定
        from module.base.button import Button
        buttons = [
            Button(area=(0, i * 30, 61, i * 30 + 22), color=(0, 0, 0),
                   button=(0, i * 30, 61, i * 30 + 22), name=f'L{i}')
            for i in range(len(regions))
        ]
        stub.buttons = buttons
        canvas = np.zeros((30 * len(regions) + 10, 61, 3), dtype=np.uint8)
        for i, region in enumerate(regions):
            canvas[i * 30:i * 30 + region.shape[0], :region.shape[1]] = region
        return scanner, stub, canvas

    def test_empty_slots_are_zero_and_not_sent_to_ocr(self):
        regions = [make_filled_card(), make_empty_card(), make_empty_card()]
        scanner, stub, canvas = self.build(regions)

        results = scanner._scan(canvas)

        self.assertEqual(len(results), 3, '返回值长度必须与按钮数一致')
        self.assertEqual(results[1], 0)
        self.assertEqual(results[2], 0)
        self.assertNotEqual(results[0], 0, '有卡的格子应被识别')
        self.assertEqual(stub.ocr_calls, [1], '只应把 1 个有卡格子送进 OCR')

    def test_all_empty_skips_ocr_entirely(self):
        regions = [make_empty_card(), make_empty_card()]
        scanner, stub, canvas = self.build(regions)

        results = scanner._scan(canvas)

        self.assertEqual(results, [0, 0])
        self.assertEqual(stub.ocr_calls, [], '全部空槽位时不应调用 OCR')

    def test_masked_card_is_scanned(self):
        regions = [make_empty_card(), make_masked_card()]
        scanner, stub, canvas = self.build(regions)

        scanner._scan(canvas)

        self.assertEqual(stub.ocr_calls, [1], '低血量遮罩卡必须照常送检')


if __name__ == '__main__':
    unittest.main()
