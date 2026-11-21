"""自律寻敌两级看门狗的单元测试（收编 PR #43 + 本地保护）。

锁定 _auto_search_stuck_check / _auto_search_progress 的行为：

快级（18s）：
1. 画面冻结（认不出自律开关）超时 → 点地图中央（第 1 次）；
2. 图标仍在运行（软进展）→ 不触发快级；
慢级（90s）：
3. 图标仍在运行但持续无进展 → 超时点开关；
4. 软进展只续快级，慢级照常到期；
第 2/3 次唤醒点开关（单次点击防互相抵消）；
5. 唤醒次数用尽 → GameStuckError 交回重启；
6. 真进展（progress）全量重置计时器与尝试计数；
7. 移动阶段入口重置（不跨战斗沿用）；
本地保护：
8. 守护模式（_combat_status_timeout 为 None）完全跳过；
9. 续接链互斥（_restart_resume_guard）跳过；
10. 未经 auto_search_moving 入口（计时器为 None）不误判。
"""

import time
import unittest
from types import SimpleNamespace

from module.combat.auto_search_combat import (
    AutoSearchCombat,
    AUTO_SEARCH_MAP_CENTER,
)
from module.exception import GameStuckError


class _FakeButton:
    def __init__(self, name):
        self.name = name
        self.button = (0, 0, 1, 1)

    def __str__(self):
        return self.name


class _StuckStub:
    """只挂载两级看门狗所需方法的最小桩。"""

    _auto_search_stuck_check = AutoSearchCombat._auto_search_stuck_check
    _auto_search_progress = AutoSearchCombat._auto_search_progress
    _auto_search_stall_reset = AutoSearchCombat._auto_search_stall_reset
    auto_search_stuck_timeout = 0.05
    auto_search_frozen_timeout = 0.05
    auto_search_stuck_max_attempt = 3
    _auto_search_stuck_timer = None
    _auto_search_frozen_timer = None
    _auto_search_offset = (5, 5)
    _combat_status_timeout = 300

    def __init__(self, running=False, daemon=False, resume_guard=False):
        self.device = SimpleNamespace(clicks=[])
        self._running = running
        self._combat_status_timeout = None if daemon else 300
        # 与真实实现一致：互斥标志挂在 __dict__，_auto_search_stuck_check 用
        # self.__dict__.get('_restart_resume_guard') 读取
        if resume_guard:
            self.__dict__['_restart_resume_guard'] = True
        self._intervals = []

    def is_auto_search_running(self):
        return self._running

    def interval_reset(self, button, interval=3):
        self._intervals.append(getattr(button, 'name', str(button)))

    def _click(self, button, control_check=True):
        self.device.clicks.append(getattr(button, 'name', str(button)))

    def _sleep(self, seconds):
        pass


def _stub(**kwargs):
    stub = _StuckStub(**kwargs)
    stub.device.click = stub._click
    stub.device.sleep = stub._sleep
    stub._auto_search_stall_reset()
    return stub


class TestFastWatchdog(unittest.TestCase):

    def test_frozen_screen_clicks_map_center(self):
        """画面冻结（认不出开关）超时：第 1 次点地图中央。"""
        stub = _stub(running=False)
        time.sleep(0.08)
        self.assertTrue(stub._auto_search_stuck_check())
        self.assertEqual(stub.device.clicks, ['AUTO_SEARCH_MAP_CENTER'])

    def test_running_icon_goes_to_slow_path(self):
        """图标显示运行中：快级不判冻结，但慢级兜底仍会触发（点开关）。"""
        stub = _stub(running=True)
        time.sleep(0.08)
        self.assertTrue(stub._auto_search_stuck_check())
        # 不点地图中央（快级被图标可见挡住），走的是开关切换
        self.assertEqual(stub.device.clicks, ['AUTO_SEARCH_MAP_OPTION_ON'])

    def test_soft_progress_only_extends_fast(self):
        """软进展只续快级：慢级仍会到期并触发。"""
        stub = _stub(running=True)
        time.sleep(0.03)
        stub._auto_search_progress(running=True)
        time.sleep(0.03)   # 快级（0.05）未到，慢级（0.05）已过
        self.assertTrue(stub._auto_search_stuck_check())
        self.assertEqual(stub.device.clicks, ['AUTO_SEARCH_MAP_OPTION_ON'])


