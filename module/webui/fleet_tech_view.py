"""舰队科技列表页的筛选、排序、分页与表格 HTML；纯逻辑便于单测。

额外约定：单元格钩子用 ``data-ft-sort`` / ``data-ft-edit`` 标记，
由页面 Mixin 注入的脚本代理成隐藏 pin 事件（见 app_fleet_technology）。
"""
from html import escape as html_escape

from module.runtime.fleet_tech import ship_type
from module.webui import lang
from module.webui.lang import t

# 舰种/属性显示顺序与上游页面保持一致（界面分类合并后的 id）。
TYPE_ORDER = (1, 2, 3, 18, 4, 5, 7, 6, 13, 12, 10, 8, 17, 19, 22)
ATTR_ORDER = (1, 2, 3, 4, 5, 6, 9, 8, 12)
RARITY_ORDER = ('N', 'R', 'SR', 'SSR', 'UR', 'PR', 'DR')
STATUS_ORDER = ('incomplete', 'unfinished', 'unowned', 'owned', 'complete')
FRONT_TYPES = (1, 2, 3, 18)
BACK_TYPES = (4, 5, 6, 7, 10, 12, 13)

PAGE_SIZE = 50
FILTER_GROUPS = ('type', 'rarity', 'nation', 'bonusType', 'attr', 'status')
SORT_KEYS = ('code', 'name', 'nation', 'type', 'pt_get', 'pt_upgrage', 'pt_level', 'total', 'get', 'level')
TABS = ('tech', 'info', 'mind')
_NAME_LANGUAGE_KEYS = {'zh-CN': 'cn', 'zh-TW': 'tw', 'en-US': 'en', 'ja-JP': 'jp'}


def tabs_html(active):
    """页签行：data-ft-tab 钩子经点击桥转发切换。"""
    labels = {'tech': 'Gui.FleetTechnology.TabTech',
              'info': 'Gui.FleetTechnology.TabInfo',
              'mind': 'Gui.FleetTechnology.TabMind'}
    items = ''.join(
        f'<button type="button" class="ft-tab{" active" if key == active else ""}" role="tab"'
        f' aria-selected="{str(key == active).lower()}" data-ft-tab="{key}">{t(labels[key])}</button>'
        for key in TABS
    )
    return f'<div class="ft-tabs" role="tablist">{items}</div>'


def combine_rows(catalog, report):
    """把目录行与进度行按群组合并；两者都有的舰船才可渲染。"""
    progress = {row['group_type']: row for row in report['ships']}
    return [dict(ship, **progress[ship['group_type']]) for ship in catalog['ships'] if ship['group_type'] in progress]


def nation_label(value):
    """阵营名；数据里没有命名的联动分组回退为「联动 · id」。"""
    key = f'Gui.FleetTechnology.Nation.{value}'
    label = t(key)
    return f"{t('Gui.FleetTechnology.Collaboration')} · {value}" if label == key else label


def rarity_label(value):
    return t(f'Gui.FleetTechnology.Rarity.{value}')


def type_label(value):
    return t(f'Gui.FleetTechnology.Type.{value}')


def attr_label(value):
    return t(f'Gui.FleetTechnology.Attr.{value}')


def status_label(value):
    return t(f'Gui.FleetTechnology.Status.{value}')


def display_name(row, language=None):
    """按当前界面语言取舰名，缺失时回落中文名。"""
    language = language or lang.LANG
    names = row.get('names') or {}
    return names.get(_NAME_LANGUAGE_KEYS.get(language, 'cn')) or row['name']


def effect_text(row, stage):
    """一条加成文案，如「驱逐/轻巡 耐久+1」；无科技返回破折号。"""
    tech = row.get('technology')
    if not tech:
        return '—'
    classes = '/'.join(dict.fromkeys(type_label(ship_type(value)) for value in tech[f'add_{stage}_shiptype']))
    return f"{classes} {attr_label(tech[f'add_{stage}_attr'])}+{tech[f'add_{stage}_value']}"


def matches(row, filters, query, scope, stage='all'):
    """组合筛选：组内多选为或，组间为且；加成组在最后统一判断。"""
    tech = row.get('technology')
    if scope == 'tech' and not tech:
        return False
    if scope == 'none' and tech:
        return False
    if query:
        needle = query.lower()
        fields = [row.get('name'), str(row['code']).rjust(3, '0'), *(row.get('names') or {}).values()]
        if not any(field and needle in str(field).lower() for field in fields):
            return False
    value = ship_type(row['type'])
    categories = [str(value), *(('front',) if value in FRONT_TYPES else ('back',) if value in BACK_TYPES else ())]
    statuses = [row['status'], *(('owned',) if row.get('owned') else ()),
                *(('incomplete',) if row['status'] in ('unowned', 'unfinished') else ())]
    for key in FILTER_GROUPS:
        if key in ('bonusType', 'attr'):
            continue
        wanted = filters.get(key)
        if not wanted:
            continue
        if key == 'type':
            current = categories
        elif key == 'status':
            current = statuses
        else:
            current = [str(row['nationality'] if key == 'nation' else row['rarity'])]
        if not any(item in current for item in wanted):
            return False
    if filters.get('bonusType') or filters.get('attr'):
        if not tech:
            return False
        effects = []
        if stage != 'level':
            effects.append((tech['add_get_shiptype'], tech['add_get_attr']))
        if stage != 'get':
            effects.append((tech['add_level_shiptype'], tech['add_level_attr']))
        wanted_types, wanted_attrs = filters.get('bonusType'), filters.get('attr')
        if not any((not wanted_types or any(str(ship_type(item)) in wanted_types for item in types))
                   and (not wanted_attrs or str(attr) in wanted_attrs)
                   for types, attr in effects):
            return False
    return True


