"""大世界港口商店「月度行动力购买」的离线行为验证。

覆盖三件事：
1. 大世界商店任务（OpsiShop）随商店流程触发本月一次性港口行动力购买；
2. 购买决策：门禁、每月一次、中断续购、跨月保护；
3. 购买流程只挑行动力商品，正常走完即视为完成，不再全商店复扫（#1106）。
"""
import copy
import unittest
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.deep import deep_get, deep_set
from module.config.utils import DEFAULT_TIME
from module.exception import GameStuckError
from module.os.tasks.scheduling import CoinTaskMixin
from module.os.tasks.shop import (
    CONFIG_PATH_BUY_PORT_ACTION_POINT,
    STATE_KEY_ACTION_POINT_PURCHASE,
    OpsiShop,
    monthly_explore_complete,
)
from module.os_shop.selector import Selector
from module.os_shop.shop import OSShop


RESET = datetime(2026, 11, 1)


class Config:
    """最小配置替身：只实现购买流程用到的 cross_get / cross_set / save。"""

    def __init__(self):
        self.data = {
            'OpsiScheduling': {
                'Scheduler': {'Enable': True},
                'OpsiScheduling': {'UseSmartSchedulingOperationCoinsPreserve': True},
                'Storage': {'Storage': {}},
            },
            'OpsiShop': {
                'OpsiShop': {'BuyActionPoint': False},
                'Storage': {'Storage': {}},
            },
            'OpsiExplore': {
                'OpsiExplore': {'ExploreProgress': ''},
                'Scheduler': {'NextRun': DEFAULT_TIME},
            },
        }
        self.task = SimpleNamespace(command='OpsiShop')
        self.modified = {}
        self.OpsiShop_DisableBeforeDate = 0
        self.task_delay = Mock()
        self.task_stop = Mock()

    def cross_get(self, keys, default=None):
        return deep_get(self.data, keys, default)

    def cross_set(self, keys, value):
        deep_set(self.data, keys, copy.deepcopy(value))

    def save(self):
        for keys, value in self.modified.items():
            self.cross_set(keys, value)
        self.modified.clear()

    def is_task_enabled(self, task):
        return self.cross_get(f'{task}.Scheduler.Enable', False)


