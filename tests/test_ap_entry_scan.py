"""入场静默识别（进海域后台读行动力 + 黄币并写 AP 账本）的离线回归，不连接游戏。

覆盖：AP 账本开关关闭时的完全静默（零 OCR / 零后台任务 / 零写盘）、
入场触发与开战前取回、未完成/空值/异常的本轮作废、连续两次入场互不污染、
本轮先到先得、收尾兜底补记与跳过、明石商店同步读数与 CoinTaskMixin 覆写链路、
侵蚀1 与短猫相接的挂钩顺序。

桩边界：config / device 用 SimpleNamespace，后台执行器用可控假执行器
（同步完成 / 永不完成 / 手动执行 / 预置脚本），OCR 用 autospec 打桩
（不加载模型）。config_name 指向不存在的配置文件，保证用例不依赖本机 config。
"""

import unittest
from concurrent.futures import Future
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import numpy as np

from module.os_handler import action_point as ap_module
from module.os_handler.action_point import ActionPointHandler
from module.os_handler.action_point_ledger import (
    SOURCE_ENTRY,
    SOURCE_ENTRY_FALLBACK,
    SOURCE_SHOP,
)

FRAME = np.zeros((720, 1280, 3), dtype=np.uint8)


def coin_ocr():
    """黄币 OCR 对象（定义在 os_status，识别逻辑按需从 action_point 懒导入）。"""
    from module.os_handler.os_status import OCR_SHOP_YELLOW_COINS

    return OCR_SHOP_YELLOW_COINS


class InlineExecutor:
    """submit 立即执行并返回已完成 future：等价于「后台任务已完成」。"""

    def __init__(self):
        self.calls = []

    def submit(self, func, *args, **kwargs):
        self.calls.append((func, args))
        future = Future()
        try:
            future.set_result(func(*args, **kwargs))
        except BaseException as e:
            future.set_exception(e)
        return future


class PendingExecutor:
    """submit 返回永不完成的 future：模拟「识别没赶在进入战斗前完成」。"""

    def __init__(self):
        self.calls = []

    def submit(self, func, *args, **kwargs):
        self.calls.append((func, args))
        return Future()


class ManualExecutor:
    """提交后不执行，等 run() 才在本线程执行：模拟主流程先继续、后台稍后完成。"""

    def __init__(self):
        self.calls = []
        self.pending = []

    def submit(self, func, *args, **kwargs):
        future = Future()
        self.calls.append((func, args))
        self.pending.append((future, func, args))
        return future

    def run(self):
        for future, func, args in self.pending:
            if future.done():
                continue
            try:
                future.set_result(func(*args))
            except BaseException as e:
                future.set_exception(e)


