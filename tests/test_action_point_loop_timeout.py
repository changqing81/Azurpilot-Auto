"""行动力弹窗进/出循环必须带超时，避免界面不匹配时截图空转。

线上背景（2026-10-14 11:01:01 → 11:04:15，194 秒）：`action_point_enter`
用无超时的 `for _ in self.loop()` 死等 `ACTION_POINT_USE`；当时游戏已回主页，
条件永不满足 → 循环截图、零点击、零日志，直到设备层 `stuck_timer_long`
（195s）抛 GameStuckError 才自愈。
"""
import unittest
from types import MethodType
from unittest.mock import patch

from module.os_handler.action_point import ActionPointHandler


class FakeHandler:
    """最小替身：loop 立即结束（模拟超时），界面全部不匹配。"""

    ACTION_POINT_ENTER_TIMEOUT = 15
    ACTION_POINT_QUIT_TIMEOUT = 15

    def __init__(self):
        self.loop_timeouts = []

    def loop(self, timeout=None):
        self.loop_timeouts.append(timeout)
        return
        yield  # pragma: no cover - 标记为生成器

    def appear(self, *args, **kwargs):
        return False

    def appear_then_click(self, *args, **kwargs):
        return False

    def handle_map_event(self):
        return False

    def interval_reset(self, *args, **kwargs):
        pass


def bind(handler, name):
    setattr(handler, name, MethodType(getattr(ActionPointHandler, name), handler))
    return handler


class ActionPointLoopTimeoutTests(unittest.TestCase):
    def test_enter_passes_timeout_and_warns(self):
        handler = bind(FakeHandler(), 'action_point_enter')
        with patch('module.os_handler.action_point.logger') as log:
            handler.action_point_enter()
        self.assertEqual(handler.loop_timeouts, [handler.ACTION_POINT_ENTER_TIMEOUT])
        log.warning.assert_called_once()

    def test_quit_passes_timeout_and_warns(self):
        handler = bind(FakeHandler(), 'action_point_quit')
        with patch('module.os_handler.action_point.logger') as log:
            handler.action_point_quit()
        self.assertEqual(handler.loop_timeouts, [handler.ACTION_POINT_QUIT_TIMEOUT])
        log.warning.assert_called_once()

    def test_timeouts_are_finite_and_short(self):
        """超时必须显著短于设备层 195s 兜底，否则事故重演。"""
        self.assertLess(ActionPointHandler.ACTION_POINT_ENTER_TIMEOUT, 60)
        self.assertLess(ActionPointHandler.ACTION_POINT_QUIT_TIMEOUT, 60)


class ActionPointSetCheckTimeoutTests(unittest.TestCase):
    """同一文件里另外两处「等回到地图」的循环也必须带超时。"""

    def test_set_passes_timeout_when_waiting_popup_close(self):
        handler = bind(FakeHandler(), 'action_point_set')
        handler.action_point_enter = lambda: None
        handler.handle_action_point = lambda *args, **kwargs: True
        with patch('module.os_handler.action_point.logger'):
            self.assertTrue(handler.action_point_set())
        self.assertEqual(handler.loop_timeouts, [handler.ACTION_POINT_QUIT_TIMEOUT])

    def test_check_passes_timeout_when_waiting_back_to_map(self):
        handler = bind(FakeHandler(), 'action_point_check')
        handler.action_point_enter = lambda: None
        handler.action_point_safe_get = lambda: None
        handler.action_point_quit = lambda: None
        handler._action_point_total = 100
        with patch('module.os_handler.action_point.logger'):
            self.assertTrue(handler.action_point_check(50))
        self.assertEqual(handler.loop_timeouts, [handler.ACTION_POINT_QUIT_TIMEOUT])


if __name__ == '__main__':
    unittest.main()