class PortActionPointPurchaseTests(unittest.TestCase):
    """大世界商店侧的月度港口行动力购买决策。"""

    def setUp(self):
        clock = patch('module.os.tasks.shop.get_os_next_reset', return_value=RESET)
        clock.start()
        self.addCleanup(clock.stop)
        self.runner = OpsiShop.__new__(OpsiShop)
        self.runner.config = Config()
        self.runner._read_disk_shop_state = Mock(return_value={})
        self.runner.perform_port_shop_purchase = Mock(return_value=True)

    def enable(self):
        self.runner.config.cross_set(CONFIG_PATH_BUY_PORT_ACTION_POINT, True)

    def mark_monthly_complete(self, next_run=None):
        self.runner.config.cross_set('OpsiExplore.OpsiExplore.ExploreProgress', '已完成百分之100.00')
        self.runner.config.cross_set('OpsiExplore.Scheduler.NextRun', next_run or RESET)

    def phase(self):
        state = self.runner._get_shop_state_value(STATE_KEY_ACTION_POINT_PURCHASE)
        return state['phase'] if isinstance(state, dict) else None

    def test_purchase_requires_monthly_exploration_complete(self):
        self.enable()
        self.assertFalse(self.runner.try_port_action_point_purchase())
        self.mark_monthly_complete()
        self.assertTrue(self.runner.try_port_action_point_purchase())

    def test_disabled_by_default(self):
        self.mark_monthly_complete()
        self.assertFalse(self.runner.try_port_action_point_purchase())
        self.runner.perform_port_shop_purchase.assert_not_called()

    def test_stale_monthly_100_percent_cannot_purchase(self):
        """上月遗留的 100% 不能授权本月购买：开荒任务已到期即视为本月未完成。"""
        self.enable()
        self.runner.config.cross_set('OpsiExplore.OpsiExplore.ExploreProgress', '已完成百分之100.00')
        self.runner.config.cross_set('OpsiExplore.Scheduler.NextRun', datetime(2026, 10, 1))
        self.assertFalse(monthly_explore_complete(self.runner.config))
        self.assertFalse(self.runner.try_port_action_point_purchase())

    def test_purchase_once_per_month_and_restarts_do_not_repeat(self):
        self.enable()
        self.mark_monthly_complete()
        self.assertTrue(self.runner.try_port_action_point_purchase())
        self.assertFalse(self.runner.try_port_action_point_purchase())
        self.runner.perform_port_shop_purchase.assert_called_once_with(action_point_only=True)
        restarted = OpsiShop.__new__(OpsiShop)
        restarted.config = self.runner.config
        restarted._read_disk_shop_state = Mock(return_value={})
        self.assertFalse(restarted.try_port_action_point_purchase())

    def test_interrupted_purchase_resumes_and_normal_return_marks_done(self):
        self.enable()
        self.mark_monthly_complete()
        self.runner.perform_port_shop_purchase.side_effect = [GameStuckError('购买中断'), True]
        with self.assertRaises(GameStuckError):
            self.runner.try_port_action_point_purchase()
        self.assertEqual(self.phase(), 'buying')
        self.assertTrue(self.runner.try_port_action_point_purchase())
        self.assertEqual(self.phase(), 'done')

    def test_legacy_false_stock_check_retry_counter_does_not_block_resume(self):
        self.enable()
        self.mark_monthly_complete()
        self.runner._set_shop_state_value(
            STATE_KEY_ACTION_POINT_PURCHASE,
            dict(reset=RESET.isoformat(), phase='buying', attempts=3),
        )
        self.assertTrue(self.runner.try_port_action_point_purchase())
        self.assertFalse(self.runner.try_port_action_point_purchase())
        self.assertEqual(self.runner.perform_port_shop_purchase.call_count, 1)
        self.assertEqual(self.phase(), 'done')

    def test_purchase_crossing_month_does_not_mark_old_month_done(self):
        self.enable()
        self.mark_monthly_complete()
        with patch('module.os.tasks.shop.get_os_next_reset',
                   side_effect=[RESET, RESET, datetime(2026, 12, 1)]):
            with self.assertRaises(GameStuckError):
                self.runner.try_port_action_point_purchase()
        self.assertEqual(self.runner._get_shop_state_value(STATE_KEY_ACTION_POINT_PURCHASE),
                         dict(reset=RESET.isoformat(), phase='buying'))

    def test_smart_scheduling_no_longer_owns_purchase(self):
        """行动力购买已从智能调度+剥离，不应再挂在 CoinTaskMixin 上。"""
        self.assertFalse(hasattr(CoinTaskMixin, '_try_scheduling_action_point_purchase'))


class ShopTaskWiringTests(unittest.TestCase):
    """os_shop 的接线：开关开启时，商店任务自己会去买行动力。"""

    def setUp(self):
        clock = patch('module.os.tasks.shop.current_time', return_value=datetime(2026, 10, 4))
        clock.start()
        self.addCleanup(clock.stop)
        self.runner = OpsiShop.__new__(OpsiShop)
        self.runner.config = Config()
        self.runner.try_port_action_point_purchase = Mock(return_value=True)
        self.runner.perform_port_shop_purchase = Mock(return_value=True)
        self.runner._os_shop_delay = Mock(return_value=datetime(2026, 11, 1))

    def test_shop_task_triggers_purchase(self):
        self.runner.os_shop()
        self.runner.try_port_action_point_purchase.assert_called_once_with()

    def test_purchase_happens_before_normal_supply_purchase(self):
        order = []
        self.runner.try_port_action_point_purchase = Mock(
            side_effect=lambda: order.append('action_point') or True)
        self.runner.perform_port_shop_purchase = Mock(
            side_effect=lambda *args, **kwargs: order.append('supply') or True)
        self.runner.os_shop()
        self.assertEqual(order, ['action_point', 'supply'])

    def test_normal_supply_purchase_still_runs_when_switch_off(self):
        self.runner.try_port_action_point_purchase = Mock(return_value=False)
        self.runner.os_shop()
        self.runner.perform_port_shop_purchase.assert_called_once_with()
        self.runner.config.task_delay.assert_called_once_with(target=datetime(2026, 11, 1))


