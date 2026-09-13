"""资源增减事件统计模块，记录并查询全资源的增加与消耗事件。

通过 SQLite 数据库按事件粒度存储每次资源变化（delta 正=增加，负=消耗），
并携带任务归因（source，即事件发生时正在运行的任务命令名），
用于统计页的资源增减趋势折线图、增减汇总表与消耗排行榜。

数据由 module/log_res/log_res.py 的 LogRes.__setattr__ 钩子在资源值变化时写入，
覆盖 Dashboard 全部 12 种资源；委托任务（Commission）的收益由专门的委托统计
模块负责，写入端已排除。
"""

import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from module.logger import logger


_local_lock = threading.Lock()
_LOCAL_DB = './config/resource_delta.db'
_table_ensured = False

# ts 列存 ISO 格式字符串，字符串比较即时间比较；
# 不限时间时用哨兵值替代 None，保证 SQL 恒为静态语句、占位符一一绑定
_TS_MIN = '1970-01-01 00:00:00'
_TS_MAX = '9999-12-31 23:59:59.999999'


def _ensure_table():
    """确保 resource_delta_events 表存在（仅首次调用时执行）"""
    global _table_ensured
    if _table_ensured:
        return
    os.makedirs(os.path.dirname(_LOCAL_DB), exist_ok=True)
    # closing 显式关闭连接：避免 Windows 上库文件被占用导致备份/清理失败
    with closing(sqlite3.connect(_LOCAL_DB)) as conn, conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS resource_delta_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance TEXT NOT NULL,
                ts TEXT NOT NULL,
                resource TEXT NOT NULL,
                delta INTEGER NOT NULL,
                source TEXT NOT NULL,
                balance INTEGER
            )
        ''')
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_delta_query '
            'ON resource_delta_events(instance, resource, ts)'
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_delta_source '
            'ON resource_delta_events(instance, source, ts)'
        )
        conn.commit()
    _table_ensured = True


def record_resource_delta(
    instance: str,
    resource: str,
    delta: int,
    source: str,
    balance: Optional[int] = None,
) -> bool:
    """记录一次资源增减事件。

    Args:
        instance: 配置实例名
        resource: Dashboard 资源名（如 Oil、Coin、ActionPoint）
        delta: 变化量，正=增加，负=消耗
        source: 归因任务命令名（task.command）
        balance: 变化后的资源数量，便于对账

    Returns:
        bool: 是否成功记录
    """
    try:
        _ensure_table()
        now = datetime.now().isoformat()
        with _local_lock, closing(sqlite3.connect(_LOCAL_DB)) as conn, conn:
            conn.execute(
                '''
                INSERT INTO resource_delta_events (
                    instance, ts, resource, delta, source, balance
                ) VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (instance, now, resource, int(delta), source, balance),
            )
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f'[统计-增减] 记录资源增减事件失败: {e}')
        return False


def get_delta_summary(
    instance: str,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
) -> List[Dict]:
    """按资源汇总增减情况。

    Args:
        instance: 配置实例名
        start_ts: 起始时间（含），ISO 格式字符串，None 表示不限
        end_ts: 结束时间（不含），ISO 格式字符串，None 表示不限

    Returns:
        list[dict]: 每项包含:
            - resource: 资源名
            - increase: 增加合计（正数）
            - decrease: 消耗合计（正数）
            - net: 净变化（增加 - 消耗）
            - events: 事件条数
    """
    try:
        _ensure_table()
        actual_start = start_ts if start_ts is not None else _TS_MIN
        actual_end = end_ts if end_ts is not None else _TS_MAX
        with closing(sqlite3.connect(_LOCAL_DB)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT resource,
                       SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS increase,
                       SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END) AS decrease,
                       SUM(delta) AS net,
                       COUNT(*) AS events
                FROM resource_delta_events
                WHERE instance = ? AND ts >= ? AND ts < ?
                GROUP BY resource
                ''',
                (instance, actual_start, actual_end),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.warning(f'[统计-增减] 获取资源增减汇总失败: {e}')
        return []


def get_delta_timeline(
    instance: str,
    bucket: str = 'day',
    days: int = 30,
) -> List[Dict]:
    """获取最近 days 天的资源净变化时间序列，按日/周/月分桶。

    Args:
        instance: 配置实例名
        bucket: 分桶粒度，'day' / 'week' / 'month'；
            week 取周一日期作标签，month 取 'YYYY-MM'
        days: 统计最近多少天的事件

    Returns:
        list[dict]: 按 bucket 升序，每项为 {'bucket': 标签, '<资源名>': 净变化}，
            只包含有事件的资源键，空桶由调用方按需补零
    """
    try:
        _ensure_table()
        start = (datetime.now() - timedelta(days=days)).isoformat()
        with closing(sqlite3.connect(_LOCAL_DB)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT ts, resource, delta FROM resource_delta_events
                WHERE instance = ? AND ts >= ?
                ''',
                (instance, start),
            ).fetchall()
        buckets: Dict[str, Dict[str, int]] = {}
        for row in rows:
            try:
                dt = datetime.fromisoformat(row['ts'])
            except (TypeError, ValueError):
                continue
            key = _bucket_key(dt, bucket)
            if key is None:
                continue
            slot = buckets.setdefault(key, {})
            slot[row['resource']] = slot.get(row['resource'], 0) + int(row['delta'] or 0)
        return [{'bucket': k, **buckets[k]} for k in sorted(buckets)]
    except Exception as e:
        logger.warning(f'[统计-增减] 获取资源增减趋势失败: {e}')
        return []


def _bucket_key(dt: datetime, bucket: str) -> Optional[str]:
    """把时间戳映射到分桶标签"""
    if bucket == 'week':
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime('%Y-%m-%d')
    if bucket == 'month':
        return dt.strftime('%Y-%m')
    return dt.strftime('%Y-%m-%d')


def get_consumption_ranking(
    instance: str,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
) -> List[Dict]:
    """获取消耗排行榜：只统计消耗事件（delta < 0），按资源+任务汇总。

    Args:
        instance: 配置实例名
        start_ts: 起始时间（含），ISO 格式字符串，None 表示不限
        end_ts: 结束时间（不含），ISO 格式字符串，None 表示不限

    Returns:
        list[dict]: 每项包含:
            - resource: 资源名
            - source: 任务命令名
            - consumed: 消耗合计（正数）
            - times: 消耗次数
            同一 resource 内按 consumed 降序；resource 之间的顺序为
            SQLite 分组输出顺序，展示排序由调用方负责
    """
    try:
        _ensure_table()
        actual_start = start_ts if start_ts is not None else _TS_MIN
        actual_end = end_ts if end_ts is not None else _TS_MAX
        with closing(sqlite3.connect(_LOCAL_DB)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT resource, source,
                       SUM(-delta) AS consumed,
                       COUNT(*) AS times
                FROM resource_delta_events
                WHERE instance = ? AND delta < 0 AND ts >= ? AND ts < ?
                GROUP BY resource, source
                ORDER BY resource, consumed DESC
                ''',
                (instance, actual_start, actual_end),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.warning(f'[统计-增减] 获取消耗排行失败: {e}')
        return []


__all__ = [
    'record_resource_delta',
    'get_delta_summary',
    'get_delta_timeline',
    'get_consumption_ranking',
]
