"""ProgressTracker 与点击网格检测的单元测试。

覆盖：
- 语义进度指纹：首次记录、重复记录、超时判定、指纹变化重置、clear
- 设备层网格点击统计：同格子循环、跨格子、字符串操作、豁免名单、记录移除同步
"""

import time
import types
from collections import deque
from types import SimpleNamespace
from unittest import TestCase

from module.base.progress_tracker import ProgressTracker
from module.exception import GameTooManyClickError


class FakeButton:
    """模拟按钮：带点击区域，按钮名即 str()。"""

    def __init__(self, name, area):
        self.name = name
        self.button = area

    def __str__(self):
        return self.name


class FakeDevice:
    """仅包含网格检测所需属性的最小 Device 替身。

    直接复用 Device 的方法（unbound），验证真实逻辑而非拷贝。
    """

    def __init__(self):
        self.click_record = deque(maxlen=15)
        self.click_grid_record = deque(maxlen=15)
        self.click_grid_whitelist = []

    from module.device.device import Device as _D

    click_grid_add = _D.click_grid_add
    click_grid_check = _D.click_grid_check
    click_record_remove = _D.click_record_remove
    click_record_clear = _D.click_record_clear


class TestProgressTracker(TestCase):
    def test_first_record_is_progress(self):
        tracker = ProgressTracker(timeout=300)
        self.assertTrue(tracker.record('A'))
        self.assertFalse(tracker.is_stuck())

    def test_same_record_is_not_progress(self):
        tracker = ProgressTracker(timeout=300)
        tracker.record('A')
        self.assertFalse(tracker.record('A'))

    def test_alternating_fingerprint_never_stuck(self):
        """A/B 交替属于正常轮换（如海域轮换），永不判卡死。"""
        tracker = ProgressTracker(timeout=0.1)
        for i in range(10):
            tracker.record('A' if i % 2 == 0 else 'B')
            time.sleep(0.03)
            self.assertFalse(tracker.is_stuck())

    def test_stuck_after_timeout(self):
        tracker = ProgressTracker(timeout=0.1)
        tracker.record('A')
        tracker.record('A')  # 第二次起计时
        time.sleep(0.15)
        self.assertTrue(tracker.is_stuck())

    def test_fingerprint_change_resets_timer(self):
        tracker = ProgressTracker(timeout=0.1)
        tracker.record('A')
        tracker.record('A')
        time.sleep(0.06)
        tracker.record('B')  # 有进展，重置
        self.assertFalse(tracker.is_stuck())

    def test_stuck_duration(self):
        tracker = ProgressTracker(timeout=10)
        tracker.record('A')
        tracker.record('A')
        time.sleep(0.05)
        self.assertGreaterEqual(tracker.stuck_duration, 0.05)

    def test_clear(self):
        tracker = ProgressTracker(timeout=0.1)
        tracker.record('A')
        tracker.record('A')
        tracker.clear()
        self.assertFalse(tracker.is_stuck())
        self.assertEqual(tracker.stuck_duration, 0.0)
        self.assertTrue(tracker.record('A'))  # clear 后首次记录算有进展

    def test_unknown_fingerprint_uses_clear(self):
        """OCR 失败等场景：无法取得指纹时 clear，按有进展处理。"""
        tracker = ProgressTracker(timeout=0.1)
        tracker.record('100')
        tracker.record('100')
        tracker.clear()  # 模拟本轮 AP 未获取
        tracker.record('100')
        time.sleep(0.15)
        self.assertFalse(tracker.is_stuck())  # 计时被 clear 重置过，仅重新起算


class TestClickGrid(TestCase):
    def _cell(self, col, row):
        """返回中心点落在指定 4x3 格子内的 area。"""
        return (col * 320 + 10, row * 240 + 10, col * 320 + 50, row * 240 + 50)

    def test_same_cell_different_buttons_triggers(self):
        """漏洞 1 场景：不同按钮对象在同一区域循环点击。"""
        device = FakeDevice()
        for i in range(11):
            device.click_grid_add(FakeButton(f'STORY_{i}', self._cell(3, 2)))
            device.click_grid_check()  # 11 次不触发
        with self.assertRaises(GameTooManyClickError):
            device.click_grid_add(FakeButton('STORY_X', self._cell(3, 2)))
            device.click_grid_check()  # 第 12 次触发

    def test_different_cells_do_not_trigger(self):
        device = FakeDevice()
        for i in range(20):
            device.click_grid_add(FakeButton(f'BTN_{i % 4}', self._cell(i % 4, i % 3)))
            device.click_grid_check()

    def test_string_operations_ignored(self):
        device = FakeDevice()
        for _ in range(20):
            device.click_grid_add('SWIPE')
            device.click_grid_check()

    def test_whitelist_ignored(self):
        device = FakeDevice()
        device.click_grid_whitelist.append('STORY_SKIP')
        for i in range(15):
            device.click_grid_add(FakeButton('STORY_SKIP', self._cell(3, 2)))
            device.click_grid_check()

    def test_remove_syncs_grid(self):
        """合法反复点击流程（快速换装）靠 remove 豁免，网格统计必须同步。"""
        device = FakeDevice()
        # 真实换装模式：每轮点击后移除同名记录，累计永远到不了阈值
        for _ in range(20):
            device.click_grid_add(FakeButton('AUTO_EQUIP_NEXT', self._cell(3, 2)))
            device.click_grid_check()
            device.click_record_remove('AUTO_EQUIP_NEXT')
        # remove 后网格清空，重新计数
        self.assertEqual(len(device.click_grid_record), 0)
        for _ in range(11):
            device.click_grid_add(FakeButton('AUTO_EQUIP_NEXT', self._cell(3, 2)))
            device.click_grid_check()  # 11 次不触发

    def test_clear_syncs_grid(self):
        device = FakeDevice()
        device.click_grid_add(FakeButton('BTN', self._cell(0, 0)))
        device.click_record_clear()
        self.assertEqual(len(device.click_grid_record), 0)

    def test_click_record_remove_returns_count(self):
        device = FakeDevice()
        for _ in range(3):
            device.click_record.append('X')
        self.assertEqual(device.click_record_remove('X'), 3)
        self.assertEqual(device.click_record_remove('X'), 0)


if __name__ == '__main__':
    from unittest import main

    main()