def sort_value(row, key):
    """排序键取值：科技点列无科技记 -1，保证排在有科技之后。"""
    tech = row.get('technology')
    if key in ('pt_get', 'pt_upgrage', 'pt_level'):
        return tech.get(key, -1) if tech else -1
    if key == 'total':
        return sum(tech[name] for name in ('pt_get', 'pt_upgrage', 'pt_level')) if tech else -1
    if key in ('get', 'level'):
        return effect_text(row, key)
    if key == 'name':
        return row['name']
    if key == 'nation':
        return row['nationality']
    if key == 'type':
        return row['type']
    return row['code']


def arrange_rows(rows, filters, query, scope, stage, sort_key, sort_direction):
    """筛选 + 排序，返回展示顺序的完整行列表。"""
    selected = [row for row in rows if matches(row, filters, query, scope, stage)]
    if sort_key not in SORT_KEYS:
        sort_key, sort_direction = 'code', 1
    return sorted(selected, key=lambda row: _CompareKey(sort_value(row, sort_key), sort_direction))


class _CompareKey:
    """混合数值/文本键的比较包装：数值直接比差，文本按本地序比较。"""

    __slots__ = ('value', 'direction')

    def __init__(self, value, direction):
        self.value, self.direction = value, direction

    def __lt__(self, other):
        if isinstance(self.value, (int, float)) and isinstance(other.value, (int, float)):
            delta = self.value - other.value
        else:
            left, right = str(self.value), str(other.value)
            delta = -1 if left < right else 1 if left > right else 0
        return delta * self.direction < 0


def page_slice(rows, page):
    """切出当前页；返回 (页行, 规整后的页码, 总页数)。"""
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    page = max(0, min(int(page), pages - 1))
    return rows[page * PAGE_SIZE:page * PAGE_SIZE + PAGE_SIZE], page, pages


def filter_options(key, rows):
    """筛选组选项 (值, 文案)；第一项永远是「全部」。"""
    head = [('', t('Gui.FleetTechnology.All'))]
    if key == 'type':
        return head + [('front', t('Gui.FleetTechnology.Front')), ('back', t('Gui.FleetTechnology.Back'))] \
            + [(str(value), type_label(value)) for value in TYPE_ORDER]
    if key == 'rarity':
        return head + [(value, rarity_label(value)) for value in RARITY_ORDER]
    if key == 'nation':
        nations = sorted({row['nationality'] for row in rows})
        return head + [(str(value), nation_label(value)) for value in nations]
    if key == 'bonusType':
        return head + [(str(value), type_label(value)) for value in TYPE_ORDER]
    if key == 'attr':
        return head + [(str(value), attr_label(value)) for value in ATTR_ORDER]
    return head + [(value, status_label(value)) for value in STATUS_ORDER]


def summary_html(catalog, report, rows):
    """汇总区：最高科技点、四大阵营、Lv.120 进度与资料版本。"""
    tech_count = sum(1 for row in rows if row.get('technology'))
    four = ''.join(
        f'<span>{nation_label(value)} {catalog["nation_points"].get(str(value), 0):,}　</span>'
        for value in (1, 2, 3, 4)
    )
    return (
        '<section class="ft-summary"><div>'
        f'<p>{t("Gui.FleetTechnology.Max")}：<b>{catalog["max_points"]:,}</b></p>'
        f'<p>{t("Gui.FleetTechnology.Four")}：{four}</p>'
        f'<p>{t("Gui.FleetTechnology.Count")}：<b>{report["completed"]} / {tech_count}</b>　'
        f'{t("Gui.FleetTechnology.Confirmed")}：{report["confirmed_points"]:,}　'
        f'<small>{t("Gui.FleetTechnology.PendingStar")}{report["pending_star"]}</small></p>'
        '</div>'
        f'<small>{t("Gui.FleetTechnology.Version")}：Lua · {html_escape(str(catalog["updated_at"]))}<br/>'
        f'{t("Gui.FleetTechnology.All")} {len(rows)} · {t("Gui.FleetTechnology.Tech")} {tech_count} · '
        f'{t("Gui.FleetTechnology.None")} {len(rows) - tech_count}</small></section>'
    )


