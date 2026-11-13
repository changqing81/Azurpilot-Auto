"""运行中委托 / 科研的快照采集与展示元数据。

统计页「正在进行中」区块的数据源在 ``cl1_data.db`` 的 ``running_projects```
表（见 module.statistics.cl1_database）。本模块负责两头：

- **写**：任务侧（委托 / 科研）在读完游戏界面后调用 ``save_*``，把当前这一屏
  的整体状态覆盖进去；
- **读**：WebUI 侧拼 HTML 时调用 ``load_snapshot`` 取快照，再用本模块的静态
  元数据把「委托类型 × 时长」「科研项目名」翻译成中文展示名与预期物品。

**为什么不塞进 cl1_data 月度整包**：月包单月可达 2.3MB，反序列化 40~60ms，
而运行中数据每几十秒就变。单独建表后，统计页读它只花几毫秒。

术语对照：
    快照(Snapshot)：某一时刻「正在跑什么」的整体状态，整体覆盖写入
    预期物品(Expected Items)：游戏内该委托/项目的固定产出表，不是本次实际掉落
"""
from datetime import datetime, timedelta

from module.logger import logger


# 委托类型 -> 中文展示名。键是 Commission.genre，值与 module/commission/project_data.py
# 的委托名称字典对应；未列出的类型直接显示 OCR 出来的日文/中文原名。
COMMISSION_GENRE_LABELS = {
    'daily_resource': '日常资源开发',
    'daily_chip': '高阶战术研发',
    'daily_event': '活动委托',
    'major_comm': '主要委托',
    'extra_drill': '航行训练',
    'extra_part': '资源运输',
    'extra_cube': '舰队演习',
    'extra_oil': '油田开采',
    'extra_book': '商船护卫',
    'extra_box': '物资运输',
    'extra_gem': '宝石开采',
    'extra_retrofit': '改装支援',
    'extra_event': '活动委托',
    'urgent_drill': '部队护送',
    'urgent_part': '资源回收',
    'urgent_book': '商船护卫',
    'urgent_box': '物资回收',
    'urgent_cube': '解救作战',
    'urgent_gem': '要员护送',
    'urgent_ship': '观舰护卫',
    'urgent_event': '紧急委托',
    'night_oil': '夜战油田',
    'night_comm': '夜间委托',
}

# 委托预期产出：genre_str（资源类型）-> 展示物品。
# 碧蓝航线委托的产出是「类型 + 时长」决定的固定表（时长越长数量越多），
# 这里只标物品种类，不猜具体数量 —— 实际数量以结算界面为准。
COMMISSION_GENRE_ITEMS = {
    'resource': ['Oil', 'Coin'],
    'chip': ['Chip'],
    'cube': ['Cube'],
    'oil': ['Oil'],
    'book': ['Book'],
    'part': ['Part'],
    'box': ['Chip'],
    'gem': ['Gem'],
    'ship': ['Ship'],
    'drill': ['Coin'],
    'event': ['Coin'],
    'retrofit': ['Chip'],
}

# 物品 key -> (中文名, 图标文件名)。图标在 assets/gui/icon/ 下。
COMMISSION_ITEM_META = {
    'Gem': ('钻石', 'icon_1.png'),
    'Cube': ('心智魔方', 'icon_2.png'),
    'Chip': ('心智单元', 'icon_3.png'),
    'Oil': ('石油', 'icon_4.png'),
    'Coin': ('金币', 'icon_5.png'),
    'Book': ('技能书', ''),
    'Part': ('改造部件', ''),
    'Ship': ('舰船', ''),
}

# 科研产出词（project_data.py 的 output 名称）-> 中文名 + 图标模板名。
# 只覆盖常见项；未命中的原样显示英文名，不阻断渲染。
RESEARCH_ITEM_LABELS = {
    'Random Blueprint': ('随机图纸', 'BlueprintAzuma'),
    'Random Gear Design': ('随机设计图', 'T0_Prototype_Triple_381mm_AA_Gun_Design'),
    'Random Gear Design T2': ('随机设计图 T2', 'T0_Prototype_Triple_381mm_AA_Gun_Design'),
    'Random Gear Design T3': ('随机设计图 T3', 'T0_Prototype_Triple_381mm_AA_Gun_Design'),
    'Coins': ('物资', 'Coins'),
    'Coins ': ('物资', 'Coins'),
    'Wisdom Cube': ('心智魔方', ''),
    'Cognitive Chips': ('心智单元', 'CognitiveChips'),
}

# 科研项目类型代码 -> 中文说明（用于卡片副标题）
RESEARCH_GENRE_LABELS = {
    'A': '基础研究',
    'B': '项目研究',
    'C': '活动研究',
    'D': '舰船研究',
    'E': '设备研究',
    'G': '资金研究',
    'H': '魔方研究',
    'Q': '部件研究',
    'T': '委托研究',
}


