"""WebUI 耄耋相接收获统计视图（方案 F：一行总览 + fold 内次级明细）。

原「本月耄耋相接收获」独立区块拆成两半：
- 总览条（meow_loot_scope）：标题 + 各物品图标合计 + 月份切换按钮，只占一行；
- 明细表收进「耄耋相接数据收集」fold 体内的次级折叠「本月收获」，默认收起。

侵蚀等级列按数据动态生成：某等级有掉落或有有效轮次才会出现（1~6 全扫），
等级配色见 ``MEOW_HAZARD_COLORS``（3=蓝、5=红 与数据收集卡徽标一致）。
版式在 ``assets/gui/css/meow-loot-alas.css``（class 前缀 ``meow-loot-``），
这里只拼结构、不写内联布局样式 —— 内联写不了媒体查询，也压不过主题的
``#pywebio-scope-opsi_stats th/td { … !important }``。
"""

from base64 import b64encode
from functools import lru_cache

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

from module.webui.app_helpers import build_fold_block


from module.webui.app_types import WebUIMixinBase


# (统计分类键, i18n 键后缀, 图标文件名, 主题色)
MEOW_LOOT_ITEMS = (
    ("Plate", "Plate", "meow_plate.png", "#BA7517"),
    ("GearDesignPlanT5", "GearDesign", "meow_gear_design.png", "#7F77DD"),
    ("OrdnanceTestingReportT4", "Ordnance", "meow_ordnance.png", "#C9A227"),
    ("CoordinateObscure", "Obscure", "meow_obscure.png", "#378ADD"),
    ("CoordinateAbyssal", "Abyssal", "meow_abyssal.png", "#E24B4A"),
    ("CatT3", "Cat", "meow_cat.png", "#D4537E"),
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
_MEOW_LOOT_ICON_ROOT = Path(__file__).resolve().parents[2] / "assets" / "gui" / "icon"


@lru_cache(maxsize=32)
def _meow_loot_icon_data_uri(icon_name: str):
    """把物品图标读成 data URI；文件缺失时返回 None。

    必须内联而不是给相对路径：远控（P2P）下 `<img src="static/...">` 会静默 404
    （app_home.py 里记过同一个坑）。图标是 48×48 PNG，六张合计约 35KB。
    """
    try:
        raw = (_MEOW_LOOT_ICON_ROOT / icon_name).read_bytes()
    except OSError:
        return None
    return f"data:image/png;base64,{b64encode(raw).decode('ascii')}"


class OpsiExportMixin(WebUIMixinBase):
    """WebUI 耄耋相接收获统计视图（总览条 + fold 内次级明细表）。"""

    def _render_meowofficer_farming(self, view=None):
        """渲染 meow_loot_scope：方案 F 的单行总览条。"""
        with use_scope("meow_loot_scope", clear=True):
            self._render_meow_loot_strip(view)

    def _load_meow_loot_view(self):
        """加载「收获总览 + 明细」共用的视图数据。

        Returns:
            dict: year/month（查看的月份）、is_current、totals（等级→分类计数）、
                rounds（等级→有效轮次）、levels（有掉落的等级升序，明细表的列）、
                round_levels（有轮次的等级升序，供摘要行）、
                grand_total（各等级合计之和）。
        """
        from module.statistics.azurstats import AzurStats

        view_month = getattr(self, "_meow_loot_month", None)
        now = current_time()
        if view_month is None:
            year, month = now.year, now.month
            is_current = True
        else:
            year, month = view_month
            is_current = False

        totals = AzurStats.get_meow_loot_monthly_totals(year=year, month=month)
        rounds = self._load_meow_loot_rounds(year, month)
        # 明细表是「收获」表：列只跟著有掉落的等级走；仅有轮次的等级
        # 不加列（否则一排 0 列），轮次进次级折叠的摘要行。
        levels = [
            hazard_level
            for hazard_level in sorted(MEOW_HAZARD_COLORS)
            if any((totals.get(hazard_level) or {}).values())
        ]
        round_levels = [
            hazard_level for hazard_level in sorted(MEOW_HAZARD_COLORS)
            if rounds.get(hazard_level)
        ]
        grand_total = sum(
            int(value)
            for hazard_level in levels
            for value in (totals.get(hazard_level) or {}).values()
        )
        return {
            "year": year,
            "month": month,
            "is_current": is_current,
            "totals": totals,
            "rounds": rounds,
            "levels": levels,
            "round_levels": round_levels,
            "grand_total": grand_total,
        }

    def _load_meow_loot_rounds(self, year, month):
        """读取各侵蚀等级当月的有效战斗轮次（1~6 全查），失败时返回空表。

        ⚠️ 必须读 ``by_hazard`` 桶：``get_meow_stats`` 只为 3/5 构建分等级数据，
        查询 1/2/4/6 时顶层字段保持为**全月总量**（不会提升分等级值），
        直接读顶层会把总量当成该等级的轮次，一份数字重复四遍。
        """
        from module.statistics.cl1_database import db as cl1_db

        instance_name = getattr(self, "alas_name", None)
        if not instance_name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance_name = all_instances[0] if all_instances else "default"

        rounds = {}
        for hazard_level in sorted(MEOW_HAZARD_COLORS):
            try:
                meow_data = cl1_db.get_meow_stats(
                    instance_name, year, month, hazard_level=hazard_level
                )
                bucket = (meow_data.get("by_hazard") or {}).get(
                    str(hazard_level), {}
                )
                value = round(float(bucket.get("effective_rounds", 0) or 0), 1)
                # 整数轮次不带小数点，0.0 显示成 0
                rounds[hazard_level] = (
                    int(value) if abs(value - int(value)) < 1e-6 else value
                )
            except Exception:
                rounds[hazard_level] = 0
        return rounds

    def _render_meow_loot_strip(self, view):
        """总览条：标题 + 图标合计 + 月份切换，整条只占一行。"""
        if view is None:
            view = self._load_meow_loot_view()

        # 月份切换按钮放在条末尾；已经不在本月时追加「回到本月」。
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
        put_row(
            [
                put_html(self._build_meow_loot_strip_html(view)),
                put_buttons(
                    buttons,
                    onclick=self._on_meow_loot_month_click,
                    small=True,
                ),
            ],
            size="auto 1fr",
        ).style(
            "align-items:center; gap:10px; flex-wrap:wrap; "
            "margin-top:20px; margin-bottom:8px"
        )

    def _build_meow_loot_strip_html(self, view):
        """总览条左侧：标题 + 各物品图标合计（跨等级求和）；全 0 时显示占位提示。"""
        if view["is_current"]:
            title = t("Gui.Stat.MeowLootTitleCurrent")
        else:
            title = t(
                "Gui.Stat.MeowLootTitleHistory",
                month=f"{view['year']:04d}-{view['month']:02d}",
            )
        html = (
            '<div class="meow-loot-strip">'
            f'<span class="meow-loot-strip-title">{title}</span>'
        )
        if view["grand_total"] <= 0 and not view["levels"]:
            html += (
                f'<span class="meow-loot-strip-empty">'
                f'{t("Gui.Stat.MeowLootEmptyNotice")}</span>'
            )
        else:
            for key, i18n_suffix, icon_name, color in MEOW_LOOT_ITEMS:
                total = sum(
                    int((view["totals"].get(hazard_level) or {}).get(key, 0) or 0)
                    for hazard_level in view["levels"]
                )
                name = t(f"Gui.Stat.MeowLootItem{i18n_suffix}")
                html += (
                    '<span class="meow-loot-strip-item" '
                    f'title="{name}">'
                    f'<span class="meow-loot-strip-icon" style="background: {color}1a;">'
                    f"{self._meow_loot_icon_html(icon_name, color, 'strip')}"
                    "</span>"
                    f"<b>{total:,}</b>"
                    "</span>"
                )
        return html + "</div>"

    def _build_meow_loot_fold_html(self, view):
        """次级折叠「本月收获」：明细表收在数据收集 fold 体内，默认收起。

        没有任何等级有掉落或轮次时不生成，避免 fold 里挂一张全 0 空表。
        """
        if not view["levels"]:
            return ""
        if view["is_current"]:
            title = t("Gui.Stat.MeowLootFoldTitle")
        else:
            title = t(
                "Gui.Stat.MeowLootFoldTitleHistory",
                month=f"{view['year']:04d}-{view['month']:02d}",
            )
        body = self._build_meow_loot_table_html(
            view["totals"], view["rounds"], view["levels"]
        )
        digest = t("Gui.Stat.MeowLootFoldDigest", n=view["grand_total"])
        round_parts = [
            t(
                "Gui.Stat.MeowRoundDigest",
                level=hazard_level,
                rounds=f"{view['rounds'][hazard_level]:g}",
            )
            for hazard_level in view["round_levels"]
        ]
        if round_parts:
            digest += " · " + " · ".join(round_parts)
        return build_fold_block(title, body, digest=digest)

    def _build_meow_fold_digest(self, meow_rows, view):
        """数据收集 fold 的摘要：条目数 + 本月收获件数与各等级轮次。

        收获部分只在查看本月时追加 —— 历史月份的数字放进去会与卡片（恒为本月）
        串味；历史月份的信息由次级折叠自己的摘要承载。
        """
        digest = t("Gui.Stat.FoldMeowDigest", n=len(meow_rows))
        if view and view["is_current"]:
            parts = []
            if view["grand_total"] > 0:
                parts.append(t("Gui.Stat.MeowLootDigestPart", n=view["grand_total"]))
            for hazard_level in view["round_levels"]:
                round_value = view["rounds"].get(hazard_level, 0)
                if round_value:
                    parts.append(
                        t(
                            "Gui.Stat.MeowRoundDigest",
                            level=hazard_level,
                            rounds=f"{round_value:g}",
                        )
                    )
            if parts:
                digest += " · " + " · ".join(parts)
        return digest

    def _on_meow_loot_month_click(self, value):
        """月份切换按钮回调：history 打开历史月份选择器，current 回到本月。

        切月后整个大世界统计分区要重渲染 —— 摘要与次级折叠都在里面。
        """
        if value == "history":
            self._show_meow_loot_month_picker()
        else:
            self._reset_meow_loot_month()

    def _show_meow_loot_month_picker(self):
        """弹出历史月份选择器。"""
        from module.statistics.azurstats import AzurStats

        now = current_time()
        months = AzurStats.get_meow_loot_available_months()
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
        """物品图标；文件缺失时退回同尺寸色点。尺寸内联，不依赖外部 CSS。"""
        box, inner_size = {"card": (30, 24), "strip": (24, 19), "row": (22, 17)}[kind]
        radius = {"card": "7", "strip": "6", "row": "5"}[kind]
        box_style = (
            f"width: {box}px; height: {box}px; display: flex; align-items: center; "
            f"justify-content: center; flex: 0 0 auto; "
            f"border-radius: {radius}px; background: {color}1a;"
        )
        data_uri = _meow_loot_icon_data_uri(icon_name) if icon_name else None
        if data_uri:
            inner = (
                f'<img src="{data_uri}" alt="" style="width: {inner_size}px; '
                f'height: {inner_size}px; display: block; object-fit: contain;">'
            )
        else:
            dot = {"card": 10, "strip": 8, "row": 8}[kind]
            inner = (
                f'<span style="display: block; width: {dot}px; height: {dot}px; '
                f'border-radius: 50%; background: {color};"></span>'
            )
        return f'<div style="{box_style}">{inner}</div>'

    def _build_meow_loot_table_html(self, loot_totals, rounds, levels):
        """明细表：行 = 物品，列 = 有数据的侵蚀等级（动态）+ 合计。

        列头带等级色（与数据收集卡徽标同源），等级与数值靠「列头+列内」
        的位置绑定，不内联混排。
        """
        head_cells = (
            f'<th class="meow-loot-cell-name">'
            f'{t("Gui.Stat.MeowLootHeaderItem")}</th>'
            + "".join(
                f'<th class="meow-loot-cell-value">'
                f'<span class="meow-loot-th-hz" '
                f'style="color: {MEOW_HAZARD_COLORS.get(hazard_level, "#888780")};">'
                f'{t("Gui.Stat.MeowLootHazardColumn", level=hazard_level)}</span></th>'
                for hazard_level in levels
            )
            + f'<th class="meow-loot-cell-value">'
            f'{t("Gui.Stat.MeowLootHeaderTotal")}</th>'
        )
        # 列宽按等级数动态分配：物品列保底 22%，其余（含合计列）均分
        name_width = max(22, 46 - len(levels) * 4)
        value_width = (100 - name_width) / (len(levels) + 1) if levels else 100
        html = (
            '<div class="meow-loot-table-wrap" style="width: 100%;">'
            '<table class="meow-loot-table" '
            'style="width: 100%; table-layout: fixed; border-collapse: collapse;">'
            f'<colgroup><col class="meow-loot-col-name" style="width: {name_width}%;">'
            + "".join(
                f'<col class="meow-loot-col-value" style="width: {value_width:.2f}%;">'
                for _ in levels
            )
            + f'<col class="meow-loot-col-value" style="width: {value_width:.2f}%;">'
            "</colgroup>"
            f"<thead><tr>{head_cells}</tr></thead><tbody>"
        )
        for key, i18n_suffix, icon_name, color in MEOW_LOOT_ITEMS:
            values = [
                int((loot_totals.get(hazard_level) or {}).get(key, 0) or 0)
                for hazard_level in levels
            ]
            value_cells = "".join(
                f'<td class="meow-loot-cell-value">{value:,}</td>' for value in values
            )
            html += (
                f'<tr><td class="meow-loot-cell-name">'
                f'<span class="meow-loot-name-cell">'
                f"{self._meow_loot_icon_html(icon_name, color, 'row')}"
                f'{t(f"Gui.Stat.MeowLootItem{i18n_suffix}")}'
                f"</span></td>{value_cells}"
                f'<td class="meow-loot-cell-value">{sum(values):,}</td></tr>'
            )
        return html + "</tbody></table></div>"
