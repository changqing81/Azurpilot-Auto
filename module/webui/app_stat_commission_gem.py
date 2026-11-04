# -*- coding: utf-8 -*-
"""WebUI 委托统计页的钻石委托成功率区块。

钻石委托只有 2 / 4 / 8 小时三档，任务侧把每次结算（含到期未获得钻石的
失败）都写进 cl1_data 的 gem_commission_entries，本模块把这些记录聚合
成「今日 / 本周 / 本月 × 三档时长」的成功率表格。

刻意不复用推送文案的排版：推送是竖排文字，界面里用表格 + 进度条才看得懂
三档之间的差异。
"""

from module.webui.app_dependencies import logger, t
from module.webui.app_types import WebUIMixinBase

# 三档时长；顺序即表格行顺序
_GEM_DURATIONS = (2, 4, 8)
# 时长 -> 通常钻石产出区间（游戏固定值，与各服委托名无关，故以时长为主键）
_GEM_DURATION_EXPECT = {2: (10, 20), 4: (25, 40), 8: (50, 80)}
# 钻石红，与收益卡片里 Gem 的配色保持一致
_GEM_COLOR = "#ff4757"
# 页面口径（今日/本周/本月按钮）-> 统计桶键。
# 用户选哪个口径，这块表就只显示哪个口径 —— 与上方收益表的今日/本周/本月联动，
# 不再三档同屏（2026-11-04 用户反馈：三档并排看着反而不知道在看什么）。
_GEM_PERIOD_BUCKETS = {
    "day": "today",
    "week": "week",
    "month": "month",
}
# 口径 -> 表头文案键，显示在标题后面，避免「现在看的是哪一档」要靠猜
_GEM_PERIOD_LABEL_KEYS = {
    "today": "Gui.Stat.CommissionIncomeDay",
    "week": "Gui.Stat.CommissionIncomeWeek",
    "month": "Gui.Stat.CommissionIncomeMonth",
}
# 空值占位：该口径下没有结算记录
_GEM_PLACEHOLDER = '<span class="gem-placeholder">&#8212;</span>'


def _gem_icon_data_uri() -> str:
    """钻石图标的内联 data URI。

    延迟导入原因：`_commission_icon_data_uri` 定义在 app_stat_commission，
    而本模块被它导入，模块级互相导入会成环。图标必须内联（远控 P2P 下
    静态 URL 会静默 404），做法与收益卡片一致。

    Returns:
        str: PNG 的 data URI；读不到图标时返回空串。
    """
    try:
        from module.webui.app_stat_commission import _commission_icon_data_uri

        return _commission_icon_data_uri("icon_1.png")
    except Exception:
        return ""