def _iso(moment):
    """datetime -> 可 JSON 化的 ISO 字符串；None 原样返回。"""
    if isinstance(moment, datetime):
        return moment.replace(microsecond=0).isoformat()
    return moment


def _parse_iso(text):
    """ISO 字符串 -> datetime；解析失败返回 None。"""
    if not text:
        return None
    if isinstance(text, datetime):
        return text
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def commission_display_name(genre, fallback=''):
    """委托类型 -> 中文展示名。

    Args:
        genre: Commission.genre，如 'urgent_gem'。
        fallback: 未收录类型时的兜底显示名（通常是 OCR 原名）。

    Returns:
        str: 展示名。
    """
    if genre and genre in COMMISSION_GENRE_LABELS:
        return COMMISSION_GENRE_LABELS[genre]
    return fallback or (genre or '未知委托')


def commission_expected_items(genre):
    """委托类型 -> 预期物品 key 列表（供展示层查图标与中文名）。

    Args:
        genre: Commission.genre，如 'urgent_gem'。

    Returns:
        list[str]: 物品 key；未收录返回空列表（展示层只显示名称与时间）。
    """
    if not genre or '_' not in genre:
        return []
    genre_str = genre.split('_', 1)[1]
    return list(COMMISSION_GENRE_ITEMS.get(genre_str, []))


def research_display_items(project_name, series=None):
    """科研项目名 -> 预期产出展示列表。

    数据来自 module/research/project_data.py 的 LIST_RESEARCH_PROJECT：
    项目名（如 'C-153-MI'）是字典键，output 列表就是固定产出。

    Args:
        project_name: 项目名，如 'C-153-MI'。
        series: 期数；None 表示按 name 前缀自动查找。

    Returns:
        list[dict]: [{'label': 中文名, 'icon': 图标模板名或空串}]；
            项目不在字典里时返回空列表。
    """
    if not project_name:
        return []
    try:
        from module.research.project_data import LIST_RESEARCH_PROJECT
    except Exception as e:
        logger.warning(f'[运行中] 读取科研项目字典失败: {e}')
        return []

    for data in LIST_RESEARCH_PROJECT:
        if data.get('name') != project_name:
            continue
        if series is not None and data.get('series') != series:
            continue
        items = []
        for output in data.get('output') or []:
            raw = str(output.get('name', '')).strip()
            if not raw:
                continue
            label, icon = RESEARCH_ITEM_LABELS.get(raw, (raw, ''))
            items.append({'label': label, 'icon': icon, 'raw': raw})
        return items
    return []


def research_duration_hours(project_name, series=None, fallback=0):
    """科研项目名 -> 时长（小时，float）。

    Args:
        project_name: 项目名。
        series: 期数。
        fallback: 查不到时的兜底值。

    Returns:
        float: 小时数。
    """
    if not project_name:
        return fallback
    try:
        from module.research.project_data import LIST_RESEARCH_PROJECT
    except Exception:
        return fallback
    for data in LIST_RESEARCH_PROJECT:
        if data.get('name') != project_name:
            continue
        if series is not None and data.get('series') != series:
            continue
        try:
            return round(float(data.get('time', 0)) / 3600, 2)
        except (TypeError, ValueError):
            return fallback
    return fallback


def research_genre_label(genre):
    """科研项目类型代码 -> 中文说明。"""
    if not genre:
        return ''
    return RESEARCH_GENRE_LABELS.get(str(genre)[0].upper(), '')


