"""模拟器卡死保护（CrashGuard）判定逻辑单元测试。

本测试锁定「防误杀」的四道闸门，确保以下场景**不会**触发重启：

1. 连续失败次数不足
2. 连续失败次数够了，但持续时长不足（防误杀核心参数）
3. 模拟器进程已不存在（是崩溃不是卡死，应走启动流程）
4. adb 设备状态不是 device（offline 只需重连）

以及「防重启风暴」的两道限制：

5. 冷却期内不判定
6. 时间窗内重启次数达上限后挂起

全部测试不依赖真设备、不启动子进程，用 Mock 替换底层探测。
"""

import threading
import time
import unittest
from unittest.mock import Mock, patch

from module.handler.emulator_crash_guard import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_HEARTBEAT_FAILURES,
    DEFAULT_MAX_RESTARTS,
    CrashGuard,
)


def make_guard(**config_values):
    """构造一个不启动线程、不碰真实设备的 CrashGuard。

    Args:
        **config_values: 覆盖默认配置值，如 `CrashConfirmSeconds=60`。

    Returns:
        CrashGuard: 已注入 Mock 调度器的实例。
    """
    defaults = {
        'CrashProtection': True,
        'CrashDryRun': False,
        'CrashHeartbeatTimeout': 5,
        'CrashHeartbeatFailures': DEFAULT_HEARTBEAT_FAILURES,
        'CrashConfirmSeconds': 60,
        'CrashCooldownSeconds': DEFAULT_COOLDOWN_SECONDS,
        'CrashMaxRestarts': DEFAULT_MAX_RESTARTS,
        'CrashRestartWindowHours': 6,
    }
    defaults.update(config_values)

    alas = Mock()
    alas.config_name = 'test'
    alas.config = Mock()
    for key, value in defaults.items():
        setattr(alas.config, f'EmulatorManagement_{key}', value)
    # task_call 是第二枪里会调用的
    alas.config.task_call = Mock()
    alas.__dict__.pop('device', None)

    guard = CrashGuard(alas)
    guard._active = True
    guard._task_name = 'TestTask'
    return guard


class TestShouldTriggerGates(unittest.TestCase):
    """判定四道闸门：任一不满足都不得触发。"""

    def test_not_enough_failures(self):
        """连续失败次数不足时不得触发。"""
        guard = make_guard(CrashHeartbeatFailures=3, CrashConfirmSeconds=0)
        guard._fail_count = 2
        guard._first_fail_time = time.monotonic() - 1000

        triggered, reason = guard._should_trigger()

        self.assertFalse(triggered)
        self.assertIn('未达阈值', reason)

    def test_enough_failures_but_duration_too_short(self):
        """次数够了但持续时长不足时不得触发（防误杀核心）。"""
        guard = make_guard(CrashHeartbeatFailures=3, CrashConfirmSeconds=60)
        guard._fail_count = 3
        # 只持续了 10 秒，远小于 60 秒阈值
        guard._first_fail_time = time.monotonic() - 10

        triggered, reason = guard._should_trigger()

        self.assertFalse(triggered, '持续时长不足时绝不能触发，这是防误杀的核心闸门')
        self.assertIn('时长不足', reason)

    def test_process_gone_does_not_trigger(self):
        """模拟器进程已不存在时不得触发（是崩溃不是卡死）。"""
        guard = make_guard(CrashHeartbeatFailures=3, CrashConfirmSeconds=0)
        guard._fail_count = 3
        guard._first_fail_time = time.monotonic() - 1000
        guard._probe_emulator_process = Mock(return_value=(False, '未找到进程'))
        guard._probe_device_status = Mock(return_value='device')

        triggered, reason = guard._should_trigger()

        self.assertFalse(triggered, '进程已消失应走启动流程，不能发 kill')
        self.assertIn('进程不存在', reason)

    def test_device_offline_does_not_trigger(self):
        """adb 状态为 offline 时不得触发（重连即可）。"""
        guard = make_guard(CrashHeartbeatFailures=3, CrashConfirmSeconds=0)
        guard._fail_count = 3
        guard._first_fail_time = time.monotonic() - 1000
        guard._probe_emulator_process = Mock(return_value=(True, 'PID=123'))
        guard._probe_device_status = Mock(return_value='offline')

        triggered, reason = guard._should_trigger()

        self.assertFalse(triggered, 'offline 状态重连 ADB 即可，不应杀模拟器')
        self.assertIn('offline', reason)

    def test_all_gates_pass(self):
        """四道闸门全部满足时才触发。"""
        guard = make_guard(CrashHeartbeatFailures=3, CrashConfirmSeconds=60)
        guard._fail_count = 3
        guard._first_fail_time = time.monotonic() - 100
        guard._probe_emulator_process = Mock(return_value=(True, 'PID=123'))
        guard._probe_device_status = Mock(return_value='device')

        triggered, reason = guard._should_trigger()

        self.assertTrue(triggered)
        self.assertIn('持续', reason)


