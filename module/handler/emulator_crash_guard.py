"""模拟器卡死保护模块。

本模块解决的核心问题：**模拟器内核僵死时，ADB 连接不会立刻断开**。
此时主线程会永久阻塞在 ADB socket 的 `recv()` 上（尤其是
`screenshot_adb` 走的 `stream=True` 分支，adbutils 在该分支不设 socket
超时），Python 层永远不会抛出异常，脚本表现为"挂起"。

与已有的 `Watchdog`（`task_failure_protection.py`）的区别：

===================  ==================================  ==============================
维度                  Watchdog                            CrashGuard
===================  ==================================  ==============================
检测信号              日志心跳（300 秒无输出）            独立 ADB 心跳探测（默认 5 秒/次）
判定依据              "没日志" = 可能卡死                  "底层通信彻底失效" = 确定卡死
恢复动作              只杀模拟器，不启动                  两枪：先重启 ADB 服务，再杀+重启
误杀风险              中（长时间静默的正常流程会误判）      低（多信号 + 持续时长双重闸门）
===================  ==================================  ==============================

设计要点：

1. **不阻塞主线程**：全部探测都在独立 daemon 线程内完成，且每次探测都用
   `subprocess.run(..., timeout=)` 起一个全新的 adb.exe 进程。不复用主线程的
   `self.u2` / `self.adb`——它们与主线程共享连接池，主线程卡住时它们也会卡住，
   根本探测不出问题。

2. **防误杀优先**：判定成立必须同时满足
   - L1：连续 `CrashHeartbeatFailures` 次心跳超时
   - L2：从首次失败起持续 `CrashConfirmSeconds` 秒
   - L3：模拟器进程仍然存活（进程没了是崩溃，走启动流程而不是杀进程）
   - L4：`adb devices` 里该 serial 状态为 `device`（`offline` 只需重连）

   游戏大更、跨图加载、网络波动只会让**画面**静止，ADB 心跳依然秒回，
   因此这些场景不会触发本保护。

3. **两枪恢复**：判定成立后先执行轻量、可逆的 `adb kill-server`。该动作会让
   所有 adb socket 立即 RST，主线程 `recv()` 立刻抛异常并走正常恢复链路。
   大量"ADB 服务假死"场景在这一步就救回来了，不需要杀模拟器。

4. **防重启风暴**：冷却期 + 时间窗计数双重限制，达到上限后停止自动恢复并告警，
   但**不退出调度器**（其他任务继续跑）。

Pages:
    本模块不直接操作游戏界面，仅做后台监控与恢复。
"""

import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from typing import Optional

from module.logger import logger

# 守护线程的轮询间隔（秒）。每次轮询执行一次心跳探测。
CRASH_GUARD_INTERVAL = 5

# 各配置项的兜底默认值（配置读取失败时使用，与 argument.yaml 保持一致）
DEFAULT_HEARTBEAT_TIMEOUT = 5
DEFAULT_HEARTBEAT_FAILURES = 3
DEFAULT_CONFIRM_SECONDS = 60
DEFAULT_COOLDOWN_SECONDS = 600
DEFAULT_MAX_RESTARTS = 3
DEFAULT_RESTART_WINDOW_HOURS = 6

# 第一枪（adb kill-server）之后等待主线程恢复的窗口（秒）。
# 主线程被 RST 打断后要先抛异常、再走 save_error_scene 等恢复流程，
# 期间会持续输出日志，30 秒足够覆盖这段。超过则认为第一枪无效。
ADB_KILL_WAIT_SECONDS = 30

# 第一枪之后轮询日志心跳的间隔（秒）
ADB_KILL_POLL_INTERVAL = 2

