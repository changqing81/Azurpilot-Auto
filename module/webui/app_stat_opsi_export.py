"""WebUI 大世界收获统计视图。

版式是左栏筛选 + 右栏内容：
- 左栏「掉落任务筛选」：按识别 genre 列出有记录的大世界任务，点选过滤明细；
- 右栏：合计条（折叠外，按类别汇总）→ 侵蚀等级卡（本地 ``opsi_meow_card.html``
  的卡片语言）→ 物品掉落明细表（数据驱动，识别出什么显示什么，含图标、
  类别、总量、出现次数、单次均值、侵蚀等级徽标）。

合计条与明细表的数据来自 ``AzurStats.get_opsi_drop_summary``（opsi_items 表，
凡大世界任务都算，侵蚀1练级除外）；等级卡的数据来自 cl1_data.db 与本地累积
CSV，两者口径不同，界面上分区呈现。

版式在 ``assets/gui/css/meow-loot-alas.css``（class 前缀 ``meow-loot-`` 与
``meow-drop-``），这里只拼结构、不写内联布局样式 —— 内联写不了媒体查询，
也压不过主题的 ``#pywebio-scope-opsi_stats th/td { … !important }``。
"""

from base64 import b64encode
from functools import lru_cache
from html import escape as html_escape

from module.logger import logger
from module.statistics.opsi_item_names import (
    CATEGORY_CAT,
    CATEGORY_COORDINATE,
    CATEGORY_CURRENCY,
    CATEGORY_DESIGN,
    CATEGORY_MATERIAL,
    CATEGORY_OTHER,
    CATEGORY_PLATE,
    CATEGORY_REPORT,
    category_of,
    item_info,
)
from module.webui.app_dependencies import (
    Path,
    close_popup,
    current_time,
    popup,
    put_buttons,
    put_html,
    put_row,
    t,
    toast,
    use_scope,
)
from module.webui.app_helpers import (
    build_fold_block,
    build_muted_notice,
    build_title_block,
)
from module.webui.app_types import WebUIMixinBase

# 总览条固定的六项（保持旧版那一行不变，0 也占位显示）。
# (统计分类键, i18n 键后缀, 图标文件名, 主题色)
MEOW_LOOT_ITEMS = (
    ("Plate", "Plate", "meow_plate.png", "#BA7517"),
    ("GearDesignPlanT5", "GearDesign", "meow_gear_design.png", "#7F77DD"),
    ("OrdnanceTestingReportT4", "Ordnance", "meow_ordnance.png", "#C9A227"),
    ("CoordinateObscure", "Obscure", "meow_obscure.png", "#378ADD"),
    ("CoordinateAbyssal", "Abyssal", "meow_abyssal.png", "#E24B4A"),
    ("CatT3", "Cat", "meow_cat.png", "#D4537E"),
)

# 合计条与明细表的类别顺序：贵重物在前、材料与货币在后。
# (类别键, i18n 键后缀, 图标文件名或 None, 主题色)；没有专属图标的类别
# 退回同尺寸色点，见 _meow_loot_icon_html。
MEOW_LOOT_CATEGORIES = (
    (CATEGORY_PLATE, "Plate", "meow_plate.png", "#BA7517"),
    (CATEGORY_DESIGN, "Design", "meow_gear_design.png", "#7F77DD"),
    (CATEGORY_REPORT, "Report", "meow_ordnance.png", "#C9A227"),
    (CATEGORY_CAT, "Cat", "meow_cat.png", "#D4537E"),
    (CATEGORY_COORDINATE, "Coordinate", "meow_obscure.png", "#378ADD"),
    (CATEGORY_MATERIAL, "Material", None, "#639922"),
    (CATEGORY_CURRENCY, "Currency", None, "#888780"),
    (CATEGORY_OTHER, "Other", None, "#B4B2A9"),
)

# 侵蚀 1~6 全套配色：3=蓝、5=红 沿用数据收集卡徽标的既定色，其余等级补齐
MEOW_HAZARD_COLORS = {
    1: "#14A08C",
    2: "#9066E0",
    3: "#378ADD",
    4: "#E8971E",
    5: "#E24B4A",
    6: "#C4588C",
}

