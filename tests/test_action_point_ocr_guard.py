"""顶栏行动力 OCR 的门禁与账本记录链路测试（MapActionPointDigit + handler），不连接游戏。

覆盖：位数门（≤4 位合法、≥5 位丢弃）、非数字拒绝、
顶栏读数→账本记录→弹窗判定的 handler 级链路、开关语义。
真实截图样张回归：设置环境变量 AP_MAPBAR_SAMPLES 指向样张目录
（文件名以 ap<期望值>_ 开头，如 ap154_zone22.png），目录不存在时自动跳过；
代码中不含任何本机路径。
"""

import os
import unittest
from glob import glob
from unittest.mock import patch

from module.os_handler.action_point import MAP_ACTION_POINT_DIGIT
from module.os_handler.action_point_ledger import SOURCE_MAP_BAR, SOURCE_POPUP


class TestMapActionPointDigitGates(unittest.TestCase):
    """位数门在 OCR 的 after_process 里执行，非法读数返回 -1 哨兵。"""

    def test_normal_three_digits(self):
        self.assertEqual(MAP_ACTION_POINT_DIGIT.after_process('154'), 154)

    def test_single_and_double_digits(self):
        self.assertEqual(MAP_ACTION_POINT_DIGIT.after_process('5'), 5)
        self.assertEqual(MAP_ACTION_POINT_DIGIT.after_process('54'), 54)

    def test_four_digits_allowed(self):
        # 一口气补过 1k 的玩法：顶栏读数不预设 200 上限
        self.assertEqual(MAP_ACTION_POINT_DIGIT.after_process('1540'), 1540)
        self.assertEqual(MAP_ACTION_POINT_DIGIT.after_process('9999'), 9999)

    def test_five_digits_rejected(self):
        # OCR 拼接：行动力 154 与舰船等级 Lv.60 连读 → 60154
        self.assertEqual(MAP_ACTION_POINT_DIGIT.after_process('60154'), -1)
        self.assertEqual(MAP_ACTION_POINT_DIGIT.invalid_reason, 'len>4')

    def test_empty_rejected(self):
        self.assertEqual(MAP_ACTION_POINT_DIGIT.after_process(''), -1)
        self.assertEqual(MAP_ACTION_POINT_DIGIT.invalid_reason, 'empty')

    def test_non_digit_noise_rejected(self):
        # 只剩非数字字符（被遮挡/未加载）→ 无有效数字 → 拒绝
        self.assertEqual(MAP_ACTION_POINT_DIGIT.after_process('Lv.'), -1)

    def test_no_idsb_lenient_mapping(self):
        # 弹窗内 OCR 会把 I/D/S/B 宽容映射成 1/0/5/8，顶栏读数不做这种映射：
        # 混入非数字字符一律按误读拒绝
        self.assertEqual(MAP_ACTION_POINT_DIGIT.after_process('1D4'), -1)
        self.assertEqual(MAP_ACTION_POINT_DIGIT.invalid_reason, 'non-digit')