def save_commission_snapshot(instance, commissions, now=None):
    """把当前运行中的委托整体覆盖进数据库。

    Args:
        instance: 实例名称（config_name）。
        commissions: Commission 对象列表（只取 status == 'running' 的会被调用方过滤，
            这里再兜一层）。
        now: 参考时间，缺省取当前时间。
    """
    if not instance:
        return
    now = now or datetime.now()
    # 上一轮记下的「委托名 -> 总时长」。运行中的卡片只有倒计时，
    # 老委托的总时长只能靠这份历史记录顺延，否则就是未知。
    known_durations = {}
    try:
        from module.statistics.cl1_database import db as _db

        previous = _db.get_running_projects(instance)
        for item in previous.get("commissions") or []:
            if not isinstance(item, dict):
                continue
            if item.get("duration_h") and item.get("duration_exact"):
                known_durations[item.get("name")] = item["duration_h"]
    except Exception:
        known_durations = {}

    records = []
    for comm in commissions or []:
        try:
            if getattr(comm, 'status', '') != 'running':
                continue
            genre = str(getattr(comm, 'genre', '') or '')
            comm_key = str(getattr(comm, 'name', '') or '')
            duration = getattr(comm, 'duration', None)
            create_time = getattr(comm, 'create_time', None)
            finish_time = getattr(comm, 'finish_time', None)
            if not finish_time and duration:
                finish_time = now + duration
            # 运行中的委托卡片显示的是倒计时，duration 拿到的是剩余时间，
            # 不能直接当总时长写库（否则会出现「要员护送 28分」这种不存在的档）。
            # 用「已跑时长 + 剩余时长」去匹配该类型的合法档位还原。
            remaining_h = (
                duration.total_seconds() / 3600
                if isinstance(duration, timedelta)
                else 0
            )
            elapsed_h = 0
            if isinstance(create_time, datetime):
                elapsed_h = max((now - create_time).total_seconds() / 3600, 0)
            # 总时长与剩余时长是**两个不同属性**，绝不能混用：
            #   总时长 = 这个委托一共要做多久（游戏里只有未启动的卡片显示）；
            #   剩余   = 还要做多久（运行中的卡片显示倒计时）。
            # 拿剩余当总时长，等于「一节课 40 分钟、过了 25 分，写成这节课 15 分钟」。
            #
            # 运行中的卡片只有倒计时，读不到总时长。所以总时长只能来自两个地方：
            #   1. 本任务启动它时，启动前卡片上的值（_started_this_run）；
            #   2. 库里上一轮记下的总时长（同名委托顺延）。
            # 两处都没有时，总时长就是**未知**，宁可不显示也不能拿剩余顶上。
            started_this_run = bool(getattr(comm, "_started_this_run", False))
            if started_this_run:
                duration_h = remaining_h   # 启动前卡片的 duration 就是总时长
                duration_exact = True
            else:
                known = known_durations.get(comm_key)
                duration_h = known
                duration_exact = known is not None
            records.append(
                {
                    'name': str(getattr(comm, 'name', '') or ''),
                    'genre': genre,
                    'duration_h': duration_h,
                    'duration_exact': duration_exact,
                    'remaining_h': round(remaining_h, 2),
                    'start_time': _iso(create_time or now),
                    'finish_time': _iso(finish_time or now),
                    'items': commission_expected_items(genre),
                }
            )
        except Exception as e:
            logger.warning(f'[运行中] 委托记录打包失败: {e}')
    try:
        from module.statistics.cl1_database import db as cl1_db

        cl1_db.save_running_commissions(instance, records)
    except Exception as e:
        logger.warning(f'[运行中] 保存委托快照失败: {e}')


# 科研槽位状态（来自 ResearchQueue._queue_status_detect）：
#   'finished' 已完成待领取 / 'running' 运行中 / 'waiting' 排队中 / 'empty' 空槽
_SLOT_DONE = 'finished'
_SLOT_RUNNING = 'running'
_SLOT_WAITING = 'waiting'
_SLOT_EMPTY = 'empty'


def build_research_records(projects, durations=None, now=None):
    """把「游戏队列的真实状态」翻译成展示记录。

    这是快照的唯一构造入口，语义是**全量重建**而不是累加：本次读到的队列
    就是此刻的全部真相，上一轮的记录一律作废。早期版本按「只写本轮启动的」
    累加，领奖/完成的项目没有清理点，会在统计页越堆越多（含永久的
    「队列外 + 待结算」幽灵卡片），故改为重建。

    时间口径（队列页只给两种时长）：
    - 队首 running：显示时长 = **剩余倒计时** → 完成时间 = now + 剩余；
    - 排队 waiting：显示时长 = **项目总时长** → 按 FIFO 从上一个完成时间
      接着排；第一个等待项从「队首完成时间」起算。
    - finished（待领取）：保留卡片让用户看到「有东西没领」，但标成待结算。

    Args:
        projects: [{'name','series','genre','duration_h','slot','in_queue'}]，
            按队列从左到右（队首在前）排列。slot 是 _queue_status_detect 的返回值。
        durations: 与 projects 一一对应的 timedelta 列表（get_queue_display_times
            的返回值）；缺省或某槽为 None 时退回字典时长。
        now: 参考时间。

    Returns:
        list[dict]: 展示记录；已跳过空槽与无法计时的项目。
    """
    now = now or datetime.now()
    durations = list(durations or [])
    # 先算「队首完成时间」，供排队项起算
    records = []
    cursor = None
    for index, project in enumerate(projects or []):
        try:
            name = str(project.get('name') or '')
            series = project.get('series')
            duration_h = float(project.get('duration_h') or 0)
            if duration_h <= 0:
                duration_h = research_duration_hours(name, series, fallback=0)
            if duration_h <= 0:
                # 连字典都查不到时长的项目无法计时，跳过（不做无根据的猜测）
                logger.warning(f'[运行中] 科研项目时长未知，跳过: {name}')
                continue
            duration = timedelta(hours=duration_h)
            slot = str(project.get('slot') or (_SLOT_RUNNING if index == 0 else _SLOT_WAITING))
            in_queue = bool(project.get('in_queue', True))
            shown = durations[index] if index < len(durations) else None

            if not in_queue:
                # 队列外第 6 位：独立计时，显示时长即剩余时间
                start_time = now
                finish_time = now + (shown if shown is not None else duration)
            elif slot == _SLOT_RUNNING:
                # 队首运行中：显示的是剩余倒计时
                start_time = now
                finish_time = now + (shown if shown is not None else duration)
                cursor = finish_time
            elif slot == _SLOT_DONE:
                # 已完成待领取：卡片还在，但已经跑完了
                start_time = now - duration
                finish_time = now
                cursor = cursor or now
            else:
                # 排队中：显示的是项目总时长，从上一个完成时间接着排
                start_time = cursor or now
                finish_time = start_time + (shown if shown is not None else duration)
                cursor = finish_time

            records.append(
                {
                    'name': name,
                    'series': series,
                    'genre': str(project.get('genre') or ''),
                    'duration_h': round(duration_h, 2),
                    'queue_index': index,
                    'in_queue': in_queue,
                    'slot': slot,
                    'start_time': _iso(start_time),
                    'finish_time': _iso(finish_time),
                    'items': research_display_items(name, series),
                }
            )
        except Exception as e:
            logger.warning(f'[运行中] 科研记录打包失败: {e}')
    return records