# 单次 adb 探测子进程的创建标志（Windows 下不弹黑窗）
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class CrashGuard:
    """模拟器卡死保护。

    独立 daemon 线程，周期性探测 ADB 心跳。判定成立时按"两枪"策略恢复：
    先重启 ADB 服务，仍无效才杀进程并重启模拟器。

    Attributes:
        alas: 调度器实例（`AzurLaneAutoLoop`），用于读取配置与访问设备。
        _thread: 守护线程对象。
        _stop_event: 停止信号。
        _active: 是否处于激活状态（仅在任务执行期间探测，空闲等待期间暂停）。
        _fail_count: 连续心跳失败次数。
        _first_fail_time: 首次失败的时刻（`time.monotonic()`），0 表示当前无失败。
        _last_log_time: 最近一次日志输出的时刻，用于判断主线程是否恢复。
        _restart_history: 历史重启完成时刻列表（`time.monotonic()`），用于时间窗计数。
        _suspended: 时间窗内重启次数达上限后置 True，本次运行不再自动恢复。
    """

    def __init__(self, alas):
        """初始化卡死保护。

        Args:
            alas: 调度器实例，需提供 `config` 与可选的 `device` 属性。
        """
        self.alas = alas
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._active = False
        self._task_name = ''

        # 判定状态
        self._fail_count = 0
        self._first_fail_time = 0.0

        # 主线程恢复检测：注册一个只记录时间戳的日志处理器
        self._last_log_time = time.monotonic()
        self._log_handler = _LogTimeHandler(self)
        self._log_handler_installed = False

        # 重启历史与冷却
        self._restart_history: list = []
        self._last_restart_end = 0.0
        self._suspended = False

        # 恢复流程互斥：避免守护线程与主线程的 _try_restart_emulator 并发
        self._recover_lock = threading.Lock()
        self._recovering = False

    # ------------------------------------------------------------------
    # 配置读取（每次动态读取，支持热更新；读取失败回退默认值）
    # ------------------------------------------------------------------

    def _get_int(self, attr: str, default: int) -> int:
        """安全读取 EmulatorManagement 组的整型配置项。

        Args:
            attr: 配置项名（如 `'CrashHeartbeatTimeout'`）。
            default: 读取失败时的回退默认值。

        Returns:
            配置值（int）；读取失败或值非法时返回 default。
        """
        try:
            value = int(getattr(self.alas.config, f'EmulatorManagement_{attr}'))
        except Exception:
            return default
        return value if value > 0 else default

    def _get_bool(self, attr: str, default: bool = False) -> bool:
        """安全读取 EmulatorManagement 组的布尔配置项。

        Args:
            attr: 配置项名（如 `'CrashProtection'`）。
            default: 读取失败时的回退默认值。

        Returns:
            配置值（bool）；读取失败时返回 default。
        """
        try:
            return bool(getattr(self.alas.config, f'EmulatorManagement_{attr}'))
        except Exception:
            return default

    def _enabled(self) -> bool:
        """卡死保护总开关（`EmulatorManagement.CrashProtection`）。"""
        return self._get_bool('CrashProtection', False)

    def _dry_run(self) -> bool:
        """演练模式（`EmulatorManagement.CrashDryRun`）：只告警不执行。"""
        return self._get_bool('CrashDryRun', False)

    # ------------------------------------------------------------------
    # 线程生命周期
    # ------------------------------------------------------------------

    def start(self):
        """启动守护线程，并注册日志时间戳处理器。"""
        if self._thread is not None and self._thread.is_alive():
            logger.warning('[卡死保护] 守护线程已在运行，跳过启动')
            return
        if not self._log_handler_installed:
            logger.addHandler(self._log_handler)
            self._log_handler_installed = True
        self._stop_event.clear()
        self._last_log_time = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='alas-crash-guard'
        )
        self._thread.start()
        logger.info('[卡死保护] 守护线程已启动')

    def stop(self):
        """停止守护线程并移除日志处理器。"""
        self._active = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._log_handler_installed:
            try:
                logger.removeHandler(self._log_handler)
            except Exception:
                pass
            self._log_handler_installed = False
        logger.info('[卡死保护] 守护线程已停止')

    def activate(self, task_name: str):
        """激活探测（任务开始执行时调用）。

        Args:
            task_name: 当前任务名，仅用于日志。
        """
        self._task_name = task_name
        self._active = True
        # 每个任务开始时重置判定状态，避免上一个任务的失败计数污染本任务
        self._fail_count = 0
        self._first_fail_time = 0.0

    def deactivate(self):
        """暂停探测（任务结束或进入恢复流程时调用）。"""
        self._active = False
        self._task_name = ''
        self._fail_count = 0
        self._first_fail_time = 0.0

    # ------------------------------------------------------------------
    # 探测（全部使用独立子进程，与主线程状态解耦）
    # ------------------------------------------------------------------

    def _adb_binary(self) -> str:
        """获取 adb 可执行文件路径。

        优先复用设备对象上已解析好的路径（`connection_attr.adb_binary`），
        设备尚未初始化时回退到系统 PATH 中的 `adb`。

        Returns:
            str: adb 可执行文件路径。
        """
        device = self.alas.__dict__.get('device', None)
        if device is not None:
            try:
                return device.adb_binary
            except Exception:
                pass
        return 'adb'

    def _serial(self) -> str:
        """获取当前设备的 ADB 序列号。

        Returns:
            str: 序列号；无法获取时返回空字符串。
        """
        device = self.alas.__dict__.get('device', None)
        if device is not None:
            try:
                return str(device.serial)
            except Exception:
                pass
        try:
            return str(self.alas.config.Emulator_Serial)
        except Exception:
            return ''

    def _run_adb(self, args, timeout):
        """用独立子进程执行一条 adb 命令。

        这是本模块的探测基石：每次调用都新建进程，不复用主线程的任何
        socket 或连接池，因此即使主线程卡死，这里依然能拿到真实结果。

        Args:
            args (list[str]): adb 的子命令参数（不含 adb 路径与 -s）。
            timeout (int | float): 子进程超时秒数。

        Returns:
            tuple[bool, str]: (是否成功, 输出文本)。失败时输出为错误信息。
        """
        cmd = [self._adb_binary()]
        serial = self._serial()
        if serial and serial not in ('auto', 'disabled', 'None'):
            cmd.extend(['-s', serial])
        cmd.extend(list(args))
        try:
            result = subprocess.run(
                cmd,
                timeout=timeout,
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_SUBPROCESS_FLAGS,
            )
        except subprocess.TimeoutExpired:
            return False, f'超时 {timeout}s'
        except Exception as e:
            return False, f'{type(e).__name__}: {e}'

        output = (result.stdout or '') + (result.stderr or '')
        if result.returncode != 0:
            return False, output.strip() or f'返回码 {result.returncode}'
        return True, output.strip()

    def probe_heartbeat(self, timeout: Optional[int] = None) -> bool:
        """探测 ADB 心跳是否存活。

        执行 `adb shell echo pong`。这是最轻量的"设备是否还活着"探针：
        正常往返不到 0.1 秒，卡死时必然超时。

        Args:
            timeout: 超时秒数，None 时读取配置 `CrashHeartbeatTimeout`。

        Returns:
            bool: 心跳正常（能拿到输出）返回 True。
        """
        if timeout is None:
            timeout = self._get_int('CrashHeartbeatTimeout', DEFAULT_HEARTBEAT_TIMEOUT)
        ok, output = self._run_adb(['shell', 'echo', 'pong'], timeout=timeout)
        if not ok:
            return False
        return 'pong' in output

    def _probe_device_status(self) -> str:
        """查询当前 serial 在 adb 设备列表中的状态。

        用于区分"设备彻底僵死"（`device` 但 shell 不通）与"只是掉线"
        （`offline`，重连即可，不应杀模拟器）。

        Returns:
            str: `'device'` / `'offline'` / `'unauthorized'` / `'absent'` / `'unknown'`。
        """
        ok, output = self._run_adb(['devices'], timeout=10)
        if not ok:
            return 'unknown'

        serial = self._serial()
        if not serial:
            return 'unknown'
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == serial:
                return parts[1]
        return 'absent'

    def _probe_emulator_process(self):
        """检查模拟器进程是否存活，并返回其进程树信息。

        用于区分两种情况：
        - 进程不存在 → 模拟器是**崩溃/被关**，应走启动流程，不能发 kill
        - 进程存在但 ADB 不通 → 才是真正的**卡死**

        Returns:
            tuple[bool, str]: (进程是否存活, 进程树描述文本)。
        """
        try:
            import psutil
        except ImportError:
            return True, 'psutil 不可用，跳过进程检查'

        platform = None
        try:
            from module.device.platform import Platform
            platform = Platform(self.alas.config, connect=False)
        except Exception:
            return True, '平台层不可用，跳过进程检查'

        instance = None
        try:
            instance = platform.emulator_instance
        except Exception:
            pass
        if instance is None:
            return True, '未识别到模拟器实例，跳过进程检查'

        exe_path = getattr(instance, 'path', '') or ''
        if not exe_path:
            return True, '模拟器路径为空，跳过进程检查'

        exe_norm = os.path.normcase(os.path.abspath(exe_path))
        lines = []
        alive = False
        try:
            for proc in psutil.process_iter():
                try:
                    proc_exe = proc.exe()
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    continue
                if not proc_exe:
                    continue
                if os.path.normcase(os.path.abspath(proc_exe)) != exe_norm:
                    continue
                alive = True
                try:
                    cmdline = ' '.join(proc.cmdline())
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    cmdline = '(无法读取命令行)'
                lines.append(f'PID={proc.pid} PPID={proc.ppid()} CMD={cmdline}')
        except Exception as e:
            return True, f'进程扫描异常: {e}'

        if not lines:
            return False, f'未找到 {exe_path} 的进程'
        return True, '\n'.join(lines)

    # ------------------------------------------------------------------
    # 判定状态机
    # ------------------------------------------------------------------

    def _in_cooldown(self) -> bool:
        """是否处于重启后的冷却期。"""
        if self._last_restart_end <= 0:
            return False
        cooldown = self._get_int('CrashCooldownSeconds', DEFAULT_COOLDOWN_SECONDS)
        return (time.monotonic() - self._last_restart_end) < cooldown

    def _clean_restart_history(self):
        """清理超出时间窗的历史重启记录。"""
        window_hours = self._get_int('CrashRestartWindowHours', DEFAULT_RESTART_WINDOW_HOURS)
        cutoff = time.monotonic() - window_hours * 3600
        self._restart_history = [t for t in self._restart_history if t >= cutoff]

    def _restart_quota_exhausted(self) -> bool:
        """时间窗内的重启次数是否已达上限。"""
        self._clean_restart_history()
        max_restarts = self._get_int('CrashMaxRestarts', DEFAULT_MAX_RESTARTS)
        return len(self._restart_history) >= max_restarts

    def _record_restart(self):
        """记录一次重启完成，用于时间窗计数与冷却期计算。"""
        now = time.monotonic()
        self._restart_history.append(now)
        self._last_restart_end = now
        self._clean_restart_history()

    def _should_trigger(self):
        """综合四层信号判定是否应触发恢复。

        返回 (是否触发, 判定说明)。只有四个条件全部满足才返回 True，
        任何一项不满足都只记日志、不动作——这是防误杀的核心。

        Returns:
            tuple[bool, str]: (是否触发, 判定说明文本)。
        """
        failures = self._get_int('CrashHeartbeatFailures', DEFAULT_HEARTBEAT_FAILURES)
        confirm = self._get_int('CrashConfirmSeconds', DEFAULT_CONFIRM_SECONDS)

        # L1：连续失败次数
        if self._fail_count < failures:
            return False, f'连续失败 {self._fail_count}/{failures} 次，未达阈值'

        # L2：持续时长（防误杀核心参数）
        elapsed = time.monotonic() - self._first_fail_time
        if elapsed < confirm:
            return False, f'已持续 {int(elapsed)}/{confirm} 秒，时长不足'

        # L3：模拟器进程必须存活（进程没了是崩溃，不是卡死）
        alive, process_info = self._probe_emulator_process()
        if not alive:
            return False, f'模拟器进程不存在（{process_info}），应走启动流程而非杀进程'

        # L4：adb 设备状态必须是 device（offline 只需重连）
        status = self._probe_device_status()
        if status != 'device':
            return False, f'adb 设备状态为 {status}，非 device，应重连而非杀进程'

        return True, (
            f'心跳连续 {self._fail_count} 次超时，持续 {int(elapsed)} 秒，'
            f'设备状态={status}，进程存活'
        )

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def _loop(self):
        """守护线程主循环：周期性探测 ADB 心跳并维护判定状态机。"""
        while not self._stop_event.wait(CRASH_GUARD_INTERVAL):
            # 未激活（空闲等待）或总开关关闭时，只重置状态不探测
            if not self._active or not self._enabled():
                self._fail_count = 0
                self._first_fail_time = 0.0
                continue

            # 已因重启风暴被挂起，不再探测
            if self._suspended:
                continue

            # 冷却期内不探测（避免重启后还没起来就被判卡死）
            if self._in_cooldown():
                self._fail_count = 0
                self._first_fail_time = 0.0
                continue

            # 正在恢复中，跳过本轮
            if self._recovering:
                continue

            try:
                self._tick()
            except Exception as e:
                # 探测本身异常绝不能拖垮守护线程
                logger.warning(f'[卡死保护] 探测异常，跳过本轮: {type(e).__name__}: {e}')

    def _tick(self):
        """执行一次探测并更新判定状态机。"""
        if self.probe_heartbeat():
            if self._fail_count > 0:
                elapsed = time.monotonic() - self._first_fail_time
                logger.info(
                    f'[卡死保护] ADB 心跳恢复，计数器清零'
                    f'（此前连续失败 {self._fail_count} 次，持续 {int(elapsed)} 秒）'
                )
            self._fail_count = 0
            self._first_fail_time = 0.0
            return

        # 心跳失败
        if self._fail_count == 0:
            self._first_fail_time = time.monotonic()
        self._fail_count += 1

        failures = self._get_int('CrashHeartbeatFailures', DEFAULT_HEARTBEAT_FAILURES)
        timeout = self._get_int('CrashHeartbeatTimeout', DEFAULT_HEARTBEAT_TIMEOUT)
        elapsed = time.monotonic() - self._first_fail_time
        logger.warning(
            f'[卡死保护] ADB 心跳失败 {self._fail_count}/{failures} 次'
            f'（echo pong 超时 {timeout}s，已持续 {int(elapsed)} 秒，任务={self._task_name}）'
        )

        triggered, reason = self._should_trigger()
        if not triggered:
            logger.info(f'[卡死保护] 暂不触发：{reason}')
            return

        # 时间窗配额检查（防重启风暴）
        if self._restart_quota_exhausted():
            self._suspend(reason)
            return

        self._recover(reason)

    # ------------------------------------------------------------------
    # 恢复流程（两枪策略）
    # ------------------------------------------------------------------

    def _recover(self, reason: str):
        """执行恢复流程：先重启 ADB 服务，仍无效才杀进程并重启模拟器。

        Args:
            reason: 判定说明文本，用于日志。
        """
        if not self._recover_lock.acquire(blocking=False):
            logger.warning('[卡死保护] 已有恢复流程在执行，跳过本轮')
            return

        self._recovering = True
        try:
            logger.hr('[卡死保护] 判定模拟器卡死', level=1)
            logger.critical(f'[卡死保护] 判定依据：{reason}')
            self._log_risk_assessment()

            # 演练模式：只告警不执行
            if self._dry_run():
                logger.critical(
                    '[卡死保护] 演练模式已开启（CrashDryRun），'
                    '本次只记录判定结果，不执行任何重启动作。'
                    '如需真正恢复，请关闭演练模式。'
                )
                self._notify(
                    '卡死保护（演练模式）',
                    f'检测到模拟器疑似卡死但未执行重启：{reason}',
                )
                return

            self._save_scene(reason)

            # ---------------- 第一枪：重启 ADB 服务 ----------------
            logger.warning('[卡死保护] 第一枪：重启 ADB 服务解除阻塞（不杀模拟器）')
            log_before = self._last_log_time
            if self._first_shot_adb_kill_server():
                if self._wait_main_thread_recover(log_before):
                    logger.info(
                        '[卡死保护] ADB 服务重启后主线程已恢复，'
                        '本次判定为「ADB 服务假死」，不重启模拟器'
                    )
                    self._notify(
                        '卡死保护',
                        f'ADB 服务假死已自动修复（未重启模拟器）：{reason}',
                    )
                    self._reset_after_recover()
                    return
                logger.warning('[卡死保护] 第一枪无效，主线程仍未恢复，准备第二枪')
            else:
                logger.warning('[卡死保护] 第一枪执行失败，直接进入第二枪')

            # ---------------- 第二枪：杀进程 + 重启 ----------------
            logger.critical('[卡死保护] 第二枪：重启模拟器')
            success = self._second_shot_restart_emulator()
            if success:
                self._record_restart()
                logger.info('[卡死保护] 模拟器重启完成，ADB 与游戏包名均已就绪')
                self._notify('卡死保护', f'模拟器卡死已自动重启恢复：{reason}')
            else:
                logger.critical('[卡死保护] 模拟器重启失败，请人工检查')
                self._notify('卡死保护', f'模拟器卡死且自动重启失败，需人工介入：{reason}')

            self._reset_after_recover()
        finally:
            self._recovering = False
            try:
                self._recover_lock.release()
            except RuntimeError:
                pass

    def _first_shot_adb_kill_server(self) -> bool:
        """第一枪：重启 adb server。

        该动作会让所有 adb socket 立即 RST，卡在 `recv()` 的主线程会立刻
        抛异常并走正常恢复链路。相比杀模拟器，它轻量且完全可逆。

        Returns:
            bool: 命令执行成功返回 True。
        """
        ok, output = self._run_adb(['kill-server'], timeout=15)
        if not ok:
            logger.warning(f'[卡死保护] adb kill-server 失败: {output}')
            return False
        logger.info('[卡死保护] adb kill-server 已执行')

        # 重新拉起 server，否则后续所有 adb 调用都会失败
        ok2, output2 = self._run_adb(['start-server'], timeout=30)
        if not ok2:
            logger.warning(f'[卡死保护] adb start-server 失败: {output2}')
        else:
            logger.info('[卡死保护] adb start-server 已执行')
        return True

    def _wait_main_thread_recover(self, log_time_before: float) -> bool:
        """等待主线程恢复。

        判断依据：主线程被 RST 打断后会抛异常并输出日志，因此只要观察到
        新的日志输出，就说明主线程已经从阻塞中醒来了。

        Args:
            log_time_before: 第一枪执行前的日志时间戳。

        Returns:
            bool: 主线程已恢复返回 True。
        """
        deadline = time.monotonic() + ADB_KILL_WAIT_SECONDS
        while time.monotonic() < deadline:
            if self._stop_event.wait(ADB_KILL_POLL_INTERVAL):
                return False
            if self._last_log_time > log_time_before:
                return True
        return False

    def _second_shot_restart_emulator(self) -> bool:
        """第二枪：停止并重启模拟器，然后等待 ADB 与游戏就绪。

        复用平台层的启停能力（含 3 次重试与启动监视），并在其后补上
        「等待 ADB 就绪」这一步——现有 `_try_restart_emulator` 缺少该环节，
        是"重启成功但 ADB 没起来"进入死循环的根源。

        Returns:
            bool: 重启并等待就绪成功返回 True。
        """
        from module.handler.task_failure_protection import (
            RESTART_OPERATION_TIMEOUT,
            emulator_op_with_timeout,
        )

        # 先确认模拟器实例可识别，识别不到绝不"猜一个杀掉"
        try:
            from module.device.platform import Platform
            platform = Platform(self.alas.config, connect=False)
            if platform.emulator_instance is None:
                logger.critical(
                    '[卡死保护] 未能识别模拟器实例，为避免误杀其他实例，'
                    '放弃本次重启。请检查「模拟器信息」中的类型/名称/路径配置。'
                )
                return False
        except Exception as e:
            logger.critical(f'[卡死保护] 平台层初始化失败，放弃本次重启: {e}')
            return False

        # 与主线程的 _try_restart_emulator 互斥
        lock = getattr(self.alas, '_emulator_lock', None)
        acquired = False
        if lock is not None:
            acquired = lock.acquire(timeout=RESTART_OPERATION_TIMEOUT + 10)
            if not acquired:
                logger.warning('[卡死保护] 等待模拟器操作锁超时，放弃本次重启')
                return False
        try:
            logger.info('[卡死保护] 正在停止模拟器...')
            emulator_op_with_timeout(
                platform.emulator_stop,
                timeout=RESTART_OPERATION_TIMEOUT,
                operation_name='[卡死保护] 停止模拟器',
            )
            time.sleep(5)

            logger.info('[卡死保护] 正在启动模拟器...')
            started = emulator_op_with_timeout(
                platform.emulator_start,
                timeout=RESTART_OPERATION_TIMEOUT,
                operation_name='[卡死保护] 启动模拟器',
            )
            if started is False:
                logger.warning('[卡死保护] 平台层报告模拟器启动失败')
                return False
        except Exception as e:
            logger.warning(f'[卡死保护] 模拟器启停失败: {type(e).__name__}: {e}')
            return False
        finally:
            if acquired:
                try:
                    lock.release()
                except RuntimeError:
                    pass

        # 清除设备缓存，下次访问时重新建立连接
        try:
            from module.base.decorator import del_cached_property
            if 'device' in self.alas.__dict__:
                del_cached_property(self.alas, 'device')
        except Exception:
            pass

        # 等待 ADB 与游戏就绪（现有 _try_restart_emulator 缺失的关键环节）
        if not self._wait_adb_ready():
            logger.warning('[卡死保护] 模拟器已启动，但 ADB/游戏在限定时间内未就绪')
            return False

        # 交回调度器：由 Restart 任务完成游戏启动与登录
        try:
            self.alas.config.task_call('Restart')
        except Exception as e:
            logger.warning(f'[卡死保护] 调度 Restart 任务失败: {e}')

        return True

    def _wait_adb_ready(self) -> bool:
        """等待模拟器启动后 ADB 与游戏包名就绪。

        三段校验，与平台层 `emulator_start_watch` 的口径保持一致：
        1. `adb devices` 中 serial 状态为 `device`
        2. `adb shell echo pong` 有响应
        3. 能查询到游戏包名

        Returns:
            bool: 全部就绪返回 True。
        """
        start = time.monotonic()

        # 阶段 1：等待设备上线（最长 300 秒）
        deadline = start + 300
        while time.monotonic() < deadline:
            if self._stop_event.wait(3):
                return False
            status = self._probe_device_status()
            if status == 'device':
                logger.info(f'[卡死保护] ADB 设备已上线，耗时 {int(time.monotonic() - start)} 秒')
                break
            logger.info(f'[卡死保护] 等待 ADB 设备上线（当前状态 {status}）')
        else:
            logger.warning('[卡死保护] 等待 ADB 设备上线超时（300 秒）')
            return False

        # 阶段 2：等待 shell 响应（最长 60 秒）
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.probe_heartbeat(timeout=5):
                logger.info('[卡死保护] ADB shell 已响应')
                break
            if self._stop_event.wait(2):
                return False
        else:
            logger.warning('[卡死保护] 等待 ADB shell 响应超时（60 秒）')
            return False

        # 阶段 3：等待游戏包名可查询（最长 60 秒）
        device = self.alas.__dict__.get('device', None)
        if device is not None:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    packages = device.list_known_packages(show_log=False)
                except Exception:
                    packages = None
                if packages:
                    logger.info('[卡死保护] 游戏包名已就绪')
                    break
                if self._stop_event.wait(3):
                    return False
            else:
                logger.warning('[卡死保护] 等待游戏包名就绪超时（60 秒）')
                return False

        return True

    def _reset_after_recover(self):
        """恢复流程结束后的状态复位。"""
        self._fail_count = 0
        self._first_fail_time = 0.0

    def _suspend(self, reason: str):
        """重启次数达上限，挂起自动恢复并告警。

        这里**不退出调度器**：卡死保护失败不应该影响其他任务的运行，
        后续失败由已有的 TaskFailureProtection 机制接管。

        Args:
            reason: 判定说明文本。
        """
        self._suspended = True
        window_hours = self._get_int('CrashRestartWindowHours', DEFAULT_RESTART_WINDOW_HOURS)
        max_restarts = self._get_int('CrashMaxRestarts', DEFAULT_MAX_RESTARTS)
        logger.critical(
            f'[卡死保护] {window_hours} 小时内已重启模拟器 {max_restarts} 次，'
            f'判定为持续性环境问题，停止自动恢复。'
            f'请人工检查模拟器状态、ADB 服务和主机资源。'
        )
        logger.critical(f'[卡死保护] 最后一次判定依据：{reason}')
        self._notify(
            '卡死保护已停止',
            f'{window_hours} 小时内重启 {max_restarts} 次仍未恢复，'
            f'已停止自动重启，请人工介入',
        )

    # ------------------------------------------------------------------
    # 日志与现场
    # ------------------------------------------------------------------

    def _log_risk_assessment(self):
        """输出误判风险自检信息，方便用户判断是否需要调整阈值。"""
        confirm = self._get_int('CrashConfirmSeconds', DEFAULT_CONFIRM_SECONDS)
        timeout = self._get_int('CrashHeartbeatTimeout', DEFAULT_HEARTBEAT_TIMEOUT)
        failures = self._get_int('CrashHeartbeatFailures', DEFAULT_HEARTBEAT_FAILURES)
        logger.info(
            f'[卡死保护] 误判风险自检：判定阈值为 '
            f'{failures} 次 × {timeout}s 超时 + 持续 {confirm}s；'
            f'游戏大更/跨图加载/网络波动只会让画面静止，不会让 ADB 心跳超时，'
            f'因此上述场景不会触发本保护。'
        )
        logger.info(
            '[卡死保护] 若确认是误判，可调大「判定确认时长」或开启「演练模式」观察。'
        )

    def _save_scene(self, reason: str):
        """保存判定现场的诊断信息。

        保存内容：
        - `threads.txt`：所有线程的调用栈（主线程卡在哪一行的唯一线索）
        - `adb_state.txt`：`adb devices -l` 输出
        - `process.txt`：模拟器进程树
        - `screenshot.png`：最后一张成功截图（如果设备已初始化）

        Args:
            reason: 判定说明文本。
        """
        try:
            import pathlib

            config_name = getattr(self.alas, 'config_name', 'alas')
            folder = pathlib.Path(f'./log/error/{config_name}/crash_guard_{int(time.time() * 1000)}')
            folder.mkdir(parents=True, exist_ok=True)
            logger.warning(f'[卡死保护] 保存判定现场: {folder}')

            # 1. 线程栈：主线程卡在 C 层时，这是唯一的排查线索
            try:
                frames = sys._current_frames()
                with open(folder / 'threads.txt', 'w', encoding='utf-8') as f:
                    f.write(f'判定原因：{reason}\n')
                    f.write(f'时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
                    f.write('=' * 70 + '\n')
                    for thread in threading.enumerate():
                        f.write(f'\n--- 线程 {thread.name} (ident={thread.ident}) ---\n')
                        frame = frames.get(thread.ident)
                        if frame is not None:
                            f.write(''.join(traceback.format_stack(frame)))
                        else:
                            f.write('(无法获取栈帧)\n')
            except Exception as e:
                logger.warning(f'[卡死保护] 保存线程栈失败: {e}')

            # 2. adb 设备状态
            try:
                ok, output = self._run_adb(['devices', '-l'], timeout=10)
                with open(folder / 'adb_state.txt', 'w', encoding='utf-8') as f:
                    f.write(f'成功={ok}\n{output}\n')
            except Exception as e:
                logger.warning(f'[卡死保护] 保存 ADB 状态失败: {e}')

            # 3. 模拟器进程树
            try:
                alive, info = self._probe_emulator_process()
                with open(folder / 'process.txt', 'w', encoding='utf-8') as f:
                    f.write(f'进程存活={alive}\n{info}\n')
            except Exception as e:
                logger.warning(f'[卡死保护] 保存进程信息失败: {e}')

            # 4. 最后一张截图
            try:
                device = self.alas.__dict__.get('device', None)
                if device is not None and getattr(device, 'has_cached_image', False):
                    from module.base.utils import save_image
                    save_image(device.image, str(folder / 'screenshot.png'))
            except Exception as e:
                logger.warning(f'[卡死保护] 保存截图失败: {e}')
        except Exception as e:
            logger.warning(f'[卡死保护] 保存判定现场失败: {e}')

    def _notify(self, title: str, content: str):
        """推送通知给用户。

        Args:
            title: 通知标题。
            content: 通知内容。
        """
        try:
            from module.notify import handle_notify, notify_webui

            config = self.alas.config
            handle_notify(
                getattr(config, 'Error_OnePushConfig', None),
                title=f'AzurPilot <{getattr(self.alas, "config_name", "alas")}> {title}',
                content=content,
            )
            notify_webui(
                getattr(self.alas, 'config_name', 'alas'),
                title=f'{title}喵！',
                content=content,
            )
        except Exception as e:
            logger.warning(f'[卡死保护] 推送通知失败: {e}')


class _LogTimeHandler(__import__('logging').Handler):
    """记录最近一次日志输出时刻的处理器。

    用于判断主线程是否已从阻塞中恢复：主线程被 `adb kill-server` 打断后
    会抛异常并输出日志，因此"有新的日志"即等价于"主线程醒了"。
    """

    def __init__(self, guard: CrashGuard):
        """初始化处理器。

        Args:
            guard: 所属的 CrashGuard 实例，日志时间戳写回其 `_last_log_time`。
        """
        super().__init__(level=__import__('logging').DEBUG)
        self._guard = guard

    def emit(self, record):
        """记录日志时间戳。

        Args:
            record: 日志记录对象。
        """
        self._guard._last_log_time = time.monotonic()