class TestHandlerRecordAndDecide(unittest.TestCase):
    """handler 级：顶栏读数 → 账本记录 → 弹窗判定（SimpleNamespace 桩，不连游戏）。

    config_name 指向不存在的配置文件，磁盘回退读到空，保证用例不依赖本机 config。
    """

    def make_handler(self, enabled=True):
        from types import SimpleNamespace

        from module.os_handler import action_point as ap_module
        from module.os_handler.action_point import ActionPointHandler

        handler = ActionPointHandler.__new__(ActionPointHandler)
        handler.config = SimpleNamespace(
            OpsiGeneral_ActionPointLedgerEnabled=enabled,
            config_name='ap_ledger_test_nonexistent',
            data={'OpsiScheduling': {'Storage': {'Storage': {}}}},
            modified={},
        )
        handler.device = SimpleNamespace(image='img')
        handler.is_in_map = lambda: True
        return handler, ap_module

    def test_ledger_disabled_always_popup(self):
        # 回退语义：账本关闭时不读顶栏、不做预判，恒弹窗（旧行为）
        handler, _ = self.make_handler(enabled=False)
        self.assertTrue(handler.need_action_point_popup(cost=120, preserve=200))
        self.assertIsNone(handler.ap_observe_from_map_bar())

    def test_recorded_enough_skips_popup(self):
        handler, ap_module = self.make_handler()
        handler._get_ap_ledger().observe(154, 354, source=SOURCE_POPUP)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', return_value=154):
            self.assertFalse(handler.need_action_point_popup(cost=120, preserve=200))

    def test_low_map_bar_truth_forces_popup(self):
        # 场景还原（2026-10-03 真机）：弹窗记录 198，一轮战斗后顶栏真值 98 → 必须弹窗
        handler, ap_module = self.make_handler()
        handler._get_ap_ledger().observe(198, 884, source=SOURCE_POPUP)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', return_value=98):
            self.assertTrue(handler.need_action_point_popup(cost=120, preserve=200))
        self.assertEqual(handler._get_ap_ledger().current, 98)

    def test_map_bar_reading_is_recorded(self):
        handler, ap_module = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', return_value=158):
            value = handler.ap_observe_from_map_bar()
        self.assertEqual(value, 158)
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 158)
        self.assertEqual(ledger.source, SOURCE_MAP_BAR)
        # 账本从没弹过窗：总行动力保持未知
        self.assertIsNone(ledger.total_with_box)

    def test_map_bar_syncs_dashboard(self):
        # 顶栏读数同步总览显示（Dashboard.ActionPoint 的当前值/总行动力/时间戳），
        # 但不写 LogRes（行动力趋势快照与统计不混入顶栏读数）
        handler, ap_module = self.make_handler()
        handler._get_ap_ledger().observe(198, 884, source=SOURCE_POPUP)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', return_value=158):
            handler.ap_observe_from_map_bar()
        modified = handler.config.modified
        self.assertEqual(modified['Dashboard.ActionPoint.Value'], 158)
        self.assertEqual(modified['Dashboard.ActionPoint.Total'], 844)
        self.assertIn('Dashboard.ActionPoint.Record', modified)

    def test_unknown_total_with_preserve_forces_popup(self):
        # 场景还原（2026-10-03 18:46 真机）：账本空、顶栏读到 171——
        # 当前值够开工（171>=120），但保留值需要含箱总量，未知 → 弹一次窗建立；
        # 若拿 171 冒充总行动力，171 <= CL1保留 200 会误判行动力不足而推迟到明天
        handler, ap_module = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', return_value=171):
            handler.ap_observe_from_map_bar()
        self.assertTrue(handler.need_action_point_popup(cost=120, preserve=200))
        # 余烬信标未收满等场景保留值为 0：不需要总量，直接按当前值放行
        self.assertFalse(handler.need_action_point_popup(cost=120, preserve=0))

    def test_garbage_reading_not_recorded(self):
        handler, ap_module = self.make_handler()
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', return_value=-1):
            self.assertIsNone(handler.ap_observe_from_map_bar())
        self.assertIsNone(handler._get_ap_ledger().current)

    def test_total_follows_from_last_popup(self):
        # 弹窗记录 198/884（箱子 686），顶栏真值 98 → 总行动力跟随修正为 784，箱子价值不变
        handler, ap_module = self.make_handler()
        handler._get_ap_ledger().observe(198, 884, source=SOURCE_POPUP)
        with patch.object(ap_module.MAP_ACTION_POINT_DIGIT, 'ocr', return_value=98):
            handler.ap_observe_from_map_bar()
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 98)
        self.assertEqual(ledger.total_with_box, 784)
        self.assertEqual(ledger.box_value, 686)

    def test_persisted_state_loaded_from_wrapper(self):
        # 回归（2026-10-03 真机日志定位）：持久化键在 Storage.Storage 之下的
        # ApLedgerState 子键。读取曾错误地取上层包装字典，from_state 找不到
        # current 字段永远返回空账本 → 持久化从未生效，每次任务重开都弹读数窗
        handler, _ = self.make_handler()
        handler.config.data = {'OpsiScheduling': {'Storage': {'Storage': {
            'ApLedgerState': {
                'v': 1, 'current': 158, 'total': 1158,
                'at': '2026-10-03 18:08:19', 'source': 'popup',
            },
            'BuyActionPointCount': 5,
        }}}}
        state = handler._load_ap_ledger_state()
        self.assertEqual(state.get('current'), 158)
        ledger = handler._get_ap_ledger()
        self.assertEqual(ledger.current, 158)
        self.assertEqual(ledger.total_with_box, 1158)


