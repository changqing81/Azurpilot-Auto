"""任务失败保护统一模块。

整合两大防护机制，防止任务失败过多导致频繁重启：

1. **任务失败跟踪**（``TaskFailureTracker``）：在用户设定的时间窗口内，
   同一错误原因累计达到阈值时，自动关闭该任务并生成通知供 WebUI 弹窗展示。

2. **看门狗**（``Watchdog``）：调度器级守护线程，检测两类卡死并强制恢复：
   - 日志心跳超时：任务执行期间超过 ``WatchdogLogTimeout`` 秒无任何日志输出，
     判定主线程卡死在 I/O 调用中（u2 HTTP / ADB shell 挂起）
   - 任务运行超时：单个任务运行超过 ``WatchdogTaskTimeout`` 分钟，判定任务
     逻辑死循环（如剧情无法跳过、寻路死循环），此时日志仍在输出但任务无法退出

两类异常的恢复方式相同：强制杀死模拟器进程，使主线程的下次 I/O 调用因
连接断开而失败并抛出异常，触发正常的异常恢复流程
（EmulatorNotRunningError → _try_restart_emulator + task_call('Restart')）。

看门狗仅在任务执行阶段激活，空闲等待（wait_until、服务器维护检查）期间
自动暂停，避免误触发。

``RestartOperationTimeout`` 为 app_stop/app_start 和模拟器启停操作提供
硬超时保护，防止恢复流程本身卡死（独立于 WatchdogEnable，始终生效）。

数据持久化在 ``log/<config_name>.task_failure.json``，结构如下::

    {
        "failures": {
            "Main": {
                "GameStuckError": [
                    "2026-08-14T10:00:00",
                    "2026-08-14T10:30:00"
                ]
            }
        },
        "notifications": [
            {
                "task": "Main",
                "reason": "GameStuckError",
                "count": 3,
                "timestamp": "2026-08-14T10:30:00",
                "read": false
            }
        ]
    }

Pages:
    本模块不直接操作游戏界面，仅做后台监控与数据记录。
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from module.logger import logger

# 看门狗配置常量（默认值，实际运行时从配置 TaskFailureProtection 组读取）
# 守护线程每 N 秒检查一次最近一条日志的时间戳和任务运行时间
WATCHDOG_CHECK_INTERVAL = 30
WATCHDOG_LOG_TIMEOUT_DEFAULT = 300
WATCHDOG_TASK_TIMEOUT_DEFAULT = 120
# 模拟器启停操作的硬超时秒数。emulator_stop/emulator_start 底层调用
# subprocess（taskkill / ldconsole / MuMuManager 等），正常情况下秒级完成，
# 但若模拟器进程僵死或子进程管理卡住，调用可能长时间不返回。
RESTART_OPERATION_TIMEOUT = 120


def _now_iso() -> str:
    """返回当前时间的 ISO 格式字符串。"""
    return datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def _parse_iso(s: str) -> Optional[datetime]:
    """解析 ISO 格式时间字符串，失败返回 None。"""
    try:
        return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
    except (ValueError, TypeError):
        return None


class _LogHeartbeatHandler(logging.Handler):
    """记录最近一条日志时间戳的日志处理器，供看门狗线程检测主线程卡死。

    任何 logger.info/warning/error 等调用都会更新 last_log_time，
    包括看门狗自身的日志——这恰好防止看门狗在触发恢复后立即再次触发。
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.last_log_time = time.monotonic()

    def emit(self, record):
        self.last_log_time = time.monotonic()


