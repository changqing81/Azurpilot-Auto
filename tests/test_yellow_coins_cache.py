"""黄币 30s TTL 缓存的行为测试，不连接游戏。

覆盖：缓存命中跳过重复 OCR、商店记账绕过缓存读新鲜值、
缓存失效后重新读取。降级旧值不写入 TTL 缓存。
"""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TestYellowCoinsCache(unittest.TestCase):
    def make_handler(self):
        from module.os_handler.os_status import OSStatus

        handler = OSStatus.__new__(OSStatus)
        handler.config = SimpleNamespace(
            data={'Dashboard': {'YellowCoin': {'Value': 0, 'Record': ''}}},
            modified={},
            save=lambda: None,
        )
        handler.device = SimpleNamespace(sleep=lambda s: None, image=None)
        handler._cache_lock = threading.Lock()
        handler._last_yellow_coins = 0
        handler.appear_then_click = lambda *args, **kwargs: False
        # 每次 loop() 给足迭代数：双读验证在第 2 次迭代 break
        handler.loop = lambda: iter(range(6))
        return handler

    def test_cache_hit_skips_reocr(self):
        from module.os_handler import os_status as os_status_module

        handler = self.make_handler()
        reads = {'count': 0}

        def fake_ocr(image):
            reads['count'] += 1
            return 287441

        with patch.object(os_status_module, 'OCR_SHOP_YELLOW_COINS') as fake_digit, \
                patch.object(os_status_module, 'LogRes'):
            fake_digit.ocr = fake_ocr
            v1 = handler.get_yellow_coins()
            v2 = handler.get_yellow_coins()

        self.assertEqual(v1, 287441)
        self.assertEqual(v2, 287441, 'TTL 内第二次调用应返回缓存值')
        self.assertEqual(reads['count'], 2, '缓存命中时不应再次 OCR（首次调用双读=2 次）')

    def test_use_cache_false_reads_fresh(self):
        # 商店记账（os_shop_get_coins）必须绕过缓存：购买后余额已变
        from module.os_handler import os_status as os_status_module

        handler = self.make_handler()
        handler.config._yellow_coins_cache = (111111, time.time())
        handler.get_purple_coins = lambda: 500
        reads = {'count': 0}

        def fake_ocr(image):
            reads['count'] += 1
            return 287441

        with patch.object(os_status_module, 'OCR_SHOP_YELLOW_COINS') as fake_digit, \
                patch.object(os_status_module, 'LogRes'), \
                patch('module.statistics.cl1_database.db'):
            fake_digit.ocr = fake_ocr
            handler.os_shop_get_coins()

        self.assertEqual(handler._shop_yellow_coins, 287441, '绕过缓存应读到新鲜值')
        self.assertGreaterEqual(reads['count'], 2)

    def test_invalidated_cache_reads_fresh(self):
        from module.os_handler import os_status as os_status_module

        handler = self.make_handler()
        handler.config._yellow_coins_cache = (111111, time.time())
        reads = {'count': 0}

        def fake_ocr(image):
            reads['count'] += 1
            return 287441

        with patch.object(os_status_module, 'OCR_SHOP_YELLOW_COINS') as fake_digit, \
                patch.object(os_status_module, 'LogRes'):
            fake_digit.ocr = fake_ocr
            # 模拟 os_shop_buy 购买后的失效动作
            handler.config._yellow_coins_cache = None
            v = handler.get_yellow_coins()

        self.assertEqual(v, 287441, '缓存被清除后应重新 OCR')
        self.assertGreaterEqual(reads['count'], 2)

    def test_degraded_value_not_cached(self):
        # OCR 失败走降级旧值时，不得把降级值当成新鲜读数写进 TTL 缓存
        from module.os_handler import os_status as os_status_module

        handler = self.make_handler()
        handler._last_yellow_coins = 999999
        reads = {'count': 0}

        def fake_ocr(image):
            reads['count'] += 1
            return 0  # 模拟遮挡/未加载，OCR 一直返回 0

        with patch.object(os_status_module, 'OCR_SHOP_YELLOW_COINS') as fake_digit, \
                patch.object(os_status_module, 'LogRes'):
            fake_digit.ocr = fake_ocr
            v = handler.get_yellow_coins()

        self.assertEqual(v, 999999, 'OCR 失败时应降级到上次已知值')
        self.assertIsNone(getattr(handler.config, '_yellow_coins_cache', None),
                          '降级值不得写入 TTL 缓存')


if __name__ == '__main__':
    unittest.main()