def save_research_snapshot(instance, projects, durations=None, queue_slot=None, now=None):
    """把当前科研队列**整体重建**后写入数据库。

    Args:
        instance: 实例名称。
        projects: 队列从左到右的项目字典列表（见 build_research_records）。
        durations: get_queue_display_times() 的返回值，与 projects 一一对应。
        queue_slot: 队列空槽数。
        now: 参考时间。
    """
    if not instance:
        return
    now = now or datetime.now()
    records = build_research_records(projects, durations=durations, now=now)
    try:
        from module.statistics.cl1_database import db as cl1_db

        cl1_db.save_running_research(
            instance,
            {
                'records': records,
                'queue_slot': queue_slot,
                'saved_at': _iso(now),
            },
        )
    except Exception as e:
        logger.warning(f'[运行中] 保存科研快照失败: {e}')


def clear_research_snapshot(instance, now=None):
    """清空科研快照（队列彻底为空时调用）。

    单独留一个显式清理口：队列空的时候不该保留任何历史项目，
    这是「幽灵卡片」最直接的来源。

    Args:
        instance: 实例名称。
        now: 参考时间。
    """
    if not instance:
        return
    now = now or datetime.now()
    try:
        from module.statistics.cl1_database import db as cl1_db

        cl1_db.save_running_research(
            instance,
            {'records': [], 'queue_slot': 5, 'saved_at': _iso(now)},
        )
    except Exception as e:
        logger.warning(f'[运行中] 清空科研快照失败: {e}')


def load_snapshot(instance):
    """读取某个实例的运行中快照，并做展示前的规整。

    Args:
        instance: 实例名称。

    Returns:
        dict: {'commissions': [...], 'research': [...], 'queue_slot': int,
            'updated_at': str}；读不到时各项为空。
    """
    empty = {
        'commissions': [],
        'research': [],
        'queue_slot': None,
        'updated_at': '',
    }
    if not instance:
        return empty
    try:
        from module.statistics.cl1_database import db as cl1_db

        raw = cl1_db.get_running_projects(instance)
    except Exception as e:
        logger.warning(f'[运行中] 读取快照失败: {e}')
        return empty

    commissions = []
    for record in raw.get('commissions') or []:
        if not isinstance(record, dict):
            continue
        item = dict(record)
        item['display_name'] = commission_display_name(
            item.get('genre'), item.get('name')
        )
        item['start_dt'] = _parse_iso(item.get('start_time'))
        item['finish_dt'] = _parse_iso(item.get('finish_time'))
        commissions.append(item)

    research_raw = raw.get('research') or {}
    research = []
    for record in research_raw.get('records') or []:
        if not isinstance(record, dict):
            continue
        item = dict(record)
        item['display_name'] = item.get('name') or ''
        item['genre_label'] = research_genre_label(item.get('genre'))
        item['start_dt'] = _parse_iso(item.get('start_time'))
        item['finish_dt'] = _parse_iso(item.get('finish_time'))
        research.append(item)

    return {
        'commissions': commissions,
        'research': research,
        'queue_slot': research_raw.get('queue_slot'),
        'updated_at': raw.get('updated_at') or '',
    }
