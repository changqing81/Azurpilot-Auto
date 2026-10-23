"""军火商外观箱槽位计数、成交确认和 GUI 配置的离线回归测试。"""

import json
import re
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import numpy as np

from module.base.timer import Timer
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_3
from module.config.config_updater import ConfigUpdater
from module.config.utils import parse_value
from module.device.device import Device
from module.shop.assets import SHOP_BUY_CONFIRM
from module.shop.base import ShopItem_250814
from module.shop.clerk import ShopClerk
from module.shop.shop_general import GeneralShop_250814
from module.shop.skin_box import DEFAULT_SKIN_BOX_LIMITS, SkinBoxLedger
from module.shop.ui import ShopUI
from module.ui.assets import SHOP_BACK_ARROW
from module.webui.utils import parse_pin_value


class SkinBoxLedgerTests(unittest.TestCase):
    """账本以固定槽位计数，交易重复回调不会重复入账。"""

    def test_three_identical_boxes_are_three_kinds(self):
        ledger = SkinBoxLedger('1.1.1')
        for slot in range(3):
            self.assertTrue(ledger.begin(slot))
            self.assertFalse(ledger.begin(slot))
            self.assertTrue(ledger.confirm(slot))
            self.assertFalse(ledger.confirm(slot))
        self.assertEqual(ledger.counts, {0: 1, 1: 1, 2: 1})
        self.assertEqual((ledger.kinds, ledger.total), (3, 3))
        self.assertEqual(ledger.finished, {0, 1, 2})

    def test_independent_targets_and_retries(self):
        ledger = SkinBoxLedger('10.5')
        ledger.begin(0)
        ledger.cancel(0)
        self.assertEqual(ledger.total, 0)
        for slot, count in ((0, 10), (1, 5)):
            for _ in range(count):
                self.assertTrue(ledger.begin(slot))
                self.assertTrue(ledger.confirm(slot))
            self.assertFalse(ledger.begin(slot))
        self.assertEqual((ledger.kinds, ledger.total), (2, 15))
        self.assertFalse(ledger.can_buy(2))

    def test_reset_and_early_soldout(self):
        ledger = SkinBoxLedger('10')
        ledger.begin(0)
        ledger.confirm(0, sold_out=True)
        self.assertFalse(ledger.can_buy(0))
        self.assertEqual(ledger.total, 1)
        ledger.reset()
        self.assertTrue(ledger.can_buy(0))
        self.assertEqual(ledger.total, 0)

    def test_ten_slots_zero_and_invalid_input(self):
        self.assertEqual(SkinBoxLedger(DEFAULT_SKIN_BOX_LIMITS).limits, (1,) * 10)
        self.assertEqual(SkinBoxLedger('1.2.3.4.5').limits, (1, 2, 3, 4, 5, 0, 0, 0, 0, 0))
        self.assertFalse(SkinBoxLedger('0.1').can_buy(0))
        for value in ('', '-1', '1..2', '1.2.', '.1', '1,2', '1\n', '1.' * 10 + '1', 1.10, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SkinBoxLedger(value)


class SkinBoxShopTests(unittest.TestCase):
    """使用真实军火商类，只替换截图和设备边界，并校验框架方法签名。"""

    def setUp(self):
        self.runner = GeneralShop_250814.__new__(GeneralShop_250814)
        self.runner.config = SimpleNamespace(
            GeneralShop_BuySkinBox=True, GeneralShop_SkinBoxBuyCounts='1.1.1.1.1.1.1.1.1.1',
            GeneralShop_Filter='', GeneralShop_ConsumeCoins=0, GeneralShop_UseGems=False,
            GeneralShop_OverflowCoins=0, GeneralShop_Refresh=False,
        )
        self.runner.device = SimpleNamespace(
            image=np.full((720, 1280, 3), 255, dtype=np.uint8),
            click=create_autospec(Device.click),
        )
        # 显式绑定设备 self，避免裸 Mock 掩盖 click 的参数错误。
        click = self.runner.device.click
        self.runner.device.click = lambda button: click(self.runner.device, button)
        self.runner._currency = 1000000
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def mock_method(self, name, **kwargs):
        """所有框架替身均保留真实签名。"""
        return self.stack.enter_context(patch.object(GeneralShop_250814, name, autospec=True, **kwargs))

    def item(self, slot, name='1', cost='Coins', price=7000, valid=True):
        """固定图像创建真实商品对象，名称可变化但位置身份不变。"""
        item = ShopItem_250814(self.runner.device.image, self.runner.shop_grid.buttons[slot])
        item.name, item.cost, item.price, item.is_valid = name, cost, price, valid
        item.group, item.sub_genre, item.tier = 'box', None, 't3'
        return item

    def test_selection_isolates_same_name_and_keeps_physical_slots(self):
        boxes = [self.item(slot, name='同名外观箱') for slot in (0, 1, 9)]
        self.assertIs(self.runner.shop_get_item_to_buy(boxes), boxes[0])
        self.runner.skin_box_ledger.begin(0)
        self.runner.skin_box_ledger.confirm(0)
        boxes[0].name = '999'
        self.assertIs(self.runner.shop_get_item_to_buy(boxes), boxes[1])
        self.assertEqual(self.runner.skin_box_slot(boxes[2]), 9)
        self.assertIsNone(self.runner.shop_get_item_to_buy([self.item(3, valid=False)]))

    def test_limits_cannot_be_bypassed_by_overflow_or_filter(self):
        self.runner.config.GeneralShop_ConsumeCoins = 1
        self.runner.config.GeneralShop_Filter = 'BoxT3'
        self.runner.config.GeneralShop_SkinBoxBuyCounts = '0'
        self.assertIsNone(self.runner.shop_get_item_to_buy([self.item(0)]))
        self.runner.config.GeneralShop_BuySkinBox = False
        self.assertIsNone(self.runner.shop_get_item_to_buy([self.item(0)]))
        # 普通商品继续走原来的溢出购买路径。
        normal = self.item(1, name='BoxT3', price=5000)
        self.assertIs(self.runner.shop_get_item_to_buy([normal]), normal)

    def test_currency_and_price_guard(self):
        self.runner._currency = 6999
        self.assertIsNone(self.runner.shop_get_item_to_buy([self.item(0)]))
        self.assertIsNone(self.runner.shop_get_item_to_buy([self.item(0, cost='Gems')]))
        self.assertIsNone(self.runner.shop_get_item_to_buy([self.item(0, price=700)]))

    def simulate_purchase(self, frames, sold_out=False, balance=1000000):
        """逐帧驱动真实状态循环，包括持续奖励弹窗和失败信息条。"""
        state = {'frame': 'shop'}

        def loop(runner, skip_first=True, timeout=None):
            for frame in frames:
                state['frame'] = frame
                yield runner.device.image

        def appear(runner, button, offset=0, interval=0, **kwargs):
            return ((button is SHOP_BACK_ARROW and state['frame'] in ('shop', 'failure'))
                    or (button is GET_ITEMS_1 and state['frame'] == 'reward')
                    or (button is GET_ITEMS_3 and state['frame'] == 'reward3'))

        def confirm(runner, button, **kwargs):
            return button is SHOP_BUY_CONFIRM and state['frame'] == 'confirm'

        self.mock_method('loop', side_effect=loop)
        self.mock_method('appear', side_effect=appear)
        self.mock_method('appear_then_click', side_effect=confirm)
        self.mock_method('shop_interval_clear')
        self.mock_method('shop_obstruct_handle', return_value=True)
        self.mock_method('info_bar_count', side_effect=lambda runner: int(state['frame'] == 'failure'))
        self.mock_method('status_get_gold_coins', return_value=balance)
        self.mock_method('shop_items', return_value=SimpleNamespace(
            item_class=lambda image, button: SimpleNamespace(is_valid=not sold_out)))
        self.stack.enter_context(patch.object(Timer, 'reached', autospec=True, return_value=True))
        return self.runner.shop_buy_execute(self.item(0))

    def test_reward_multiple_frames_counts_once_and_stops_at_soldout(self):
        self.runner.config.GeneralShop_SkinBoxBuyCounts = '10'
        self.assertTrue(self.simulate_purchase(['shop', 'confirm', 'reward', 'reward', 'shop'], sold_out=True))
        self.assertEqual(self.runner.skin_box_ledger.counts, {0: 1})
        self.assertFalse(self.runner.skin_box_ledger.can_buy(0))

    def test_failed_purchase_is_retryable_and_not_counted(self):
        self.assertFalse(self.simulate_purchase(['shop', 'confirm', 'failure']))
        self.assertEqual(self.runner.skin_box_ledger.counts, {})
        self.assertTrue(self.runner.skin_box_ledger.can_buy(0))

    def test_uncertain_network_result_blocks_repeated_purchase(self):
        self.assertFalse(self.simulate_purchase(['shop', 'confirm', 'shop', 'shop']))
        self.assertEqual(self.runner.skin_box_ledger.counts, {})
        self.assertEqual(self.runner.skin_box_ledger.pending, 0)
        self.assertFalse(self.runner.skin_box_ledger.can_buy(0))

    def test_balance_change_without_reward_is_not_counted_or_retried(self):
        self.assertFalse(self.simulate_purchase(['shop', 'confirm', 'failure'], balance=993000))
        self.assertEqual(self.runner.skin_box_ledger.total, 0)
        self.assertEqual(self.runner.skin_box_ledger.pending, 0)

    def test_preexisting_reward_does_not_count(self):
        self.assertFalse(self.simulate_purchase(['reward', 'shop', 'confirm', 'failure']))
        self.assertEqual(self.runner.skin_box_ledger.total, 0)

    def test_confirm_is_not_clicked_twice_during_network_delay(self):
        self.assertTrue(self.simulate_purchase(['shop', 'confirm', 'confirm', 'reward3', 'shop']))
        self.assertEqual(self.runner.appear_then_click.call_count, 1)
        # 持续显示的购买确认框不能被通用弹窗处理器再次点击。
        self.runner.shop_obstruct_handle.assert_not_called()

    def test_targets_larger_than_twelve_finish_independently(self):
        self.runner.config.GeneralShop_SkinBoxBuyCounts = '10.5'
        items = [self.item(0), self.item(1)]
        self.mock_method('shop_get_items', return_value=items)
        self.mock_method('shop_currency', return_value=1000000)

        def execute(runner, item, skip_first_screenshot=True):
            # 模拟每笔真实成交回调，主循环和筛选仍执行真实代码。
            slot = runner.skin_box_slot(item)
            return runner.skin_box_ledger.begin(slot) and runner.skin_box_ledger.confirm(slot)

        execute_mock = self.mock_method('shop_buy_execute', side_effect=execute)
        self.assertTrue(self.runner.shop_buy())
        self.assertEqual(execute_mock.call_count, 15)
        self.assertEqual(self.runner.skin_box_ledger.counts, {0: 10, 1: 5})
        self.assertEqual(self.runner.skin_box_ledger.kinds, 2)

    def test_empty_filter_can_run_skin_box_purchase(self):
        buy = self.mock_method('shop_buy', return_value=True)
        self.mock_method('_meowfficer_overflow_buy')
        self.runner.run()
        buy.assert_called_once_with(self.runner)

    def test_non_skin_item_keeps_original_purchase_execution(self):
        with patch.object(ShopClerk, 'shop_buy_execute', autospec=True) as original:
            item = self.item(0, price=5000)
            self.runner.shop_buy_execute(item, skip_first_screenshot=False)
            original.assert_called_once_with(self.runner, item, skip_first_screenshot=False)

    def test_refresh_requires_changed_shelf_and_resets_once(self):
        ledger = self.runner.skin_box_ledger
        ledger.begin(0)
        ledger.confirm(0)
        before = [(False, np.zeros((8, 8, 3)))]
        after = [(True, np.full((8, 8, 3), 255))]
        self.mock_method('_skin_box_refresh_view', side_effect=[before, after, after])
        self.mock_method('loop', return_value=iter([None, None]))
        self.mock_method('appear', return_value=True)
        with patch.object(ShopUI, 'shop_refresh', autospec=True, return_value=True):
            self.assertTrue(self.runner.shop_refresh())
        self.assertEqual(ledger.counts, {})
        self.assertTrue(ledger.can_buy(0))

    def test_failed_or_unchanged_refresh_preserves_progress(self):
        ledger = self.runner.skin_box_ledger
        ledger.begin(0)
        ledger.confirm(0)
        view = [(True, np.zeros((8, 8, 3)))]
        self.mock_method('_skin_box_refresh_view', return_value=view)
        self.mock_method('loop', side_effect=lambda *args, **kwargs: iter([None, None]))
        self.mock_method('appear', return_value=True)
        for result in (False, True):
            with patch.object(ShopUI, 'shop_refresh', autospec=True, return_value=result):
                self.assertFalse(self.runner.shop_refresh())
            self.assertEqual(ledger.counts, {0: 1})


class SkinBoxConfigTests(unittest.TestCase):
    """验证点号数量从 PyWebIO 保存到配置合并后仍是完整字符串。"""

    def test_gui_and_runtime_preserve_trailing_zero(self):
        args = json.loads(Path('module/config/argument/args.json').read_text(encoding='utf-8'))
        data = args['ShopFrequent']['GeneralShop']['SkinBoxBuyCounts']
        for value in ('1.10', '10.0', '10.5', '0', '1.2.3.4.5', DEFAULT_SKIN_BOX_LIMITS):
            with self.subTest(value=value):
                saved = parse_pin_value(value, valuetype=data['valuetype'], widget_type=data['type'])
                self.assertEqual(parse_value(saved, data), value)
                self.assertIsNotNone(re.fullmatch(data['validate'], value))
        updated = ConfigUpdater().config_update({
            'ShopFrequent': {'GeneralShop': {'SkinBoxBuyCounts': '1.10'}}})
        self.assertEqual(updated['ShopFrequent']['GeneralShop']['SkinBoxBuyCounts'], '1.10')

    def test_translations_have_no_placeholder(self):
        for lang in ('zh-CN', 'en-US', 'ja-JP', 'zh-TW'):
            content = json.loads(Path(f'module/config/i18n/{lang}.json').read_text(encoding='utf-8'))
            for text in content['GeneralShop']['SkinBoxBuyCounts'].values():
                self.assertNotIn('GeneralShop.SkinBoxBuyCounts.', text)

    def test_other_numeric_and_checkbox_values_keep_conversion(self):
        self.assertEqual(parse_value('10.5', {'value': 0.0}), 10.5)
        self.assertEqual(parse_value('10', {'value': 0}), 10)
        self.assertFalse(parse_value([], {'type': 'checkbox'}))


if __name__ == '__main__':
    unittest.main()
