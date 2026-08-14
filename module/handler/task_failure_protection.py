"""任务失败保护模块。

跟踪任务失败记录，在用户设定的时间窗口内同一错误原因累计达到阈值时，
自动关闭该任务并生成通知供 WebUI 弹窗展示。

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
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from module.logger import logger


def _now_iso() -> str:
    """返回当前时间的 ISO 格式字符串。"""
    return datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def _parse_iso(s: str) -> Optional[datetime]:
    """解析 ISO 格式时间字符串，失败返回 None。"""
    try:
        return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
    except (ValueError, TypeError):
        return None


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
