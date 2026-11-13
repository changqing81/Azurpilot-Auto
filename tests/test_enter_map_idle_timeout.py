"""进入地图阶段的空闲兜底回归测试（2026-11-13 事故）。

事故现象：enter_map 的两个退出条件（campaign_click / fleet_click > 5）都按
"点了几次"计数，只有点击才会累积。一旦某个分支被上层拦下、整个循环一次都不点，
这些计数永远是 0，只能干等地设备层 60 秒判死并重启游戏（现场 190 秒 0 点击，
870 圈空转）。锁定：

1. 连续 enter_map_idle_timeout 秒没有任何点击（按 device.click_count 判断，
   不看 detect_record——那里只是检测过，不代表操作过）必须主动抛 GameStuckError；
2. 该超时要明显小于设备层 60 秒卡死判定；
3. 有点击进展时不得误报。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.exception import GameStuckError
from module.map.map_operation import MapOperation


class FakeTimer:
    """可手动触发的 Timer 替身，避免测试真的等 40 秒。"""

    instances = []

    def __init__(self, limit, count=0):
        self.limit = limit
        self.count = count
        self.expired = False
        self.reset_calls = 0
        FakeTimer.instances.append(self)

    def start(self):
        return self

    def reset(self):
        self.reset_calls += 1
        self.expired = False
        return self

    def clear(self):
        return self

    def reached(self):
        return self.expired

    @classmethod
    def idle_timer(cls):
        for timer in cls.instances:
            if timer.limit == MapOperation.enter_map_idle_timeout:
                return timer
        return None

    @classmethod
    def framework_timers(cls):
        """enter_map 内部的 campaign/map/fleet 计时器（limit=5）。"""
        return [t for t in cls.instances if t.limit == 5]

    @classmethod
    def reset_registry(cls):
        cls.instances = []


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class _EnterMapExit(Exception):
    """测试用的提前退出信号，不是被测代码的异常。"""


class EnterMapIdleTimeoutTest(unittest.TestCase):

    def setUp(self):
        FakeTimer.reset_registry()
        self.frames = 0
        self.device = SimpleNamespace(
            click_count=0,
            detect_record={'AUTO_SEARCH_MENU_CONTINUE', 'IN_MAP'},
        )
        self.task = MapOperation.__new__(MapOperation)
        self.task.device = self.device
        self.task.config = SimpleNamespace(campaign_name='campaign_16_4')
        self.task.config.DropRecord_CombatRecord = 'do_not'
        self.task.stat = SimpleNamespace(new=Mock(return_value=_NullContext()))
        self.task.is_in_map = Mock(return_value=False)
        self.task.appear = Mock(return_value=False)
        self.task.appear_then_click = Mock(return_value=False)
        self.task.handle_handover_conflict = Mock(return_value=False)
        self.task.handle_auto_search_continue = Mock(return_value=False)
        self.task.handle_retirement = Mock(return_value=False)
        self.task.handle_use_data_key = Mock(return_value=False)
        self.task.handle_submarine_support_popup = Mock(return_value=False)
        self.task.handle_combat_low_emotion = Mock(return_value=False)
        self.task.handle_urgent_commission = Mock(return_value=False)
        self.task.handle_2x_book_popup = Mock(return_value=False)
        self.task.handle_submarine_cost_popup = Mock(return_value=False)
        self.task.handle_story_skip = Mock(return_value=False)
        self.task.map_is_auto_search = False
        self.task.handle_in_map_with_enemy_searching = Mock(return_value=False)

        patcher = patch('module.map.map_operation.Timer', FakeTimer)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _screenshot(self, on_frame=None):
        def screenshot():
            self.frames += 1
            if on_frame is not None:
                on_frame()
        self.device.screenshot = screenshot

    def test_idle_enter_map_raises_stuck(self):
        """整个循环一次都没点击：空闲兜底触发，主动抛 GameStuckError。"""

        def expire():
            if self.frames >= 3:
                timer = FakeTimer.idle_timer()
                assert timer is not None, 'enter_map 必须创建空闲计时器'
                timer.expired = True

        self._screenshot(expire)

        with self.assertRaises(GameStuckError):
            self.task.enter_map(Mock(), mode='normal', skip_first_screenshot=False)

    def test_idle_timeout_precedes_device_watchdog(self):
        """空闲兜时必须明显小于设备层 60 秒卡死判定，否则兜不住。"""
        self.assertLess(MapOperation.enter_map_idle_timeout, 60)

    def test_progress_keeps_idle_timer_alive(self):
        """有点击进展时不得误报：空闲计时器要被重置。"""

        def click_each_frame():
            self.device.click_count += 1
            if self.frames >= 5:
                raise _EnterMapExit

        self._screenshot(click_each_frame)

        with self.assertRaises(_EnterMapExit):
            self.task.enter_map(Mock(), mode='normal', skip_first_screenshot=False)

        idle_timer = FakeTimer.idle_timer()
        self.assertIsNotNone(idle_timer)
        self.assertGreaterEqual(idle_timer.reset_calls, 4, '每帧都有点击，空闲计时器应反复重置')


if __name__ == '__main__':
    unittest.main()
