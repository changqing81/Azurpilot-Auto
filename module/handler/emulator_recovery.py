"""模拟器恢复协调器：后台只请求，旧任务退出后由调度器恢复。"""

import json
import threading
from contextlib import contextmanager
from pathlib import Path

from module.device.session import DeviceSessionExpired, RecoveryStopped
from module.exception import EmulatorOpBusy, RecoveryFailed
from module.handler.emulator_health import AdbProbe
from module.handler.emulator_recovery_state import InstanceLease, RecoveryBlocked
from module.logger import logger


RECOVERY_TIMEOUT = 420


class EmulatorRecovery:
    """一次恢复持有完整实例租约；失败计账，禁止残留执行者与新任务并行。"""

    def __init__(self, alas):
        self.alas = alas
        self.owner = threading.get_ident()
        self._request_lock = threading.Lock()
        self.pending = None
        self.cancel = threading.Event()
        self.worker = None

    def option(self, group, key, default):
        """动作前读取落盘开关，任务长时间阻塞时也能响应 GUI 关闭。"""
        value = getattr(self.alas.config, f'{group}_{key}', default)
        name = getattr(self.alas, 'config_name', None)
        if isinstance(name, str):
            path = Path('config') / f'{name}.json'
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    # 模拟器选项已迁到独立任务，新位置优先，兼容旧配置。
                    legacy = data.get('Alas', {}).get(group, {}).get(key, value)
                    if group == 'EmulatorManagement':
                        value = data.get('EmulatorSettings', {}).get(group, {}).get(key, legacy)
                    else:
                        value = legacy
                except (OSError, ValueError, AttributeError, TypeError):
                    # 无法确认配置时不允许破坏性动作。
                    return False if isinstance(default, bool) else default
        return value if type(value) is type(default) else default

    def allowed(self, source):
        event = getattr(self.alas, 'stop_event', None)
        if event is not None and event.is_set():
            return False
        if self.cancel.is_set() or self.option('EmulatorManagement', 'CrashDryRun', False):
            return False
        if source == 'crash':
            return self.option('EmulatorManagement', 'CrashProtection', False)
        if source in ('scheduled', 'force_scheduled_restart'):
            enabled = self.option('EmulatorManagement', 'ScheduledEmulatorRestart', False)
            return enabled and (source == 'scheduled' or self.option('EmulatorManagement', 'ForceScheduledRestart', False))
        if source == 'offline':
            return self.option('Error', 'AdbOfflineRestart', False)
        if source == 'game':
            return self.option('Error', 'GameStuckRestart', False)
        return self.option('Error', 'HandleError', True)

    def request(self, reason, source='crash', device=None):
        """不在检测线程启停或替换设备；旧任务在下一个设备边界退出。"""
        if not self.allowed(source):
            return False
        with self._request_lock:
            current = self.alas.__dict__.get('device')
            if device is not None and current is not device:
                return False
            if current is None or self.pending is not None:
                return False
            self.pending = (reason, source)
            current.invalidate_session(reason)
        logger.warning(f'[模拟器恢复] 已请求退出旧任务: {reason}')
        return True

    @contextmanager
    def task_scope(self):
        """在异常分类之前识别会话失效，底层重试改写异常也不会丢失请求。"""
        if self.pending is not None:
            raise DeviceSessionExpired('任务开始前存在设备恢复请求')
        try:
            yield
        except Exception as exc:
            if self.pending is not None:
                raise DeviceSessionExpired('设备恢复请求要求退出当前任务') from exc
            raise
        else:
            if self.pending is not None:
                raise DeviceSessionExpired('任务返回时存在设备恢复请求')

    def handle_pending(self):
        """只能在任务栈退出后调用；不能把旧任务的返回值当成成功。"""
        if self.pending is None:
            return False
        reason, source = self.pending
        self.pending = None
        if not self.allowed(source):
            raise RecoveryStopped('恢复请求已取消，旧设备保持失效，请重新启动配置')
        logger.warning(f'[模拟器恢复] 旧任务已退出，开始处理: {reason}')
        if not self.restart(source):
            raise RecoveryStopped('恢复开关已关闭，停止当前配置')
        self.alas.config.task_call('Restart')
        return True

    def probe(self, platform=None):
        """冻结明确的 serial 和已解析 ADB 路径，删除 Device 不改变探测目标。"""
        device = self.alas.__dict__.get('device')
        target = platform if platform is not None else device
        if target is None:
            from module.device.platform import Platform
            target = Platform(self.alas.config, connect=False)
        return AdbProbe(target.adb_binary, target.serial,
                        getattr(self.alas.config, 'Emulator_PackageName', 'auto'))

    def restart(self, source='error'):
        """调度线程独占恢复；超时只取消后续动作，返回可重试的失败。

        Raises:
            RecoveryBlocked: 当前状态不允许执行（旧执行者存活、目标变更等）。
            RecoveryFailed: 本轮恢复未成功，属设备侧问题，调用方应退避重试。
        """
        if threading.get_ident() != self.owner:
            raise RecoveryBlocked('恢复只能在旧任务退出后的调度线程执行')
        if not self.allowed(source):
            return False
        if source == 'game' and self.probe().heartbeat():
            logger.warning('[模拟器恢复] 系统心跳正常，游戏异常不升级为模拟器重启')
            return False
        if self.worker is not None and self.worker.is_alive():
            raise RecoveryFailed('上一恢复执行者尚未退出，本次恢复跳过')
        self.cancel.clear()
        result = []
        error = []

        def checkpoint():
            if not self.allowed(source):
                # 用户主动关闭恢复开关/发出停止请求：这是人为终止条件，
                # 与"设备没能恢复"性质不同，仍按约定停止配置。
                raise RecoveryStopped('恢复过程中收到停止请求或开关已关闭')

        def recover():
            # 锁由实际执行者持有到结束；调用方超时不会释放另一线程的锁。
            lock = self.alas._emulator_lock
            if not lock.acquire(blocking=False):
                error.append(EmulatorOpBusy('已有模拟器恢复正在执行'))
                return
            try:
                from module.device.platform import Platform
                from module.handler.emulator_target import RecoveryTarget
                checkpoint()
                platform = Platform(self.alas.config, connect=False)
                target = RecoveryTarget(platform)
                previous = self.alas.__dict__.get('device')
                previous_serial = getattr(previous, 'serial', platform.serial)
                if previous_serial != platform.serial:
                    raise RecoveryBlocked('配置中的设备目标已改变，取消旧实例恢复')
                probe = self.probe(platform)
                with InstanceLease(target.identity) as lease:
                    checkpoint()
                    lease.reserve(
                        maximum=max(1, self.option('EmulatorManagement', 'CrashMaxRestarts', 3)),
                        window=max(1, self.option('EmulatorManagement', 'CrashRestartWindowHours', 6)) * 3600,
                        cooldown=max(60, self.option('EmulatorManagement', 'CrashCooldownSeconds', 600)),
                    )
                    old = self.alas.__dict__.get('device')
                    if old is not None:
                        old.close_session()
                    checkpoint()
                    # 每个事务只执行一轮停启，平台内部重试不能绕过统一额度。
                    target.stop(checkpoint)
                    if not target.wait_stopped(self.cancel, checkpoint):
                        raise RecoveryBlocked('未能确认目标实例完全停止，拒绝启动第二个实例')
                    checkpoint()
                    target.start(checkpoint)
                    if not probe.ready(self.cancel, timeout=300):
                        raise RecoveryBlocked('系统、目标游戏包或截图未在期限内就绪')
                    checkpoint()
                    result.append(True)
            except BaseException as exc:
                error.append(exc)
            finally:
                lock.release()

        self.worker = threading.Thread(target=recover, name='alas-instance-recovery', daemon=True)
        self.worker.start()
        # 给关闭、系统启动和资源释放一个统一上限；每个命令仍有自身超时。
        self.worker.join(timeout=RECOVERY_TIMEOUT)
        if self.worker.is_alive():
            self.cancel.set()
            logger.critical('[模拟器恢复] 本轮恢复超时；旧执行者退出前禁止再发起新恢复')
            # 设备侧超时不是脚本的错：抛可重试的 RecoveryFailed，
            # 由调度器退避后继续尝试，而不是结束整个配置进程。
            raise RecoveryFailed('模拟器恢复超时，需要人工检查')
        if error or not result:
            self.cancel.set()
            detail = str(error[0]) if error else '恢复未完成'
            # 人为终止（用户关开关/取消）沿用原语义；设备侧失败改为可重试。
            if any(isinstance(exc, RecoveryStopped) for exc in error):
                raise RecoveryStopped(detail)
            logger.critical(f'[模拟器恢复] 本轮恢复未成功: {detail}')
            raise RecoveryFailed(detail)
        self.alas.__dict__.pop('device', None)
        logger.info('[模拟器恢复] 系统已就绪；下一轮重新建立设备并执行 Restart，尚未确认游戏登录成功')
        return True
