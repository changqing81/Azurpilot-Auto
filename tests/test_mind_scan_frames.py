"""船坞扫描稳定检测：切页转场坏帧不得被当成稳定画面进入位移估算。

现场来自 2026-10-16 的四次 MindCalculatorScanError「相邻船坞截图没有明确的
重叠位移」：转场与截图坏帧（一次为全 0 帧、一次为 32-45 灰度噪声帧）里卡片区
尚未渲染，坏帧之间「零差异」会骗过「连续三帧稳定」检测；一旦拿它去和拖拽前
的正常帧比较重叠位移，必然失败并中断整轮扫描（现场 previous 即全 0 数组）。
"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from module.retire.fleet_tech_scan import FleetTechScan
from module.retire.mind_scan import MindCalculatorScan
from module.runtime.mind_recognition import Card, ScanMerger, ScanPolicy, grid_rows
from module.ui.page import page_dock

FIXTURES = Path(__file__).parent / 'fixtures'


def dock_card(row, col, level=100):
    return Card(93 + col * 165, 80 + row * 227, col,
                dict(group_type=10102, name='测试舰', level=level, review=False), 6, True, 0)


class CountTimer:
    """按 reached() 调用次数到期的假计时器，模拟连续稳定帧数。"""

    def __init__(self, count):
        self.count = count
        self.calls = 0

    def start(self):
        self.calls = 0
        return self

    def reset(self):
        self.calls = 0

    def reached(self):
        self.calls += 1
        return self.calls >= self.count


class FixedTimer:
    def __init__(self, reached):
        self._reached = reached

    def start(self):
        return self

    def reset(self):
        pass

    def reached(self):
        return self._reached


def fake_timer(limit, count=0):
    """稳定计时（count=3）按帧数到期；拖拽计时（0.8s）立即放行；
    progress/waiting 超时在有限的测试帧序列里不触发。"""
    if count == 3:
        return CountTimer(3)
    if not count and limit < 1:
        return FixedTimer(True)
    return FixedTimer(False)


class ScriptedDevice:
    """按脚本依次供给截图；每帧附带当帧的滚动条位置。"""

    def __init__(self, script):
        self.script = script
        self.index = 0
        self.image, self.current_thumb = script[0]

    def screenshot(self):
        self.image, self.current_thumb = self.script[min(self.index, len(self.script) - 1)]
        self.index += 1

    def click_record_clear(self):
        pass


class FakeScroll:
    total = 1000

    def __init__(self, device):
        self.device = device

    def appear(self, main):
        return True

    def match_color(self, main):
        """与真实 Scroll 一致：返回轨道长度上的布尔掩码，_scan_pages 自行取行索引。"""
        mask = np.zeros(self.total, dtype=bool)
        mask[self.device.current_thumb] = True
        return mask

    def set_top(self, main, **kwargs):
        pass

    def at_top(self, main):
        return True


class SpyMerger(ScanMerger):
    def __init__(self):
        super().__init__(group_identity=True)
        self.frames = []

    def advance(self, image, **kwargs):
        self.frames.append(image)
        return super().advance(image, **kwargs)


class BlankFrameRegressionTests(unittest.TestCase):
    def test_transition_blank_frames_are_skipped_instead_of_killing_the_scan(self):
        real = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
        rng = np.random.default_rng(20261016)
        blank = np.zeros((720, 1280, 3), np.uint8)  # 10:38 现场：全 0 帧
        near_black = rng.integers(32, 46, (720, 1280, 3), dtype=np.uint8)  # 09:00 现场：32-45 噪声帧
        mid_thumb = np.arange(400, 480)
        bottom_thumb = np.arange(930, 1000)

        # 每段四帧：首帧建立基线，随后三帧通过稳定计数。
        # 修复前，坏帧段的稳定帧会被送去估算位移，与上一页正常帧比较必抛
        # 「没有明确的重叠位移」，测试在此失败。
        script = ([(real, mid_thumb)] * 4  # 第一页读完，拖拽一行
                  + [(real, mid_thumb)] * 4  # 新画面未对位，继续拖拽
                  + [(blank, mid_thumb)] * 4  # 转场坏帧群：修复点
                  + [(near_black, mid_thumb)] * 4
                  + [(blank, mid_thumb)] * 4
                  + [(real, bottom_thumb)] * 4)  # 画面恢复且滚动条到底，收尾
        device = ScriptedDevice(script)
        reads, drags = [], []
        scanner = object.__new__(FleetTechScan)
        scanner.device = device
        scanner.appear = lambda target: target is page_dock.check_button
        scanner.handle_popup_confirm = lambda: False
        scanner._policy = ScanPolicy(95, 125, sorted_desc=True)
        scanner._drag_rows = drags.append
        scanner._read_cards = lambda image, source, names, levels: reads.append(image) or [
            dock_card(row, col) for row in range(3) for col in range(7)]
        scanner._save_scan_evidence = lambda image: ''
        merger = SpyMerger()
        scanner._make_merger = lambda: merger

        with patch('module.retire.mind_scan.Timer', fake_timer), \
                patch('module.retire.mind_scan.DOCK_SCROLL', FakeScroll(device)):
            scanner._scan_pages(None, None)

        # 所有进入位移估算的帧都必须是有内容的船坞画面。
        for frame in merger.frames:
            self.assertGreater(int(np.array(frame.convert('L')).max()), 100)
        # 坏帧没有换来过任何读页或拖拽：只有第一页与到底页被读取。
        self.assertEqual(len(reads), 2)
        self.assertEqual(drags, [227, 227])
        self.assertEqual(merger.offset, 0)
        self.assertTrue(scanner._scan_metadata['reached_bottom'])

    def test_scroll_animation_frames_with_partial_grid_are_skipped(self):
        """滚动动画中间帧（网格仅两行）不得进入位移估算。

        真机现场（10:38 / 11:45 两次都在第 47 页崩）：拖拽后稳定检测挑中的
        恰是半位移画面（visible_rows=[93,320] 只两行），位移不是整数行，
        拿去估算必判「没有明确的重叠位移」，整轮白扫。
        """
        real = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
        partial = np.roll(real, -114, axis=0)  # 卡片半位移的滚动中途帧
        mid_thumb = np.arange(400, 480)
        bottom_thumb = np.arange(930, 1000)
        script = ([(real, mid_thumb)] * 4  # 第一页读完
                  + [(partial, mid_thumb)] * 4  # 动画段：网格报告两行（修复点）
                  + [(real, mid_thumb)] * 4  # 画面恢复完整三排
                  + [(real, bottom_thumb)] * 4)  # 到底收尾
        device = ScriptedDevice(script)

        def fake_grid_rows(image):
            # 当帧是 script[k] 时 device.index == k + 1；动画段即 script[4..7]。
            if 5 <= min(device.index, len(script) - 1) <= 8:
                return [93, 320]
            return grid_rows(image)

        reads, drags = [], []
        scanner = object.__new__(FleetTechScan)
        scanner.device = device
        scanner.appear = lambda target: target is page_dock.check_button
        scanner.handle_popup_confirm = lambda: False
        scanner._policy = ScanPolicy(95, 125, sorted_desc=True)
        scanner._drag_rows = drags.append
        scanner._read_cards = lambda image, source, names, levels: reads.append(image) or [
            dock_card(row, col) for row in range(3) for col in range(7)]
        scanner._save_scan_evidence = lambda image: ''
        merger = SpyMerger()
        scanner._make_merger = lambda: merger

        with patch('module.retire.mind_scan.Timer', fake_timer), \
                patch('module.retire.mind_scan.DOCK_SCROLL', FakeScroll(device)), \
                patch('module.retire.mind_scan.grid_rows', fake_grid_rows):
            scanner._scan_pages(None, None)

        for frame in merger.frames:
            self.assertFalse(np.array_equal(np.array(frame), partial))
        self.assertEqual(len(reads), 2)
        self.assertEqual(drags, [227, 227])
        self.assertTrue(scanner._scan_metadata['reached_bottom'])

    def test_interleaved_blank_frames_do_not_stall_stability(self):
        """好帧-坏帧交替时稳定计数照常累计，不被 progress 兜底误杀。

        真机现场（11:53 本轮）：第 1 页后拖拽即遇坏帧反复刷新基准，
        stable 永远 count=1，20 秒后被「船坞扫描画面没有继续滚动」兜底击杀。
        """
        real = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
        blank = np.zeros((720, 1280, 3), np.uint8)
        mid_thumb = np.arange(400, 480)
        bottom_thumb = np.arange(930, 1000)
        script = ([(real, mid_thumb)] * 4  # 第一页读完
                  + [(real, mid_thumb), (blank, mid_thumb)] * 4  # 好帧-坏帧交替
                  + [(real, bottom_thumb)] * 3)  # 恢复后到底收尾
        device = ScriptedDevice(script)

        reads, drags = [], []
        scanner = object.__new__(FleetTechScan)
        scanner.device = device
        scanner.appear = lambda target: target is page_dock.check_button
        scanner.handle_popup_confirm = lambda: False
        scanner._policy = ScanPolicy(95, 125, sorted_desc=True)
        scanner._drag_rows = drags.append
        scanner._read_cards = lambda image, source, names, levels: reads.append(image) or [
            dock_card(row, col) for row in range(3) for col in range(7)]
        scanner._save_scan_evidence = lambda image: ''
        merger = SpyMerger()
        scanner._make_merger = lambda: merger

        with patch('module.retire.mind_scan.Timer', fake_timer), \
                patch('module.retire.mind_scan.DOCK_SCROLL', FakeScroll(device)):
            scanner._scan_pages(None, None)

        # 交替段的第四个「好帧」凑满稳定即推进（第二次拖拽），坏帧从未进入估算。
        for frame in merger.frames:
            self.assertGreater(int(np.array(frame.convert('L')).max()), 100)
        self.assertEqual(len(reads), 2)
        self.assertEqual(drags, [227, 227])
        self.assertTrue(scanner._scan_metadata['reached_bottom'])

    def test_frame_content_rejects_blank_and_near_black_but_keeps_dock_and_scroll(self):
        real = np.array(Image.open(FIXTURES / 'mind_dock_stuck.png').convert('RGB'))
        self.assertTrue(MindCalculatorScan._frame_has_content(MindCalculatorScan._stable_pixels(real)))
        for blank in (np.zeros((720, 1280, 3), np.uint8),
                      np.random.default_rng(1).integers(32, 46, (720, 1280, 3), dtype=np.uint8)):
            self.assertFalse(MindCalculatorScan._frame_has_content(MindCalculatorScan._stable_pixels(blank)))
        # 只剩滚动条的帧（卡片区未渲染）仍低于有效内容下限。
        only_scroll = np.zeros((720, 1280, 3), np.uint8)
        only_scroll[482:559, 1238:1250] = (247, 211, 66)
        self.assertFalse(MindCalculatorScan._frame_has_content(MindCalculatorScan._stable_pixels(only_scroll)))


class ScanStuckGuardTests(unittest.TestCase):
    """读页/OCR 复读期间画面静止，不得被设备层「截图未变化」误杀。"""

    def test_guard_relaxes_image_stuck_limit_only_around_the_scan(self):
        from module.device.device import Device

        class FakeDevice:
            stuck_timeout_override = Device.stuck_timeout_override

            def __init__(self):
                self._stuck_image_timer = SimpleNamespace(limit=30)

        scanner = object.__new__(MindCalculatorScan)
        scanner.device = FakeDevice()
        with scanner._scan_stuck_guard():
            self.assertEqual(scanner.device._stuck_image_timer.limit,
                             MindCalculatorScan.SCAN_IMAGE_STUCK_LIMIT)
        self.assertEqual(scanner.device._stuck_image_timer.limit, 30)

    def test_guard_degrades_to_noop_for_stub_devices(self):
        scanner = object.__new__(MindCalculatorScan)
        scanner.device = Mock()  # 普通 Mock 不支持 with：必须静默降级
        with scanner._scan_stuck_guard():
            pass
        scanner.device = SimpleNamespace(image=None)
        with scanner._scan_stuck_guard():
            pass


if __name__ == '__main__':
    unittest.main()