class PortActionPointTests(unittest.TestCase):
    """港口商店侧的专购行为（只买行动力、不复扫、可续购）。"""

    def item(self, name='ActionPoint', count=5):
        return SimpleNamespace(name=name, count=count, total_count=5, price=100,
                               cost='YellowCoins', is_known_item=lambda: name != 'DefaultItem')

    def port_runner(self, appear=True):
        runner = OpsiShop.__new__(OpsiShop)
        runner.zone = SimpleNamespace(is_azur_port=True)
        runner.appear = Mock(return_value=appear)
        for name in ('port_enter', 'port_shop_enter', 'port_shop_quit', 'port_quit'):
            setattr(runner, name, Mock())
        return runner

    def test_selector_buys_only_action_point(self):
        runner = Selector()
        runner.config = SimpleNamespace(task=SimpleNamespace(command='OpsiShop'))
        runner._opsi_action_point_purchase = True
        ap, material, sold = self.item(), self.item('DevelopmentMaterialT1'), self.item(count=0)
        self.assertEqual(runner.items_filter_in_os_shop([ap, material, sold]), [ap])

    def test_action_point_purchase_can_use_reserved_coins(self):
        runner = OSShop.__new__(OSShop)
        runner._opsi_action_point_purchase = True
        runner._shop_yellow_coins = 12345
        self.assertEqual(runner.get_currency_coins(self.item()), 12345)

    def test_action_point_purchase_sets_entire_stock(self):
        runner = OSShop.__new__(OSShop)
        runner.config = SimpleNamespace()
        runner.device = SimpleNamespace(image=None)
        runner._opsi_action_point_purchase = True
        runner._shop_yellow_coins = 100000
        runner.ui_ensure_index = Mock()
        item = self.item(count=25)
        item.total_count = 25
        with patch('module.os_shop.shop.OCR_SHOP_AMOUNT.ocr', return_value=1):
            self.assertTrue(runner.shop_buy_amount_handler(item))
        self.assertEqual(runner.ui_ensure_index.call_args.args[0], 25)

    def test_purchase_does_not_rescan_locked_or_unrecognized_goods(self):
        runner = self.port_runner()
        runner.handle_port_supply_buy = Mock(return_value=True)
        runner.scan_all = Mock(return_value=[self.item('DefaultItem')])
        self.assertTrue(runner.perform_port_shop_purchase(action_point_only=True))
        runner.scan_all.assert_not_called()
        self.assertFalse(runner._opsi_action_point_purchase)

    def test_resumed_purchase_with_no_available_action_points_completes(self):
        runner = self.port_runner()
        runner.handle_port_supply_buy = Mock(return_value=False)
        runner.scan_all = Mock(return_value=[])
        self.assertTrue(runner.perform_port_shop_purchase(action_point_only=True))
        runner.scan_all.assert_not_called()
        self.assertFalse(runner._opsi_action_point_purchase)

    def test_normal_shop_result_is_preserved(self):
        for action_only, empty in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(action_only=action_only, empty=empty):
                runner = self.port_runner()
                runner.handle_port_supply_buy = Mock(return_value=not empty)
                runner.scan_all = Mock(side_effect=AssertionError('购买后不应全商店复扫'))
                self.assertEqual(
                    OpsiShop.perform_port_shop_purchase(runner, action_point_only=action_only),
                    action_only or not empty,
                )
                self.assertFalse(runner._opsi_action_point_purchase)

    def test_missing_shop_still_raises(self):
        runner = self.port_runner(appear=False)
        with self.assertRaises(GameStuckError):
            runner.perform_port_shop_purchase(action_point_only=True)
        self.assertFalse(runner._opsi_action_point_purchase)


if __name__ == '__main__':
    unittest.main()