def _sort_head(key, label, sort_key, sort_direction, rowspan=1, colspan=1):
    """可点击排序的表头单元格。"""
    attrs = ''
    if rowspan != 1:
        attrs += f' rowspan="{rowspan}"'
    if colspan != 1:
        attrs += f' colspan="{colspan}"'
    if key:
        state = 'none'
        mark = '↕'
        if sort_key == key:
            state = 'ascending' if sort_direction == 1 else 'descending'
            mark = '↑' if sort_direction == 1 else '↓'
        attrs += f' aria-sort="{state}" class="ft-sortable" data-ft-sort="{key}"'
        return f'<th{attrs}>{label} <small class="ft-sort-mark">{mark}</small></th>'
    return f'<th{attrs}>{label}</th>'


def table_html(rows, sort_key, sort_direction, edit_mode):
    """双层表头数据表格；rows 为当前页行。"""
    head = (
        '<thead><tr>'
        + _sort_head('code', t('Gui.FleetTechnology.Code'), sort_key, sort_direction, rowspan=2)
        + _sort_head('name', t('Gui.FleetTechnology.Name'), sort_key, sort_direction, rowspan=2)
        + _sort_head('nation', t('Gui.FleetTechnology.Nation'), sort_key, sort_direction, rowspan=2)
        + _sort_head('type', t('Gui.FleetTechnology.ShipClass'), sort_key, sort_direction, rowspan=2)
        + f'<th colspan="4">{t("Gui.FleetTechnology.Points")}</th>'
        + f'<th colspan="2">{t("Gui.FleetTechnology.Bonuses")}</th>'
        + f'<th rowspan="2">{t("Gui.FleetTechnology.Status")}</th>'
        + (f'<th rowspan="2">{t("Gui.FleetTechnology.RowEdit")}</th>' if edit_mode else '')
        + '</tr><tr>'
        + _sort_head('pt_get', t('Gui.FleetTechnology.Get'), sort_key, sort_direction)
        + _sort_head('pt_upgrage', t('Gui.FleetTechnology.Star'), sort_key, sort_direction)
        + _sort_head('pt_level', 'Lv.120', sort_key, sort_direction)
        + _sort_head('total', t('Gui.FleetTechnology.Total'), sort_key, sort_direction)
        + _sort_head('get', t('Gui.FleetTechnology.Unlock'), sort_key, sort_direction)
        + _sort_head('level', 'Lv.120', sort_key, sort_direction)
        + '</tr></thead>'
    )
    body = ''.join(_row_html(row, edit_mode) for row in rows)
    return f'<div class="ft-table-wrap"><table class="ft-table">{head}<tbody>{body}</tbody></table></div>'


def _row_html(row, edit_mode):
    """一行数据；状态色与稀有度色走 CSS 类。"""
    tech = row.get('technology')
    points = [tech[name] if tech else '—' for name in ('pt_get', 'pt_upgrage', 'pt_level')]
    if tech:
        points.append(sum(tech[name] for name in ('pt_get', 'pt_upgrage', 'pt_level')))
    else:
        points.append('—')
    level = row.get('level')
    badge = f'<span class="ft-status">{status_label(row["status"])}</span>'
    if isinstance(level, int) and level < 120:
        badge += f'<small> Lv.{level}</small>'
    if row.get('manual'):
        badge += f'<small title="{t("Gui.FleetTechnology.Manual")}"> ✎</small>'
    if not tech:
        badge += f'<small> · {t("Gui.FleetTechnology.None")}</small>'
    edit_cell = ''
    if edit_mode:
        edit_cell = (f'<td class="ft-edit"><button type="button" class="ft-edit-btn" '
                     f'data-ft-edit="{row["group_type"]}">{t("Gui.FleetTechnology.RowEdit")}</button></td>')
    return (
        f'<tr class="ft-{row["status"]}">'
        f'<td>{str(row["code"]).rjust(3, "0")}</td>'
        f'<td class="ft-name ft-rarity-{html_escape(row["rarity"])}">{html_escape(display_name(row))}</td>'
        f'<td>{nation_label(row["nationality"])}</td>'
        f'<td>{type_label(ship_type(row["type"]))}</td>'
        + ''.join(f'<td class="ft-num">{value:,}</td>' if isinstance(value, int) else f'<td class="ft-num">{value}</td>'
                  for value in points)
        + f'<td class="ft-effect">{effect_text(row, "get")}</td>'
        + f'<td class="ft-effect">{effect_text(row, "level")}</td>'
        + f'<td class="ft-status-cell">{badge}</td>'
        + edit_cell
        + '</tr>'
    )


def bonus_grid_html(report):
    """总属性加成网格：按舰种分组显示各属性累计。"""
    blocks = []
    for ship_class, attrs in report.get('bonuses', {}).items():
        if not attrs:
            continue
        values = ' · '.join(f'{attr_label(attr)}+{value}' for attr, value in attrs.items())
        blocks.append(f'<div class="ft-bonus-item"><b>{type_label(int(ship_class))}</b> {values}</div>')
    return ''.join(blocks)
