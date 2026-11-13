"""统计页「正在进行中」区块：运行中的委托与科研项目。

数据来自 module.statistics.running_projects（cl1_data.db 的 running_projects
轻量表），展示层只做「读 + 拼 HTML」，不写库、不截图、不新增轮询任务。

**三版样式**：改 _RUNNING_STYLE 一行即可切换，CSS 全在
assets/gui/css/statistics-alas.css 的 .st-running-* 里。

- ``cards``  ：卡片网格（默认，信息最全）
- ``table``  ：紧凑表格（一屏看更多，适合窄屏）
- ``compact``：单行条目（最省高度，贴在标题下面）

设计约束（与统计页其余区块一致）：
1. 图标必须内联成 data URI —— 远控 P2P 下静态路径会静默 404；
2. 这里只返回 HTML 字符串，不碰 scope：调用方负责 put_html，
   便于无会话环境下做纯字符串单测；
3. 任何异常都吞掉并返回空串，宁可少一块也不能让整个统计面板白屏。
"""
from datetime import datetime
from html import escape

from module.logger import logger
from module.webui.app_types import WebUIMixinBase


# 版式开关：cards / table / compact
_RUNNING_STYLE = 'cards'

# 状态文案（纯展示层，不依赖 i18n：四语言加键流程反而更脆）
_STATE_LABELS = {
    'running': '进行中',
    'waiting': '队列中',
    'queueoff': '队列外',
    'settle': '待结算',
}

# 时间缺失时的占位符
_NO_START = '—'


def _fmt_time(moment, now=None):
    """datetime -> 「MM-DD HH:MM」；当天只显示时分，更好读。

    Args:
        moment: 目标时间。
        now: 参考时间，缺省取当前时间。

    Returns:
        str: 格式化文本；moment 为空时返回占位符。
    """
    if not isinstance(moment, datetime):
        return _NO_START
    if now is None:
        now = datetime.now()
    if moment.date() == now.date():
        return f"{moment:%H:%M}"
    return f"{moment:%m-%d %H:%M}"


def _fmt_remain(finish, now=None):
    """剩余时长 -> 「剩余 1小时31分」这类可读文本。

    Args:
        finish: 预计完成时间。
        now: 参考时间。

    Returns:
        tuple[bool, str]: (是否已完成, 展示文本)。
    """
    if not isinstance(finish, datetime):
        return False, ''
    if now is None:
        now = datetime.now()
    remain = finish - now
    total = int(remain.total_seconds())
    if total <= 0:
        return True, '已完成 · 待结算'
    hours, rest = divmod(total, 3600)
    minutes = rest // 60
    if hours:
        return False, f'剩余 {hours}小时{minutes:02d}分'
    if minutes:
        return False, f'剩余 {minutes}分'
    return False, f'剩余 {rest}秒'


def _fmt_hours(hours):
    """时长（小时，float）-> 「4小时」「2.5小时」「30分」。

    Args:
        hours: 小时数。

    Returns:
        str: 展示文本。
    """
    try:
        value = float(hours or 0)
    except (TypeError, ValueError):
        return ''
    if value <= 0:
        return ''
    if value < 1:
        return f'{int(round(value * 60))}分'
    if abs(value - round(value)) < 0.05:
        return f'{int(round(value))}小时'
    return f'{value:g}小时'


def _icon_data_uri(filename):
    """物品图标 -> data URI；取不到时返回空串（调用方退回纯文字）。

    Args:
        filename: assets/gui/icon/ 下的文件名，如 icon_1.png。

    Returns:
        str: data URI 或空串。
    """
    if not filename:
        return ''
    try:
        from module.webui.app_stat_commission import _commission_icon_data_uri

        return _commission_icon_data_uri(filename)
    except Exception:
        return ''


def _research_icon_data_uri(template_name):
    """科研物品模板图 -> data URI；取不到时返回空串。

    Args:
        template_name: assets/stats/research_items/ 下的模板名（不含 .png）。

    Returns:
        str: data URI 或空串。
    """
    if not template_name:
        return ''
    try:
        from module.webui.app_stat_research import _research_item_icon_data_uri

        return _research_item_icon_data_uri(template_name) or ""
    except Exception:
        return ''