def emulator_op_with_timeout(func, *, timeout, operation_name):
    """带硬超时执行模拟器启停操作，防止恢复流程本身卡死。

    emulator_stop / emulator_start 底层调用 subprocess（taskkill /
    ldconsole / MuMuManager 等），正常情况下秒级完成。但若模拟器进程
    僵死或子进程管理卡住，调用可能长时间不返回。此方法在独立 daemon
    线程中执行操作，超时后抛出 TimeoutError，由外层 try/except 捕获
    并由调用方决定退避重试。

    Args:
        func: 无参数的可调用对象。
        timeout (int | float): 超时秒数。
        operation_name (str): 操作名称，用于日志。

    Raises:
        TimeoutError: 操作超时。
        Exception: 操作本身抛出的异常会被原样向上抛出。
    """
    result = [None]
    exception = [None]

    def worker():
        try:
            result[0] = func()
        except BaseException as e:
            exception[0] = e

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        logger.critical(
            f'[Watchdog] {operation_name} 超过 {timeout}s 未完成，'
            f'跳过此操作（daemon 线程残留，进程退出时自动清理）'
        )
        raise TimeoutError(
            f'{operation_name} 超过 {timeout}s 未完成'
        )

    if exception[0] is not None:
        raise exception[0]
    return result[0]