class CommissionGemStatisticsMixin(WebUIMixinBase):
    """钻石委托成功率区块（2 / 4 / 8 小时 × 今日 / 本周 / 本月）。"""

    def _load_gem_commission_stats(self, instance_name, year, month, month_data=None):
        """读取三档钻石委托统计。

        只读不写：结算由任务侧（commission.py / cl1_database）完成，
        WebUI 不在这里补记任何记录。月数据由调用方传入，避免和委托收益
        重复读同一个月的整包 JSON。

        Args:
            instance_name: 实例名称
            year: 所选年份
            month: 所选月份
            month_data: 该月整包统计数据；传 None 时自行查库

        Returns:
            dict: 形如 {'today': {2: 桶, ...}, 'week': {...}, 'month': {...}}；
                查库失败时返回 None，由渲染层退回空表。
        """
        from module.statistics.cl1_database import db as cl1_db

        try:
            return cl1_db.get_gem_commission_stats_by_periods(
                instance_name, year=year, month=month, month_data=month_data
            )
        except Exception as e:
            logger.warning(f"[WebUI-统计] 钻石委托统计读取失败: {e}")
            return None

    def _build_commission_gem_html(self, income_data) -> str:
        """构造钻石委托成功率区块的 HTML。

        表格跟随上方「今日 / 本周 / 本月」按钮的口径：选哪个就只显示哪个，
        与收益表保持一致，不再把三档并排堆在一起。

        结构：一个数字占一格。表头给出语义（成功率 / 成功次数 / 委托次数 /
        获得钻石），格内只放裸数字，不拼 "5/18" 这类合写。行 = 三档时长 + 合计。

        样式全部走 `.gem-*` 类名（见 assets/gui/css/statistics-alas.css），
        这里只输出结构 —— 远控（P2P）下这段 HTML 每次重绘都要经 WebSocket 分片
        传输，内联 style 曾占整块 68% 的字节。

        Args:
            income_data: `_load_commission_income_data` 的返回值，用到
                period（页面口径）、gem_stats（三档统计）、is_current_month、
                month_suffix。

        Returns:
            str: 整块 HTML。
        """
        stats = income_data.get("gem_stats") or {}
        if not isinstance(stats, dict):
            stats = {}
        # 页面口径 day/week/month；历史月份已被 _effective_commission_period 收敛为 month
        period = income_data.get("period", "month")
        bucket_key = _GEM_PERIOD_BUCKETS.get(period, "month")
        bucket = stats.get(bucket_key) if isinstance(stats.get(bucket_key), dict) else {}

        html = (
            '<div class="commission-gem-section" style="width: 100% !important; '
            'display: block !important; box-sizing: border-box;">'
        )
        # 与上方收益表格之间的分隔线
        html += '<div class="gem-divider"></div>'
        html += self._build_gem_header_html(income_data.get("month_suffix", ""), bucket_key)
        html += self._build_gem_table_html(bucket)

        # 该口径下没有任何结算记录时给一句提示，避免用户对着满屏「—」猜
        if not _gem_has_data(bucket):
            html += f'<p class="gem-nodata">{t("Gui.Stat.CommissionGemNoData")}</p>'
        return html + "</div>"

    @staticmethod
    def _build_gem_header_html(month_suffix: str, bucket_key: str) -> str:
        """区块标题 + 当前口径 + 一行口径说明。"""
        icon_path = _gem_icon_data_uri()
        icon_html = '<img class="gem-icon" src="' + icon_path + '">' if icon_path else ""
        period_label = t(_GEM_PERIOD_LABEL_KEYS.get(bucket_key, "Gui.Stat.CommissionIncomeMonth"))
        return (
            '<div class="gem-title-row">'
            + icon_html
            + '<span class="gem-title">' + t("Gui.Stat.CommissionGemTitle") + "</span>"
            # 当前口径紧跟标题：用户点上方按钮后，一眼能确认这块显示的是哪一档
            + '<span class="gem-period-badge">' + period_label + "</span>"
            + '<span class="gem-suffix">' + month_suffix + "</span>"
            + "</div>"
            + '<div class="gem-hint">' + t("Gui.Stat.CommissionGemHint") + "</div>"
        )

    def _build_gem_table_html(self, bucket: dict) -> str:
        """五列表格：委托时长 / 成功率 / 成功次数 / 委托次数 / 获得钻石。"""
        html = (
            '<div class="gem-scroll">'
            '<table class="commission-income-table commission-gem-table">'
            '<colgroup><col class="gem-col-duration"><col class="gem-col-rate">'
            '<col class="gem-col-num"><col class="gem-col-num">'
            '<col class="gem-col-num"></colgroup>'
            "<thead><tr>"
        )
        for align, label_key in (
            ("left", "Gui.Stat.CommissionGemHeaderDuration"),
            ("right", "Gui.Stat.CommissionGemHeaderRate"),
            ("right", "Gui.Stat.CommissionGemHeaderSuccessCount"),
            ("right", "Gui.Stat.CommissionGemHeaderTotalCount"),
            ("right", "Gui.Stat.CommissionGemHeaderReward"),
        ):
            html += f'<th class="gem-th-{align}">{t(label_key)}</th>'
        html += "</tr></thead><tbody>"
        for hour in _GEM_DURATIONS:
            item = _gem_bucket_of(bucket, hour)
            html += '<tr class="gem-row">'
            html += '<td class="gem-duration-cell">' + _build_gem_duration_cell(hour) + "</td>"
            html += self._build_gem_value_cells(item)
            html += "</tr>"
        # 合计：三档时长相加
        total = _sum_gem_bucket(bucket)
        html += '<tr class="gem-row gem-row-total">'
        html += '<td class="gem-duration-cell gem-total-label">' + t("Gui.Stat.CommissionGemRowTotal") + "</td>"
        html += self._build_gem_value_cells(total)
        html += "</tr>"
        return html + "</tbody></table></div>"

    @staticmethod
    def _build_gem_value_cells(bucket) -> str:
        """四格数据：成功率 / 成功次数 / 委托次数 / 获得钻石。"""
        html = '<td class="gem-rate-cell">' + _build_gem_rate_html(bucket) + "</td>"
        for field in ("success", "count", "reward"):
            html += '<td class="gem-num-cell">' + _build_gem_number_html(bucket, field) + "</td>"
        return html



