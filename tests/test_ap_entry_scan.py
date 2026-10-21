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
from datetime import timedelta
from unittest.mock import MagicMock, patch

import numpy as np

from module.config.time_source import now as current_time
from module.os_handler import action_point as ap_module
from module.os_handler.action_point import ActionPointHandler
from module.os_handler.action_point_ledger import (
    SOURCE_AUTO_SEARCH,
    SOURCE_ENTRY,
    SOURCE_ENTRY_FALLBACK,
    SOURCE_SHOP,
)

FRAME = np.zeros((720, 1280, 3), dtype=np.uint8)


def coin_ocr():
    """静默读数的黄币 OCR 对象（action_point 模块持有，关掉耗时日志）。"""
    return ap_module.SILENT_YELLOW_COIN_OCR


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

    def setUp(self):
        # 黄币 OCR 全类默认打桩（返回 0 = 非法读数）：用例不加载模型，
        # 需要具体黄币值的用例在自身块内再次 patch 覆盖
        self.enterContext(patch.object(coin_ocr(), 'ocr', return_value=0))

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
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True) as ap_ocr, \
                patch.object(coin_ocr(), 'ocr') as coin_read:
            handler.ap_entry_scan_start()
            handler.ap_entry_scan_tick()
            self.assertFalse(handler.ap_entry_scan_commit_before_battle())
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
            self.assertFalse(handler.ap_entry_scan_shop_sync())
        ap_ocr.assert_not_called()
        coin_read.assert_not_called()
        self.assertEqual(executor.calls, [])
        self.assertIsNone(handler._get_ap_ledger().current)
        self.assertEqual(handler.config.modified, {})
        self.assertFalse(handler.config.save.called)


