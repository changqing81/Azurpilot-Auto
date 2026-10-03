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