class TestCooldown(unittest.TestCase):
    """冷却期：重启后给模拟器与游戏充分的启动时间。"""

    def test_no_cooldown_initially(self):
        """从未重启过时不在冷却期。"""
        guard = make_guard()
        self.assertFalse(guard._in_cooldown())

    def test_in_cooldown_right_after_restart(self):
        """刚重启完应在冷却期。"""
        guard = make_guard(CrashCooldownSeconds=600)
        guard._last_restart_end = time.monotonic()

        self.assertTrue(guard._in_cooldown())

    def test_cooldown_expired(self):
        """冷却期过后不再拦截。"""
        guard = make_guard(CrashCooldownSeconds=60)
        guard._last_restart_end = time.monotonic() - 120

        self.assertFalse(guard._in_cooldown())


class TestRestartStormProtection(unittest.TestCase):
    """防重启风暴：时间窗内达上限后挂起。"""

    def test_quota_not_exhausted(self):
        """重启次数未达上限时配额充足。"""
        guard = make_guard(CrashMaxRestarts=3, CrashRestartWindowHours=6)
        guard._restart_history = [time.monotonic(), time.monotonic()]

        self.assertFalse(guard._restart_quota_exhausted())

    def test_quota_exhausted(self):
        """重启次数达上限时配额耗尽。"""
        guard = make_guard(CrashMaxRestarts=3, CrashRestartWindowHours=6)
        guard._restart_history = [time.monotonic() for _ in range(3)]

        self.assertTrue(guard._restart_quota_exhausted())

    def test_old_records_are_cleaned(self):
        """超出时间窗的旧记录应被清理，不占用配额。"""
        guard = make_guard(CrashMaxRestarts=3, CrashRestartWindowHours=1)
        # 3 次重启都发生在 2 小时前，超出 1 小时时间窗
        old = time.monotonic() - 2 * 3600
        guard._restart_history = [old, old, old]

        self.assertFalse(
            guard._restart_quota_exhausted(),
            '超出时间窗的记录必须被清理，否则重启一次就永久挂起',
        )
        self.assertEqual(guard._restart_history, [])

    def test_record_restart_updates_history_and_cooldown(self):
        """记录重启应同时更新历史与冷却起点。"""
        guard = make_guard()
        guard._record_restart()

        self.assertEqual(len(guard._restart_history), 1)
        self.assertGreater(guard._last_restart_end, 0)
        self.assertTrue(guard._in_cooldown())

    def test_suspend_after_quota(self):
        """配额耗尽后应挂起并置位 _suspended。"""
        guard = make_guard(CrashMaxRestarts=3)
        guard._restart_history = [time.monotonic() for _ in range(3)]
        guard._notify = Mock()

        guard._suspend('测试原因')

        self.assertTrue(guard._suspended)
        guard._notify.assert_called_once()


class TestDryRun(unittest.TestCase):
    """演练模式：只告警不执行任何重启动作。"""

    def test_dry_run_does_not_restart(self):
        """演练模式下不得调用第二枪。"""
        guard = make_guard(CrashDryRun=True)
        guard._second_shot_restart_emulator = Mock(return_value=True)
        guard._first_shot_adb_kill_server = Mock(return_value=True)
        guard._save_scene = Mock()
        guard._notify = Mock()

        guard._recover('测试判定')

        guard._second_shot_restart_emulator.assert_not_called()
        guard._first_shot_adb_kill_server.assert_not_called()
        guard._save_scene.assert_not_called()
        guard._notify.assert_called_once()

    def test_dry_run_disabled_executes(self):
        """关闭演练模式时应真正执行恢复。"""
        guard = make_guard(CrashDryRun=False)
        guard._save_scene = Mock()
        guard._notify = Mock()
        # 第一枪成功且主线程恢复 → 不走第二枪
        guard._first_shot_adb_kill_server = Mock(return_value=True)
        guard._wait_main_thread_recover = Mock(return_value=True)

        guard._recover('测试判定')

        guard._save_scene.assert_called_once()
        guard._first_shot_adb_kill_server.assert_called_once()


