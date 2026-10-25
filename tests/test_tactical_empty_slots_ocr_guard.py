"""战术学院空槽位 OCR 崩溃的回归测试（2026-10-25 星宇机事故）。

事故链：四个战术槽全空闲（'空闲'×4）时 TACTICAL_CHECK 仍短暂在场，
_tactical_get_finish() 过滤出 buttons=[] 后仍把空列表送进 Duration.ocr()，
AlOcr 的 ONNX CPU 批量路径把空 img_list 喂进 TextRecInput，
onnx 后处理解包抛 ValueError: not enough values to unpack (expected 2, got 0)。
当天 Tactical 任务 12 连崩（每轮重启游戏后立即复发）。

修复 = 两道防线：

1. tactical_class._tactical_get_finish(): 空按钮列表提前返回 []，
   交由上层 _handle_tactical_finish 的 empty_confirm 兜底（点击返回）。
2. al_ocr._ocr_for_single_lines_direct(): 空输入短路返回 []，
   保护所有"过滤后可能为零个区域"的 OCR 调用方（根本防线）。

本测试同时锁定：防线 2 必须位于 _ensure_loaded() 之前，
空输入不应触发模型加载。
"""

import ast
import unittest
from pathlib import Path
from unittest.mock import Mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TACTICAL_SOURCE_PATH = PROJECT_ROOT / 'module' / 'tactical' / 'tactical_class.py'
AL_OCR_SOURCE_PATH = PROJECT_ROOT / 'module' / 'ocr' / 'al_ocr.py'


class TestTacticalGetFinishEmptyGuard(unittest.TestCase):
    """防线 1：空按钮列表不得进入 OCR。"""

    @classmethod
    def setUpClass(cls):
        cls.source = TACTICAL_SOURCE_PATH.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)

    def test_empty_buttons_guard_exists(self):
        """_tactical_get_finish 必须包含空列表提前返回。"""
        func = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == '_tactical_get_finish'
        )
        source_lines = self.source.splitlines()
        body_source = '\n'.join(source_lines[func.lineno - 1: func.end_lineno])
        self.assertIn(
            'if not buttons:',
            body_source,
            '_tactical_get_finish 缺少空按钮列表保护，四槽全空闲会再次把 [] 送进 OCR',
        )
        # 提前返回必须重置 tactical_finish 并返回（保证 run() 收尾走 task_delay(success=False)）
        self.assertIn('self.tactical_finish = []', body_source)

    def test_guard_before_ocr_call(self):
        """空列表判断必须位于 ocr.ocr() 调用之前。"""
        func = next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == '_tactical_get_finish'
        )
        source_lines = self.source.splitlines()
        body_source = '\n'.join(source_lines[func.lineno - 1: func.end_lineno])
        self.assertLess(
            body_source.index('if not buttons:'),
            body_source.index('remains = ocr.ocr('),
            '空列表保护必须在 OCR 调用之前',
        )


class TestAlOcrEmptyInputShortCircuit(unittest.TestCase):
    """防线 2：批量 OCR 空输入短路。"""

    @classmethod
    def setUpClass(cls):
        cls.source = AL_OCR_SOURCE_PATH.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)

    def _get_direct_func(self):
        return next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef) and node.name == '_ocr_for_single_lines_direct'
        )

    def test_short_circuit_exists(self):
        """_ocr_for_single_lines_direct 必须对空 img_list 短路返回。"""
        func = self._get_direct_func()
        source_lines = self.source.splitlines()
        body_source = '\n'.join(source_lines[func.lineno - 1: func.end_lineno])
        self.assertIn(
            'if not img_list:',
            body_source,
            '批量 OCR 入口缺少空输入短路，空列表会再次送进 TextRecInput 触发 ValueError',
        )
        self.assertIn('return []', body_source)

    def test_short_circuit_before_model_load(self):
        """短路必须位于 _ensure_loaded() 之前：空输入不触发模型加载。"""
        func = self._get_direct_func()
        source_lines = self.source.splitlines()
        body_source = '\n'.join(source_lines[func.lineno - 1: func.end_lineno])
        self.assertLess(
            body_source.index('if not img_list:'),
            body_source.index('self._ensure_loaded()'),
            '空输入短路应放在模型加载之前，空输入无需加载模型',
        )

    def test_behavior_empty_list_returns_empty_without_model(self):
        """行为验证：空列表返回 []，且完全不触碰模型加载。"""
        from module.ocr.al_ocr import AlOcr

        ocr = AlOcr(config=Mock(), name='en')
        # model 保持 None：若短路失效，后续 _ensure_loaded 会尝试真实加载模型并失败
        self.assertIsNone(ocr.model)
        result = ocr._ocr_for_single_lines_direct([])
        self.assertEqual(result, [])
        self.assertIsNone(ocr.model, '空输入不应触发模型加载')


if __name__ == '__main__':
    unittest.main()
