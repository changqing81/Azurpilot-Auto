"""ShipScanner 按帧缓存的回归测试。

同一张截图重复扫描时复用未过滤结果（子扫描器只跑一次 OCR），
换帧后必须重新扫描；只改过滤范围不清缓存，改子扫描器开关
或移动网格位置时必须清缓存。
"""

import unittest
from unittest.mock import patch

import numpy as np

import module.retire.scanner as scanner_module
from module.retire.scanner import ShipScanner

GRID_COUNT = 14  # CARD_GRIDS 为 7x2


class StubSubScanner:
    """替换真实子扫描器的桩：返回固定结果并记录被调用的次数。"""

    def __init__(self, values):
        self.values = list(values)
        self._results = []
        self._enabled = True
        self.calls = 0

    @property
    def results(self):
        return self._results

    def scan(self, image, cached=False, output=False):
        self.calls += 1
        if cached:
            self._results.extend(self.values)
            return None
        return list(self.values)

    def clear(self):
        self._results.clear()

    def move(self, vector):
        pass

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def limit_value(self, value):
        return value


def make_scanner(level_values=None):
    """构造子扫描器全部被桩替换的 ShipScanner（不加载 OCR 模型）。

    Returns:
        tuple: (ShipScanner 实例, {属性名: 桩})
    """
    values = list(level_values) if level_values is not None else list(range(1, GRID_COUNT + 1))
    stubs = {
        'level': StubSubScanner(values),
        'emotion': StubSubScanner([None] * GRID_COUNT),
        'rarity': StubSubScanner([None] * GRID_COUNT),
        'fleet': StubSubScanner([None] * GRID_COUNT),
        'status': StubSubScanner([None] * GRID_COUNT),
        'hash': StubSubScanner([None] * GRID_COUNT),
    }
    class_names = {
        'LevelScanner': 'level',
        'EmotionScanner': 'emotion',
        'RarityScanner': 'rarity',
        'FleetScanner': 'fleet',
        'StatusScanner': 'status',
        'HashGenerator': 'hash',
    }
    patchers = [patch.object(scanner_module, name, lambda stub=stubs[key]: stub)
                for name, key in class_names.items()]
    for item in patchers:
        item.start()
    try:
        scanner = ShipScanner(level=(1, 125), emotion=(0, 150), fleet=0, status='free')
    finally:
        for item in patchers:
            item.stop()
    return scanner, stubs


class ShipScannerFrameCacheTest(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.other_image = np.full((720, 1280, 3), 255, dtype=np.uint8)

    def test_same_frame_scans_once(self):
        """同一张截图重复扫描，子扫描器只跑一次，两次结果一致。"""
        scanner, stubs = make_scanner()

        first = scanner.scan(self.image, output=False)
        second = scanner.scan(self.image, output=False)

        self.assertEqual(stubs['level'].calls, 1)
        self.assertEqual([s.level for s in first], [s.level for s in second])

    def test_new_frame_scans_again(self):
        """换帧（新的截图对象）必须重新扫描。"""
        scanner, stubs = make_scanner()

        scanner.scan(self.image, output=False)
        scanner.scan(self.other_image, output=False)

        self.assertEqual(stubs['level'].calls, 2)

    def test_move_invalidates_cache(self):
        """网格移动后旧按钮坐标失效，必须重新扫描。"""
        scanner, stubs = make_scanner()

        scanner.scan(self.image, output=False)
        scanner.move((0, 20))
        scanner.scan(self.image, output=False)

        self.assertEqual(stubs['level'].calls, 2)

    def test_disable_invalidates_cache(self):
        """关闭子扫描器会改变原始结果，必须重新扫描。"""
        scanner, stubs = make_scanner()

        scanner.scan(self.image, output=False)
        scanner.disable('rarity')
        scanner.scan(self.image, output=False)

        self.assertEqual(stubs['level'].calls, 2)

    def test_set_limitation_false_invalidates_cache(self):
        """set_limitation(属性=False) 等价于关闭子扫描器，必须重新扫描。"""
        scanner, stubs = make_scanner()

        scanner.scan(self.image, output=False)
        scanner.set_limitation(rarity=False)
        scanner.scan(self.image, output=False)

        self.assertEqual(stubs['level'].calls, 2)

    def test_set_limitation_value_keeps_cache_and_filters(self):
        """只改过滤范围不清缓存，过滤照常生效。"""
        scanner, stubs = make_scanner()

        self.assertEqual(len(scanner.scan(self.image, output=False)), GRID_COUNT)
        scanner.set_limitation(level=(10, 20))
        filtered = scanner.scan(self.image, output=False)

        self.assertEqual(stubs['level'].calls, 1)
        self.assertEqual([s.level for s in filtered], list(range(10, GRID_COUNT + 1)))

    def test_returned_list_is_safe_to_modify(self):
        """调用方对返回列表排序不会污染帧缓存。"""
        scanner, stubs = make_scanner()

        ships = scanner.scan(self.image, output=False)
        ships.sort(key=lambda s: -s.level)
        again = scanner.scan(self.image, output=False)

        self.assertEqual(stubs['level'].calls, 1)
        self.assertEqual([s.level for s in again], list(range(1, GRID_COUNT + 1)))


if __name__ == '__main__':
    unittest.main()
