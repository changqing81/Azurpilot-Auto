"""计划作战快速模式：跳过选项滑动检查的回归，不连接游戏。"""

import unittest
from unittest.mock import Mock

import numpy as np

from module.os_handler.strategic import StrategicSearchHandler


class TestStrategicSearchSkipCheck(unittest.TestCase):
    """用 Mock 替换面板交互，验证 OpsiGeneral.SkipStrategicSearchCheck 对启动流程的分派。"""

    def make_handler(self, skip):
        handler = StrategicSearchHandler.__new__(StrategicSearchHandler)
        handler.config = Mock(OpsiGeneral_SkipStrategicSearchCheck=skip)
        handler.strategy_search_enter = Mock()
        handler.strategic_search_set_tab = Mock()
        handler.strategic_search_set_option = Mock(return_value=True)
        handler.strategic_search_confirm = Mock()
        return handler

    def test_default_still_checks_options(self):
        handler = self.make_handler(skip=False)
        self.assertTrue(handler.strategic_search_start(skip_first_screenshot=True))
        handler.strategy_search_enter.assert_called_once_with()
        handler.strategic_search_set_tab.assert_called_once_with()
        handler.strategic_search_set_option.assert_called_once_with()
        handler.strategic_search_confirm.assert_called_once_with()

    def test_skip_check_confirms_directly(self):
        handler = self.make_handler(skip=True)
        self.assertTrue(handler.strategic_search_start(skip_first_screenshot=True))
        handler.strategy_search_enter.assert_called_once_with()
        handler.strategic_search_set_tab.assert_not_called()
        handler.strategic_search_set_option.assert_not_called()
        handler.strategic_search_confirm.assert_called_once_with()

    def test_failed_option_check_retries(self):
        handler = self.make_handler(skip=False)
        handler.strategic_search_set_option = Mock(side_effect=[False, True])
        self.assertTrue(handler.strategic_search_start(skip_first_screenshot=True))
        self.assertEqual(handler.strategic_search_set_option.call_count, 2)
        handler.strategic_search_confirm.assert_called_once_with()

    def test_gives_up_after_three_failures(self):
        handler = self.make_handler(skip=False)
        handler.strategic_search_set_option = Mock(return_value=False)
        self.assertFalse(handler.strategic_search_start(skip_first_screenshot=True))
        self.assertEqual(handler.strategic_search_set_option.call_count, 3)
        handler.strategic_search_confirm.assert_not_called()


class TestStrategicSearchSetTabGuard(unittest.TestCase):
    """标签页设置的连点防御（2026-10-07 tooltip 遮挡事故）：

    悬停说明气泡盖住"已净化"标签时颜色判定持续失败，原实现无间隔无超时地
    连点直到触发 GameTooManyClickError。现要求：点击限速、超时放行。
    """

    def make_handler(self, image):
        handler = StrategicSearchHandler.__new__(StrategicSearchHandler)
        handler.device = Mock()
        handler.device.image = image
        calls = []
        handler.device.click = lambda button: calls.append(str(button))
        handler._clicks = calls
        return handler

    def test_secured_tab_breaks_without_clicking(self):
        # 标签已选中（蓝色 B=200）：一次都不点，立即退出
        handler = self.make_handler(np.full((720, 1280, 3), (100, 150, 200), dtype=np.uint8))
        handler.loop = Mock(return_value=iter([None] * 5))
        handler.strategic_search_set_tab()
        self.assertEqual(handler._clicks, [])

    def test_obscured_tab_clicks_are_throttled_then_released(self):
        # 标签一直被气泡遮挡（全黑 B=0）：限速点击而非连点，超时后放行不抛错
        handler = self.make_handler(np.zeros((720, 1280, 3), dtype=np.uint8))
        handler.loop = Mock(return_value=iter([None] * 20))
        handler.strategic_search_set_tab()
        # 瞬时完成的 20 次迭代都在 2 秒限速窗口内，只允许第一次点击
        self.assertEqual(len(handler._clicks), 1)

    def test_click_then_secured_breaks(self):
        # 第一帧未选中点击一次，第二帧选中后立即退出
        handler = self.make_handler(np.zeros((720, 1280, 3), dtype=np.uint8))
        blue = np.full((720, 1280, 3), (100, 150, 200), dtype=np.uint8)

        def fake_loop(skip_first=True, timeout=None):
            yield
            handler.device.image = blue
            yield

        handler.loop = fake_loop
        handler.strategic_search_set_tab()
        self.assertEqual(len(handler._clicks), 1)


if __name__ == '__main__':
    unittest.main()