class RunningProjectsMixin(WebUIMixinBase):
    """统计页「正在进行中」区块。"""

    # ------------------------------------------------------------------
    # 取数
    # ------------------------------------------------------------------

    def _load_running_snapshot(self):
        """读取运行中快照；实例名缺失或读库失败时返回空快照。

        Returns:
            dict: load_snapshot 的返回值。
        """
        from module.statistics.running_projects import load_snapshot

        instance = getattr(self, 'alas_name', None)
        if not instance:
            try:
                from module.config.utils import alas_instance

                all_instances = alas_instance()
                instance = all_instances[0] if all_instances else None
            except Exception:
                instance = None
        return load_snapshot(instance)

    # ------------------------------------------------------------------
    # 委托区块
    # ------------------------------------------------------------------

    def _build_running_commission_html(self, snapshot=None, now=None):
        """拼「正在进行中的委托」HTML。

        Args:
            snapshot: _load_running_snapshot 的结果；None 时自行读取。
            now: 参考时间。

        Returns:
            str: HTML 片段；无数据时返回空态卡片，出错返回空串。
        """
        try:
            now = now or datetime.now()
            if snapshot is None:
                snapshot = self._load_running_snapshot()
            records = list(snapshot.get('commissions') or [])
            if not records:
                return self._build_running_empty_html(
                    '正在进行中的委托',
                    '当前没有正在运行的委托',
                    '委托任务下次运行后，这里会显示正在进行中的委托',
                )
            running = sum(
                1
                for r in records
                if not isinstance(r.get('finish_dt'), datetime)
                or r['finish_dt'] > now
            )
            records.sort(
                key=lambda r: (
                    isinstance(r.get('finish_dt'), datetime)
                    and r['finish_dt'] <= now,
                    r.get('finish_dt') or datetime.max,
                )
            )
            cards = [self._build_running_commission_card(r, now) for r in records]
            return self._wrap_running_section(
                title='正在进行中的委托',
                subtitle=f'共 {len(records)} 个',
                tags=[
                    (f'进行中 {running}', 'ok'),
                    (f'待结算 {len(records) - running}', 'warn')
                    if len(records) - running
                    else None,
                ],
                body=self._build_running_body(cards),
            )
        except Exception as e:
            logger.warning(f'[WebUI-统计] 进行中委托渲染失败: {e}')
            return ''

    def _build_running_commission_card(self, record, now):
        """单条委托卡片的 HTML。

        Args:
            record: load_snapshot 里的一条委托记录。
            now: 参考时间。

        Returns:
            str: 卡片 HTML。
        """
        name = escape(record.get('display_name') or '未知委托')
        hours = _fmt_hours(record.get('duration_h'))
        start = _fmt_time(record.get('start_dt'), now)
        finish = _fmt_time(record.get('finish_dt'), now)
        done, remain = _fmt_remain(record.get('finish_dt'), now)
        state = 'settle' if done else 'running'
        items = self._build_running_items(record.get('items') or [])
        return self._render_running_card(
            name=name,
            hours=hours,
            badge=_STATE_LABELS[state],
            state=state,
            start=start,
            finish=finish,
            remain=remain,
            remain_done=done,
            items=items,
        )

    # ------------------------------------------------------------------
    # 科研区块
    # ------------------------------------------------------------------

    def _build_running_research_html(self, snapshot=None, now=None):
        """拼「进行中的科研项目」HTML。

        Args:
            snapshot: _load_running_snapshot 的结果；None 时自行读取。
            now: 参考时间。

        Returns:
            str: HTML 片段；无数据时返回空态卡片，出错返回空串。
        """
        try:
            now = now or datetime.now()
            if snapshot is None:
                snapshot = self._load_running_snapshot()
            records = list(snapshot.get('research') or [])
            if not records:
                return self._build_running_empty_html(
                    '进行中的科研项目',
                    '科研队列空闲',
                    '科研任务填充队列后，这里会显示 1 条进行中 + 最多 5 条排队中',
                )
            cards = [self._build_running_research_card(r, now) for r in records]
            running = 0
            queued = 0
            outside = 0
            for record in records:
                state, waiting = self._research_slot_state(record, now)
                if not record.get('in_queue', True):
                    outside += 1
                elif waiting:
                    queued += 1
                else:
                    running += 1
            tags = []
            if running:
                tags.append((f'进行中 {running}', 'ok'))
            if queued:
                tags.append((f'队列中 {queued}', 'accent'))
            if outside:
                tags.append((f'队列外 {outside}', 'warn'))
            return self._wrap_running_section(
                title='进行中的科研项目',
                subtitle='队列 FIFO，等待项为按链条推算的预计时间',
                tags=tags,
                body=self._build_running_body(cards),
            )
        except Exception as e:
            logger.warning(f'[WebUI-统计] 进行中科研渲染失败: {e}')
            return ''

    @staticmethod
    def _research_slot_state(record, now):
        """判断一条科研记录的槽位状态。

        **以游戏读到的真实槽位为准**（slot 字段），时间只作兜底：
        早期版本纯靠时间推算，导致「已领完的项目」和「队列外第 6 位」
        在界面上分不清、也会残留成永远不消失的卡片。

        Args:
            record: 科研记录。
            now: 参考时间。

        Returns:
            tuple[str, bool]: (状态键, 是否在排队等待)。
        """
        if not record.get('in_queue', True):
            # 队列外第 6 位：独立计时，已完成时同样是待结算
            finish = record.get('finish_dt')
            if isinstance(finish, datetime) and finish <= now:
                return 'settle', False
            return 'queueoff', False
        slot = str(record.get('slot') or '')
        if slot == 'finished':
            return 'settle', False
        if slot == 'waiting':
            return 'waiting', True
        if slot == 'running':
            return 'running', False
        # 旧快照没有 slot 字段：退回时间推算
        finish = record.get('finish_dt')
        start = record.get('start_dt')
        if isinstance(start, datetime) and start > now:
            return 'waiting', True
        if isinstance(finish, datetime) and finish <= now:
            return 'settle', False
        return 'running', False

    def _build_running_research_card(self, record, now):
        """单条科研卡片的 HTML。

        Args:
            record: load_snapshot 里的一条科研记录。
            now: 参考时间。

        Returns:
            str: 卡片 HTML。
        """
        name = escape(record.get('name') or '未知项目')
        hours = _fmt_hours(record.get('duration_h'))
        start = _fmt_time(record.get('start_dt'), now)
        finish = _fmt_time(record.get('finish_dt'), now)
        state, waiting = self._research_slot_state(record, now)
        if waiting:
            badge = f'队列 {int(record.get("queue_index", 0))}'
            remain = ''
            done = False
        else:
            badge = _STATE_LABELS.get(state, '进行中')
            done, remain = _fmt_remain(record.get('finish_dt'), now)
        genre_label = record.get('genre_label') or ''
        items = self._build_running_research_items(record.get('items') or [])
        return self._render_running_card(
            name=name,
            hours=hours,
            badge=badge,
            state=state,
            start=start,
            finish=finish,
            remain=remain,
            remain_done=done,
            items=items,
            subtitle=genre_label,
        )

    # ------------------------------------------------------------------
    # 共享渲染片段
    # ------------------------------------------------------------------

    def _build_running_body(self, cards):
        """按 _RUNNING_STYLE 组装卡片容器。

        Args:
            cards: 卡片 HTML 列表。

        Returns:
            str: 容器 HTML。
        """
        style = _RUNNING_STYLE
        if style == 'table':
            return (
                '<div class="st-running-table">'
                + ''.join(cards)
                + '</div>'
            )
        if style == 'compact':
            return (
                '<div class="st-running-compact">'
                + ''.join(cards)
                + '</div>'
            )
        return '<div class="st-running-grid">' + ''.join(cards) + '</div>'

    def _wrap_running_section(self, title, subtitle, tags, body):
        """区块外框：标题 + 副标题 + 标签 + 卡片体。

        Args:
            title: 标题文本。
            subtitle: 副标题文本。
            tags: [(文本, 变体)] 列表；None 项会被跳过。
            body: 卡片容器 HTML。

        Returns:
            str: 区块 HTML。
        """
        tag_html = ""
        for tag in tags or []:
            if not tag:
                continue
            text, variant = tag
            tag_html += (
                f'<span class="st-running-tag st-running-tag--{variant}">'
                f"{escape(text)}</span>"
            )
        return (
            '<div class="st-running-section">'
            '<div class="st-running-head">'
            f'<span class="st-running-title">{escape(title)}</span>'
            f'<span class="st-running-sub">{escape(subtitle)}</span>'
            '<span class="st-running-spacer"></span>'
            f"{tag_html}"
            '</div>'
            f"{body}"
            '</div>'
        )

    def _render_running_card(
        self, name, hours, badge, state, start, finish, remain, remain_done, items,
        subtitle='',
    ):
        """三种版式共用的卡片渲染（按 _RUNNING_STYLE 出不同结构）。

        Args:
            name: 主标题。
            hours: 时长档文本。
            badge: 状态徽标文本。
            state: 状态键（决定徽标配色）。
            start: 开始时间文本。
            finish: 完成时间文本。
            remain: 剩余时间文本。
            remain_done: 是否已完成（影响剩余文案配色）。
            items: 物品行 HTML。
            subtitle: 副标题（科研类型）。

        Returns:
            str: 卡片 HTML。
        """
        hours_html = (
            f'<span class="st-running-hours">{escape(hours)}</span>' if hours else ''
        )
        sub_html = (
            f'<span class="st-running-sub2">{escape(subtitle)}</span>'
            if subtitle
            else ''
        )
        remain_html = ''
        if remain:
            klass = 'st-running-remain'
            if remain_done:
                klass += ' st-running-remain--settle'
            remain_html = f'<span class="{klass}">{escape(remain)}</span>'
        badge_html = (
            f'<span class="st-running-state st-running-state--{state}">'
            f"{escape(badge)}</span>"
        )
        items_html = (
            f'<div class="st-running-items">{items}</div>' if items else ''
        )
        if _RUNNING_STYLE == 'compact':
            return (
                '<div class="st-running-card">'
                '<div class="st-running-top">'
                f'<span class="st-running-name">{escape(name)}</span>'
                f"{hours_html}{sub_html}"
                f"{badge_html}"
                '</div>'
                '<div class="st-running-times">'
                f'<span>开始 <b>{escape(start)}</b></span>'
                f'<span>完成 <b>{escape(finish)}</b></span>'
                f"{remain_html}"
                '</div>'
                f"{items_html}"
                '</div>'
            )
        if _RUNNING_STYLE == 'table':
            return (
                '<div class="st-running-card">'
                f'<div class="st-running-td-name">{escape(name)}{hours_html}</div>'
                f'<div class="st-running-td">{escape(start)}</div>'
                f'<div class="st-running-td">{escape(finish)}</div>'
                f'<div class="st-running-td">{remain_html or "&mdash;"}</div>'
                f'<div class="st-running-td-badge">{badge_html}</div>'
                '</div>'
            )
        return (
            '<div class="st-running-card">'
            '<div class="st-running-top">'
            f'<span class="st-running-name">{escape(name)}</span>'
            f"{hours_html}{sub_html}"
            f"{badge_html}"
            '</div>'
            '<div class="st-running-times">'
            f'<span>开始 <b>{escape(start)}</b></span>'
            f'<span>完成 <b>{escape(finish)}</b></span>'
            f"{remain_html}"
            '</div>'
            f"{items_html}"
            '</div>'
        )

    def _build_running_items(self, item_keys):
        """委托预期物品行 HTML。

        Args:
            item_keys: 物品 key 列表（Gem/Cube/...）。

        Returns:
            str: 物品 HTML；无物品返回空串。
        """
        from module.statistics.running_projects import COMMISSION_ITEM_META

        parts = []
        for key in item_keys or []:
            label, icon = COMMISSION_ITEM_META.get(key, (key, ''))
            uri = _icon_data_uri(icon)
            img = f'<img src="{uri}" alt="">' if uri else ''
            parts.append(
                f'<span class="st-running-item">{img}<span>{escape(label)}</span></span>'
            )
        return ''.join(parts)

    def _build_running_research_items(self, items):
        """科研预期产出行 HTML。

        Args:
            items: [{"label", "icon", "raw"}] 列表。

        Returns:
            str: 物品 HTML；无产出返回空串。
        """
        parts = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get('label') or item.get('raw') or '')
            if not label:
                continue
            uri = _research_icon_data_uri(item.get('icon') or '')
            img = f'<img src="{uri}" alt="">' if uri else ''
            parts.append(
                f'<span class="st-running-item">{img}<span>{escape(label)}</span></span>'
            )
        return ''.join(parts)

    def _build_running_empty_html(self, title, main, hint):
        """空态区块 HTML。

        Args:
            title: 区块标题。
            main: 主文案。
            hint: 补充说明。

        Returns:
            str: 区块 HTML。
        """
        body = (
            '<div class="st-running-empty">'
            f"<b>{escape(main)}</b><br>{escape(hint)}"
            '</div>'
        )
        return self._wrap_running_section(title=title, subtitle="", tags=[], body=body)