class Watchdog:
    """调度器级看门狗守护线程。

    通过日志心跳和任务运行时间两类检测发现主线程卡死或任务死循环，
    并强制杀死模拟器进程以解除阻塞。持有调度器（alas）实例引用以
    动态读取配置，支持配置热更新。

    生命周期：
        start() → [activate(task) ↔ deactivate()] * n → stop()

    仅在 activate() 与 deactivate() 之间执行检测，空闲等待期间
    线程空转（每 WATCHDOG_CHECK_INTERVAL 秒醒一次），不误触发。
    """

    def __init__(self, alas):
        """初始化看门狗。

        Args:
            alas: AzurLaneAutoScript 调度器实例，用于读取配置和设备。
        """
        self.alas = alas
        self._stop_event = threading.Event()
        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._heartbeat = _LogHeartbeatHandler()
        self._task_start = 0.0
        self._task_name = ''

    # ------------------------------------------------------------------
    # 配置读取（每次检查时动态读取，支持热更新；配置非法时回退默认值）
    # ------------------------------------------------------------------

    def _get_config_int(self, attr: str, default: int) -> int:
        """安全读取 TaskFailureProtection 组的整型配置项。

        Args:
            attr: 配置项名（如 'WatchdogLogTimeout'）。
            default: 读取失败时的回退默认值。

        Returns:
            配置值（int），读取失败时返回 default。
        """
        try:
            return int(getattr(self.alas.config, f'TaskFailureProtection_{attr}'))
        except Exception:
            return default

    def _enabled(self) -> bool:
        """看门狗总开关（TaskFailureProtection.WatchdogEnable）。"""
        try:
            return bool(self.alas.config.TaskFailureProtection_WatchdogEnable)
        except Exception:
            return True

    # ------------------------------------------------------------------
    # 线程生命周期
    # ------------------------------------------------------------------

    def start(self):
        """启动看门狗守护线程，并注册日志心跳处理器。"""
        if self._thread is not None and self._thread.is_alive():
            logger.warning('[Watchdog] 看门狗已在运行，跳过启动')
            return
        if self._heartbeat not in logger.handlers:
            logger.addHandler(self._heartbeat)
        self._stop_event.clear()
        self._heartbeat.last_log_time = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='alas-watchdog'
        )
        self._thread.start()
        logger.info('[Watchdog] 看门狗已启动')

    def stop(self):
        """停止看门狗守护线程。"""
        self._active = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info('[Watchdog] 看门狗已停止')

    def activate(self, task_name: str):
        """激活看门狗检测，标记任务开始执行。

        Args:
            task_name: 当前执行的任务名（如 'Main'），用于超时日志。
        """
        # 防御：若上一任务异常退出未 deactivate，重置计时器避免累积误判
        self._task_start = time.monotonic()
        self._task_name = task_name
        self._active = True

    def deactivate(self):
        """暂停看门狗检测（任务结束或进入恢复流程时调用）。"""
        self._active = False
        self._task_start = 0.0

    # ------------------------------------------------------------------
    # 检测循环
    # ------------------------------------------------------------------

    def _loop(self):
        """看门狗主循环：检测两类异常并强制恢复。

        1. 日志心跳超时：任务执行期间 WATCHDOG_LOG_TIMEOUT 秒无任何日志输出
           → 主线程卡死在 I/O 调用中（u2 HTTP / ADB shell）
        2. 任务运行时间超时：单个任务运行超过配置的 WatchdogTaskTimeout 分钟
           → 任务逻辑死循环（如 story_skip 不断点击但剧情无法跳过）
           此时日志仍在更新，但任务无法自然退出
        """
        while not self._stop_event.wait(WATCHDOG_CHECK_INTERVAL):
            if not self._active or not self._enabled():
                continue

            # 检查 1：日志心跳超时（主线程卡死）
            log_timeout = self._get_config_int(
                'WatchdogLogTimeout', WATCHDOG_LOG_TIMEOUT_DEFAULT)
            if log_timeout > 0:
                elapsed_log = time.monotonic() - self._heartbeat.last_log_time
                if elapsed_log > log_timeout:
                    self._recover(elapsed_log, reason='log_timeout')
                    continue

            # 检查 2：任务运行时间超时（逻辑死循环）
            if self._task_start > 0:
                timeout_min = self._get_config_int(
                    'WatchdogTaskTimeout', WATCHDOG_TASK_TIMEOUT_DEFAULT)
                if timeout_min > 0:
                    elapsed_task = time.monotonic() - self._task_start
                    if elapsed_task > timeout_min * 60:
                        self._recover(
                            elapsed_task,
                            reason='task_timeout',
                            task_name=self._task_name,
                        )

    def _recover(self, elapsed, reason='log_timeout', task_name=''):
        """看门狗恢复动作：强制杀死模拟器进程以解除主线程阻塞。

        主线程可能卡在 u2 HTTP 调用、ADB shell、截图等 I/O 操作中，
        或陷入逻辑死循环（如 story_skip 不断点击但剧情无法跳过）。
        杀死模拟器进程会同时杀死 atx-agent，使主线程的下次 I/O 调用
        因连接断开而失败并抛出异常，触发正常的异常恢复流程。

        本方法的日志会更新 last_log_time，防止看门狗在恢复期间重复触发；
        若主线程仍未恢复，下一个阈值周期后看门狗会再次触发。
        emulator_stop() 本身也可能卡住（如 psutil 遍历缓慢或 subprocess
        不返回），因此用 emulator_op_with_timeout 包装，超时后放弃本轮
        恢复，等待下一个阈值周期重试。
        """
        if reason == 'task_timeout':
            timeout_min = self._get_config_int(
                'WatchdogTaskTimeout', WATCHDOG_TASK_TIMEOUT_DEFAULT)
            logger.critical(
                f'[Watchdog] 任务 `{task_name}` 已运行 {int(elapsed)} 秒'
                f'（超过 {timeout_min} 分钟），判定逻辑死循环，'
                f'强制杀死模拟器进程以中断任务'
            )
        else:
            log_timeout = self._get_config_int(
                'WatchdogLogTimeout', WATCHDOG_LOG_TIMEOUT_DEFAULT)
            logger.critical(
                f'[Watchdog] 任务执行中已 {int(elapsed)} 秒无任何日志输出'
                f'（超过 {log_timeout} 秒），判定主线程卡死，'
                f'强制杀死模拟器进程以解除阻塞'
            )

        try:
            from module.device.platform import Platform
            platform = Platform(self.alas.config, connect=False)
            emulator_op_with_timeout(
                platform.emulator_stop,
                timeout=RESTART_OPERATION_TIMEOUT,
                operation_name='[Watchdog] 强制停止模拟器',
            )
            logger.info(
                '[Watchdog] 已强制停止模拟器，主线程的下次 I/O 调用将失败并触发恢复'
            )
        except TimeoutError:
            logger.warning('[Watchdog] 强制停止模拟器超时，等待下个周期重试')
        except Exception as e:
            logger.warning(f'[Watchdog] 强制停止模拟器失败: {e}')


