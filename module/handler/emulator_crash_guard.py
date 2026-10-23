"""后台卡死检测：只提交恢复请求，绝不在旧任务运行时启停模拟器。"""

import threading
import time

from module.handler.emulator_recovery_state import RecoveryBlocked
from module.logger import logger

CRASH_GUARD_INTERVAL = 5
DEFAULT_HEARTBEAT_TIMEOUT = 5
DEFAULT_HEARTBEAT_FAILURES = 3
DEFAULT_CONFIRM_SECONDS = 60
DEFAULT_COOLDOWN_SECONDS = 600
DEFAULT_MAX_RESTARTS = 3
DEFAULT_RESTART_WINDOW_HOURS = 6


class CrashGuard:
    """连续失败与持续时间双重确认；日志输出永不作为恢复成功证据。"""

    def __init__(self, alas):
        self.alas = alas
        self._thread = None
        self._stop_event = threading.Event()
        self._active = False
        self._task_name = ''
        self._generation = 0
        self._fail_count = 0
        self._first_fail_time = 0.0
        self._state_lock = threading.Lock()

    def _get_int(self, attr, default):
        value = self.alas.recovery.option('EmulatorManagement', attr, default)
        return value if value > 0 else default

    def _enabled(self):
        return self.alas.recovery.option('EmulatorManagement', 'CrashProtection', False)

    def start(self):
        """停止尚未完成时保留旧线程，禁止 clear 同一事件后生成双守护线程。"""
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name='alas-crash-guard', daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self.deactivate()
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning('[卡死保护] 正在等待有界探测退出，保留线程引用')
                return False
            self._thread = None
        return True

    def activate(self, task_name):
        with self._state_lock:
            self._generation += 1
            self._task_name = task_name
            self._active = True
            self._fail_count = 0
            self._first_fail_time = 0.0

    def deactivate(self):
        with self._state_lock:
            self._generation += 1
            self._active = False
            self._task_name = ''
            self._fail_count = 0
            self._first_fail_time = 0.0

    def probe_heartbeat(self, timeout=None):
        if timeout is None:
            timeout = self._get_int('CrashHeartbeatTimeout', DEFAULT_HEARTBEAT_TIMEOUT)
        return self.alas.recovery.probe().heartbeat(timeout)

    def _probe_device_status(self):
        return self.alas.recovery.probe().status()

    def _probe_emulator_process(self):
        from module.device.platform import Platform
        from module.handler.emulator_target import RecoveryTarget
        try:
            target = RecoveryTarget(Platform(self.alas.config, connect=False))
            return target.running(), str(target.identity)
        except (RecoveryBlocked, OSError, ValueError) as exc:
            return None, str(exc)

    def _should_trigger(self):
        failures = self._get_int('CrashHeartbeatFailures', DEFAULT_HEARTBEAT_FAILURES)
        # 已保存的激进阈值也不能把一次瞬时失败升级为重启。
        confirm = max(60, self._get_int('CrashConfirmSeconds', DEFAULT_CONFIRM_SECONDS))
        if self._fail_count < max(3, failures):
            return False, '连续失败次数不足'
        if time.monotonic() - self._first_fail_time < confirm:
            return False, '持续确认时间不足'
        status = self._probe_device_status()
        if status not in ('device', 'offline', 'unknown'):
            return False, f'设备状态 {status} 不允许自动恢复'
        # unknown 不能证明 server 正常，禁止把共享 server 故障归因给单实例。
        if status == 'unknown':
            return False, 'ADB 状态未知，停止升级恢复'
        alive, info = self._probe_emulator_process()
        if alive is None:
            return False, f'实例身份或状态无法确认: {info}'
        return True, f'目标实例连续通信失败，实例运行状态={alive}'

    def _tick(self):
        with self._state_lock:
            generation = self._generation
            device = self.alas.__dict__.get('device')
        if device is None:
            return
        alive = self.probe_heartbeat()
        with self._state_lock:
            if generation != self._generation or not self._active or self._stop_event.is_set():
                return
            if alive:
                self._fail_count = 0
                self._first_fail_time = 0.0
                return
            if self._fail_count == 0:
                self._first_fail_time = time.monotonic()
            self._fail_count += 1
        triggered, reason = self._should_trigger()
        if triggered:
            if self.alas.recovery.option('EmulatorManagement', 'CrashDryRun', False):
                logger.warning(f'[卡死保护] 演练判定: {reason}；未执行恢复')
                return
            # 探测期间任务可能结束、开关可能关闭；执行前重新核对代次。
            with self._state_lock:
                if generation != self._generation or not self._active or self._stop_event.is_set():
                    return
                if self.alas.recovery.request(reason, source='crash', device=device):
                    self._active = False

    def _loop(self):
        while not self._stop_event.wait(CRASH_GUARD_INTERVAL):
            if not self._active or not self._enabled():
                continue
            try:
                self._tick()
            except Exception as exc:
                logger.warning(f'[卡死保护] 探测失败，未执行启停: {exc}')
