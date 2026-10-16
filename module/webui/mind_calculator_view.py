"""心智计算器页签的过滤、统计与清单 HTML；纯逻辑便于单测。

清单行带原始索引（供编辑/删除定位）；状态四态与上游一致：
计入（included）/ 同名合并（merged）/ 待核对（review）/ 已排除（excluded）。
"""
import csv
import io
import json
from html import escape as html_escape

from module.runtime.mind_calculator import RARITIES, calculate
from module.webui.lang import t

PAGE_SIZE = 50
STATUS_FILTERS = ('all', 'included', 'merged', 'review', 'excluded')
STAGE_LABEL_KEYS = ('Stage0', 'Stage1', 'Stage2', 'Stage3', 'Stage4', 'Stage5')
IMPORT_ACCEPT = ('.json', '.csv')
SHOT_ACCEPT = ('.png', '.jpg', '.jpeg', '.webp')


def status_label(status):
    return t(f'Gui.MindCalculator.Status.{status.capitalize()}')


def rarity_label(rarity):
    return t(f'Gui.MindCalculator.Rarity.{rarity}') if rarity else '—'


def calculate_rows(ships):
    """实时计算清单；返回 (result, [(原始索引, 计算行)])。"""
    result = calculate(ships)
    return result, list(enumerate(result['ships']))


def filter_rows(rows, query, status_filter, rarity_filter, min_level, max_level):
    """搜索船名 + 状态 + 基础稀有度 + 等级范围过滤；保留原始索引。"""
    needle = (query or '').strip().casefold()
    output = []
    for index, row in rows:
        if needle and needle not in row['name'].casefold():
            continue
        if status_filter != 'all' and row['status'] != status_filter:
            continue
        if rarity_filter and row.get('base_rarity') != rarity_filter:
            continue
        level = row.get('level')
        if min_level and (not isinstance(level, int) or level < int(min_level)):
            continue
        if max_level and (not isinstance(level, int) or level > int(max_level)):
            continue
        output.append((index, row))
    return output


def page_slice(rows, page):
    """切出当前页；返回 (页行, 规整后的页码, 总页数)。"""
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    page = max(0, min(int(page), pages - 1))
    return rows[page * PAGE_SIZE:page * PAGE_SIZE + PAGE_SIZE], page, pages


def intro_html(result, catalog_updated_at):
    """说明条：计费规则、内置资料来源与清单更新时间。"""
    return (
        '<div class="ft-mind-intro">'
        f'<p>{t("Gui.MindCalculator.Hint")}</p>'
        f'<p class="ft-mind-meta">{t("Gui.MindCalculator.CatalogSource")} · '
        f'{t("Gui.MindCalculator.ListUpdated", time=html_escape(str(catalog_updated_at)))}</p>'
        '</div>'
    )


def stats_html(result):
    """四张统计卡：心智单元Ⅰ / 物资 / 计入舰船 / 待核对。"""
    number = lambda value: f'{int(value or 0):,}'  # noqa: E731
    return (
        '<div class="ft-stats">'
        f'<div class="ft-stat"><span>{t("Gui.MindCalculator.Mind")}</span>'
        f'<b>{number(result["mind"])}</b><small>{t("Gui.MindCalculator.MindTarget")}</small></div>'
        f'<div class="ft-stat"><span>{t("Gui.MindCalculator.Gold")}</span>'
        f'<b>{number(result["gold"])}</b><small>{t("Gui.MindCalculator.GoldRate")}</small></div>'
        f'<div class="ft-stat"><span>{t("Gui.MindCalculator.Included")}</span>'
        f'<b>{number(result["included"])}</b>'
        f'<small>{t("Gui.MindCalculator.MergedCount", count=result["merged"])}</small></div>'
        f'<div class="ft-stat"><span>{t("Gui.MindCalculator.Review")}</span>'
        f'<b>{number(result["review"])}</b><small>{t("Gui.MindCalculator.ReviewHint")}</small></div>'
        '</div>'
    )


def summary_table_html(result):
    """稀有度 × 觉醒阶段汇总（含各阶段剩余心智与合计）。"""
    head = ''.join(
        f'<th>{t(f"Gui.MindCalculator.{key}")}</th>' for key in STAGE_LABEL_KEYS
    )
    body = []
    for row in result['summary']:
        if not row['count'] and not row['mind']:
            continue
        name = f'<span class="ft-rarity-{row["rarity"]}">{rarity_label(row["rarity"])}</span>'
        cells = ''.join(f'<td class="ft-num">{count}</td>' for count in row['stages'])
        body.append(
            f'<tr><td>{name}</td>{cells}'
            f'<td class="ft-num">{row["mind"]:,}</td>'
            f'<td class="ft-num">{row["gold"]:,}</td></tr>'
        )
    if not body:
        body.append(f'<tr><td colspan="9" class="ft-empty">{t("Gui.MindCalculator.Empty")}</td></tr>')
    return (
        '<div class="ft-panel"><h3>'
        f'{t("Gui.MindCalculator.Summary")}</h3>'
        '<div class="ft-table-scroll"><table class="ft-table"><thead><tr>'
        f'<th>{t("Gui.MindCalculator.Rarity")}</th>{head}'
        f'<th>{t("Gui.MindCalculator.Mind")}</th><th>{t("Gui.MindCalculator.Gold")}</th>'
        '</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div></div>'
    )


