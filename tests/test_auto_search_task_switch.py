"""任务切换时结算菜单必须让位结束任务，不能空转（2026-11-13 事故回归）。

事故现象：Main2 刷 16-4 自动搜索，Research 在 20:10:12 到期。此后
handle_auto_search_continue_retry() 每轮都因 config.task_switched() 为真而
一次都不点击就 break，外层 MapOperation.enter_map 看到「再次前往」仍在画面上
就 continue 重进，整个循环 190 秒 0 次点击，最终被设备层判成
GameStuckError: [设备-卡死] 等待时间过长 并重启游戏。

锁定三件事：
1. 检测到任务切换时必须抛 TaskEnd（交由 alas.py 调度循环正常收尾），
   不能只是 break 返回；
2. 让位前要尝试退出结算菜单，且退出有超时，退不出去也要让位；
3. 没有任务切换时行为不变：菜单不在就不点击、返回 False。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.config.config import TaskEnd
from module.handler.auto_search import AutoSearchHandler


class _TaskSwitchStub:
    """只挂载结算菜单重试链路的最小桩。"""

    handle_auto_search_continue_retry = AutoSearchHandler.handle_auto_search_continue_retry
    handle_auto_search_continue = AutoSearchHandler.handle_auto_search_continue
    _stop_for_task_switched = AutoSearchHandler._stop_for_task_switched
    _auto_search_menu_offset = AutoSearchHandler._auto_search_menu_offset
    auto_search_continue_interval = AutoSearchHandler.auto_search_continue_interval
    auto_search_continue_retry = AutoSearchHandler.auto_search_continue_retry
    auto_search_switch_exit_timeout = AutoSearchHandler.auto_search_switch_exit_timeout
    _auto_search_continue_attempts = 0

    def __init__(self, switched=True, menu=True, frames=4):
        self.frames = frames
        self.exit_timeout = 'unset'
        self.clicked = []
        self.task_stop_calls = []
        self.config = SimpleNamespace(
            task_switched=Mock(return_value=switched),
            task_stop=Mock(side_effect=self._task_stop),
        )
        self.device = SimpleNamespace(click=lambda button: self.clicked.append(button))
        self._menu = menu

    def _task_stop(self, message=''):
        self.task_stop_calls.append(message)
        raise TaskEnd(message)

    def appear(self, button, offset=(20, 20), interval=0):
        return self._menu

    def loop(self, *args, **kwargs):
        for _ in range(self.frames):
            yield None
        raise AssertionError('handle_auto_search_continue_retry 在给定帧数内没有结束')

    def ensure_auto_search_exit(self, skip_first_screenshot=True, timeout=None):
        self.exit_timeout = timeout
        return True


class TestTaskSwitchStopsCampaign(unittest.TestCase):

    def test_switched_task_raises_task_end(self):
        """任务切换时必须抛 TaskEnd 结束当前任务，而不是静默 break。"""
        stub = _TaskSwitchStub(switched=True)

        with self.assertRaises(TaskEnd):
            stub.handle_auto_search_continue_retry()

        self.assertEqual(len(stub.task_stop_calls), 1, '必须真正停止任务')
        self.assertEqual(stub.clicked, [], '让位过程中不得点击「再次前往」')

    def test_exit_menu_before_yield(self):
        """让位前要尝试退出结算菜单，且使用短超时避免退不出去被判卡死。"""
        stub = _TaskSwitchStub(switched=True)

        with self.assertRaises(TaskEnd):
            stub.handle_auto_search_continue_retry()

        self.assertEqual(stub.exit_timeout, stub.auto_search_switch_exit_timeout)
        self.assertLessEqual(
            stub.auto_search_switch_exit_timeout, 30,
            '退出超时必须明显小于设备层 60 秒卡死判定')

    def test_exit_timeout_still_yields(self):
        """退出结算菜单失败（超时）也必须让位，不能继续空转。"""
        stub = _TaskSwitchStub(switched=True)
        stub.ensure_auto_search_exit = Mock(return_value=False)

        with self.assertRaises(TaskEnd):
            stub.handle_auto_search_continue_retry()

        self.assertEqual(len(stub.task_stop_calls), 1)

    def test_no_switch_keeps_old_behavior(self):
        """没有任务切换时行为不变：菜单在就正常点击并返回 True。"""
        # 点击生效后菜单消失，让重试流程正常结束
        stub = _TaskSwitchStub(switched=False)

        def loop_after_click(*args, **kwargs):
            for index in range(stub.frames):
                if stub.clicked and index > 0:
                    stub._menu = False
                yield None
            raise AssertionError('handle_auto_search_continue_retry 在给定帧数内没有结束')

        stub.loop = loop_after_click

        clicked = stub.handle_auto_search_continue_retry()

        self.assertTrue(clicked)
        self.assertTrue(stub.clicked)
        self.assertEqual(stub.task_stop_calls, [])

    def test_menu_missing_returns_false(self):
        """菜单不在时直接返回 False，不点击、不停任务。"""
        stub = _TaskSwitchStub(switched=True, menu=False)

        self.assertFalse(stub.handle_auto_search_continue_retry())
        self.assertEqual(stub.clicked, [])
        self.assertEqual(stub.task_stop_calls, [])


class TestFastForwardContinuePropagates(unittest.TestCase):
    """FastForwardHandler 覆写的「再次前往」不得吞掉 TaskEnd。"""

    def test_fast_forward_propagates_task_end(self):
        from module.handler.fast_forward import FastForwardHandler

        class _FastForwardStub:
            handle_auto_search_continue = FastForwardHandler.handle_auto_search_continue
            _auto_search_menu_offset = AutoSearchHandler._auto_search_menu_offset

            def __init__(self):
                self.map_is_2x_book = False
                self.config = SimpleNamespace(Campaign_Use2xBook=False)
                self.handle_2x_book_setting = Mock()
                self.handle_auto_search_continue_retry = Mock(side_effect=TaskEnd('switched'))
                self.interval_reset = Mock()

            def appear(self, button, offset=(20, 20), interval=0):
                return True

        stub = _FastForwardStub()

        with self.assertRaises(TaskEnd):
            stub.handle_auto_search_continue(drop=None)


if __name__ == '__main__':
    unittest.main()