class TestAutoSearchScan(EntryScanTestCase):
    """自动搜索（跑图）期间的周期读数：不停下跑图，账本跟着消耗走。"""

    def commit_entry(self, handler, executor):
        """走完入场读数：派发 → 后台完成 → 开战前取回。"""
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', return_value=233542):
            handler.ap_entry_scan_start()
            executor.run()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())

    def test_periodic_reading_committed_and_throttled(self):
        executor = ManualExecutor()
        handler, _ = self.make_handler(executor=executor)
        # 账本过期（超过新鲜窗口）才会触发跑图读数
        stale_at = current_time() - timedelta(seconds=handler.AP_LEDGER_FRESH_SECONDS + 1)
        handler._get_ap_ledger().observe(157, source=SOURCE_ENTRY, at=stale_at)
        handler.__dict__['_ap_entry_scan_periodic'] = True
        handler.__dict__['_ap_entry_scan_next_at'] = 0
        handler.ap_entry_scan_tick()
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(handler.__dict__['_ap_entry_scan_state']['source'], SOURCE_AUTO_SEARCH)
        # 结果回来：取回并写账本（来源 auto_search）
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=140), \
                patch.object(coin_ocr(), 'ocr', return_value=233542), \
                patch.object(ActionPointHandler, '_ap_entry_scan_frame_in_map', return_value=True):
            executor.run()
            handler.ap_entry_scan_tick()
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 140)
        self.assertEqual(ledger.yellow_coins, 233542)
        self.assertEqual(ledger.source, SOURCE_AUTO_SEARCH)
        # 账本刚写、且周期未到：不再派发新读数
        dispatched = len(executor.calls)
        handler.ap_entry_scan_tick()
        self.assertEqual(len(executor.calls), dispatched)

    def test_fresh_ledger_skips_periodic_read(self):
        # 账本刚被商店同步/入场识别刷新过：跑图期间不重复读数，直接继续跑图
        executor = ManualExecutor()
        handler, _ = self.make_handler(executor=executor)
        self.commit_entry(handler, executor)
        dispatched = len(executor.calls)
        handler.__dict__['_ap_entry_scan_next_at'] = 0
        handler.ap_entry_scan_tick()
        self.assertEqual(len(executor.calls), dispatched)

    def test_stale_ledger_triggers_periodic_read(self):
        executor = ManualExecutor()
        handler, _ = self.make_handler(executor=executor)
        stale_at = current_time() - timedelta(seconds=handler.AP_LEDGER_FRESH_SECONDS + 1)
        handler._get_ap_ledger().observe(157, source=SOURCE_ENTRY, at=stale_at)
        handler.__dict__['_ap_entry_scan_periodic'] = True
        handler.__dict__['_ap_entry_scan_next_at'] = 0
        handler.ap_entry_scan_tick()
        self.assertEqual(len(executor.calls), 1)

    def test_pending_scan_not_piled_up(self):
        # 上一次还没回来时不派发新的，避免排队堆积
        executor = ManualExecutor()
        handler, _ = self.make_handler(executor=executor)
        handler.ap_entry_scan_start()
        dispatched = len(executor.calls)
        handler.__dict__['_ap_entry_scan_next_at'] = 0
        handler.ap_entry_scan_tick()
        handler.__dict__['_ap_entry_scan_next_at'] = 0
        handler.ap_entry_scan_tick()
        self.assertEqual(len(executor.calls), dispatched)

    def test_non_map_frame_not_recorded(self):
        # 跑图期间画面会经过战斗/加载：帧不是海域地图就不采纳读数
        executor = ManualExecutor()
        handler, _ = self.make_handler(executor=executor)
        self.commit_entry(handler, executor)
        before = handler._get_ap_ledger().current
        handler.__dict__['_ap_entry_scan_next_at'] = 0
        with patch.object(ActionPointHandler, '_ap_entry_scan_frame_in_map', return_value=False), \
                patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=99):
            handler.ap_entry_scan_tick()
            executor.run()
            handler.ap_entry_scan_tick()
        self.assertEqual(handler._get_ap_ledger().current, before)

    def test_wrap_up_disarms_periodic_scan(self):
        executor = ManualExecutor()
        handler, _ = self.make_handler(executor=executor)
        handler.ap_entry_scan_start()
        self.assertTrue(handler.__dict__['_ap_entry_scan_periodic'])
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=-1):
            handler.ap_entry_scan_fallback(reason='收尾')
        self.assertFalse(handler.__dict__.get('_ap_entry_scan_periodic', False))
        dispatched = len(executor.calls)
        handler.__dict__['_ap_entry_scan_next_at'] = 0
        handler.ap_entry_scan_tick()
        self.assertEqual(len(executor.calls), dispatched)

    def test_handle_map_event_preserves_result_and_ticks(self):
        from module.os_handler.map_event import MapEventHandler

        handler, _ = self.make_handler()
        with patch.object(MapEventHandler, 'handle_map_event', autospec=True, return_value=True) as super_call, \
                patch.object(ActionPointHandler, 'ap_entry_scan_tick', autospec=True) as tick:
            self.assertTrue(handler.handle_map_event(drop='drop'))
        super_call.assert_called_once_with(handler, drop='drop')
        tick.assert_called_once_with(handler)


