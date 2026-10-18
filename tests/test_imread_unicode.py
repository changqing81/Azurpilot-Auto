"""Unicode 安全图像读取工具 imread_unicode 的单元测试。

部署目录在 Windows 上可能包含中文（如 D:\\碧蓝全自动），
cv2.imread 无法打开非 ASCII 路径且只返回 None，
imread_unicode 必须在这类路径下也能读出与 ASCII 路径一致的结果。
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from module.base.utils import imread_unicode

ASSET = Path(__file__).resolve().parents[1] / 'assets/ship/dock_level_digits.png'


class TestImreadUnicode(unittest.TestCase):
    def test_matches_cv2_imread(self):
        """ASCII 路径下与 cv2.imread 输出逐字节一致。"""
        expected = cv2.imread(str(ASSET), cv2.IMREAD_GRAYSCALE)
        self.assertIsNotNone(expected)
        result = imread_unicode(ASSET, cv2.IMREAD_GRAYSCALE)
        np.testing.assert_array_equal(result, expected)

    def test_color_flag(self):
        """显式彩色标志同样有效。"""
        result = imread_unicode(ASSET, cv2.IMREAD_COLOR)
        self.assertEqual(result.ndim, 3)
        self.assertEqual(result.shape[2], 3)

    def test_missing_file_returns_none(self):
        """文件缺失返回 None，与 cv2.imread 行为一致。"""
        self.assertIsNone(imread_unicode(ASSET.parent / 'no_such_file.png'))

    def test_empty_file_returns_none(self):
        """空文件返回 None，不抛异常。"""
        try:
            tmp = Path(tempfile.mkdtemp(prefix='azurpilot_unicode_'))
        except OSError as e:
            self.skipTest(f'环境禁止写临时目录：{e}')
        try:
            target = tmp / 'empty.png'
            target.write_bytes(b'')
            self.assertIsNone(imread_unicode(target))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(sys.platform.startswith('win'), '仅 Windows 存在非 ASCII 路径限制')
    def test_unicode_path(self):
        """中文路径必须能读到与 ASCII 路径一致的图像。"""
        try:
            tmp = Path(tempfile.mkdtemp(prefix='碧蓝全自动_'))
            target = tmp / '模板_中文.png'
            shutil.copyfile(ASSET, target)
        except OSError as e:
            self.skipTest(f'环境禁止写临时目录：{e}')
        try:
            self.assertIsNone(cv2.imread(str(target)),
                              '环境意外：cv2.imread 能读中文路径，本用例失去意义')
            result = imread_unicode(target, cv2.IMREAD_GRAYSCALE)
            expected = cv2.imread(str(ASSET), cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(result, '中文路径下 imread_unicode 读取失败')
            np.testing.assert_array_equal(result, expected)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
