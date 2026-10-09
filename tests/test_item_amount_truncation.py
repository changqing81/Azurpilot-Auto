"""AmountOcr.ocr_with_validation 超限兜底回归测试。

覆盖重试 3 次后仍超上限的两条新契约路径：
- 基类（strict_amount_max=False）：保守截去末位（71→7、721→72），
  供战斗掉落等未确认场景兜底；
- strict_amount_max=True：拒绝无法确认的超限读数返回 0，保留截图供核对，
  真实数量由 use_digit_templates 的字形匹配在读 OCR 之前解决
  （见 test_statistics_amount_digits 的真实切片回归）。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import numpy as np

from module.statistics.item import AMOUNT_OCR, AmountOcr


class StrictAmountOcr(AmountOcr):
    """与掉落统计场景同配置：拒绝猜值，但字形匹配关闭以固定 OCR 读数。"""
    strict_amount_max = True


class TestAmountTruncation(unittest.TestCase):
    """超限兜底的截断与拒绝。"""

    def _ocr_with_fixed_reading(self, reading, item_name, ocr=None):
        """固定 OCR 读数，跑完整 ocr_with_validation（含重试与兜底）。"""
        ocr = ocr if ocr is not None else AMOUNT_OCR
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        model = SimpleNamespace(atomic_ocr_for_single_lines=lambda *_: [reading])
        with patch.object(type(ocr), "pre_process",
                          return_value=np.zeros((10, 10), dtype=np.uint8)):
            with patch.object(type(ocr), "cnocr", new_callable=PropertyMock,
                              return_value=model):
                return ocr.ocr_with_validation(
                    image, item_name=item_name, direct_ocr=True, trim=False
                )

    def test_two_digit_truncates_last_digit(self):
        """71 超 GearDesignPlanGunT5 上限 50，基类保守截去末位得 7。"""
        result = self._ocr_with_fixed_reading("71", "GearDesignPlanGunT5")
        self.assertEqual(result, 7)

    def test_three_digit_truncates_last_digit(self):
        """721 截去末位得 72，仍超限；完整拒绝见 strict 用例。"""
        result = self._ocr_with_fixed_reading("721", "OrdnanceTestingReportT4")
        self.assertEqual(result, 72)

    def test_four_digit_truncates_last_digit(self):
        """7221 截去末位得 722。"""
        result = self._ocr_with_fixed_reading("7221", "OrdnanceTestingReportT4")
        self.assertEqual(result, 722)

    def test_item_without_limit_passes_through(self):
        """无精确上限的物品（如 T3 报告）不做超限兜底，原值返回。"""
        result = self._ocr_with_fixed_reading("721", "OrdnanceTestingReportT3")
        self.assertEqual(result, 721)

    def test_just_over_limit_truncates(self):
        """51 超 OrdnanceTestingReportT4 上限 50，截去末位得 5。"""
        result = self._ocr_with_fixed_reading("51", "OrdnanceTestingReportT4")
        self.assertEqual(result, 5)

    def test_within_limit_not_truncated(self):
        """读数在上限内不触发兜底，原值返回。"""
        result = self._ocr_with_fixed_reading("3", "GearDesignPlanGunT5")
        self.assertEqual(result, 3)

    def test_strict_rejects_overflow_without_guessing(self):
        """strict 场景超限读数返回 0 拒绝猜值，由调用方跳过本格并留证。"""
        result = self._ocr_with_fixed_reading(
            "71", "GearDesignPlanGunT5", ocr=StrictAmountOcr([], threshold=96))
        self.assertEqual(result, 0)

    def test_strict_accepts_within_limit(self):
        """strict 场景上限内读数照常返回。"""
        result = self._ocr_with_fixed_reading(
            "3", "GearDesignPlanGunT5", ocr=StrictAmountOcr([], threshold=96))
        self.assertEqual(result, 3)


if __name__ == "__main__":
    unittest.main()
