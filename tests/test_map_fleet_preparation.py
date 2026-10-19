"""作战准备阶段舰队清空的回归测试。

覆盖 module/map/map_fleet_preparation.py：

1. FleetOperator.clear() 的点击节奏：清空已生效时零点击，
   未生效时按最小间隔（3 秒）兜底补点，不出现同一按钮的快速连点。
2. fleet_preparation() 的潜艇让位分支不再预点 FLEET_2_CLEAR，
   清空舰队2 由 fleet_2.clear() 单次完成。
"""

import unittest
from unittest.mock import Mock, patch

import numpy as np

import module.base.timer as timer_module
from module.base.button import Button
from module.map import map_fleet_preparation
from module.map.map_fleet_preparation import FleetOperator, FleetPreparation


# FLEET_*_IN_USE 区域的两个样本：噪声图灰度 std 远大于阈值 27（使用中），
# 纯灰图 std 为 0（已清空）。
IN_USE_IMAGE = np.random.RandomState(0).randint(0, 256, size=(20, 40, 3), dtype=np.uint8)
CLEARED_IMAGE = np.full((20, 40, 3), 128, dtype=np.uint8)


def make_button(name):
    """构造仅用于记录点击的按钮（不带素材文件，避免依赖 assets）。"""
    return Button(area=(0, 0, 10, 10), color=(0, 0, 0), button=(0, 0, 10, 10), name=name)


class FakeClock:
    """受控时钟，用于驱动 Timer 的时间判定。

    起点取非零值：Timer 用 `_start > 0` 表示"已启动"，
    从 0 起算会被当成从未启动（生产环境的单调时钟不会为 0）。
    """

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeDevice:
    """记录每次点击及其发生时刻的假设备。"""

    def __init__(self, clock, frame_time=0.2):
        self.clock = clock
        self.frame_time = frame_time
        self.clicks = []
        self.screenshots = 0

    def click(self, button, *args, **kwargs):
        self.clicks.append((self.clock.now, button))

    def screenshot(self):
        self.screenshots += 1
        self.clock.advance(self.frame_time)


class FakeMain:
    """FleetOperator 需要的最小宿主接口。"""

    def __init__(self, device, allow=True):
        self.device = device
        self.allow = allow
        self.image = CLEARED_IMAGE

    def appear(self, button, offset=None):
        return self.allow

    def image_crop(self, area, copy=False):
        return self.image

    def handle_popup_confirm(self, name=''):
        return False


def make_operator(main):
    return FleetOperator(
        choose=make_button('CHOOSE'),
        advice=make_button('ADVICE'),
        bar=make_button('BAR'),
        clear=make_button('CLEAR'),
        in_use=make_button('IN_USE'),
        hard_satisfied=make_button('HARD'),
        main=main,
    )


class FleetOperatorClearTest(unittest.TestCase):
    def test_clear_skips_click_when_already_cleared(self):
        """槽位已空时 clear() 不点击直接退出。"""
        clock = FakeClock()
        device = FakeDevice(clock)
        main = FakeMain(device)
        main.image = CLEARED_IMAGE
        operator = make_operator(main)

        operator.clear(skip_first_screenshot=True)

        self.assertEqual(device.clicks, [])
        self.assertEqual(device.screenshots, 0)

    def test_clear_clicks_once_then_retries_after_interval(self):
        """清空未生效时按 3 秒间隔补点，生效后退出，不做快连点。"""
        clock = FakeClock()
        device = FakeDevice(clock)
        main = FakeMain(device)
        # 前 3.2 秒舰队槽位仍显示"使用中"，之后清空动画结束
        start = clock.now
        main.image_crop = (
            lambda area, copy=False: IN_USE_IMAGE if clock.now - start < 3.2 else CLEARED_IMAGE
        )
        operator = make_operator(main)

        with patch.object(timer_module, 'time', clock):
            operator.clear(skip_first_screenshot=True)

        self.assertEqual(len(device.clicks), 2)
        first, second = device.clicks[0][0], device.clicks[1][0]
        self.assertEqual(first, 1000.0)
        # 浮点累加误差下用 2.9 兜底，实际为 Timer(3, count=6) 的 3 秒间隔
        self.assertGreaterEqual(second - first, 2.9)


class FleetPreparationSubmarineTest(unittest.TestCase):
    def fleet_preparation(self, submarine_fleet=1):
        main = Mock()
        main.config = Mock()
        main.config.Fleet_SkipPreparation = False
        main.config.Fleet_Fleet1 = 1
        main.config.Fleet_Fleet2 = 2
        main.config.Submarine_Fleet = submarine_fleet
        main.config.SERVER = 'cn'
        main.map_fleet_checked = False
        main.appear.return_value = False

        fleet_1, fleet_2, submarine = Mock(), Mock(), Mock()
        for operator in (fleet_1, fleet_2, submarine):
            operator.is_hard_satisfied.return_value = None
            operator.allow.return_value = True

        with patch.object(map_fleet_preparation, 'FleetOperator', autospec=True) as operator_class:
            operator_class.side_effect = [fleet_1, fleet_2, submarine]
            result = FleetPreparation.fleet_preparation(main)
        return main, (fleet_1, fleet_2, submarine), result

    def test_clear_fleet_2_is_clicked_only_by_clear(self):
        """潜艇让位分支不再预点 FLEET_2_CLEAR，清空由 fleet_2.clear() 负责。"""
        main, (fleet_1, fleet_2, submarine), result = self.fleet_preparation()

        self.assertTrue(result)
        # 预点已删除：整个分支不应再直接点击设备
        main.device.click.assert_not_called()
        submarine.ensure_to_be.assert_called_once_with(1)
        fleet_2.clear.assert_called_once_with()
        fleet_1.ensure_to_be.assert_called_once_with(1)
        fleet_2.ensure_to_be.assert_called_once_with(2)


if __name__ == '__main__':
    unittest.main()
