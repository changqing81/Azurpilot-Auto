"""多实例 OCR ONNX 线程数上限回归测试，不加载实际模型。"""

import os
import unittest
from unittest.mock import patch

from module.ocr import al_ocr


def settings(backend='onnx'):
    """构造本地 OCR 工厂所需的不可变设置快照。"""
    return al_ocr.OcrSettings(
        backend=backend,
        device='cpu',
        allow_vendor_execution_providers=False,
        model_version='PP-OCRv6',
    )


class TestOnnxThreadOptions(unittest.TestCase):
    def test_default_and_overrides(self):
        cases = [('2', 2), ('1', 1), ('8', 8), ('200', 16), ('0', 1), ('bad', 2)]
        with patch.object(al_ocr.os, 'cpu_count', return_value=32):
            for value, expected in cases:
                with self.subTest(value=value), patch.dict(os.environ, {'AZURPILOT_OCR_THREADS': value}):
                    options = al_ocr._onnx_thread_options()
                    self.assertEqual(options['EngineConfig.onnxruntime.intra_op_num_threads'], expected)
                    self.assertEqual(options['EngineConfig.onnxruntime.inter_op_num_threads'], 1)

    def test_cpu_count_cap(self):
        with patch.object(al_ocr.os, 'cpu_count', return_value=1), patch.dict(
            os.environ, {'AZURPILOT_OCR_THREADS': '4'}
        ):
            self.assertEqual(
                al_ocr._onnx_thread_options()['EngineConfig.onnxruntime.intra_op_num_threads'], 1
            )

    def test_all_onnx_model_factories_receive_limit(self):
        # 本地工厂签名是 (name, settings)，设置通过 OcrSettings 快照传入，
        # 与上游直接读模块级 config 的实现不同，故用快照对象替代 mock config。
        snapshot = settings()
        with patch.dict(os.environ, {'AZURPILOT_OCR_THREADS': '2'}), patch.object(
            al_ocr, '_get_onnx_model_params', return_value=('model.onnx', 'keys.txt', 'PP-OCRv6')
        ), patch.object(al_ocr, '_configure_windows_ml_sessions', side_effect=lambda ocr, *a, **kw: ocr), patch.object(
            al_ocr, 'RecOnlyOCR'
        ) as rec_factory, patch.object(al_ocr, 'RapidOCR') as det_factory, patch.object(
            al_ocr, 'DetOnlyOCR'
        ) as ncnn_det_factory:
            al_ocr._create_ocr('cn', snapshot)
            al_ocr._create_det_ocr_for_onnx('cn', snapshot)
            al_ocr._create_det_ocr_for_ncnn()
            for factory in (rec_factory, det_factory, ncnn_det_factory):
                params = factory.call_args.kwargs['params']
                self.assertEqual(params['EngineConfig.onnxruntime.intra_op_num_threads'], 2)
                self.assertEqual(params['EngineConfig.onnxruntime.inter_op_num_threads'], 1)


if __name__ == '__main__':
    unittest.main()
