"""验证 Windows ML 设备选择的独显优先级（本地定制）。

背景：Windows 上虚拟显示器适配器（远程控制虚拟屏、模拟器虚拟屏、
Virtual Display Driver）会镜像独显的名称与显存，且不填 Discrete 元数据，
导致同型号设备出现多个候选。若绑到这类会被热插拔的适配器，DirectML
会话会在 run() 时失效并中断任务。因此候选顺序必须让真实独显排在最前。
"""

import unittest
from types import SimpleNamespace

from module.ocr.windows_ml import _iter_preferred_devices


class FakeDeviceType:
    NPU = 'NPU'
    GPU = 'GPU'
    CPU = 'CPU'


def make_device(adapter, *, discrete=None, high_perf=None, ep='DmlExecutionProvider',
                device_type=FakeDeviceType.GPU, description='NVIDIA GeForce RTX 4070 SUPER',
                video_memory='11999 MB'):
    """构造一个仿真的 ORT EP 设备。"""
    metadata = {
        'Description': description,
        'DxgiAdapterNumber': str(adapter),
        'DxgiVideoMemory': video_memory,
    }
    if discrete is not None:
        metadata['Discrete'] = discrete
    if high_perf is not None:
        metadata['DxgiHighPerformanceIndex'] = high_perf
    return SimpleNamespace(
        ep_name=ep,
        device=SimpleNamespace(type=device_type, metadata=metadata),
    )


class FakeOrt:
    OrtHardwareDeviceType = FakeDeviceType

    def __init__(self, devices):
        self._devices = devices

    def get_ep_devices(self):
        return self._devices


class TestOcrDevicePriority(unittest.TestCase):
    def test_real_discrete_gpu_is_preferred_over_virtual_adapters(self):
        """真实独显（Discrete=1）必须排在镜像同一块显卡的虚拟适配器之前。"""
        virtual_high = make_device(2, high_perf='3')
        virtual_low = make_device(1, high_perf='2')
        real = make_device(0, discrete='1', high_perf='0')
        cpu = make_device(0, ep='CPUExecutionProvider',
                          device_type=FakeDeviceType.CPU, description='Intel Core i5')
        ort = FakeOrt([cpu, virtual_high, virtual_low, real])

        devices = _iter_preferred_devices(
            ort, device_preference='auto', allow_vendor_execution_providers=True)

        self.assertEqual(len(devices), 3)
        self.assertIs(devices[0], real)
        self.assertEqual(devices[0].device.metadata['Discrete'], '1')
        # 其余虚拟适配器按高性能索引升序兜底
        self.assertIs(devices[1], virtual_low)
        self.assertIs(devices[2], virtual_high)

    def test_virtual_adapters_still_usable_without_discrete_marker(self):
        """机器上只有虚拟适配器（无 Discrete 标记）时仍应返回候选，不能空手而归。"""
        first = make_device(1, high_perf='1')
        second = make_device(2, high_perf='2')
        ort = FakeOrt([second, first])

        devices = _iter_preferred_devices(
            ort, device_preference='auto', allow_vendor_execution_providers=True)

        self.assertEqual(list(devices), [first, second])

    def test_missing_high_performance_index_sorts_last(self):
        """缺少 DxgiHighPerformanceIndex 的设备排在已知索引之后。"""
        unknown = make_device(3)
        known = make_device(4, high_perf='7')
        ort = FakeOrt([unknown, known])

        devices = _iter_preferred_devices(
            ort, device_preference='auto', allow_vendor_execution_providers=True)

        self.assertEqual(list(devices), [known, unknown])

    def test_gpu_preference_returns_discrete_first(self):
        """显式选择 gpu 时同样优先真实独显。"""
        virtual = make_device(1, high_perf='1')
        real = make_device(0, discrete='1', high_perf='0')
        ort = FakeOrt([virtual, real])

        devices = _iter_preferred_devices(
            ort, device_preference='gpu', allow_vendor_execution_providers=True)

        self.assertEqual(list(devices), [real, virtual])




class TestIntegratedGpuCandidate(unittest.TestCase):
    """核显 DML 候选回归（2026-10-24）：核显机器必须能选中 DML 核显。"""

    def test_auto_prefers_real_discrete_then_integrated(self):
        """auto 模式：独显优先，核显排在其后、OpenVINO CPU 之前。"""
        real = make_device(0, discrete='1', high_perf='0')
        integrated = make_device(3, discrete='0', high_perf='1',
                                 description='Intel(R) Iris(R) Xe Graphics')
        virtual = make_device(1, high_perf='2',
                              description='Intel(R) Iris(R) Xe Graphics')
        ort = FakeOrt([virtual, integrated, real])

        devices = _iter_preferred_devices(
            ort, device_preference='auto', allow_vendor_execution_providers=True)

        # 独显最前，核显入选，虚拟适配器（无 Discrete 标记）被核显候选排除
        self.assertIs(devices[0], real)
        self.assertIn(integrated, devices)
        self.assertNotIn(virtual, devices)

    def test_gpu_preference_includes_integrated(self):
        """显式 gpu：独显排最前，核显作为无独显机器的兜底。"""
        integrated = make_device(0, discrete='0', high_perf='0',
                                 description='Intel(R) Iris(R) Xe Graphics')
        ort = FakeOrt([integrated])

        devices = _iter_preferred_devices(
            ort, device_preference='gpu', allow_vendor_execution_providers=True)

        self.assertEqual(list(devices), [integrated])


if __name__ == '__main__':
    unittest.main()
