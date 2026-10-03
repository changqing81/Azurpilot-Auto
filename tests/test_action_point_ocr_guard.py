"""顶栏行动力 OCR 的门禁测试（MapActionPointDigit），不连接游戏。

覆盖：位数门（≤4 位合法、≥5 位丢弃）、范围门、变化率门、连续一致门、
复核额度（HourlyQuota）耗尽后不再弹窗。
真实截图样张回归：设置环境变量 AP_MAPBAR_SAMPLES 指向样张目录
（文件名以 ap<期望值>_ 开头，如 ap154_zone22.png），目录不存在时自动跳过；
代码中不含任何本机路径。
"""

import os
import unittest
from datetime import datetime, timedelta
from glob import glob

from module.os_handler.action_point import MAP_ACTION_POINT_DIGIT
from module.os_handler.action_point_ledger import ActionPointLedger, ApEstimate, HourlyQuota

T0 = datetime(2026, 10, 3, 12, 0, 0)


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


class TestJudgeChain(unittest.TestCase):
    def setUp(self):
        self.est = ApEstimate(152, 352, 60, 'medium')

    def test_ok_within_tolerance(self):
        self.assertEqual(ActionPointLedger.judge_map_bar_reading(154, self.est)[0], 'ok')
        self.assertEqual(ActionPointLedger.judge_map_bar_reading(150, self.est)[0], 'ok')

    def test_suspect_beyond_tolerance(self):
        verdict, value, reason = ActionPointLedger.judge_map_bar_reading(100, self.est)
        self.assertEqual(verdict, 'suspect')
        self.assertEqual(value, 100)
        self.assertEqual(reason, 'drift=-52')

    def test_reject_sentinel(self):
        verdict, value, reason = ActionPointLedger.judge_map_bar_reading(-1, self.est)
        self.assertEqual(verdict, 'reject')
        self.assertEqual(reason, 'negative')


class TestVerifyQuota(unittest.TestCase):
    """复核额度：只有"读数存疑"的复核受限制，且额度耗尽后不再复核。"""

    def make_ledger(self):
        ledger = ActionPointLedger()
        ledger.observe(154, 354, source='popup', at=T0)
        return ledger

    def test_ok_reading_does_not_consume_quota(self):
        quota = HourlyQuota(3)
        ledger = self.make_ledger()
        est = ledger.estimate(now=T0)
        verdict, _, _ = ActionPointLedger.judge_map_bar_reading(154, est)
        self.assertEqual(verdict, 'ok')
        # ok 读数不需要复核，额度不动
        self.assertTrue(quota.allow(now=T0))
        self.assertEqual(quota.events, [])

    def test_suspect_consumes_quota_until_exhausted(self):
        quota = HourlyQuota(3)
        ledger = self.make_ledger()
        t = T0
        for i in range(3):
            est = ledger.estimate(now=t)
            verdict, _, _ = ActionPointLedger.judge_map_bar_reading(100, est)
            self.assertEqual(verdict, 'suspect')
            self.assertTrue(quota.allow(now=t), f'第 {i + 1} 次复核应有额度')
            quota.record(now=t)
            # 复核后弹窗校准回真值
            ledger.observe(154, 354, source='popup', at=t)
        self.assertFalse(quota.allow(now=t), '额度耗尽后不再复核')

    def test_exhausted_quota_falls_back_to_projection(self):
        quota = HourlyQuota(3)
        ledger = self.make_ledger()
        for _ in range(3):
            quota.record(now=T0)
        est = ledger.estimate(now=T0)
        verdict, _, _ = ActionPointLedger.judge_map_bar_reading(100, est)
        self.assertEqual(verdict, 'suspect')
        # 额度耗尽：不弹窗，继续用推演值
        self.assertFalse(quota.allow(now=T0))
        self.assertEqual(ledger.estimate(now=T0).current, 154)


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
        from PIL import Image

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