# ---------------------------------------------------------------------------
# 纯函数：渲染用的取值/合计/判空。放在模块级而不是 mixin 上，单测可以直接调。
# ---------------------------------------------------------------------------


def _build_gem_duration_cell(hour: int) -> str:
    """时长单元格：钻石点 + 「N 小时」+ 该档奖励区间。"""
    low, high = _GEM_DURATION_EXPECT.get(hour, (0, 0))
    return (
        '<div class="gem-duration-inner">'
        '<span class="gem-dot"></span>'
        '<div class="gem-duration-text">'
        '<span class="gem-duration-name">'
        + t("Gui.Stat.CommissionGemDurationHour", hour=hour)
        + "</span>"
        '<span class="gem-duration-expect">'
        + t("Gui.Stat.CommissionGemExpect", low=low, high=high)
        + "</span></div></div>"
    )


def _gem_bucket_of(buckets, hour: int) -> dict:
    """从「{2: 桶, 4: 桶, 8: 桶}」里取出某档时长的桶。

    库里结构被污染时退回空桶，不让整块渲染炸掉。

    Args:
        buckets: 某个口径下的时长桶字典，如 {2: {...}, 4: {...}, 8: {...}}
        hour: 委托时长（2 / 4 / 8）

    Returns:
        dict: 该时长的统计桶；取不到时返回空字典。
    """
    if not isinstance(buckets, dict):
        return {}
    item = buckets.get(hour)
    return item if isinstance(item, dict) else {}


def _gem_number(bucket, field: str) -> int:
    """从桶里取一个整数指标；脏数据按 0 处理。"""
    if not isinstance(bucket, dict):
        return 0
    try:
        return int(bucket.get(field, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _sum_gem_bucket(buckets) -> dict:
    """把三档时长合成一个桶，供合计行使用。"""
    total = {"count": 0, "success": 0, "reward": 0, "rate": 0.0}
    for hour in _GEM_DURATIONS:
        item = _gem_bucket_of(buckets, hour)
        for field in ("count", "success", "reward"):
            total[field] += _gem_number(item, field)
    if total["count"]:
        total["rate"] = round(total["success"] * 100 / total["count"], 1)
    return total


def _gem_has_data(buckets) -> bool:
    """该口径下是否有任何结算记录（决定是否给「暂无记录」提示）。"""
    for hour in _GEM_DURATIONS:
        if _gem_number(_gem_bucket_of(buckets, hour), "count") > 0:
            return True
    return False


def _build_gem_rate_html(bucket) -> str:
    """成功率格子：百分比 + 细进度条；无记录时给占位符。"""
    count = _gem_number(bucket, "count")
    if not count:
        # 0 次结算时成功率没有意义，别显示误导性的 0.0%
        return _GEM_PLACEHOLDER
    try:
        rate = float(bucket.get("rate", 0.0) or 0.0)
    except (AttributeError, TypeError, ValueError):
        return _GEM_PLACEHOLDER
    width = max(0.0, min(rate, 100.0))
    # 0% 压灰（gem-rate-dim）：否则「0.0%」和真有数据的格子一样抢眼
    dim = " gem-rate-dim" if rate <= 0 else ""
    return (
        f'<div class="gem-rate{dim}">{rate:.1f}%</div>'
        '<div class="gem-bar">'
        f'<div class="gem-bar-fill" style="width: {width:.1f}%;"></div></div>'
    )


def _build_gem_number_html(bucket, field: str) -> str:
    """成功次数 / 委托次数 / 获得钻石：单个裸数字，语义由表头承担。"""
    return str(_gem_number(bucket, field))

