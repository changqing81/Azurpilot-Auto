"""自律停滞唤醒的单元测试。

锁定 _auto_search_stall_detected / _auto_search_stall_wake_up 的行为：

1. 无操作未超时：不唤醒；
2. 超时但画面在推进（战斗加载 / 战斗执行 / 结算菜单）：不唤醒；
3. 超时且画面停滞 + 自律图标仍显示运行中：唤醒一次；
4. 唤醒后复查窗口内仍无操作：报 GameStuckError 交回重启，不无限等待；
5. 每次进入移动阶段重新计数（不跨战斗沿用）；
6. 守护模式（_combat_status_timeout 为 None）完全跳过；
7. device.click 自增 click_count 不会被误判成"又操作过"。

阈值依据 2026-11-20 日志中 201 次「自动搜索移动中」实测：
正常段内最长无操作 5.9 秒，取 120 秒是 20 倍余量。
"""

import time
import unittest
from types import SimpleNamespace

from module.combat.auto_search_combat import AutoSearchCombat
from module.exception import GameStuckError


class _StallStub:
    """只挂载停滞检测所需方法的最小桩。

    直接复用真实方法（unbound），验证真实逻辑而非拷贝。
    """

    _auto_search_stall_detected = AutoSearchCombat._auto_search_stall_detected
    _auto_search_stall_wake_up = AutoSearchCombat._auto_search_stall_wake_up
    _auto_search_stall_reset = AutoSearchCombat._auto_search_stall_reset
    auto_search_stall_seconds = 0.05
    auto_search_stall_retry_seconds = 0.05
    auto_search_stall_max_wake = 1
    _auto_search_stall_clicks = 0
    _auto_search_stall_wake = 0
    _auto_search_stall_timer = None
    _auto_search_offset = (5, 5)
    _combat_status_timeout = 300

    def __init__(self, loading=False, executing=False, menu=False, running=True,
                 daemon=False):
        self.device = SimpleNamespace(click_count=0, clicks=[])
        self._loading = loading
        self._executing = executing
        self._menu = menu
        self._running = running
        if daemon:
            self._combat_status_timeout = None
        self._intervals = {}

    # --- 画面识别替身 ---
    def is_combat_loading(self):
        return self._loading

    def is_combat_executing(self):
        return self._executing

    def is_in_auto_search_menu(self):
        return self._menu

    def match_template_color(self, button, offset=(20, 20), interval=0,
                             similarity=0.85, threshold=30):
        return self._running

    def interval_reset(self, button, interval=3):
        self._intervals[getattr(button, 'name', str(button))] = True

    def _click(self, button):
        """模拟 device.click：既记录点击，也让 click_count 自增。"""
        self.device.clicks.append(getattr(button, 'name', str(button)))
        self.device.click_count += 1

    # 桩上把 device.click 指向 _click
    def __getattr__(self, item):
        raise AttributeError(item)


class _FakeButton:
    name = 'AUTO_SEARCH_MAP_OPTION_ON'


class TestAutoSearchStall(unittest.TestCase):

    def _stub(self, **kwargs):
        stub = _StallStub(**kwargs)
        stub.device.click = stub._click
        stub._auto_search_stall_reset()
        return stub

    def test_no_wake_before_timeout(self):
        """刚进入移动阶段、未超时：不唤醒。"""
        stub = self._stub()
        self.assertFalse(stub._auto_search_stall_detected())

    def test_wake_after_timeout(self):
        """超时 + 画面停滞 + 图标运行中：唤醒一次。"""
        stub = self._stub()
        time.sleep(0.08)
        self.assertTrue(stub._auto_search_stall_detected())
        self.assertTrue(stub._auto_search_stall_wake_up())
        self.assertEqual(len(stub.device.clicks), 1, '应点一次自律开关')

    def test_wake_click_resets_click_count_baseline(self):
        """唤醒点击让 click_count 自增，不能被下一帧当成"新操作"。"""
        stub = self._stub()
        time.sleep(0.08)
        stub._auto_search_stall_wake_up()
        baseline = stub._auto_search_stall_clicks
        self.assertEqual(baseline, stub.device.click_count,
                         '基线应等于点击后的计数值')
        # 紧接着一帧不因刚才那次点击而被判为"有操作"
        self.assertFalse(stub._auto_search_stall_detected())

    def test_no_wake_while_combat_loading(self):
        """战斗加载中属于正常推进，不唤醒。"""
        stub = self._stub(loading=True)
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stall_detected())

    def test_no_wake_while_combat_executing(self):
        stub = self._stub(executing=True)
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stall_detected())

    def test_no_wake_in_auto_search_menu(self):
        stub = self._stub(menu=True)
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stall_detected())

    def test_no_wake_when_icon_not_running(self):
        """图标不显示运行中：不是"显示在跑却不动"，不碰。"""
        stub = self._stub(running=False)
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stall_detected())

    def test_click_progress_resets_timer(self):
        """任何真实点击都会重置计时，正常流程不会被误唤醒。"""
        stub = self._stub()
        time.sleep(0.03)
        stub.device.click_count += 1          # 模拟一次正常点击
        self.assertFalse(stub._auto_search_stall_detected())
        time.sleep(0.03)
        self.assertFalse(stub._auto_search_stall_detected(), '计时器已被重置')

    def test_raise_after_wake_failed(self):
        """唤醒一次后复查窗口内仍无操作：主动报错交回重启。"""
        stub = self._stub()
        time.sleep(0.08)
        stub._auto_search_stall_wake_up()
        time.sleep(0.08)
        with self.assertRaises(GameStuckError):
            stub._auto_search_stall_detected()

    def test_no_raise_after_wake_if_progress(self):
        """唤醒后恢复正常移动（有点击）：不报错。"""
        stub = self._stub()
        time.sleep(0.08)
        stub._auto_search_stall_wake_up()
        stub.device.click_count += 1          # 自律恢复了，产生了新点击
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stall_detected())

    def test_reset_on_new_move_stage(self):
        """进入新的移动阶段后重新计数，不跨战斗沿用。"""
        stub = self._stub()
        time.sleep(0.08)
        stub._auto_search_stall_wake_up()
        stub._auto_search_stall_reset()
        self.assertEqual(stub._auto_search_stall_wake, 0, '唤醒次数应归零')
        self.assertFalse(stub._auto_search_stall_detected(), '新阶段重新计时')

    def test_daemon_mode_skipped(self):
        """守护模式下用户可能手动暂停，脚本不得主动点击。"""
        stub = self._stub(daemon=True)
        stub._combat_status_timeout = None
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stall_detected())

    def test_no_timer_guard(self):
        """未经 auto_search_moving 入口（计时器为 None）时不崩、不误判。"""
        stub = self._stub()
        stub._auto_search_stall_timer = None
        time.sleep(0.08)
        self.assertFalse(stub._auto_search_stall_detected())


if __name__ == '__main__':
    unittest.main()
