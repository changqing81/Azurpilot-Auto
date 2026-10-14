"""大世界「等待移动/摄像机稳定」必须有总超时，且检测失败帧不得伪装成有效读数。

线上背景（2026-10-14 12:28:21 → 12:29:10，49 秒，用户手动停止）：meow-5 塞壬
研究装置选完「反复尝试探测隐藏的资源」后，画面停在瓦片检测失效状态（瓦片中心
恒定 0.791 < HOMO_CENTER_THRESHOLD 0.8）。`OSCamera.update_os()` 吞掉
MapDetectionError 后把 homo_loca 硬编成 (53,60)，`Homography.search_tile_corner()`
又只凭 1 个单点误报给出 (79,19)。两簇坐标相距 48.5px，超过
`OSFleet.wait_until_walk_stable()` 的 5.5px 稳定判据 → 确认计时器每轮清零 →
56 轮无限循环。而 `OSMapOperation.map_rescan_current()` 里的自律寻敌
（os_auto_search_run）排在 wait_until_walk_stable 返回之后，永远执行不到。

回归要点：
1. `update_os()` 失败时必须返回 False 并保留上一次有效 homo_loca，不能伪造坐标；
2. `search_tile_corner()` 单点匹配必须拒绝；
3. `wait_until_walk_stable()` / `wait_until_camera_stable()` 必须有总超时，
   且超时值必须短于设备层 stuck_timer（60s），否则 GameStuckError 抢先触发。
"""
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.exception import MapDetectionError
from module.map_detection.homography import HOMO_CORNER_MIN_MATCHES, Homography
from module.os.camera import OSCamera
from module.os.fleet import OS_WAIT_STABLE_TIMEOUT, OSFleet


def bind(handler, name):
    setattr(handler, name, MethodType(getattr(OSFleet, name), handler))
    return handler


def run_with_clock(clock, func):
    """用假时钟驱动 Timer，模拟「每轮迭代 0.7 秒」。"""
    with patch('module.base.timer.time', lambda: clock['t']):
        return func()


CONFIRM_TIMER = SimpleNamespace(limit=3, count=4, reached=lambda: False, reset=lambda: None)


class FakeDevice:
    def __init__(self):
        self.intervals = []

    def screenshot_interval_set(self, *args):
        self.intervals.append(args)


class FakeStable:
    """最小替身：只提供 wait_until_*_stable 用到的成员。

    loop 每次 yield 都把假时钟推进 0.7 秒，模拟一次截图。
    """

    def __init__(self, clock, update_os=None, loca=None):
        self.device = FakeDevice()
        self.clock = clock
        self._update_os = update_os if update_os is not None else (lambda: False)
        self._loca = loca if loca is not None else []
        self.iterations = 0
        self.view = SimpleNamespace(backend=SimpleNamespace(homo_loca=None))

    def loop(self, skip_first=False):
        while True:
            self.clock['t'] += 0.7
            self.iterations += 1
            yield

    def update_os(self):
        result = self._update_os()
        if result and self._loca:
            self.view.backend.homo_loca = self._loca[self.iterations % len(self._loca)]
        return result

    # 事件/界面处理器全部"什么都没发生"
    def handle_map_event(self, drop=None):
        return False

    def handle_retirement(self):
        return False

    def handle_walk_out_of_step(self):
        return False

    def handle_popup_confirm(self, *args, **kwargs):
        return False

    def handle_manjuu(self):
        return False

    def is_in_globe(self):
        return False

    def is_in_storage(self):
        return False

    def is_in_os_mission(self):
        return False

    def handle_os_game_tips(self):
        return False

    def is_in_map_order(self):
        return False

    def combat_appear(self):
        return False

    def appear(self, *args, **kwargs):
        return False

    def appear_then_click(self, *args, **kwargs):
        return False

    def enemy_searching_appear(self):
        return False

    def is_in_map(self):
        return False

    def enemy_searching_color_initial(self):
        pass

    def match_template_color(self, *args, **kwargs):
        return True