class TestActionPointUseGuard(unittest.TestCase):
    """USE 只点一次；弹窗 OCR 滞后、仅单项变化及任务重入均不得连点。"""

    def make_handler(self, readings=()):
        from types import SimpleNamespace
        from module.os_handler.action_point import ActionPointHandler

        handler = ActionPointHandler.__new__(ActionPointHandler)
        handler.config = SimpleNamespace(data={}, modified={}, config_name='ap_use_test_nonexistent')
        clicks = []
        handler.device = SimpleNamespace(
            image='img', screenshot=lambda: None, sleep=lambda _: None,
            click=lambda button: clicks.append(button),
        )
        handler._action_point_current = 97
        handler._action_point_box = [11026, 0, 0, 12]
        handler._ap_use_blocked = False
        handler._pending = None
        handler._load_ap_use_pending = lambda: handler._pending

        def save_pending(value):
            handler._pending = value
        handler._save_ap_use_pending = save_pending
        handler.appear = lambda *args, **kwargs: True
        handler.handle_popup_confirm = lambda *args, **kwargs: False
        handler.is_current_ap_visible = lambda: True
        iterator = iter(readings)

        def update():
            current, stock = next(iterator, (handler._action_point_current, handler._action_point_box[3]))
            handler._action_point_current = current
            handler._action_point_box[3] = stock
        handler.action_point_update = update
        return handler, clicks

    def test_stale_ocr_never_clicks_twice_and_leaves_pending(self):
        from module.exception import RequestHumanTakeover
        from module.os_handler import action_point as ap_module

        handler, clicks = self.make_handler([(97, 12)] * 4)

        class ShortTimer:
            def __init__(self, *args):
                self.calls = 0
            def start(self):
                return self
            def reached(self):
                self.calls += 1
                return self.calls > 4

        with patch.object(ap_module, 'Timer', ShortTimer):
            with self.assertRaisesRegex(RequestHumanTakeover, '未确认'):
                handler.action_point_use(selected_index=3)
        self.assertEqual(len(clicks), 1)
        self.assertEqual(handler._pending['current'], 97)
        self.assertEqual(handler._pending['stock'], 12)
        with self.assertRaises(RequestHumanTakeover):
            handler.action_point_use(selected_index=3)
        self.assertEqual(len(clicks), 1)

    def test_only_one_signal_changes_does_not_confirm(self):
        from module.exception import RequestHumanTakeover
        from module.os_handler import action_point as ap_module

        handler, clicks = self.make_handler([(197, 12), (97, 11)])

        class ShortTimer:
            def __init__(self, *args):
                self.calls = 0
            def start(self):
                return self
            def reached(self):
                self.calls += 1
                return self.calls > 2

        with patch.object(ap_module, 'Timer', ShortTimer):
            with self.assertRaises(RequestHumanTakeover):
                handler.action_point_use(selected_index=3)
        self.assertEqual(len(clicks), 1)
        self.assertIsNotNone(handler._pending)

    def test_confirmed_ocr_and_stock_allow_next_box(self):
        from module.os_handler import action_point as ap_module

        handler, clicks = self.make_handler([(97, 12), (197, 11)])

        class ShortTimer:
            def __init__(self, *args):
                self.calls = 0
            def start(self):
                return self
            def reached(self):
                self.calls += 1
                return self.calls > 3

        with patch.object(ap_module, 'Timer', ShortTimer):
            self.assertTrue(handler.action_point_use(selected_index=3))
        self.assertEqual(len(clicks), 1)
        self.assertIsNone(handler._pending)
        self.assertFalse(handler._ap_use_blocked)

    def test_unwritable_guard_never_clicks_use(self):
        from module.exception import RequestHumanTakeover

        handler, clicks = self.make_handler()
        handler._save_ap_use_pending = lambda value: (_ for _ in ()).throw(
            RequestHumanTakeover('保护文件不可写'))
        with self.assertRaisesRegex(RequestHumanTakeover, '不可写'):
            handler.action_point_use(selected_index=3)
        self.assertEqual(clicks, [])

    def test_buy_path_checks_pending_before_switching_tab(self):
        from module.exception import RequestHumanTakeover

        handler, clicks = self.make_handler()
        handler._pending = {'index': 3, 'current': 97, 'stock': 12}
        handler.action_point_set_button = lambda index: self.fail('不应切换到购买页签')
        # 报错必须带哨兵文件路径，否则用户不知道删哪个文件
        with self.assertRaisesRegex(RequestHumanTakeover, 'ap-use-pending'):
            handler.action_point_buy()
        self.assertEqual(clicks, [])

    def test_pending_from_previous_run_blocks_before_click(self):
        from module.exception import RequestHumanTakeover

        handler, clicks = self.make_handler()
        handler._pending = {'index': 3, 'current': 97, 'stock': 12}
        with self.assertRaisesRegex(RequestHumanTakeover, '尚未确认'):
            handler.action_point_use(selected_index=3)
        self.assertEqual(clicks, [])

    def test_pending_is_persisted_and_blocks_new_handler(self):
        import tempfile
        from pathlib import Path
        from module.exception import RequestHumanTakeover

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ap_use_test.json.ap-use-pending'
            handler, clicks = self.make_handler()
            del handler._save_ap_use_pending
            del handler._load_ap_use_pending
            handler._ap_use_pending_path = lambda: str(path)
            handler._save_ap_use_pending({
                'index': 3, 'current': 97, 'stock': 12,
            })
            self.assertEqual(handler._load_ap_use_pending()['stock'], 12)
            self.assertNotEqual(path.suffix, '.json')  # 不会被配置实例枚举器当作模组实例
            new_handler, new_clicks = self.make_handler()
            del new_handler._load_ap_use_pending
            new_handler._ap_use_pending_path = lambda: str(path)
            with self.assertRaisesRegex(RequestHumanTakeover, '尚未确认'):
                new_handler.action_point_use(selected_index=3)
            self.assertEqual(clicks, [])
            self.assertEqual(new_clicks, [])
            # 确认已刷新后由程序自动清除；没有强制让用户手改实例配置。
            handler._save_ap_use_pending(None)
            self.assertFalse(path.exists())

    def test_cleanup_after_manual_delete_is_success(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ap_use_test.json.ap-use-pending'
            handler, clicks = self.make_handler()
            del handler._save_ap_use_pending
            del handler._load_ap_use_pending
            handler._ap_use_pending_path = lambda: str(path)
            handler._save_ap_use_pending({'index': 3, 'current': 97, 'stock': 12})
            path.unlink()  # 用户在等待确认期间已按日志提示手动删除
            # USE 已确认，清除哨兵时文件不存在应视为清理成功，不得抛人工接管
            handler._save_ap_use_pending(None)
            self.assertFalse(path.exists())
            self.assertEqual(clicks, [])

    def test_invalid_guard_file_never_allows_another_click(self):
        import tempfile
        from pathlib import Path
        from module.exception import RequestHumanTakeover

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ap-use-pending.json'
            for content in ('', '{}', 'null', '[]', '{broken'):
                with self.subTest(content=content):
                    path.write_text(content, encoding='utf-8')
                    handler, clicks = self.make_handler()
                    del handler._load_ap_use_pending
                    handler._ap_use_pending_path = lambda: str(path)
                    with self.assertRaises(RequestHumanTakeover):
                        handler.action_point_use(selected_index=3)
                    self.assertEqual(clicks, [])

    def test_handle_action_point_does_not_retry_after_timeout(self):
        from types import SimpleNamespace
        from module.exception import RequestHumanTakeover

        handler, clicks = self.make_handler()
        handler._is_in_action_point = lambda: True
        handler.action_point_safe_get = lambda: None
        handler.action_point_set_button = lambda index: True
        handler.action_point_buy = lambda **kwargs: False
        handler.config.OS_ACTION_POINT_PRESERVE = 200
        handler.config.OpsiGeneral_BuyActionPointLimit = 0
        handler._action_point_total = 1297
        handler._action_point_box = [11026, 0, 0, 12]
        handler.action_point_use = lambda **kwargs: (_ for _ in ()).throw(
            RequestHumanTakeover('USE 未确认'))
        with self.assertRaises(RequestHumanTakeover):
            handler.handle_action_point(
                zone=SimpleNamespace(hazard_level=1, is_port=False),
                pinned='DANGEROUS', cost=120,
            )
        self.assertEqual(clicks, [])

    def test_oil_purchase_confirms_by_oil_decrease_and_ap_increase(self):
        from module.os_handler import action_point as ap_module

        handler, clicks = self.make_handler()
        handler.action_point_update = lambda: (
            setattr(handler, '_action_point_current', 197),
            handler._action_point_box.__setitem__(0, 7026),
        )

        class ShortTimer:
            def __init__(self, *args):
                self.calls = 0
            def start(self):
                return self
            def reached(self):
                self.calls += 1
                return self.calls > 2

        with patch.object(ap_module, 'Timer', ShortTimer):
            self.assertTrue(handler.action_point_use(selected_index=0))
        self.assertEqual(len(clicks), 1)
        self.assertIsNone(handler._pending)

    def test_one_confirmed_box_reaches_target_without_repeating_read(self):
        from types import SimpleNamespace

        handler, _ = self.make_handler()
        handler._is_in_action_point = lambda: True
        reads = []
        handler.action_point_safe_get = lambda: reads.append('read')
        handler.action_point_set_button = lambda index: True
        handler.action_point_quit = lambda: None
        handler.config.OS_ACTION_POINT_PRESERVE = 200
        handler.config.OpsiGeneral_BuyActionPointLimit = 0
        handler._action_point_total = 1297
        used = []

        def use_once(selected_index):
            used.append(selected_index)
            handler._action_point_current = 197
            handler._action_point_box[3] -= 1
        handler.action_point_use = use_once
        self.assertTrue(handler.handle_action_point(
            zone=SimpleNamespace(hazard_level=1, is_port=False),
            pinned='DANGEROUS', cost=120,
        ))
        self.assertEqual(used, [3])
        self.assertEqual(reads, ['read'])

    def test_buy_success_skips_extra_read(self):
        from types import SimpleNamespace

        handler, _ = self.make_handler()
        handler._is_in_action_point = lambda: True
        reads = []
        handler.action_point_safe_get = lambda: reads.append('read')
        handler.action_point_quit = lambda: None
        handler.action_point_buy = lambda **kwargs: True
        handler.config.OS_ACTION_POINT_PRESERVE = 200
        handler.config.OpsiGeneral_BuyActionPointLimit = 5
        handler._action_point_current = 197
        handler._action_point_total = 1297
        handler._action_point_box = [11026, 0, 0, 12]
        self.assertTrue(handler.handle_action_point(
            zone=SimpleNamespace(hazard_level=1, is_port=False),
            pinned='DANGEROUS', cost=120,
        ))
        # 买油成功后 action_point_buy 内部已双重确认并重读，不得再 safe_get 一次
        self.assertEqual(reads, ['read'])

    def test_one_click_cannot_confirm_two_boxes(self):
        from module.exception import RequestHumanTakeover
        from module.os_handler import action_point as ap_module

        handler, clicks = self.make_handler([(297, 10)])

        class ShortTimer:
            def __init__(self, *args):
                self.calls = 0
            def start(self):
                return self
            def reached(self):
                self.calls += 1
                return self.calls > 1

        with patch.object(ap_module, 'Timer', ShortTimer):
            with self.assertRaises(RequestHumanTakeover):
                handler.action_point_use(selected_index=3)
        self.assertEqual(len(clicks), 1)
        self.assertIsNotNone(handler._pending)


class TestSampleImages(unittest.TestCase):
    """真实截图样张回归：样张目录由环境变量 AP_MAPBAR_SAMPLES 指定，
    文件名以 ap<期望值>_ 开头（如 ap154_zone22.png）。目录不存在时跳过。"""

    SAMPLES_DIR = os.environ.get('AP_MAPBAR_SAMPLES', '')

    def setUp(self):
        if not self.SAMPLES_DIR:
            self.skipTest('未设置 AP_MAPBAR_SAMPLES，跳过真实截图样张回归')
        self.files = sorted(glob(os.path.join(self.SAMPLES_DIR, 'ap*_*.png')))
        if not self.files:
            self.skipTest('样张目录为空，跳过')

    def test_samples_read_expected_value(self):
        for path in self.files:
            name = os.path.basename(path)
            expected = int(name.split('_')[0][2:])
            with self.subTest(sample=name):
                image = np_array(path)
                raw = MAP_ACTION_POINT_DIGIT.ocr(image)
                self.assertEqual(
                    raw, expected,
                    f'样张 {name}: OCR 读到 {raw}，期望 {expected}',
                )


def np_array(path):
    from PIL import Image

    import numpy as np

    return np.array(Image.open(path).convert('RGB'))


if __name__ == '__main__':
    unittest.main()