def ships_table_html(rows_page, result):
    """舰船清单表：船名（来源）/ 等级 / 基础稀有度 / 状态 / 心智 / 操作。"""
    body = []
    for index, row in rows_page:
        status = row['status']
        name = html_escape(row['name'])
        source = html_escape(row.get('source') or '')
        source_html = f'<small class="ft-mind-source" title="{source}">{source}</small>' if source else ''
        rarity = rarity_label(row.get('base_rarity')) if row.get('base_rarity') else '—'
        status_cell = (
            f'<span class="ft-status ft-mind-status-{status}">{status_label(status)}</span>'
        )
        body.append(
            f'<tr class="ft-mind-{status}">'
            f'<td class="ft-name">{name}{source_html}</td>'
            f'<td class="ft-num">{row.get("level") if isinstance(row.get("level"), int) else "—"}</td>'
            f'<td>{rarity}</td>'
            f'<td class="ft-status-cell">{status_cell}</td>'
            f'<td class="ft-num">{row.get("mind", 0):,}</td>'
            '<td class="ft-status-cell">'
            f'<button type="button" class="ft-edit-btn" data-mc-edit="{index}">'
            f'{t("Gui.MindCalculator.Edit")}</button> '
            f'<button type="button" class="ft-edit-btn" data-mc-del="{index}">'
            f'{t("Gui.MindCalculator.Delete")}</button>'
            '</td></tr>'
        )
    return (
        '<div class="ft-table-scroll"><table class="ft-table"><thead><tr>'
        f'<th>{t("Gui.MindCalculator.Name")}</th>'
        f'<th>{t("Gui.MindCalculator.Level")}</th>'
        f'<th>{t("Gui.MindCalculator.BaseRarity")}</th>'
        f'<th>{t("Gui.MindCalculator.Status")}</th>'
        f'<th>{t("Gui.MindCalculator.Mind")}</th>'
        f'<th>{t("Gui.MindCalculator.Actions")}</th>'
        '</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def filter_options():
    """清单过滤的选项表：(状态值, 文案) 与稀有度档位。"""
    statuses = [('all', t('Gui.MindCalculator.All'))]
    statuses += [(status, status_label(status)) for status in STATUS_FILTERS[1:]]
    rarities = [('', t('Gui.MindCalculator.AllRarities'))]
    rarities += [(rarity, rarity_label(rarity)) for rarity in RARITIES]
    return statuses, rarities


def parse_import(filename, content):
    """解析导入的清单文件（.json / .csv）为 ship 列表。

    Raises:
        ValueError: 扩展名不支持、内容无法解析或没有任何有效条目。
    """
    name = (filename or '').lower()
    if name.endswith('.json'):
        try:
            data = json.loads(content)
        except (ValueError, TypeError) as exc:
            raise ValueError(str(exc)) from exc
        if isinstance(data, dict):
            data = data.get('ships', [])
        if not isinstance(data, list):
            raise ValueError('JSON 需要是舰船列表或含 ships 列表的对象')
        rows = [row for row in data if isinstance(row, dict)]
    elif name.endswith('.csv'):
        text = content.lstrip('\ufeff')
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(row) for row in reader]
    else:
        raise ValueError('仅支持 .json / .csv 清单文件')
    ships = []
    for row in rows:
        ship_name = str(row.get('name', '') or '').strip()
        if not ship_name:
            continue
        try:
            level = int(str(row.get('level', '') or 0))
        except ValueError:
            level = 0
        ships.append(dict(
            name=ship_name,
            level=level,
            rarity=str(row.get('rarity', '') or ''),
            base_rarity=str(row.get('base_rarity', '') or ''),
            excluded=str(row.get('excluded', '')).strip().lower() in ('1', 'true', 'yes'),
            review=str(row.get('review', '')).strip().lower() in ('1', 'true', 'yes'),
            source=str(row.get('source', '') or ''),
        ))
    if not ships:
        raise ValueError('没有解析到任何舰船条目')
    return ships


EXPORT_COLUMNS = ('name', 'level', 'rarity', 'base_rarity', 'status', 'mind', 'gold', 'source')


def export_content(result, fmt):
    """把清单（含计算列）导出为 (文件名, MIME, 文本)。"""
    rows = []
    for row in result['ships']:
        rows.append({key: row.get(key, '') if key not in ('mind', 'gold')
                     else row.get(key, 0) for key in EXPORT_COLUMNS})
    if fmt == 'json':
        return 'mind_calculator.json', 'application/json', \
            json.dumps({'ships': rows}, ensure_ascii=False, indent=2)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS))
    writer.writeheader()
    writer.writerows(rows)
    # BOM：Excel 直接打开时中文列名不乱码。
    return 'mind_calculator.csv', 'text/csv', '\ufeff' + buffer.getvalue()