class WaitStableTimeoutConstantTests(unittest.TestCase):
    """超时常量本身必须是有限且短于设备层兜底的值。"""

    def test_timeout_is_finite(self):
        self.assertIsInstance(OS_WAIT_STABLE_TIMEOUT, (int, float))
        self.assertGreater(OS_WAIT_STABLE_TIMEOUT, 0)

    def test_timeout_shorter_than_device_stuck_timer(self):
        """必须短于设备层 60s 的 stuck_timer，否则 GameStuckError 抢先重启。"""
        from module.device.device import Device
        self.assertLess(OS_WAIT_STABLE_TIMEOUT, Device.stuck_timer.limit)

    def test_corner_min_matches_at_least_three(self):
        self.assertGreaterEqual(HOMO_CORNER_MIN_MATCHES, 3)


class WalkStableTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.clock = {'t': 1000.0}

    def test_breaks_when_detection_never_succeeds(self):
        """画面一直无法定位：确认计时器永远等不到，必须靠总超时退出。"""
        fake = bind(FakeStable(self.clock, update_os=lambda: False), 'wait_until_walk_stable')
        with patch('module.os.fleet.logger') as log:
            result = run_with_clock(self.clock, lambda: fake.wait_until_walk_stable(confirm_timer=CONFIRM_TIMER))
        self.assertEqual(result, '')
        self.assertGreaterEqual(fake.iterations, int(OS_WAIT_STABLE_TIMEOUT / 0.7) - 1)
        log.warning.assert_called()

    def test_breaks_when_position_jitters(self):
        """检测一直成功但坐标在两簇值之间反复跳：也必须靠总超时退出。"""
        fake = bind(FakeStable(self.clock, update_os=lambda: True,
                               loca=[[79, 19], [53, 60]]), 'wait_until_walk_stable')
        with patch('module.os.fleet.logger') as log:
            result = run_with_clock(self.clock, lambda: fake.wait_until_walk_stable(confirm_timer=CONFIRM_TIMER))
        self.assertEqual(result, '')
        self.assertGreaterEqual(fake.iterations, int(OS_WAIT_STABLE_TIMEOUT / 0.7) - 1)
        log.warning.assert_called()

    def test_failed_detection_does_not_feed_stability_check(self):
        """定位失败的帧不得产生 [单应位置] 日志（旧行为会把假坐标当读数）。"""
        fake = bind(FakeStable(self.clock, update_os=lambda: False), 'wait_until_walk_stable')
        with patch('module.os.fleet.logger') as log:
            run_with_clock(self.clock, lambda: fake.wait_until_walk_stable(confirm_timer=CONFIRM_TIMER))
        logged = [str(call) for call in log.info.call_args_list]
        self.assertFalse(any('单应位置' in item for item in logged))


class CameraStableTimeoutTests(unittest.TestCase):
    def test_detection_failure_terminates_bounded(self):
        """检测持续失败时 wait_until_camera_stable 必须在有限轮次内返回。"""
        clock = {'t': 1000.0}
        fake = bind(FakeStable(clock, update_os=lambda: False), 'wait_until_camera_stable')
        with patch('module.os.fleet.logger'):
            run_with_clock(clock, fake.wait_until_camera_stable)
        self.assertLess(fake.iterations, int(OS_WAIT_STABLE_TIMEOUT / 0.7))

    def test_never_stable_breaks_on_timeout(self):
        """坐标持续跳变时确认计时器永不满足，必须靠总超时退出。"""
        clock = {'t': 1000.0}
        fake = bind(FakeStable(clock, update_os=lambda: True,
                               loca=[[79, 19], [53, 60]]), 'wait_until_camera_stable')
        with patch('module.os.fleet.logger') as log:
            run_with_clock(clock, fake.wait_until_camera_stable)
        self.assertGreaterEqual(fake.iterations, int(OS_WAIT_STABLE_TIMEOUT / 0.7) - 1)
        log.warning.assert_called()