class TestEntryScanRound(EntryScanTestCase):
    """入场触发 → 开战前取回：成功写入 / 作废各分支。"""

    def test_records_ap_and_coins_before_battle(self):
        handler, executor = self.make_handler()
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', return_value=233542):
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

    def test_ledger_save_is_quiet(self):
        # 账本落盘只改内部键（账本 + 总览显示）：保存日志降 debug，不刷「保存配置」行
        handler, _ = self.make_handler()
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', return_value=1):
            handler.ap_entry_scan_start()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        handler.config.save.assert_called_with(quiet=True)

    def test_ledger_save_not_quiet_with_other_pending_keys(self):
        # 待写集合混入别的改动时照旧 INFO：不能把别人的保存日志一起压掉
        handler, _ = self.make_handler()
        handler.config.modified['Scheduler.NextRun'] = '2026-10-20 12:00:00'
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', return_value=1):
            handler.ap_entry_scan_start()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        handler.config.save.assert_called_with(quiet=False)

    def test_result_consumed_once(self):
        handler, _ = self.make_handler()
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', return_value=1):
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
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True,
                          side_effect=ValueError('boom')):
            handler.ap_entry_scan_start()
            self.assertFalse(handler.ap_entry_scan_commit_before_battle())
        self.assertIsNone(handler._get_ap_ledger().current)

    def test_empty_reading_is_voided(self):
        # 顶栏尚未渲染：OCR 返回 -1 哨兵 → 本轮作废
        handler, _ = self.make_handler()
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=-1):
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

        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, side_effect=read), \
                patch.object(coin_ocr(), 'ocr', return_value=233542):
            handler.ap_entry_scan_start()
            handler.device.image = second  # 主流程继续截图，后台此时才跑 OCR
            executor.run()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        self.assertEqual(handler._get_ap_ledger().current, 158)

    def test_consecutive_entries_are_independent(self):
        # 短猫相接连续快速进出海域：第一次的作废结果不污染第二次
        executor = ScriptedExecutor([None, (158, 233542)])
        handler, _ = self.make_handler(executor=executor)
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=158), \
                patch.object(coin_ocr(), 'ocr', return_value=233542):
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
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157):
            handler.ap_entry_scan_start()
            self.assertFalse(handler.ap_entry_scan_commit_before_battle())
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=158), \
                patch.object(coin_ocr(), 'ocr', return_value=233542):
            self.assertTrue(handler.ap_entry_scan_fallback(reason='侵蚀1练级收尾'))
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 158)
        self.assertEqual(ledger.yellow_coins, 233542)
        self.assertEqual(ledger.source, SOURCE_ENTRY_FALLBACK)

    def test_skipped_when_round_recorded(self):
        handler, _ = self.make_handler()
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', return_value=1):
            handler.ap_entry_scan_start()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True) as ap_ocr:
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
        ap_ocr.assert_not_called()

    def test_skipped_outside_map(self):
        # 不在大世界地图上时不读顶栏：别的画面上的数字不能当读数
        handler, _ = self.make_handler(in_map=False)
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True) as ap_ocr:
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
        ap_ocr.assert_not_called()

    def test_invalid_reading_not_written(self):
        handler, _ = self.make_handler()
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=-1):
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
        self.assertIsNone(handler._get_ap_ledger().current)

    def test_can_retry_after_failed_fallback(self):
        # 兜底失败不置位「本轮已记录」，之后仍可补记
        handler, _ = self.make_handler()
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=-1):
            self.assertFalse(handler.ap_entry_scan_fallback(reason='收尾'))
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=158), \
                patch.object(coin_ocr(), 'ocr', return_value=1):
            self.assertTrue(handler.ap_entry_scan_fallback(reason='收尾'))
        self.assertEqual(handler._get_ap_ledger().current, 158)


class TestInternalOnlyModified(unittest.TestCase):
    """总览显示值判据：只有 Dashboard.* 与调用方声明的内部键才算内部状态。"""

    def test_display_values_are_internal(self):
        from module.config.config import is_internal_only_modified

        self.assertTrue(is_internal_only_modified({'Dashboard.Oil.Value': 1}))
        self.assertTrue(is_internal_only_modified(
            {'Dashboard.YellowCoin.Value': 1, 'Dashboard.YellowCoin.Record': 2}
        ))
        self.assertTrue(is_internal_only_modified(
            {'Dashboard.ActionPoint.Value': 1, 'OpsiScheduling.Storage.Storage.ApLedgerState': {}},
            extra_keys=('OpsiScheduling.Storage.Storage.ApLedgerState',),
        ))

    def test_mixed_or_empty_is_not_internal(self):
        from module.config.config import is_internal_only_modified

        self.assertFalse(is_internal_only_modified({}))
        self.assertFalse(is_internal_only_modified({'Scheduler.NextRun': 'x'}))
        self.assertFalse(is_internal_only_modified(
            {'Dashboard.Oil.Value': 1, 'Scheduler.NextRun': 'x'}
        ))
        # 没有声明为内部键的状态键不算内部状态
        self.assertFalse(is_internal_only_modified(
            {'OpsiScheduling.Storage.Storage.ApLedgerState': {}}
        ))