class TaskFailureTracker:
    """任务失败记录跟踪器。

    每个配置实例对应一个独立的跟踪器，数据持久化到 JSON 文件。
    数据文件存放在 ``log/`` 目录下，避免与 ``config/`` 目录中的配置文件混淆。
    跟踪器记录每个任务的每种错误原因的失败时间戳列表，
    并在达到阈值时生成通知。
    """

    def __init__(self, config_name: str):
        self.config_name = config_name
        self.filepath = self._make_filepath(config_name)
        self._data: Dict[str, Any] = self._load()

    @staticmethod
    def _make_filepath(config_name: str) -> str:
        """将失败记录文件存放在 ``log/`` 目录下。

        使用 ``log/<config_name>.task_failure.json``，与配置文件分离，
        避免 ``config/`` 目录中的杂项文件被误识别为配置文件。
        文件不存在时会自动创建 ``log/`` 目录。

        Args:
            config_name: 配置实例名。

        Returns:
            失败记录文件的绝对路径。
        """
        log_dir = os.path.join(os.getcwd(), 'log')
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError:
            pass
        return os.path.join(log_dir, f'{config_name}.task_failure.json')

    def _load(self) -> Dict[str, Any]:
        """从磁盘加载失败记录，文件不存在或损坏时返回空结构。"""
        if not os.path.exists(self.filepath):
            return {'failures': {}, 'notifications': []}
        try:
            with open(self.filepath, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {'failures': {}, 'notifications': []}
            data.setdefault('failures', {})
            data.setdefault('notifications', [])
            return data
        except Exception as e:
            logger.warning(f'[TaskFailureProtection] 加载失败记录文件失败，已重置: {e}')
            return {'failures': {}, 'notifications': []}

    def _save(self) -> None:
        """将失败记录写入磁盘。"""
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f'[TaskFailureProtection] 保存失败记录文件失败: {e}')

    def record_failure(self, task: str, reason: str) -> int:
        """记录一次任务失败。

        清理超过时间窗口的旧记录后，将本次失败追加到对应任务和错误原因下。
        同时清理已读通知，避免通知列表无限增长。

        Args:
            task: 任务名称（如 ``Main``）。
            reason: 错误原因（异常类名，如 ``GameStuckError``）。

        Returns:
            当前时间窗口内同一错误原因的累计失败次数。
        """
        now = datetime.now()
        now_iso = _now_iso()

        failures = self._data.setdefault('failures', {})
        task_failures = failures.setdefault(task, {})
        timestamps = task_failures.setdefault(reason, [])

        # 追加本次失败时间
        timestamps.append(now_iso)

        # 限制单个原因的记录上限，避免文件膨胀
        if len(timestamps) > 50:
            timestamps = timestamps[-50:]
            task_failures[reason] = timestamps

        self._save()
        count = len(timestamps)
        logger.info(
            f'[TaskFailureProtection] 任务 `{task}` 因 `{reason}` 失败，'
            f'当前累计 {count} 次'
        )
        return count

    def get_failure_count(self, task: str, reason: str, time_window_hours: int) -> int:
        """获取指定时间窗口内同一任务同一错误原因的失败次数。

        Args:
            task: 任务名称。
            reason: 错误原因。
            time_window_hours: 时间窗口（小时）。

        Returns:
            时间窗口内的失败次数。
        """
        cutoff = datetime.now() - timedelta(hours=time_window_hours)
        timestamps = (
            self._data.get('failures', {})
            .get(task, {})
            .get(reason, [])
        )
        count = 0
        for ts in timestamps:
            dt = _parse_iso(ts)
            if dt is not None and dt >= cutoff:
                count += 1
        return count

    def should_disable_task(
        self, task: str, reason: str, max_failures: int, time_window_hours: int
    ) -> bool:
        """判断任务是否应被自动关闭。

        Args:
            task: 任务名称。
            reason: 错误原因。
            max_failures: 最大允许失败次数。
            time_window_hours: 时间窗口（小时）。

        Returns:
            达到阈值返回 True。
        """
        count = self.get_failure_count(task, reason, time_window_hours)
        if count >= max_failures:
            logger.warning(
                f'[TaskFailureProtection] 任务 `{task}` 因 `{reason}` '
                f'在 {time_window_hours} 小时内失败 {count}/{max_failures} 次，'
                f'触发自动关闭'
            )
            return True
        return False

    def add_notification(self, task: str, reason: str, count: int) -> None:
        """添加一条任务关闭通知。

        通知用于 WebUI 弹窗展示，标记为未读。

        Args:
            task: 被关闭的任务名称。
            reason: 触发关闭的错误原因。
            count: 累计失败次数。
        """
        notifications = self._data.setdefault('notifications', [])
        notifications.append({
            'task': task,
            'reason': reason,
            'count': count,
            'timestamp': _now_iso(),
            'read': False,
        })
        self._save()

    def get_unread_notifications(self) -> List[Dict[str, Any]]:
        """获取所有未读通知。

        Returns:
            未读通知列表，每项包含 task/reason/count/timestamp 字段。
        """
        return [
            n for n in self._data.get('notifications', [])
            if not n.get('read', False)
        ]

    def mark_notification_read(self, task: str) -> None:
        """将指定任务的通知标记为已读。

        Args:
            task: 任务名称。
        """
        changed = False
        for n in self._data.get('notifications', []):
            if n.get('task') == task and not n.get('read', False):
                n['read'] = True
                changed = True
        if changed:
            self._save()

    def clear_task_failures(self, task: str) -> None:
        """清除指定任务的所有失败记录。

        任务被成功关闭或用户重新启用后调用。

        Args:
            task: 任务名称。
        """
        failures = self._data.get('failures', {})
        if task in failures:
            del failures[task]
            self._save()

    def clear_task_notifications(self, task: str) -> None:
        """删除指定任务的所有通知记录。"""
        notifications = self._data.get('notifications', [])
        self._data['notifications'] = [
            n for n in notifications if n.get('task') != task
        ]
        self._save()

    def reset_task(self, task: str) -> None:
        """完全重置指定任务的失败跟踪状态。

        清除失败记录和通知，用于用户重新启用任务时。

        Args:
            task: 任务名称。
        """
        self.clear_task_failures(task)
        self.clear_task_notifications(task)

    def cleanup_old_failures(self, time_window_hours: int) -> None:
        """清理超过时间窗口的旧失败记录。

        定期调用以避免记录文件无限增长。

        Args:
            time_window_hours: 时间窗口（小时），超过此时间的记录将被删除。
        """
        cutoff = datetime.now() - timedelta(hours=time_window_hours)
        failures = self._data.get('failures', {})
        changed = False

        for task in list(failures.keys()):
            task_failures = failures[task]
            for reason in list(task_failures.keys()):
                original = task_failures[reason]
                kept = [
                    ts for ts in original
                    if _parse_iso(ts) is not None and _parse_iso(ts) >= cutoff
                ]
                if len(kept) != len(original):
                    if kept:
                        task_failures[reason] = kept
                    else:
                        del task_failures[reason]
                    changed = True
            if not task_failures:
                del failures[task]
                changed = True

        # 清理已读通知（保留最近 50 条）
        notifications = self._data.get('notifications', [])
        read_notifications = [n for n in notifications if n.get('read', False)]
        if len(read_notifications) > 50:
            keep_ids = set(id(n) for n in read_notifications[-50:])
            self._data['notifications'] = [
                n for n in notifications
                if not n.get('read', False) or id(n) in keep_ids
            ]
            changed = True

        if changed:
            self._save()