class UpdateOsFailureSemanticsTests(unittest.TestCase):
    """update_os() 的返回值与 homo_loca 保留策略。"""

    class FakeBackend:
        def __init__(self):
            self.homo_loca = None
            self.load_calls = 0

        def load(self, image):
            self.load_calls += 1

    class FakeView:
        def __init__(self, fail_times):
            self.backend = UpdateOsFailureSemanticsTests.FakeBackend()
            self.fail_times = fail_times
            self.load_calls = 0

        def load(self, image):
            self.load_calls += 1
            if self.load_calls <= self.fail_times:
                raise MapDetectionError('Failed to find a free tile')

    class FakeCamera:
        _OS_DETECT_FALLBACK_LOCA = OSCamera._OS_DETECT_FALLBACK_LOCA

        def __init__(self, fail_times=0):
            self.view = UpdateOsFailureSemanticsTests.FakeView(fail_times)
            self.device = SimpleNamespace(image=None)
            self.view_init_calls = 0

        def _view_init(self):
            self.view_init_calls += 1

    def _run(self, fail_times, preset_loca=None, calls=1):
        fake = bind(UpdateOsFailureSemanticsTests.FakeCamera(fail_times), 'update_os')
        if preset_loca is not None:
            fake.view.backend.homo_loca = np.array(preset_loca)
        results = [fake.update_os() for _ in range(calls)]
        return fake, results

    def test_success_returns_true(self):
        fake, results = self._run(0)
        self.assertEqual(results, [True])
        self.assertTrue(fake.os_detect_success)

    def test_failure_returns_false_and_uses_fallback(self):
        """首次即失败：退回兜底坐标，且 backend.load 必须被还原。"""
        fake, results = self._run(1)
        self.assertEqual(results, [False])
        self.assertFalse(fake.os_detect_success)
        self.assertEqual(tuple(fake.view.backend.homo_loca), OSCamera._OS_DETECT_FALLBACK_LOCA)
        # backend.load 必须被还原：还原后调用它应有正常效果，
        # 否则第二次起地图检测会被永久打成 no-op。
        self.assertEqual(fake.view.backend.load_calls, 0)
        fake.view.backend.load(None)
        self.assertEqual(fake.view.backend.load_calls, 1)

    def test_failure_keeps_last_good_loca(self):
        """先成功后失败：必须沿用上一次的 homo_loca，不能被覆盖成固定值。"""
        fake, _ = self._run(0, preset_loca=[52.9, 58.9], calls=1)
        fake.view.fail_times = 1
        fake.view.load_calls = 0
        ok = fake.update_os()
        self.assertFalse(ok)
        self.assertNotEqual(tuple(fake.view.backend.homo_loca), OSCamera._OS_DETECT_FALLBACK_LOCA)
        np.testing.assert_array_equal(fake.view.backend.homo_loca, np.array([52.9, 58.9]))


class SearchTileCornerTests(unittest.TestCase):
    """search_tile_corner 必须拒绝单点误报。"""

    def _homography(self):
        instance = Homography.__new__(Homography)
        instance.config = SimpleNamespace(
            HOMO_TILE=(140, 140),
            HOMO_CENTER_OFFSET=(48, 48),
            HOMO_CORNER_OFFSET_LIST=[(-42, -42), (68, -42), (-42, 69), (69, 69)],
        )
        instance.homo_loca = None
        instance.map_inner = None
        return instance

    def _match(self, hot_templates, value=0.9):
        """按模板序号返回匹配结果图，只有列出的模板存在高分匹配点。"""
        import module.map_detection.homography as hm

        def fake_match(image, template, method):
            result = np.zeros((8, 8), dtype=np.float32)
            if template in hot_templates:
                result[template + 1, 2] = value
            return result
        return patch.object(hm.cv2, 'matchTemplate', fake_match)

    def test_single_match_is_rejected(self):
        instance = self._homography()
        assets = SimpleNamespace(tile_corner_image_list=[0, 1, 2, 3])
        with patch('module.map_detection.homography.ASSETS', assets), self._match({0}):
            ok = instance.search_tile_corner(np.zeros((8, 8)), threshold=0.8)
        self.assertFalse(ok)
        self.assertIsNone(instance.homo_loca)

    def test_enough_matches_is_accepted(self):
        instance = self._homography()
        assets = SimpleNamespace(tile_corner_image_list=[0, 1, 2, 3])
        with patch('module.map_detection.homography.ASSETS', assets), self._match({0, 1, 2}):
            ok = instance.search_tile_corner(np.zeros((8, 8)), threshold=0.8)
        self.assertTrue(ok)
        self.assertIsNotNone(instance.homo_loca)


if __name__ == '__main__':
    unittest.main()