class TestShopCoinsQuietSave(unittest.TestCase):
    """商店读黄币/紫币后的保存：只含总览显示值时静默，混入其它键照旧 INFO。"""

    def make_status(self, modified):
        from types import SimpleNamespace

        from module.os_handler.os_status import OSStatus

        status = OSStatus.__new__(OSStatus)
        status.config = SimpleNamespace(
            modified=dict(modified),
            save=MagicMock(),
            config_name='ap_entry_scan_test_nonexistent',
            task=SimpleNamespace(command='OpsiHazard1Leveling'),
        )
        status.get_yellow_coins = lambda use_cache=False: 87933
        status.get_purple_coins = lambda: 6357
        return status

    def test_display_only_save_is_quiet(self):
        status = self.make_status({'Dashboard.YellowCoin.Value': 87933})
        with patch('module.statistics.cl1_database.db') as cl1_db:
            status.os_shop_get_coins()
        cl1_db.add_coins_snapshot.assert_called_once()
        status.config.save.assert_called_with(quiet=True)

    def test_mixed_keys_save_stays_info(self):
        status = self.make_status({
            'Dashboard.YellowCoin.Value': 87933,
            'Scheduler.NextRun': '2026-10-20 12:00:00',
        })
        with patch('module.statistics.cl1_database.db'):
            status.os_shop_get_coins()
        status.config.save.assert_called_with(quiet=False)


class TestSilentOcrInstances(unittest.TestCase):
    """静默读数用独立 OCR 实例：不刷耗时日志，黄币沿用商店黄币取色参数。"""

    def test_silent_instances_do_not_log(self):
        self.assertFalse(ap_module.SILENT_ACTION_POINT_DIGIT.SHOW_LOG)
        self.assertFalse(ap_module.SILENT_YELLOW_COIN_OCR.SHOW_LOG)
        self.assertIsNot(ap_module.SILENT_ACTION_POINT_DIGIT, ap_module.MAP_ACTION_POINT_DIGIT)

    def test_shop_coin_instances_do_not_log(self):
        # 黄币/紫币读数按行动力同样的口径静默（os_status 里的商店凭证 OCR）
        from module.os_handler.os_status import (
            OCR_OS_SHOP_PURPLE_COINS,
            OCR_SHOP_PURPLE_COINS,
            OCR_SHOP_YELLOW_COINS,
        )

        self.assertFalse(OCR_SHOP_YELLOW_COINS.SHOW_LOG)
        self.assertFalse(OCR_SHOP_PURPLE_COINS.SHOW_LOG)
        self.assertFalse(OCR_OS_SHOP_PURPLE_COINS.SHOW_LOG)

    def test_silent_coin_ocr_keeps_shop_params(self):
        from module.os_handler.os_status import OCR_SHOP_YELLOW_COINS

        self.assertEqual(ap_module.SILENT_YELLOW_COIN_OCR.letter, OCR_SHOP_YELLOW_COINS.letter)
        self.assertEqual(ap_module.SILENT_YELLOW_COIN_OCR.threshold, OCR_SHOP_YELLOW_COINS.threshold)


class TestDecisionReadFreshness(EntryScanTestCase):
    """决策读的新鲜窗口：账本刚被刷新过就不再重复 OCR。"""

    def test_skipped_when_ledger_fresh(self):
        handler, _ = self.make_handler()
        handler._get_ap_ledger().observe(157, source=SOURCE_ENTRY, yellow_coins=233542)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True) as ap_ocr:
            self.assertEqual(handler.ap_observe_from_map_bar(), 157)
        ap_ocr.assert_not_called()

    def test_runs_when_ledger_stale(self):
        handler, _ = self.make_handler()
        stale_at = current_time() - timedelta(seconds=handler.AP_LEDGER_FRESH_SECONDS + 1)
        handler._get_ap_ledger().observe(157, source=SOURCE_ENTRY, at=stale_at)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=140) as ap_ocr:
            self.assertEqual(handler.ap_observe_from_map_bar(), 140)
        ap_ocr.assert_called_once()

    def test_explicit_image_ignores_freshness(self):
        # 调用方给了具体帧：按该帧读数，不受新鲜窗口约束
        handler, _ = self.make_handler()
        handler._get_ap_ledger().observe(157, source=SOURCE_ENTRY)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=140) as ap_ocr:
            self.assertEqual(handler.ap_observe_from_map_bar(image='frame'), 140)
        ap_ocr.assert_called_once()