# 图标外框尺寸：(外框边长, 内图边长, 圆角)，按用途分三档
_ICON_BOX = {"card": (30, 24, "7"), "strip": (24, 19, "6"), "row": (22, 17, "5")}
# 无图可用时的色点直径
_ICON_DOT = {"card": 10, "strip": 8, "row": 8}

_MEOW_LOOT_ICON_ROOT = Path(__file__).resolve().parents[2] / "assets" / "gui" / "icon"
_OPSI_ITEM_ICON_ROOT = (
    Path(__file__).resolve().parents[2] / "assets" / "stats" / "opsi_items"
)


@lru_cache(maxsize=32)
def _meow_loot_icon_data_uri(icon_name: str):
    """把类别图标读成 data URI；文件缺失时返回 None。

    必须内联而不是给相对路径：远控（P2P）下 `<img src="static/...">` 会静默 404
    （app_home.py 里记过同一个坑）。图标是 48×48 PNG，六张合计约 35KB。
    """
    try:
        raw = (_MEOW_LOOT_ICON_ROOT / icon_name).read_bytes()
    except OSError:
        return None
    return f"data:image/png;base64,{b64encode(raw).decode('ascii')}"


@lru_cache(maxsize=128)
def _opsi_item_icon_data_uri(item_name: str):
    """把大世界物品图标读成 data URI；找不到模板时返回 None。

    模板文件名就是物品名（如 ``PlatePlaneT4.png``）。同一物品可能有多张不同
    稀有度的模板（``Superconductive_Metals_2.png``），按文件名排序取第一张。
    读不到时调用方退回色点 —— 猫箱、金币这类没有独立模板的物品走的就是这条。

    Args:
        item_name (str): 物品模板名。

    Returns:
        str: PNG 的 data URI；无对应模板时返回 None。
    """
    if not item_name:
        return None
    candidates = sorted(_OPSI_ITEM_ICON_ROOT.glob(f"{item_name}.png"))
    candidates += sorted(_OPSI_ITEM_ICON_ROOT.glob(f"{item_name}_*.png"))
    for path in candidates:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        return f"data:image/png;base64,{b64encode(raw).decode('ascii')}"
    return None