class TestTwoShotStrategy(unittest.TestCase):
    """两枪策略：第一枪有效则不杀模拟器。"""

    def test_first_shot_success_skips_restart(self):
        """第一枪（adb kill-server）有效时不重启模拟器。"""
        guard = make_guard()
        guard._save_scene = Mock()
        guard._notify = Mock()
        guard._first_shot_adb_kill_server = Mock(return_value=True)
        guard._wait_main_thread_recover = Mock(return_value=True)
        guard._second_shot_restart_emulator = Mock(return_value=True)

        guard._recover('ADB 假死')

        guard._second_shot_restart_emulator.assert_not_called()
        self.assertEqual(guard._fail_count, 0, '恢复后应重置失败计数')

    def test_first_shot_fail_falls_through_to_restart(self):
        """第一枪无效时才执行第二枪。"""
        guard = make_guard()
        guard._save_scene = Mock()
        guard._notify = Mock()
        guard._first_shot_adb_kill_server = Mock(return_value=True)
        guard._wait_main_thread_recover = Mock(return_value=False)
        guard._second_shot_restart_emulator = Mock(return_value=True)

        guard._recover('真卡死')

        guard._second_shot_restart_emulator.assert_called_once()

    def test_concurrent_recover_is_skipped(self):
        """恢复流程并发时应跳过，不能重复杀模拟器。"""
        guard = make_guard()
        guard._recover_lock.acquire()
        try:
            guard._recover('并发测试')
            # 拿不到锁时直接返回，不会走到保存现场
            self.assertFalse(guard._recovering)
        finally:
            guard._recover_lock.release()


class TestTickStateMachine(unittest.TestCase):
    """探测循环的状态机：成功清零、失败累加。"""

    def test_success_resets_counter(self):
        """心跳成功应清零失败计数。"""
        guard = make_guard()
        guard._fail_count = 2
        guard._first_fail_time = time.monotonic() - 100
        guard.probe_heartbeat = Mock(return_value=True)

        guard._tick()

        self.assertEqual(guard._fail_count, 0)
        self.assertEqual(guard._first_fail_time, 0.0)

    def test_first_failure_starts_timer(self):
        """首次失败应记录起始时刻。"""
        guard = make_guard()
        guard.probe_heartbeat = Mock(return_value=False)
        guard._probe_emulator_process = Mock(return_value=(True, 'PID=1'))
        guard._probe_device_status = Mock(return_value='device')

        before = time.monotonic()
        guard._tick()

        self.assertEqual(guard._fail_count, 1)
        self.assertGreaterEqual(guard._first_fail_time, before)

    def test_failure_accumulates_without_trigger(self):
        """失败累加但未达阈值时不触发恢复。"""
        guard = make_guard(CrashHeartbeatFailures=3)
        guard.probe_heartbeat = Mock(return_value=False)
        guard._probe_emulator_process = Mock(return_value=(True, 'PID=1'))
        guard._probe_device_status = Mock(return_value='device')
        guard._recover = Mock()

        for _ in range(2):
            guard._tick()

        guard._recover.assert_not_called()
        self.assertEqual(guard._fail_count, 2)


class TestConfigFallback(unittest.TestCase):
    """配置读取失败时必须回退默认值，不能抛异常。"""

    def test_missing_config_uses_default(self):
        """配置项不存在时返回默认值。"""
        alas = Mock()
        alas.config = Mock(spec=[])  # 任何属性访问都会 AttributeError
        guard = CrashGuard(alas)

        self.assertEqual(
            guard._get_int('CrashHeartbeatTimeout', 5),
            5,
            '配置读取失败必须回退默认值，否则保护模块自身会崩',
        )
        self.assertFalse(guard._get_bool('CrashProtection', False))

    def test_zero_or_negative_falls_back(self):
        """配置值非法（0 或负数）时回退默认值。"""
        guard = make_guard(CrashHeartbeatTimeout=0)

        self.assertEqual(guard._get_int('CrashHeartbeatTimeout', 5), 5)

    def test_enabled_reads_config(self):
        """总开关正确反映配置。"""
        self.assertTrue(make_guard(CrashProtection=True)._enabled())
        self.assertFalse(make_guard(CrashProtection=False)._enabled())


if __name__ == '__main__':
    unittest.main()