class ScriptedExecutor:
    """按调用顺序返回预置结果：None = 该次任务永不完成，异常对象 = 任务失败。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def submit(self, func, *args, **kwargs):
        self.calls.append((func, args))
        future = Future()
        spec = self.results.pop(0)
        if spec is None:
            return future
        if isinstance(spec, BaseException):
            future.set_exception(spec)
        else:
            future.set_result(spec)
        return future


class EntryScanTestCase(unittest.TestCase):
    """handler 级用例基类：SimpleNamespace 桩 + 可控后台执行器。"""

    def make_handler(self, enabled=True, executor=None, in_map=True):
        from types import SimpleNamespace

        handler = ActionPointHandler.__new__(ActionPointHandler)
        handler.config = SimpleNamespace(
            OpsiGeneral_ActionPointLedgerEnabled=enabled,
            config_name='ap_entry_scan_test_nonexistent',
            data={'OpsiScheduling': {'Storage': {'Storage': {}}}},
            modified={},
            save=MagicMock(),
        )
        handler.device = SimpleNamespace(image=FRAME)
        handler.is_in_map = lambda: in_map
        # 测试里关掉等新帧重试，避免无效读数真的等满超时
        handler.AP_ENTRY_SCAN_REFIRE_TIMEOUT = 0
        executor = executor if executor is not None else InlineExecutor()
        self.enterContext(patch.object(ap_module, 'async_executor', executor))
        return handler, executor


class TestSwitchOff(EntryScanTestCase):
    """AP 账本开关关闭：完全退回原有行为，零额外 OCR、零写盘、零后台任务。"""

    def test_all_entry_scan_calls_are_noops(self):
        handler, executor = self.make_handler(enabled=False)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True) as ap_ocr, \
                patch.object(coin_ocr(), 'ocr', autospec=True) as coin_read:
            handler.ap_entry_scan_start()
            self.assertFalse(handler.ap_entry_scan_commit_before_battle())
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
            self.assertFalse(handler.ap_entry_scan_shop_sync())
        ap_ocr.assert_not_called()
        coin_read.assert_not_called()
        self.assertEqual(executor.calls, [])
        self.assertIsNone(handler._get_ap_ledger().current)
        self.assertEqual(handler.config.modified, {})
        self.assertFalse(handler.config.save.called)


class TestEntryScanRound(EntryScanTestCase):
    """入场触发 → 开战前取回：成功写入 / 作废各分支。"""

    def test_records_ap_and_coins_before_battle(self):
        handler, executor = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', autospec=True, return_value=233542):
            handler.ap_entry_scan_start()
            # 触发阶段只提交后台任务，不写账本（主流程不等结果）
            self.assertEqual(len(executor.calls), 1)
            self.assertIsNone(handler._get_ap_ledger().current)
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 157)
        self.assertEqual(ledger.yellow_coins, 233542)
        self.assertEqual(ledger.source, SOURCE_ENTRY)
        # 与顶栏读数同口径：刷新本轮行动力属性与总览显示
        self.assertEqual(handler._action_point_current, 157)
        self.assertEqual(handler.config.modified['Dashboard.ActionPoint.Value'], 157)
        self.assertTrue(handler.config.save.called)

    def test_result_consumed_once(self):
        handler, _ = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', autospec=True, return_value=1):
            handler.ap_entry_scan_start()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
            # 结果只消费一次：重复取回不再处理，也不重复计数
            self.assertFalse(handler.ap_entry_scan_commit_before_battle())

    def test_pending_scan_is_voided(self):
        # 识别没在进入战斗前完成 → 本轮作废，不写账本
        handler, _ = self.make_handler(executor=PendingExecutor())
        handler.ap_entry_scan_start()
        self.assertFalse(handler.ap_entry_scan_commit_before_battle())
        self.assertIsNone(handler._get_ap_ledger().current)
        self.assertEqual(handler.config.modified, {})

    def test_ocr_failure_is_voided(self):
        handler, _ = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True,
                          side_effect=ValueError('boom')):
            handler.ap_entry_scan_start()
            self.assertFalse(handler.ap_entry_scan_commit_before_battle())
        self.assertIsNone(handler._get_ap_ledger().current)

    def test_empty_reading_is_voided(self):
        # 顶栏尚未渲染：OCR 返回 -1 哨兵 → 本轮作废
        handler, _ = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=-1):
            handler.ap_entry_scan_start()
            self.assertFalse(handler.ap_entry_scan_commit_before_battle())
        self.assertIsNone(handler._get_ap_ledger().current)

    def test_retry_reads_next_frame(self):
        # 首帧顶栏没渲染出来，主流程换了一帧后重试成功：读数来自新帧
        executor = ManualExecutor()
        handler, _ = self.make_handler(executor=executor)
        first = np.zeros((720, 1280, 3), dtype=np.uint8)
        second = np.zeros((720, 1280, 3), dtype=np.uint8)
        second[0, 0, 0] = 9
        handler.device.image = first
        handler.AP_ENTRY_SCAN_REFIRE_TIMEOUT = 1.5

        def read(image, direct_ocr=False):
            return 158 if int(image[0, 0, 0]) == 9 else -1

        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, side_effect=read), \
                patch.object(coin_ocr(), 'ocr', autospec=True, return_value=233542):
            handler.ap_entry_scan_start()
            handler.device.image = second  # 主流程继续截图，后台此时才跑 OCR
            executor.run()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        self.assertEqual(handler._get_ap_ledger().current, 158)

    def test_consecutive_entries_are_independent(self):
        # 短猫相接连续快速进出海域：第一次的作废结果不污染第二次
        executor = ScriptedExecutor([None, (158, 233542)])
        handler, _ = self.make_handler(executor=executor)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=158), \
                patch.object(coin_ocr(), 'ocr', autospec=True, return_value=233542):
            handler.ap_entry_scan_start()
            self.assertFalse(handler.ap_entry_scan_commit_before_battle())  # 第一次作废
            handler.ap_entry_scan_start()  # 第二次入场独立触发
            self.assertEqual(len(executor.calls), 2)
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 158)
        self.assertEqual(ledger.source, SOURCE_ENTRY)


class TestFallback(EntryScanTestCase):
    """收尾兜底识别：本轮未记录才补记。"""

    def test_records_when_entry_voided(self):
        handler, _ = self.make_handler(executor=PendingExecutor())
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157):
            handler.ap_entry_scan_start()
            self.assertFalse(handler.ap_entry_scan_commit_before_battle())
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=158), \
                patch.object(coin_ocr(), 'ocr', autospec=True, return_value=233542):
            self.assertTrue(handler.ap_entry_scan_fallback(reason='侵蚀1练级收尾'))
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 158)
        self.assertEqual(ledger.yellow_coins, 233542)
        self.assertEqual(ledger.source, SOURCE_ENTRY_FALLBACK)

    def test_skipped_when_round_recorded(self):
        handler, _ = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', autospec=True, return_value=1):
            handler.ap_entry_scan_start()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True) as ap_ocr:
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
        ap_ocr.assert_not_called()

    def test_skipped_outside_map(self):
        # 不在大世界地图上时不读顶栏：别的画面上的数字不能当读数
        handler, _ = self.make_handler(in_map=False)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True) as ap_ocr:
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
        ap_ocr.assert_not_called()

    def test_invalid_reading_not_written(self):
        handler, _ = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=-1):
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
        self.assertIsNone(handler._get_ap_ledger().current)

    def test_can_retry_after_failed_fallback(self):
        # 兜底失败不置位「本轮已记录」，之后仍可补记
        handler, _ = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=-1):
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=158), \
                patch.object(coin_ocr(), 'ocr', autospec=True, return_value=1):
            self.assertTrue(handler.ap_entry_scan_fallback(reason='收尾'))
        self.assertEqual(handler._get_ap_ledger().current, 158)


class TestAkashiShopSync(EntryScanTestCase):
    """明石商店：与黄币识别同刻读一次行动力，两项一并入账。"""

    def test_records_ap_with_shop_coins(self):
        handler, _ = self.make_handler()
        handler._shop_yellow_coins = 233542
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157):
            self.assertTrue(handler.ap_entry_scan_shop_sync())
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 157)
        self.assertEqual(ledger.yellow_coins, 233542)
        self.assertEqual(ledger.source, SOURCE_SHOP)

    def test_invalid_ap_not_written(self):
        handler, _ = self.make_handler()
        handler._shop_yellow_coins = 233542
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=-1):
            self.assertFalse(handler.ap_entry_scan_shop_sync())
        self.assertIsNone(handler._get_ap_ledger().current)

    def test_overwrites_round_record_with_fresh_reading(self):
        # 本轮入场已记录：商店读到的是更晚的新鲜值，允许覆盖（先到先得、不重复计数）
        handler, _ = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', autospec=True, return_value=1):
            handler.ap_entry_scan_start()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        handler._shop_yellow_coins = 233542
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=140):
            self.assertTrue(handler.ap_entry_scan_shop_sync())
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 140)
        self.assertEqual(ledger.yellow_coins, 233542)
        self.assertEqual(ledger.source, SOURCE_SHOP)
        # 覆盖之后本轮仍算已记录，收尾兜底不再补记
        self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))


class TestCoinTaskMixinShopHooks(unittest.TestCase):
    """CoinTaskMixin 的两个覆写：只有明石商店流程内才同步读行动力。"""

    def make_stub(self):
        from module.os.tasks.scheduling import CoinTaskMixin

        calls = []

        class ShopStub:
            def os_shop_get_coins(self):
                calls.append('coins')
                self._shop_yellow_coins = 233542
                return 'coins'

            def handle_akashi_supply_buy(self, grid):
                calls.append('akashi')
                self.os_shop_get_coins()
                return 'buy'

        class TaskStub(CoinTaskMixin, ShopStub):
            """把 CoinTaskMixin 摆在桩前面，等价于任务类里它在 MRO 中的位置。"""

            def ap_entry_scan_shop_sync(self, yellow_coins=None):
                calls.append(('sync', yellow_coins))
                return True

        return TaskStub(), calls

    def test_only_inside_akashi_shop(self):
        stub, calls = self.make_stub()
        # 港口商店等其它路径读黄币：不同步行动力
        self.assertEqual(stub.os_shop_get_coins(), 'coins')
        self.assertEqual(calls, ['coins'])
        # 明石商店购买流程：读黄币的同刻同步一次行动力，并复用刚读到的黄币
        self.assertEqual(stub.handle_akashi_supply_buy('grid'), 'buy')
        self.assertEqual(calls, ['coins', 'akashi', 'coins', ('sync', 233542)])
        # 出店后标记清除：之后读黄币不再同步
        self.assertNotIn('_ap_akashi_shop_active', stub.__dict__)
        stub.os_shop_get_coins()
        self.assertEqual(calls[-1], 'coins')

    def test_marker_cleared_when_shop_raises(self):
        stub, _ = self.make_stub()
        stub.os_shop_get_coins = lambda: (_ for _ in ()).throw(RuntimeError('boom'))
        with self.assertRaises(RuntimeError):
            stub.handle_akashi_supply_buy('grid')
        self.assertNotIn('_ap_akashi_shop_active', stub.__dict__)


class TestTaskWiring(unittest.TestCase):
    """两个任务的挂钩顺序：入场触发 → 开战前取回 → 战斗 → 收尾兜底。"""

    def test_hazard_leveling_order(self):
        from types import SimpleNamespace

        from module.os.tasks.hazard_leveling import OpsiHazard1Leveling

        calls = []
        task = OpsiHazard1Leveling.__new__(OpsiHazard1Leveling)
        task.config = SimpleNamespace(
            override=lambda *a, **k: None,
            is_task_enabled=lambda *a, **k: False,
            cross_get=lambda *a, **k: False,
            OpsiHazard1Leveling_MinimumActionPointReserve=200,
            OpsiHazard1Leveling_TargetZone=0,
            OpsiHazard1Leveling_RecordSeaMiles=False,
            OS_ACTION_POINT_PRESERVE=0,
            OpsiFleet_Fleet=1,
        )
        task._cl1_consume_next_search_ready = lambda: True
        task.need_action_point_popup = lambda **kwargs: False
        task.get_yellow_coins = lambda: 200000
        task.is_running_smart_scheduling_task = lambda: False
        task._cl1_resource_check = lambda *a: None
        task._cl1_ap_check = lambda: None
        task.check_and_notify_action_point_threshold = lambda: None
        task.zone = SimpleNamespace(zone_id=1, hazard_level=1)
        task.is_zone_name_hidden = True
        task.fleet_set = lambda *a: None
        task._record_ap_and_coins = lambda **kwargs: None
        task.is_in_map = lambda: True
        task._cl1_run_battle = lambda: calls.append('battle')

        with patch.object(OpsiHazard1Leveling, 'ap_entry_scan_start', autospec=True,
                          side_effect=lambda self: calls.append('start')), \
                patch.object(OpsiHazard1Leveling, 'ap_entry_scan_commit_before_battle', autospec=True,
                             side_effect=lambda self: calls.append('commit')), \
                patch.object(OpsiHazard1Leveling, 'ap_entry_scan_fallback', autospec=True,
                             side_effect=lambda self, reason='': calls.append('fallback')):
            task.run_hazard1_leveling_once()
        self.assertEqual(calls, ['start', 'commit', 'battle', 'fallback'])

    def test_meow_traditional_zone_order(self):
        from types import SimpleNamespace

        from module.os.tasks.meowfficer_farming import OpsiMeowfficerFarming

        calls = []
        task = OpsiMeowfficerFarming.__new__(OpsiMeowfficerFarming)
        task.config = SimpleNamespace(OpsiFleet_Fleet=1, check_task_switch=lambda: calls.append('switch'))
        task.globe_goto = lambda *a, **k: calls.append('goto')
        task.fleet_set = lambda *a: None
        task.meow_search_metrics_start = lambda: None
        task.meow_search_metrics_end = lambda: None
        task.run_strategic_search = lambda: calls.append('search')
        task.should_run_post_search_scan = lambda: False
        task._meow_debug_clip = lambda: nullcontext()
        task.handle_after_auto_search = lambda: None
        task._meow_round_wrap_up = lambda: calls.append('wrap_up')

        with patch.object(OpsiMeowfficerFarming, 'ap_entry_scan_start', autospec=True,
                          side_effect=lambda self: calls.append('start')), \
                patch.object(OpsiMeowfficerFarming, 'ap_entry_scan_commit_before_battle', autospec=True,
                             side_effect=lambda self: calls.append('commit')):
            task._meow_handle_traditional_zone(SimpleNamespace(zone_id=1))
        self.assertEqual(calls, ['goto', 'start', 'commit', 'search', 'wrap_up', 'switch'])

    def test_meow_wrap_up_records_akashi_and_falls_back(self):
        from module.os.tasks.meowfficer_farming import OpsiMeowfficerFarming

        calls = []
        task = OpsiMeowfficerFarming.__new__(OpsiMeowfficerFarming)
        with patch.object(OpsiMeowfficerFarming, '_meow_record_akashi_if_solved', autospec=True,
                          side_effect=lambda self: calls.append('akashi')), \
                patch.object(OpsiMeowfficerFarming, 'ap_entry_scan_fallback', autospec=True,
                             side_effect=lambda self, reason='': calls.append(('fallback', reason))):
            task._meow_round_wrap_up()
        self.assertEqual(calls, ['akashi', ('fallback', '短猫相接收尾')])


if __name__ == '__main__':
    unittest.main()