class TestAkashiShopSync(EntryScanTestCase):
    """明石商店：与黄币识别同刻读一次行动力，两项一并入账。"""

    def test_records_ap_with_shop_coins(self):
        handler, _ = self.make_handler()
        handler._shop_yellow_coins = 233542
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157):
            self.assertTrue(handler.ap_entry_scan_shop_sync())
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 157)
        self.assertEqual(ledger.yellow_coins, 233542)
        self.assertEqual(ledger.source, SOURCE_SHOP)

    def test_invalid_ap_not_written(self):
        handler, _ = self.make_handler()
        handler._shop_yellow_coins = 233542
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=-1):
            self.assertFalse(handler.ap_entry_scan_shop_sync())
        self.assertIsNone(handler._get_ap_ledger().current)

    def test_overwrites_round_record_with_fresh_reading(self):
        # 本轮入场已记录：商店读到的是更晚的新鲜值，允许覆盖（先到先得、不重复计数）
        handler, _ = self.make_handler()
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=157), \
                patch.object(coin_ocr(), 'ocr', return_value=1):
            handler.ap_entry_scan_start()
            self.assertTrue(handler.ap_entry_scan_commit_before_battle())
        handler._shop_yellow_coins = 233542
        with patch.object(ap_module.SILENT_ACTION_POINT_DIGIT, 'ocr', autospec=True, return_value=140):
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
                for _ in range(getattr(self, 'purchases', 1)):
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

    def test_sync_once_per_shop_visit(self):
        # 一次逛店买多件：每买一件读一次黄币，但只同步一次行动力
        stub, calls = self.make_stub()
        stub.purchases = 4
        stub.handle_akashi_supply_buy('grid')
        self.assertEqual(calls.count(('sync', 233542)), 1)
        # 再次进店：重新同步一次
        stub.purchases = 2
        stub.handle_akashi_supply_buy('grid')
        self.assertEqual(calls.count(('sync', 233542)), 2)


class TestQuietConfigSave(unittest.TestCase):
    """Config.save(quiet=...) 的日志级别：内部状态保存降 debug，其余照旧 INFO。"""

    def make_config(self, modified):
        from module.config.config import AzurLaneConfig

        config = AzurLaneConfig.__new__(AzurLaneConfig)
        config.modified = dict(modified)
        config.data = {}
        config.config_name = 'ap_entry_scan_test_nonexistent'
        config.write_file = MagicMock()
        return config

    def test_quiet_save_logs_debug_only(self):
        from module.config import config as config_module

        config = self.make_config({'OpsiScheduling.Storage.Storage.ApLedgerState': {'v': 1}})
        with patch.object(config_module.logger, 'info') as info, \
                patch.object(config_module.logger, 'debug') as debug:
            config.save(quiet=True)
        debug.assert_called_once()
        info.assert_not_called()
        # 改动照常写入并清空待写集合
        self.assertEqual(config.data['OpsiScheduling']['Storage']['Storage']['ApLedgerState'], {'v': 1})
        self.assertEqual(config.modified, {})
        self.assertTrue(config.write_file.called)

    def test_normal_save_logs_info(self):
        from module.config import config as config_module

        config = self.make_config({'OpsiScheduling.Storage.Storage.ApLedgerState': {'v': 1}})
        with patch.object(config_module.logger, 'info') as info, \
                patch.object(config_module.logger, 'debug') as debug:
            config.save()
        info.assert_called_once()
        debug.assert_not_called()


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
            OpsiHazard1Leveling_RecordSeaMiles=-1,
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
