"""岛屿摇杆触控 island_swipe_hold 的分发测试。

island_swipe_hold 曾只实现 minitouch 一种触控方案，其他方案走到分发处
什么都不做、静默失效（岛屿计划的地图移动因此只支持一个触控方案）。

这里锁定：
1. argument.yaml 里 Emulator.ControlMethod 的每个可选值都会恰好调用一个后端实现；
2. hold_time（毫秒）原样透传给后端，不做秒/毫秒换算；
3. 未知触控方案回退到 ADB 近似实现，而不是静默无效。

所有后端 Mock 均使用 autospec，按真实签名校验调用参数（防 Mock 盲区）。
"""
import contextlib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from module.device.control import Control
from module.device.method.adb import Adb

REPO_ROOT = Path(__file__).parent.parent

# 岛屿模块的实际调用形态：摇杆中心 -> 偏移点，按住毫秒数
P1 = (218, 507)
P2 = (218, 441)
HOLD = 800

BACKENDS = [
    'island_swipe_hold_minitouch',
    'island_swipe_hold_uiautomator2',
    'island_swipe_hold_maatouch',
    'island_swipe_hold_scrcpy',
    'island_swipe_hold_nemu_ipc',
    'island_swipe_hold_adb',
]


def _make_control(method):
    """构造一个绕过 __init__ 的最小 Control 实例，仅注入触控方案配置。"""
    control = Control.__new__(Control)
    control.config = MagicMock()
    control.config.Emulator_ControlMethod = method
    return control


class TestIslandSwipeHoldDispatch(unittest.TestCase):
    def _dispatch(self, method):
        """按指定触控方案调用一次 island_swipe_hold，返回各后端 Mock 的映射。"""
        control = _make_control(method)
        with contextlib.ExitStack() as stack:
            mocks = {
                name: stack.enter_context(patch.object(Control, name, autospec=True))
                for name in BACKENDS
            }
            control.island_swipe_hold(P1, P2, HOLD)
        return mocks

    def _assert_hold_passthrough(self, mock):
        """后端应被调用一次，且 (p1, p2, hold_time) 原样透传。

        ensure_int 会把坐标转成 numpy 数组，统一转回元组比较；
        autospec 的 Mock 可能记录 self，因此只比对末尾三个参数。
        """
        mock.assert_called_once()
        p1, p2, hold_time = mock.call_args.args[-3:]
        self.assertEqual(tuple(int(v) for v in p1), P1)
        self.assertEqual(tuple(int(v) for v in p2), P2)
        self.assertEqual(hold_time, HOLD)

    def test_every_control_method_option_is_supported(self):
        """ControlMethod 的每个可选项都应有恰好一个后端实现被调用。"""
        with open(REPO_ROOT / 'module/config/argument/argument.yaml', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        options = data['Emulator']['ControlMethod']['option']
        self.assertIsInstance(options, list)
        self.assertTrue(options)

        for option in options:
            with self.subTest(control_method=option):
                mocks = self._dispatch(option)
                called = [name for name, mock in mocks.items() if mock.called]
                self.assertEqual(
                    len(called), 1,
                    f'触控方案 {option} 应恰好分发一个后端，实际: {called}'
                )
                self._assert_hold_passthrough(mocks[called[0]])

    def test_unknown_method_falls_back_to_adb(self):
        """未知触控方案应回退到 ADB 近似实现，而不是像旧版一样静默无效。"""
        mocks = self._dispatch('NotARealMethod')
        self.assertTrue(mocks['island_swipe_hold_adb'].called)
        self._assert_hold_passthrough(mocks['island_swipe_hold_adb'])


class TestIslandSwipeHoldAdb(unittest.TestCase):
    """island_swipe_hold_adb 的手势注入测试。

    两段近似（input swipe 到偏移点 + 在偏移点原地长按）的第二段触摸起点
    不在摇杆中心，游戏抓不住摇杆，角色只会挪动一下就停（真机 2026-09-19）。
    SDK >= 30 必须用 input motionevent 注入「中心按下 → 移到偏移点 →
    保持 → 抬起」的连续手势；更老的系统回退两段近似并警告。
    """

    P1 = (218, 507)
    P2 = (218, 441)

    def _make(self, sdk_ver):
        adb = Adb.__new__(Adb)
        # 实例属性遮蔽 cached_property，避免测试触发 getprop
        adb.sdk_ver = sdk_ver
        return adb

    def test_motionevent_continuous_gesture(self):
        adb = self._make(31)
        with contextlib.ExitStack() as stack:
            shell = stack.enter_context(patch.object(Adb, 'adb_shell', autospec=True))
            # sleep 是 Connection 上的 staticmethod，autospec 会按普通函数
            # 绑定 self 导致签名 TypeError，这里只需记录调用，用普通 Mock
            sleep = stack.enter_context(patch.object(Adb, 'sleep'))
            adb.island_swipe_hold_adb(self.P1, self.P2, 800)

        cmds = [call.args[1] for call in shell.call_args_list]
        self.assertEqual(cmds, [
            ['input', 'motionevent', 'DOWN', *self.P1],
            ['input', 'motionevent', 'MOVE', *self.P2],
            ['input', 'motionevent', 'UP', *self.P2],
        ])
        # 保持发生在宿主侧：MOVE 与 UP 之间睡 hold_time 毫秒
        self.assertEqual(sleep.call_args.args[-1], 0.8)

    def test_old_sdk_falls_back_to_two_segment(self):
        adb = self._make(28)
        with contextlib.ExitStack() as stack:
            shell = stack.enter_context(patch.object(Adb, 'adb_shell', autospec=True))
            swipe = stack.enter_context(patch.object(Adb, 'swipe_adb', autospec=True))
            adb.island_swipe_hold_adb(self.P1, self.P2, 800)

        self.assertEqual(shell.call_count, 0)
        self.assertEqual(swipe.call_count, 2)


if __name__ == '__main__':
    unittest.main()