class OpsiExportMixin(WebUIMixinBase):
    """WebUI 大世界收获统计视图（左栏筛选 + 合计条 + 等级卡 + 物品明细表）。"""

    def _render_meowofficer_farming(self, view=None, drop=None, meow_rows=None):
        """渲染 meow_loot_scope：「大世界收获」区块。

        Args:
            view (dict): 月份视图（见 ``_load_meow_loot_view``）；None 时自取。
            drop (dict): 掉落汇总（见 ``_load_opsi_drop_view``）；None 时自取。
            meow_rows (list): 侵蚀等级卡的数据行；None 时按空列表渲染。
        """
        if view is None:
            view = self._load_meow_loot_view()
        if drop is None:
            drop = self._load_opsi_drop_view()
        if meow_rows is None:
            meow_rows = []

        with use_scope("meow_loot_scope", clear=True):
            put_html(build_title_block(t("Gui.Stat.OpsiDropTitle")))
            put_html(self._build_meow_instance_html())
            self._render_meow_loot_strip(view, drop)
            put_html(
                build_fold_block(
                    t("Gui.Stat.OpsiDropFoldTitle"),
                    self._build_meow_cards_html(meow_rows)
                    + self._build_opsi_drop_table_html(drop),
                    digest=t(
                        "Gui.Stat.OpsiDropFoldDigest",
                        n=int(drop.get("records") or 0),
                        total=f"{int(drop.get('grand_total') or 0):,}",
                    ),
                )
            )

    def _load_meow_loot_view(self):
        """加载月份视图数据（合计条与明细表共用）。

        Returns:
            dict: year / month（当前查看的月份）、is_current（是否本月）。
        """
        view_month = getattr(self, "_meow_loot_month", None)
        now = current_time()
        if view_month is None:
            year, month = now.year, now.month
            is_current = True
        else:
            year, month = view_month
            is_current = False
        return {"year": year, "month": month, "is_current": is_current}

    def _load_opsi_drop_view(self):
        """加载「大世界收获」的合计与明细数据。

        Returns:
            dict: 见 ``AzurStats.get_opsi_drop_summary``，另加 year / month /
                is_current（月份视图）与 genre（当前筛选的任务，None 表示全部）。
        """
        from module.statistics.azurstats import AzurStats

        view = self._load_meow_loot_view()
        genre = getattr(self, "_meow_task_filter", None)
        try:
            drop = AzurStats.get_opsi_drop_summary(
                year=view["year"], month=view["month"], genre=genre
            )
        except Exception:
            logger.warning('[Statistics] 汇总大世界掉落失败', exc_info=True)
            drop = {
                "items": [],
                "records": 0,
                "tasks": {},
                "grand_total": 0,
                "unknown": 0,
            }
        drop.update(view)
        drop["genre"] = genre
        return drop

    def _build_meow_instance_html(self):
        """当前实例名（固定显示，不做选择器）。

        统计口径按设备（device_id）隔离，实例名只作展示 —— 用户要求这里
        固定显示正在跑的实例，不提供切换。
        """
        instance = getattr(self, "alas_name", None)
        if not instance:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance = all_instances[0] if all_instances else "default"
        return (
            '<div class="meow-loot-instance">'
            f'<span class="meow-loot-instance-label">'
            f'{t("Gui.Stat.OpsiDropInstanceLabel")}</span>'
            f'<span class="meow-loot-instance-value">'
            f'{html_escape(str(instance))}</span>'
            "</div>"
        )

    # ---------- 总览条 ----------

    def _meow_month_buttons(self, view):
        """合计条右侧的月份切换按钮。"""
        buttons = [
            {
                "label": t("Gui.Stat.MeowLootViewHistory"),
                "value": "history",
                "color": "secondary",
            }
        ]
        if not view["is_current"]:
            buttons.append(
                {
                    "label": t("Gui.Stat.MeowLootBackToCurrent"),
                    "value": "current",
                    "color": "primary",
                }
            )
        return buttons

    def _render_meow_loot_strip(self, view, drop):
        """总览条：标题 + 六项图标合计 + 月份切换，整条只占一行。"""
        put_row(
            [
                put_html(self._build_meow_loot_strip_html(view, drop)),
                put_buttons(
                    self._meow_month_buttons(view),
                    onclick=self._on_meow_loot_month_click,
                    small=True,
                ),
            ],
            size="auto 1fr",
        ).style(
            "align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px"
        )

    def _build_meow_loot_strip_html(self, view, drop):
        """合计条左侧：标题 + 各类别合计（跨任务求和）；全 0 时显示占位提示。"""
        if view["is_current"]:
            title = t("Gui.Stat.MeowLootTitleCurrent")
        else:
            title = t(
                "Gui.Stat.MeowLootTitleHistory",
                month=f"{view['year']:04d}-{view['month']:02d}",
            )
        html = (
            '<div class="meow-loot-strip">'
            f'<span class="meow-loot-strip-title">{html_escape(title)}</span>'
        )
        totals = self._opsi_strip_totals(drop.get("items") or [])
        for key, suffix, icon_name, color in MEOW_LOOT_ITEMS:
            total = totals.get(key, 0)
            name = t(f"Gui.Stat.MeowLootItem{suffix}")
            html += (
                '<span class="meow-loot-strip-item" '
                f'title="{html_escape(str(name))}">'
                f'<span class="meow-loot-strip-icon" style="background: {color}1a;">'
                f"{self._meow_loot_icon_html(icon_name, color, 'strip')}"
                "</span>"
                f"<b>{int(total):,}</b>"
                "</span>"
            )
        return html + "</div>"

    @staticmethod
    def _opsi_strip_totals(items):
        """总览条六项的合计。

        口径与旧版一致：金菜取 ``Plate*``、彩图纸只取 ``GearDesignPlan*T5``、
        金机密只取 ``OrdnanceTestingReport*T4``、金猫箱只取 ``CatT3``。明细表
        按类别全量显示，总览条只挑这六项贵重掉落，保持用户熟悉的那一行不变。

        Args:
            items (list[dict]): ``get_opsi_drop_summary`` 的 items。

        Returns:
            dict[str, int]: 统计分类键 -> 数量合计；六项都会出现，没掉过为 0。
        """
        totals = {key: 0 for key, *_ in MEOW_LOOT_ITEMS}
        for item in items:
            name = str(item.get("name") or "")
            amount = int(item.get("amount") or 0)
            for key in totals:
                if key == "Plate":
                    matched = name.startswith("Plate")
                elif key == "GearDesignPlanT5":
                    matched = name.startswith("GearDesignPlan") and name.endswith("T5")
                elif key == "OrdnanceTestingReportT4":
                    matched = (
                        name.startswith("OrdnanceTestingReport")
                        and name.endswith("T4")
                    )
                else:
                    matched = name.startswith(key)
                if matched:
                    totals[key] += amount
                    break
        return totals

    # ---------- 右栏：物品明细表 ----------

    def _build_opsi_drop_table_html(self, drop):
        """物品掉落明细表：图标 + 名称 + 总量 + 次数 + 均值 + 等级徽标。

        行按类别分组（顺序见 MEOW_LOOT_CATEGORIES），组内按总量降序 ——
        类别做组标题行而不是列，物品名就有整行宽度，长名字不会挤成两行。
        """
        items = drop.get("items") or []
        if not items:
            return build_muted_notice(t("Gui.Stat.OpsiDropEmptyNotice"))

        grouped = {}
        for item in items:
            grouped.setdefault(category_of(item.get("name")), []).append(item)

        head = (
            '<div class="meow-drop-table">'
            '<div class="meow-drop-table-head">'
            f'<span class="meow-drop-cell-name">{t("Gui.Stat.OpsiDropItemHeader")}</span>'
            f'<span class="meow-drop-cell-amount">'
            f'{t("Gui.Stat.OpsiDropAmountHeader")}</span>'
            f'<span class="meow-drop-cell-count">'
            f'{t("Gui.Stat.OpsiDropCountHeader")}</span>'
            f'<span class="meow-drop-cell-avg">{t("Gui.Stat.OpsiDropAvgHeader")}</span>'
            f'<span class="meow-drop-cell-level">'
            f'{t("Gui.Stat.OpsiDropLevelHeader")}</span>'
            "</div>"
        )
        body = ""
        for key, suffix, _icon, color in MEOW_LOOT_CATEGORIES:
            rows = grouped.get(key)
            if not rows:
                continue
            total = sum(int(row.get("amount") or 0) for row in rows)
            body += (
                '<div class="meow-drop-group">'
                f'<span class="meow-drop-group-dot" style="background: {color};"></span>'
                f'<span class="meow-drop-group-name">'
                f'{t(f"Gui.Stat.OpsiDropCategory{suffix}")}</span>'
                f'<span class="meow-drop-group-total">{total:,}</span>'
                "</div>"
            )
            for row in rows:
                body += self._build_opsi_drop_row_html(row, color)

        return head + body + "</div>"

    def _build_opsi_drop_row_html(self, row, color):
        """明细表的一行。"""
        name = str(row.get("name") or "")
        info = item_info(name)
        amount = int(row.get("amount") or 0)
        count = int(row.get("count") or 0)
        avg = row.get("avg") or 0
        levels = row.get("levels") or {}
        badges = "".join(
            f'<span class="meow-drop-badge" '
            f'style="background: {MEOW_HAZARD_COLORS.get(level, "#888780")}1a; '
            f'color: {MEOW_HAZARD_COLORS.get(level, "#888780")};">'
            f'{t("Gui.Stat.OpsiDropLevelBadge", level=int(level), count=int(times))}'
            f"</span>"
            for level, times in sorted(levels.items())
        )
        avg_text = f"{float(avg):,.1f}" if count else "-"
        return (
            '<div class="meow-drop-row">'
            '<span class="meow-drop-cell-name">'
            f"{self._opsi_item_icon_html(name, color)}"
            f'<span class="meow-drop-item-name">{html_escape(str(info["zh"]))}</span>'
            "</span>"
            f'<span class="meow-drop-cell-amount">{amount:,}</span>'
            f'<span class="meow-drop-cell-count">{count:,}</span>'
            f'<span class="meow-drop-cell-avg">{avg_text}</span>'
            f'<span class="meow-drop-cell-level">{badges}</span>'
            "</div>"
        )

    # ---------- 月份切换交互 ----------

    def _on_meow_loot_month_click(self, value):
        """月份切换按钮回调：history 打开历史月份选择器，current 回到本月。"""
        if value == "history":
            self._show_meow_loot_month_picker()
        else:
            self._reset_meow_loot_month()

    def _show_meow_loot_month_picker(self):
        """弹出历史月份选择器。"""
        from module.statistics.azurstats import AzurStats

        now = current_time()
        months = AzurStats.get_opsi_drop_available_months()
        buttons = [
            {
                "label": t(
                    "Gui.Stat.MeowLootCurrentMonthOption",
                    month=f"{now.year:04d}-{now.month:02d}",
                ),
                "value": None,
                "color": "primary",
            }
        ]
        buttons += [
            {"label": f"{y:04d}-{m:02d}", "value": (y, m), "color": "secondary"}
            for y, m in months
            if (y, m) != (now.year, now.month)
        ]
        if len(buttons) == 1:
            toast(t("Gui.Stat.MeowLootNoHistoryMonth"))
            return

        with popup(t("Gui.Stat.MeowLootPickMonthTitle")):
            put_buttons(buttons, onclick=lambda v: self._set_meow_loot_month(v))

    def _set_meow_loot_month(self, value):
        """设置要查看的月份并重绘大世界统计分区。value 为 None 表示本月。"""
        close_popup()
        self._meow_loot_month = value
        self._render_opsi_stats()

    def _reset_meow_loot_month(self):
        """回到本月视图。"""
        self._meow_loot_month = None
        self._render_opsi_stats()

    # ---------- HTML 构造 ----------

    @staticmethod
    def _meow_loot_icon_html(icon_name, color, kind):
        """类别图标；文件缺失时退回同尺寸色点。尺寸内联，不依赖外部 CSS。"""
        box, inner_size, radius = _ICON_BOX[kind]
        data_uri = _meow_loot_icon_data_uri(icon_name) if icon_name else None
        if data_uri:
            inner = (
                f'<img src="{data_uri}" alt="" style="width: {inner_size}px; '
                f'height: {inner_size}px; display: block; object-fit: contain;">'
            )
        else:
            dot = _ICON_DOT[kind]
            inner = (
                f'<span style="display: block; width: {dot}px; height: {dot}px; '
                f'border-radius: 50%; background: {color};"></span>'
            )
        return (
            f'<span class="meow-loot-icon" style="width: {box}px; height: {box}px; '
            f'border-radius: {radius}px; background: {color}1a;">{inner}</span>'
        )

    @staticmethod
    def _opsi_item_icon_html(item_name, color):
        """明细表行首的物品图标：优先识别层模板图，缺失时退回色点。"""
        box, inner_size, radius = _ICON_BOX["row"]
        data_uri = _opsi_item_icon_data_uri(item_name)
        if data_uri:
            inner = (
                f'<img src="{data_uri}" alt="" style="width: {inner_size}px; '
                f'height: {inner_size}px; display: block; object-fit: contain;">'
            )
        else:
            dot = _ICON_DOT["row"]
            inner = (
                f'<span style="display: block; width: {dot}px; height: {dot}px; '
                f'border-radius: 50%; background: {color};"></span>'
            )
        return (
            f'<span class="meow-drop-item-icon" style="width: {box}px; '
            f'height: {box}px; border-radius: {radius}px; background: {color}1a;">'
            f"{inner}</span>"
        )
