"""WebUI 大世界收获统计视图。

版式对齐参考图版1（左栏筛选 + 右栏内容）：
- 页头：OS 徽标 + 「大世界收获 / Operation Siren Drop Statistics」+ 实例胶囊，
  月份胶囊是按钮（点开历史月份选择器）；
- 左栏「掉落任务筛选」：DropRecord 的 8 个开关 + 「全部任务」，点选按任务过滤明细；
- 右栏：收获合计条 →「掉落明细」常驻区（侵蚀等级卡 + 物品掉落明细表，表内可
  「其余 N 项」展开）。明细常驻显示、不再折叠；唯一的外层「大世界收获」折叠块
  在 ``_render_opsi_summary`` 里只建一次，展开一次后任意切换筛选/月份都不会
  被折回去（折叠块若随回调重建，会回到默认收起态，实测用户不可接受）。

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
    put_column,
    put_html,
    put_row,
    t,
    toast,
    use_scope,
)
from module.webui.app_helpers import (
    build_muted_notice,
)
from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked

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

# 类别 -> 主题色 / i18n 键后缀（明细表「类别」列用，从上面那张表派生）
_MEOW_CATEGORY_COLORS = {
    key: color for key, _suffix, _icon, color in MEOW_LOOT_CATEGORIES
}
_MEOW_CATEGORY_SUFFIX = {
    key: suffix for key, suffix, _icon, _color in MEOW_LOOT_CATEGORIES
}

# 侵蚀 1~6 全套配色：3=蓝、5=红 沿用数据收集卡徽标的既定色，其余等级补齐
MEOW_HAZARD_COLORS = {
    1: "#14A08C",
    2: "#9066E0",
    3: "#378ADD",
    4: "#E8971E",
    5: "#E24B4A",
    6: "#C4588C",
}

# 左栏「掉落任务筛选」的 8 个开关，顺序对齐参考图版1 与配置页 DropRecord 分组。
# (i18n 键后缀, 归属 genre 元组, 是否计入统计)
# genre 归属与 argument.yaml 的注释一致：跨月每日跟大世界每日、档案坐标跟隐秘
# 海域、月度Boss跟深渊坐标共用一项；None 表示兜底（其余所有 opsi_* 任务）。
OPSI_DROP_TASKS = (
    ("MeowfficerFarming", ("opsi_meowfficer_farming",), True),
    ("Abyssal", ("opsi_abyssal", "opsi_month_boss"), True),
    ("Obscure", ("opsi_obscure", "opsi_archive"), True),
    ("Stronghold", ("opsi_stronghold",), True),
    ("Daily", ("opsi_daily", "opsi_cross_month"), True),
    ("Explore", ("opsi_explore",), True),
    ("Hazard1Leveling", ("opsi_hazard1_leveling",), False),
    ("Other", None, True),
)

# 「全部任务」按钮的取值：与任务后缀区分开，避免和某个任务名撞车
OPSI_DROP_FILTER_ALL = "__all__"
# 兜底开关的后缀（genre 集合要按本月实际数据算补集）
OPSI_DROP_FILTER_OTHER = "Other"

# 明细表默认只列前几行，其余收进「其余 N 项」按钮
OPSI_DROP_TABLE_PREVIEW = 5

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

    @render_locked
    def _render_meowofficer_farming(self, view=None, drop=None, meow_rows=None):
        """渲染 meow_loot_scope：「大世界收获」区块。

        ``@render_locked`` 是必需的：``use_scope(..., clear=True)`` 与紧随其后的
        第一次输出之间存在窗口（要跑 ``_build_meow_header_html`` 等），而本区块
        同时被 PyWebIO 按钮回调线程、页面的 ``_render_opsi_stats`` 和后台刷新
        任务调用。窗口内若另一个线程也清了同一个 scope，两次清空互相抵消，
        内容就会整块翻倍（实测症状：页头出现两个）。会话级可重入锁把
        「清空 → 输出」串起来，从根上消掉这类重复。

        Args:
            view (dict): 月份视图（见 ``_load_meow_loot_view``）；None 时自取。
            drop (dict): 掉落汇总（见 ``_load_opsi_drop_view``）；None 时自取。
            meow_rows (list): 侵蚀等级卡的数据行；None 时沿用上一次渲染的缓存
                —— 任务筛选、月份切换、「其余 N 项」这类按钮回调只重绘本区块，
                不必重建 cl1 数据。
        """
        if meow_rows is None:
            meow_rows = getattr(self, "_meow_loot_cards", None) or []
        else:
            self._meow_loot_cards = meow_rows
        if view is None:
            view = self._load_meow_loot_view()
        if drop is None:
            drop = self._load_opsi_drop_view()

        # 外层「大世界收获」折叠块由 _render_opsi_summary 创建（只建一次，包住
        # 本 scope），本函数只重绘折叠内的内容。任务筛选、月份切换、「其余 N 项」
        # 等按钮回调都走这里 —— 折叠块若写在本函数里，每次回调 clear 都会把
        # <details> 重建回默认收起态，用户展开一次后点任何按钮都会被折回去。
        with use_scope("meow_loot_scope", clear=True):
            put_row(
                [
                    put_html(self._build_meow_header_html()),
                    put_buttons(
                        self._meow_month_buttons(view),
                        onclick=self._on_meow_loot_month_click,
                        small=True,
                    ),
                ],
                size="1fr auto",
            ).style("align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px")
            put_row(
                [
                    # size="auto" 是必须的：put_column 默认给每行 1fr，会把左栏的
                    # 标题/按钮/提示三行拉成等高，按钮被顶到整栏正中（实测踩过）。
                    put_column(
                        self._meow_filter_placeables(drop), size="auto"
                    ),
                    put_column(
                        [
                            put_html(self._build_meow_loot_strip_html(view, drop)),
                            *self._meow_drop_detail_placeables(meow_rows, drop),
                        ],
                        size="auto",
                    ),
                ],
                size="168px minmax(0, 1fr)",
            ).style("align-items:start; gap:14px")

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
                is_current（月份视图）与 genre（当前筛选的开关键，
                ``OPSI_DROP_FILTER_ALL`` 或 None 表示全部）。
        """
        from module.statistics.azurstats import AzurStats

        view = self._load_meow_loot_view()
        filter_key = getattr(self, "_meow_task_filter", None)
        if filter_key == OPSI_DROP_FILTER_ALL:
            filter_key = None
        try:
            if filter_key == OPSI_DROP_FILTER_OTHER:
                # 兜底开关 = 其余所有大世界任务，集合只能从本月实际数据里算补集
                full = AzurStats.get_opsi_drop_summary(
                    year=view["year"], month=view["month"]
                )
                named = {
                    genre
                    for _suffix, genres, _counted in OPSI_DROP_TASKS
                    if genres
                    for genre in genres
                }
                genres = [g for g in (full.get("tasks") or {}) if g not in named]
                if genres:
                    drop = AzurStats.get_opsi_drop_summary(
                        year=view["year"], month=view["month"], genre=genres
                    )
                else:
                    drop = dict(full, items=[], records=0, grand_total=0, unknown=0)
            else:
                drop = AzurStats.get_opsi_drop_summary(
                    year=view["year"],
                    month=view["month"],
                    genre=self._opsi_task_genres(filter_key),
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
        drop["genre"] = filter_key
        return drop

    @staticmethod
    def _opsi_task_genres(filter_key):
        """把左栏开关键翻成要过滤的 genre 列表；None 表示不过滤（全部任务）。

        兜底开关（``OPSI_DROP_FILTER_OTHER``）要按数据算补集，这里返回 None
        交给 ``_load_opsi_drop_view`` 处理。
        """
        for suffix, genres, _counted in OPSI_DROP_TASKS:
            if suffix == filter_key:
                return genres
        return None

    @staticmethod
    def _opsi_task_record_count(genres, tasks):
        """某个开关名下的记录条数（供左栏按钮显示）。

        Args:
            genres (tuple | None): 该开关归属的 genre；None 表示兜底。
            tasks (dict): ``get_opsi_drop_summary`` 的 tasks（全月全任务）。

        Returns:
            int: 记录条数合计。
        """
        if genres is None:
            named = {
                genre
                for _suffix, group, _counted in OPSI_DROP_TASKS
                if group
                for genre in group
            }
            return sum(
                int(count or 0)
                for genre, count in tasks.items()
                if genre not in named
            )
        return sum(int(tasks.get(genre, 0) or 0) for genre in genres)

    def _build_meow_header_html(self):
        """页头左侧：OS 徽标 + 中英标题 + 实例胶囊。

        统计口径按设备（device_id）隔离，实例名只作展示 —— 用户要求这里
        固定显示正在跑的实例，不提供切换；月份胶囊是右侧的按钮，见
        ``_meow_month_buttons``。
        """
        from module.config.utils import alas_instance

        all_instances = alas_instance()
        instance = getattr(self, "alas_name", None)
        if not instance:
            instance = all_instances[0] if all_instances else "default"
        label = html_escape(str(instance))
        if all_instances and instance == all_instances[0]:
            label += t("Gui.Stat.OpsiDropInstanceDefault")
        return (
            '<div class="meow-loot-head">'
            '<span class="meow-loot-badge">OS</span>'
            '<div class="meow-loot-head-text">'
            f'<div class="meow-loot-head-title">{t("Gui.Stat.OpsiDropTitle")}</div>'
            f'<div class="meow-loot-head-subtitle">'
            f'{t("Gui.Stat.OpsiDropSubtitle")}</div>'
            "</div>"
            f'<span class="meow-loot-pill" title="'
            f'{html_escape(t("Gui.Stat.OpsiDropInstanceLabel"))}">{label}</span>'
            "</div>"
        )

    # ---------- 页头 ----------

    def _meow_month_buttons(self, view):
        """页头右侧的月份胶囊：点开历史月份选择器。"""
        buttons = [
            {
                "label": f"{view['year']:04d}-{view['month']:02d}",
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

    # ---------- 左栏：掉落任务筛选 ----------

    def _meow_filter_placeables(self, drop):
        """左栏「掉落任务筛选」：全部任务 + 8 个开关 + 共用映射提示。

        按钮文案是「任务名 · 本月记录数」，选中态用按钮主色表示 —— PyWebIO
        的按钮标签是纯文本（会转义），塞不进参考图那种右对齐的「已选/未选」
        芯片，所以状态改由按钮配色承担。侵蚀1练级不进统计，单独标注且置灰。
        """
        placeables = [
            put_html(
                '<div class="meow-loot-filter-head">'
                f'<div class="meow-loot-filter-title">'
                f'{t("Gui.Stat.OpsiDropFilterTitle")}</div>'
                f'<div class="meow-loot-filter-hint">'
                f'{t("Gui.Stat.OpsiDropFilterHint")}</div>'
                "</div>"
            )
        ]
        current = drop.get("genre")
        tasks = drop.get("tasks") or {}
        buttons = [
            {
                "label": t("Gui.Stat.OpsiDropFilterAll"),
                "value": OPSI_DROP_FILTER_ALL,
                "color": "primary" if current is None else "secondary",
            }
        ]
        for suffix, genres, counted in OPSI_DROP_TASKS:
            if not counted:
                # 侵蚀1练级不进统计（genre 被数据层排除），记录数恒为 0，
                # 筛选按钮没有意义 —— 用户裁定直接移除
                continue
            label = t(f"Gui.Stat.OpsiDropTask{suffix}")
            count = self._opsi_task_record_count(genres, tasks)
            buttons.append(
                {
                    "label": f"{label} · {count:,}",
                    "value": suffix,
                    "color": "primary" if current == suffix else "secondary",
                }
            )
        placeables.append(
            put_buttons(
                buttons,
                onclick=self._on_meow_task_click,
                small=True,
                # 不能用 link_style=True：PyWebIO 在 link 模式下会**忽略 color**，
                # 8 个开关会全部渲染成同一个蓝色链接，选中态直接丢失。
                # 保留普通按钮，用 primary / secondary / light 三档表示
                # 已选 / 未选 / 不统计。
            ).style(
                # PyWebIO 的 put_column/put_row 产出的是无 class 的 grid div，
                # 左栏按钮没法用 CSS 选中，只能就地内联成竖排。
                "display:flex; flex-direction:column; align-items:stretch; gap:2px"
            )
        )
        placeables.append(
            put_html(
                f'<div class="meow-loot-filter-note">'
                f'{t("Gui.Stat.OpsiDropSharedNote")}</div>'
            )
        )
        return placeables

    # ---------- 右栏：掉落明细折叠块 ----------

    def _meow_drop_detail_placeables(self, meow_rows, drop):
        """「掉落明细」常驻输出列表：明细标签 + 侵蚀等级卡 + 物品明细表。

        用户定稿：明细常驻显示，不再包折叠块 —— 外层「大世界收获」展开后直接
        可见，任务筛选 / 月份切换也不会把它折回去。明细表默认只列前
        ``OPSI_DROP_TABLE_PREVIEW`` 行，长表靠「其余 N 项」按钮按需展开
        （状态存 ``_meow_drop_expanded``，切换筛选/月份时刻意不重置，省得
        用户反复点开）。

        这里必须返回 PyWebIO Output 列表而不是 HTML 串：「其余 N 项」是挂了
        回调的按钮（独立 DOM 节点），塞不进 HTML 串里。
        """
        digest = t(
            "Gui.Stat.OpsiDropFoldDigest",
            n=int(drop.get("records") or 0),
            total=f"{int(drop.get('grand_total') or 0):,}",
        )
        label = (
            '<div class="meow-drop-detail-label">'
            f'{html_escape(t("Gui.Stat.OpsiDropFoldTitle"))} · {html_escape(digest)}'
            "</div>"
        )
        placeables = [
            put_html(label),
            put_html(self._build_meow_cards_html(meow_rows)),
            put_html(self._build_opsi_drop_table_html(drop)),
        ]
        hidden = self._opsi_drop_hidden_rows(drop)
        if getattr(self, "_meow_drop_expanded", False):
            more_button = {
                "label": t("Gui.Stat.OpsiDropCollapse"),
                "value": "less",
                "color": "secondary",
            }
        elif hidden:
            more_button = {
                "label": t("Gui.Stat.OpsiDropMoreItems", n=hidden),
                "value": "more",
                "color": "secondary",
            }
        else:
            more_button = None
        if more_button is not None:
            placeables.append(
                put_buttons(
                    [more_button],
                    onclick=self._on_meow_drop_more_click,
                    small=True,
                    link_style=True,
                    # 居中只能内联：put_collapse 时代靠
                    # `#pywebio-scope-meow_loot_scope details .btn-group` 选中，
                    # 明细不再折叠后没有 details 祖先可挂选择器，而本区块里还有
                    # 筛选/月份按钮的 .btn-group，宽泛选中会误伤。
                ).style("display:flex; justify-content:center; margin-top:6px")
            )
        return placeables

    @staticmethod
    def _opsi_drop_hidden_rows(drop):
        """明细表被折叠掉的行数（展开状态见 ``_meow_drop_expanded``）。"""
        total = len(drop.get("items") or [])
        return max(0, total - OPSI_DROP_TABLE_PREVIEW)

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
        """物品掉落明细表：图标 + 名称 + 类别 + 总量 + 次数 + 均值 + 等级徽标。

        行按总量降序平铺（与参考图版1 一致），类别单独成列而不是分组标题行。
        默认只列前 ``OPSI_DROP_TABLE_PREVIEW`` 行，其余交给「其余 N 项」按钮
        展开（见 ``_meow_drop_detail_placeables``）。
        """
        items = drop.get("items") or []
        if not items:
            return build_muted_notice(t("Gui.Stat.OpsiDropEmptyNotice"))

        expanded = bool(getattr(self, "_meow_drop_expanded", False))
        shown = items if expanded else items[:OPSI_DROP_TABLE_PREVIEW]

        head = (
            '<div class="meow-drop-table">'
            f'<div class="meow-drop-table-hint">'
            f'{t("Gui.Stat.OpsiDropPrefsHint")}</div>'
            '<div class="meow-drop-table-head">'
            f'<span class="meow-drop-cell-name">{t("Gui.Stat.OpsiDropItemHeader")}</span>'
            f'<span class="meow-drop-cell-cat">'
            f'{t("Gui.Stat.OpsiDropCategoryHeader")}</span>'
            f'<span class="meow-drop-cell-amount">'
            f'{t("Gui.Stat.OpsiDropAmountHeader")}</span>'
            f'<span class="meow-drop-cell-count">'
            f'{t("Gui.Stat.OpsiDropCountHeader")}</span>'
            f'<span class="meow-drop-cell-avg">{t("Gui.Stat.OpsiDropAvgHeader")}</span>'
            f'<span class="meow-drop-cell-level">'
            f'{t("Gui.Stat.OpsiDropLevelHeader")}</span>'
            "</div>"
        )
        body = "".join(self._build_opsi_drop_row_html(row) for row in shown)
        return (
            head
            + body
            + f'<div class="meow-drop-source">'
            f'{t("Gui.Stat.OpsiDropSourceNote")}</div>'
            "</div>"
        )

    def _build_opsi_drop_row_html(self, row):
        """明细表的一行。"""
        name = str(row.get("name") or "")
        info = item_info(name)
        amount = int(row.get("amount") or 0)
        count = int(row.get("count") or 0)
        avg = row.get("avg") or 0
        levels = row.get("levels") or {}
        category = category_of(name)
        color = _MEOW_CATEGORY_COLORS.get(category, "#888780")
        cat_label = t(
            f"Gui.Stat.OpsiDropCategory{_MEOW_CATEGORY_SUFFIX.get(category, 'Other')}"
        )
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
            '<span class="meow-drop-cell-cat">'
            f'<span class="meow-drop-cat-dot" style="background: {color};"></span>'
            f"{cat_label}"
            "</span>"
            f'<span class="meow-drop-cell-amount">{amount:,}</span>'
            f'<span class="meow-drop-cell-count">{count:,}</span>'
            f'<span class="meow-drop-cell-avg">{avg_text}</span>'
            f'<span class="meow-drop-cell-level">{badges}</span>'
            "</div>"
        )

    # ---------- 交互 ----------

    @render_locked
    def _on_meow_loot_month_click(self, value):
        """月份胶囊回调：history 打开历史月份选择器，current 回到本月。"""
        if value == "history":
            self._show_meow_loot_month_picker()
        else:
            self._reset_meow_loot_month()

    @render_locked
    def _on_meow_task_click(self, value):
        """左栏任务筛选回调：value 是开关键，``OPSI_DROP_FILTER_ALL`` 表示全部。

        再点一次已选中的任务等于取消筛选，避免用户找不到回「全部」的路。
        """
        if value == OPSI_DROP_FILTER_ALL:
            self._meow_task_filter = None
        elif getattr(self, "_meow_task_filter", None) == value:
            self._meow_task_filter = None
        else:
            self._meow_task_filter = value
        # 刻意不重置 _meow_drop_expanded：用户展开过的明细表不因切换筛选被折回
        self._render_meowofficer_farming()

    @render_locked
    def _on_meow_drop_more_click(self, value):
        """「其余 N 项 / 收起」回调：切换明细表的展开状态。"""
        self._meow_drop_expanded = value == "more"
        self._render_meowofficer_farming()

    @render_locked
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

    @render_locked
    def _set_meow_loot_month(self, value):
        """设置要查看的月份并重绘收获区块。value 为 None 表示本月。

        只重绘 meow_loot_scope（不整块 _render_opsi_stats）：外层「大世界收获」
        折叠的开合状态因此不被重置；侵蚀卡数据（``_build_meow_rows``）固定按
        当前月构建、与查看月份无关，沿用缓存即可。
        """
        close_popup()
        self._meow_loot_month = value
        self._render_meowofficer_farming()

    @render_locked
    def _reset_meow_loot_month(self):
        """回到本月视图。只重绘收获区块，理由同 ``_set_meow_loot_month``。"""
        self._meow_loot_month = None
        self._render_meowofficer_farming()

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
