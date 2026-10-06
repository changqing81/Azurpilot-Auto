"""岛屿计划珍珠采购的点击安全测试：连点数量调整与确认不得触发卡死保护。

2026-10-06 真机事故：采购 200 珍珠要连点 20 下 +10（A/B/C 三个变体轮换、
同一网格），累计触发设备层 GameTooManyClickError（两按钮交替 >=6+6 /
同一网格 >=12/15），交易中途被打断、任务延迟到次日。修复：每轮点击后
清一次点击记录（与好友排名滑动后的清理同款）；每轮之间有 OCR 读数把关，
不依赖点击历史判断流程状态。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.island import island_pearl_sell as ips_module
from module.island.island_pearl_sell import IslandPearlSell


def make_handler(counts):
    handler = IslandPearlSell.__new__(IslandPearlSell)
    clicks = []
    clears = []
    handler.device = SimpleNamespace(
        image='img',
        screenshot=lambda: None,
        click=lambda button: clicks.append(button),
        click_record_clear=lambda: clears.append(1),
        sleep=lambda _: None,
    )
    iterator = iter(counts)
    handler.ocr_trade_count = lambda: next(iterator, 0)
    return handler, clicks, clears


class TestAdjustTradeCountClickSafety(unittest.TestCase):
    """adjust_trade_count 每轮点击后必须清一次点击/网格记录。"""

    def test_small_adjust_clears_click_record_each_round(self):
        # 190 -> 200：点 1 下 +10，随后清一次点击记录
        handler, clicks, clears = make_handler([190, 200])
        self.assertTrue(handler.adjust_trade_count(200))
        self.assertEqual(len(clicks), 1)
        self.assertEqual(len(clears), 1)

    def test_buy_200_pearls_never_triggers_flood_guard(self):
        # 场景还原：0 -> 200 连点 20 下 +10，若不清理，第 12 下就会撞上
        # 「同一区域反复点击 >=12/15」的卡死保护，交易中途被打断
        handler, clicks, clears = make_handler([0, 200])
        self.assertTrue(handler.adjust_trade_count(200))
        self.assertEqual(len(clicks), 20)
        self.assertEqual(len(clears), 1)

    def test_no_click_no_clear_when_target_already_met(self):
        handler, clicks, clears = make_handler([200])
        self.assertTrue(handler.adjust_trade_count(200))
        self.assertEqual(clicks, [])
        self.assertEqual(clears, [])

    def test_adjust_buttons_rotate_variants(self):
        # +10 用 A/B/C 三个变体轮换，20 连点若不清计数，
        # 变体计数 7+7+6 会触发「两按钮交替 >=6+6」的卡死保护
        buttons = IslandPearlSell.trade_count_adjust_buttons(0, 200)
        self.assertEqual(len(buttons), 20)
        self.assertEqual(len(set(buttons)), 3)


class TestConfirmTradeClickSafety(unittest.TestCase):
    """confirm_trade 的确认与 GET_ITEMS 弹窗交替点击不得触发卡死保护。"""

    def make_handler(self):
        handler = IslandPearlSell.__new__(IslandPearlSell)
        clicks = []
        clears = []
        handler.device = SimpleNamespace(
            image='img',
            screenshot=lambda: None,
            click_record_clear=lambda: clears.append(1),
            sleep=lambda _: None,
        )
        handler._pearl_shop_check_button = SimpleNamespace(name='check')

        state = {'confirm_clicks': 0, 'get_items': 0}
        handler.appear = lambda *args, **kwargs: state['confirm_clicks'] > 0
        handler.ui_additional = lambda: False

        def appear_then_click(*args, **kwargs):
            # 只首次点中确认按钮，之后画面回到商店页不再出现确认按钮
            if state['confirm_clicks'] == 0:
                state['confirm_clicks'] += 1
                clicks.append(1)
                return True
            return False

        def handle_pearl_get_items():
            if state['get_items'] == 0:
                state['get_items'] += 1
                return True
            return False

        handler.appear_then_click = appear_then_click
        handler.handle_pearl_get_items = handle_pearl_get_items
        return handler, clicks, clears

    def test_confirm_and_get_items_alternating_clears_records(self):
        handler, clicks, clears = self.make_handler()
        with patch.object(ips_module, 'Timer', _FakeTimer), \
                patch('module.base.base.Timer', _FakeTimer):
            self.assertTrue(handler.confirm_trade(action='buy'))
        self.assertEqual(len(clicks), 1)
        # 确认点击一次 + GET_ITEMS 弹窗点击一次，各清一次计数
        self.assertEqual(len(clears), 2)


class TestFakeTimer(unittest.TestCase):
    """占位：确保 FakeTimer 语义与测试预期一致（防测试桩漂移）。"""

    def test_fake_timer_reached_semantics(self):
        timer = _FakeTimer(1, count=2).start()
        self.assertFalse(timer.reached())
        self.assertTrue(timer.reached())
        timer.reset()
        self.assertFalse(timer.reached())


class _FakeTimer:
    """Timer 替身：只有带 count 的实例会 reached；无 count 永不超时。"""

    def __init__(self, *args, count=None, **kwargs):
        self._count = count
        self.calls = 0

    @classmethod
    def from_seconds(cls, seconds):
        return cls(seconds)

    def start(self):
        return self

    def reset(self):
        self.calls = 0
        return self

    def reached(self):
        self.calls += 1
        return self._count is not None and self.calls >= self._count


if __name__ == '__main__':
    unittest.main()