class TestSlowWatchdog(unittest.TestCase):

    def test_frozen_long_hits_switch(self):
        """快级第一次点中央后再次到期：第 2 次点开关。"""
        stub = _stub(running=False)
        time.sleep(0.08)
        stub._auto_search_stuck_check()          # 第 1 次：点中央
        time.sleep(0.08)
        self.assertTrue(stub._auto_search_stuck_check())   # 第 2 次：点开关
        self.assertEqual(stub.device.clicks,
                         ['AUTO_SEARCH_MAP_CENTER', 'AUTO_SEARCH_MAP_OPTION_ON'])

    def test_switch_click_is_single(self):
        """点开关只点一次（连点两下会互相抵消），且带 interval_reset。"""
        stub = _stub(running=False)
        time.sleep(0.08)
        stub._auto_search_stuck_check()          # 第 1 次
        time.sleep(0.08)
        stub._auto_search_stuck_check()          # 第 2 次：开关
        self.assertEqual(stub.device.clicks.count('AUTO_SEARCH_MAP_OPTION_ON'), 1)
        self.assertIn('AUTO_SEARCH_MAP_OPTION_ON', stub._intervals)

    def test_raise_after_attempts_exhausted(self):
        """3 次唤醒仍无进展：抛 GameStuckError。"""
        stub = _stub(running=False)
        for _ in range(3):
            time.sleep(0.08)
            stub._auto_search_stuck_check()
        time.sleep(0.08)
        with self.assertRaises(GameStuckError):
            stub._auto_search_stuck_check()


class TestProgress(unittest.TestCase):

    def test_real_progress_resets_everything(self):
        """真进展：全量重置，尝试计数归零，重计 3 次。"""
        stub = _stub(running=False)
        time.sleep(0.08)
        stub._auto_search_stuck_check()          # 第 1 次
        stub._auto_search_progress()             # 真进展
        self.assertEqual(stub.__dict__.get('_auto_search_stuck_attempt'), None)
        # 重置后再次到期：又是第 1 次（点中央）而不是第 2 次
        time.sleep(0.08)
        stub._auto_search_stuck_check()
        self.assertEqual(stub.device.clicks,
                         ['AUTO_SEARCH_MAP_CENTER', 'AUTO_SEARCH_MAP_CENTER'])


class TestResetOnNewStage(unittest.TestCase):

    def test_reset_on_new_move_stage(self):
        """进入新的移动阶段：计时器与尝试计数全归零。"""
        stub = _stub(running=False)
        time.sleep(0.08)
        stub._auto_search_stuck_check()
        stub._auto_search_stall_reset()
        self.assertEqual(stub.__dict__.get('_auto_search_stuck_attempt'), None)
        time.sleep(0.08)
        stub._auto_search_stuck_check()
        self.assertEqual(stub.device.clicks,
                         ['AUTO_SEARCH_MAP_CENTER', 'AUTO_SEARCH_MAP_CENTER'])


class TestLocalGuards(unittest.TestCase):

    def test_daemon_mode_skipped(self):
        """守护模式：用户手动暂停时不抢操作。"""
        stub = _stub(daemon=True)
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stuck_check())
        self.assertEqual(stub.device.clicks, [])

    def test_resume_guard_skipped(self):
        """续接链互斥：AutoSearchRecovery 自己管唤醒。"""
        stub = _stub(resume_guard=True)
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stuck_check())
        self.assertEqual(stub.device.clicks, [])

    def test_no_timer_guard(self):
        """未经 auto_search_moving 入口（计时器为 None）不误判。"""
        stub = _stub(running=False)
        stub._auto_search_stuck_timer = None
        stub._auto_search_frozen_timer = None
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stuck_check())


if __name__ == '__main__':
    unittest.main()
