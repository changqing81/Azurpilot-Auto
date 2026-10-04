"""游戏停服维护公告查询。

按游戏服务器（cn/en/jp/tw）查询官方停服维护公告，返回下一次维护的开始
时间。数据来自 api-blhx-maintain 聚合接口，作战委托的「维护当天作战委托」
与科研批量模式的「维护当天科研」共用这里的查询与解析。

查询只包含服务器名、不携带任何敏感数据，结果只影响任务调度判断。
"""

import re
from datetime import datetime, timedelta, timezone

import requests

from module.config.time_source import now as current_time
from module.config.utils import SERVER_TO_TIMEZONE
from module.logger import logger

# 停服维护时间接口，顶层是国服公告的聚合结果，servers 里按 cn/jp/en/tw 分别给出
# maintenance_date(YYYY-MM-DD) 与 start_time(HH:MM)
MAINTAIN_API = 'https://api-blhx-maintain.nanoda.work/api/maintenance'
# 接口里服务器时区的形状，如 `UTC+8`、`UTC-7`、`UTC+08:00`
MAINTAIN_TIMEZONE = re.compile(r'^UTC([+-])(\d{1,2})(?::?(\d{2}))?$')


def query_maintain_payload(server):
    """查询停服维护接口，取出指定游戏服务器的那一份公告。

    接口顶层是国服新闻聚合出来的结果，各服务器自己的公告在 `servers` 里，
    并且带各自的时区。只有拿不到 `servers`（旧版接口）时才退回顶层数据。

    Args:
        server (str): 游戏服务器标识，cn / en / jp / tw。

    Returns:
        tuple[dict | None, datetime.timedelta, str]:
            (维护公告, 公告时间所用的兜底时区, 失败原因)。取不到公告时公告为 None。
    """
    try:
        response = requests.get(MAINTAIN_API, timeout=10).json()
    except Exception as e:
        logger.warning(f'[维护公告] 查询维护时间失败，按不维护处理: {e}')
        return None, timedelta(), '查询维护时间失败'

    data = response.get('data') or {}
    if not data:
        detail = response.get('message') or response.get('status')
        logger.warning(f'[维护公告] 维护接口没有返回数据，按不维护处理: {detail}')
        return None, timedelta(), '维护接口没有返回数据'

    servers = data.get('servers')
    if not isinstance(servers, dict) or not servers:
        # 顶层公告来自国服新闻，退回它时只能按国服时区解释
        logger.warning('[维护公告] 维护接口没有按服务器返回数据，退回顶层公告（国服）')
        return data, SERVER_TO_TIMEZONE['cn'], ''

    payload = servers.get(server)
    if not payload:
        reason = (data.get('server_errors') or {}).get(server) or '接口未返回该服务器'
        logger.warning(f'[维护公告] {server} 的维护公告查询失败，按不维护处理: {reason}')
        return None, timedelta(), f'{server} 维护公告查询失败'

    return payload, SERVER_TO_TIMEZONE.get(server, SERVER_TO_TIMEZONE['cn']), ''


def parse_maintain_timezone(payload, default):
    """解析维护公告所用的服务器时区。

    接口在每条服务器公告里给了 timezone（如 `UTC+8`），以它为准；缺失或者
    格式不认识时用兜底时区。

    Args:
        payload (dict): 一条服务器维护公告。
        default (datetime.timedelta): 兜底时区偏移。

    Returns:
        datetime.timedelta: 相对 UTC 的时区偏移。
    """
    text = str(payload.get('timezone', '')).strip().upper()
    match = MAINTAIN_TIMEZONE.match(text)
    if not match:
        logger.warning(f'[维护公告] 无法识别的服务器时区 {text!r}，按 {default} 处理')
        return default

    offset = timedelta(hours=int(match.group(2)), minutes=int(match.group(3) or 0))
    return offset if match.group(1) == '+' else -offset


def query_maintain_today(server):
    """今天有没有停服维护，有的话返回维护开始时间（可能已经开始）。

    数据来自 api-blhx-maintain，按指定游戏服务器取对应公告，公告里的服务器
    本地时间先换算成本机时间。不是今天的维护一律返回 None；今天已经开始的
    维护仍然返回时间，好让调用方区分「今天没事了」和「等维护」。

    Args:
        server (str): 游戏服务器标识，cn / en / jp / tw。

    Returns:
        tuple[datetime.datetime | None, str, bool]:
            (今天的维护开始时间, 原因, 这次有没有成功查到公告)。
            没查到公告时第三个值为 False，调用方应过一会儿再查，而不是当成
            「今天没有维护」直接等到明天。
    """
    payload, default, error = query_maintain_payload(server)
    if payload is None:
        return None, error, False

    try:
        start = datetime.strptime(
            f"{payload.get('maintenance_date', '')} {payload.get('start_time', '')}",
            '%Y-%m-%d %H:%M')
    except ValueError:
        logger.warning(f'[维护公告] 维护公告时间无法识别，按不维护处理: '
                       f"{payload.get('maintenance_date')} {payload.get('start_time')}")
        return None, '维护公告时间无法识别', False

    # 服务器本地时间 → 本机时间，后续调度用的都是本机时间
    offset = parse_maintain_timezone(payload, default)
    start = start.replace(tzinfo=timezone(offset)).astimezone().replace(tzinfo=None)

    now = current_time()
    if start.date() != now.date():
        if start < now:
            return None, f'{start} 已经过去', True
        return None, f'下次维护 {start}，不是今天', True
    if start <= now:
        return start, f'今天 {start} 的维护已经过去', True

    name = payload.get('name') or server
    return start, f'今天 {start} 停服维护（{name}）', True
