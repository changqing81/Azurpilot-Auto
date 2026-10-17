"""WebUI 科研掉落统计视图。

数据来自 module.statistics.research_stats.collect()：科研领奖时解析入库的
掉落记录（彩装备、图纸、心智、物资），按「期数」与「心智/物资」两个口径
汇总展示，周期与期数由按钮回调切换后重绘本面板。
"""

from datetime import datetime, timedelta
from html import escape

from module.webui.app_dependencies import (
    logger,
    put_buttons,
    put_html,
    t,
    use_scope,
)
from module.webui.app_helpers import (
    build_metric_grid,
    build_muted_notice,
    build_simple_table,
    build_title_block,
)
from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked

# 视图口径，与 module.statistics.research_stats 的 SCOPE_* 对应
_SCOPE_SERIES = "series"
_SCOPE_CONSUMABLE = "consumable"

# 汇总周期按钮：(取值, i18n 键)。"all" 从 2020 年起 —— 科研掉落不会早于此。
_PERIOD_OPTIONS = (
    ("month", "Gui.Stat.ResearchPeriodMonth"),
    ("days30", "Gui.Stat.ResearchPeriodDays30"),
    ("days90", "Gui.Stat.ResearchPeriodDays90"),
    ("all", "Gui.Stat.ResearchPeriodAll"),
)
_DEFAULT_PERIOD = "days90"
_PERIOD_ALL_START = datetime(2020, 1, 1)

# 稀有度徽标配色（彩 > 金 > 紫 > 蓝，与游戏内一致）
_RARITY_COLORS = {6: "#e8732a", 5: "#e8732a", 4: "#d9a01e", 3: "#9a6bd9", 2: "#5a9bd9"}
_RARITY_FALLBACK_COLOR = "#8a919e"

# 科研物品模板图（assets/stats/research_items 经 /static/assets 挂载）。
# 必须用**相对路径**：手机远控（P2P）把页面挂在 /p2p/<peer>/ 子路径下，
# 绝对路径 /static/... 会指向信号服务器根而 404（同仓库其他 static 引用惯例）。
_ITEM_ICON_PREFIX = "static/assets/stats/research_items"


def _research_period_window(period: str, now: datetime = None):
    """汇总周期 -> ``(start, end)`` 取数窗口。

    Args:
        period: 'month' | 'days30' | 'days90' | 'all'。
        now: 参考时间，缺省取当前时间。

    Returns:
        (datetime, datetime): 窗口 [start, end)。
    """
    if now is None:
        now = datetime.now()
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now
    if period == "days30":
        return now - timedelta(days=30), now
    if period == "days90":
        return now - timedelta(days=90), now
    return _PERIOD_ALL_START, now


def _series_options(data=None):
    """期数按钮的取值范围。

    与上游前端「固定列出 1~9 期」一致：期数取自名称表（每件物品带 series
    字段），这样新期刚开、库里还没有该期记录时也能先看它的收集清单；
    名称表缺失时才退回「有记录的期数」。

    Args:
        data: collect() 的返回；仅作名称表缺失时的兜底来源。

    Returns:
        list[int]: 期数列表（降序）。
    """
    from module.statistics.research_stats import load_name_table

    table = load_name_table()
    series = {info.get("series") for info in table.values() if info.get("series")}
    if series:
        return sorted(series, reverse=True)
    return sorted(
        {int(item) for item in (data or {}).get("available") or [] if item},
        reverse=True,
    )


class ResearchStatisticsMixin(WebUIMixinBase):
    """WebUI 科研掉落统计视图。"""

    @render_locked
    def _render_research_stats(self):
        """渲染科研面板；口径 / 周期 / 期数切换由按钮回调触发整体重绘。"""
        try:
            data = self._load_research_stats_data()
        except Exception as e:
            logger.warning(f"[WebUI-统计] 科研掉落读取失败: {e}")
            with use_scope("research_stats", clear=True):
                put_html(build_muted_notice(escape(t("Gui.Stat.ResearchEmptyTitle"))))
            return

        with use_scope("research_stats", clear=True):
            put_html(build_title_block(escape(t("Gui.Stat.ResearchTitle"))))
            if data is None:
                put_html(build_muted_notice(escape(t("Gui.Stat.ResearchEmptyTitle"))))
                return
            put_html(
                build_metric_grid(
                    [
                        t("Gui.Stat.ResearchMetricTotal"),
                        t("Gui.Stat.ResearchMetricToday"),
                        t("Gui.Stat.ResearchMetricMonth"),
                        t("Gui.Stat.ResearchMetricRecords"),
                    ],
                    [
                        f"{int(data.get('total', 0)):,}",
                        f"{int(data.get('today', 0)):,}",
                        f"{int(data.get('month', 0)):,}",
                        f"{int(data.get('records', 0)):,}",
                    ],
                )
            )
            # 控件先于空态渲染：某个周期没有记录时，用户仍要能切周期 / 切期数，
            # 否则会被空态锁死在当前选择上。
            self._output_research_controls(data)
            if not data.get("items"):
                put_html(
                    '<div class="st-empty">'
                    f"<b>{escape(t('Gui.Stat.ResearchEmptyTitle'))}</b><br>"
                    f"{escape(t('Gui.Stat.ResearchEmptyDesc'))}</div>"
                )
                return
            put_html(self._build_research_table_html(data))
            put_html(build_muted_notice(escape(t("Gui.Stat.ResearchNote"))))

    def _load_research_stats_data(self):
        """读取科研掉落的汇总数据；无实例名时返回 None。"""
        from module.statistics import research_stats

        instance_name = getattr(self, "alas_name", None)
        if not instance_name:
            return None

        scope = getattr(self, "_research_stats_scope", _SCOPE_SERIES)
        period = getattr(self, "_research_stats_period", _DEFAULT_PERIOD)
        series = int(getattr(self, "_research_stats_series", 0) or 0)
        start, end = _research_period_window(period)
        data = research_stats.collect(
            instance_name, series=series, scope=scope, start=start, end=end
        )
        # 期数 0 表示「自动取最新有记录的一期」，collect 会解析出实际期数，
        # 记下来供按钮高亮与下次加载沿用。
        if scope == _SCOPE_SERIES and data.get("series"):
            self._research_stats_series = int(data["series"])
        return data

    def _output_research_controls(self, data):
        """渲染口径 / 汇总周期 / 期数三行按钮（期数行仅期数口径出现）。"""
        scope = data.get("scope", _SCOPE_SERIES)
        period = getattr(self, "_research_stats_period", _DEFAULT_PERIOD)

        def on_scope_click(value):
            self._research_stats_scope = value
            self._render_research_stats()

        put_buttons(
            [
                {
                    "label": t("Gui.Stat.ResearchScopeSeries"),
                    "value": _SCOPE_SERIES,
                    "color": "primary" if scope == _SCOPE_SERIES else "secondary",
                },
                {
                    "label": t("Gui.Stat.ResearchScopeConsumable"),
                    "value": _SCOPE_CONSUMABLE,
                    "color": "primary" if scope == _SCOPE_CONSUMABLE else "secondary",
                },
            ],
            onclick=on_scope_click,
            small=True,
            scope="research_stats",
        ).style("margin-bottom:.35rem;")

        def on_period_click(value):
            self._research_stats_period = value
            self._render_research_stats()

        put_buttons(
            [
                {
                    "label": t(key),
                    "value": value,
                    "color": "primary" if value == period else "secondary",
                }
                for value, key in _PERIOD_OPTIONS
            ],
            onclick=on_period_click,
            small=True,
            scope="research_stats",
        ).style("margin-bottom:.35rem;")

        if scope != _SCOPE_SERIES:
            return
        series_options = _series_options(data)
        if not series_options:
            return

        def on_series_click(value):
            self._research_stats_series = int(value)
            self._render_research_stats()

        put_buttons(
            [
                {
                    "label": t("Gui.Stat.ResearchSeriesOption", series=item),
                    "value": item,
                    "color": "primary" if item == data.get("series") else "secondary",
                }
                for item in series_options
            ],
            onclick=on_series_click,
            small=True,
            scope="research_stats",
        ).style("margin-bottom:.6rem;")

    @staticmethod
    def _research_number(value, dim=False):
        """数值单元格：0 值淡化，其余千分位。"""
        if isinstance(value, float):
            text = f"{value:g}"
        else:
            text = f"{int(value):,}"
        if dim:
            return f'<span style="opacity:.5">{text}</span>'
        return text

    def _build_research_table_html(self, data):
        """明细表：物品（图标 + 名称 + 稀有度）/ 数量 / 次数 / 平均 / 今日 / 本月。

        期数口径下未掉过的固定清单项同样列出（数量为 0，淡显），
        与游戏内「本期还差哪几张图纸」的关切一致。
        """
        from module.statistics.research_stats import RARITY_LABELS

        headers = [
            t("Gui.Stat.ResearchHeaderItem"),
            t("Gui.Stat.ResearchHeaderAmount"),
            t("Gui.Stat.ResearchHeaderCount"),
            t("Gui.Stat.ResearchHeaderAvg"),
            t("Gui.Stat.ResearchMetricToday"),
            t("Gui.Stat.ResearchMetricMonth"),
        ]
        rows = []
        for item in data.get("items") or []:
            name = str(item.get("name", ""))
            rarity = item.get("rarity")
            color = _RARITY_COLORS.get(rarity, _RARITY_FALLBACK_COLOR)
            rarity_label = RARITY_LABELS.get(rarity, "")
            badge = (
                f'<span class="research-rarity" style="color:{color}">'
                f"{escape(rarity_label)}</span>"
                if rarity_label
                else ""
            )
            item_cell = (
                '<span class="research-item">'
                f'<img class="research-item-icon" alt="" '
                f'src="{_ITEM_ICON_PREFIX}/{escape(name, quote=True)}.png">'
                f'<span class="research-item-name">'
                f'{escape(str(item.get("zh") or name))}</span>'
                f"{badge}</span>"
            )
            amount = int(item.get("amount", 0))
            count = int(item.get("count", 0))
            avg = item.get("avg", 0) or 0
            today = int(item.get("today", 0))
            month = int(item.get("month", 0))
            rows.append(
                [
                    item_cell,
                    self._research_number(amount, dim=amount <= 0),
                    self._research_number(count, dim=count <= 0),
                    self._research_number(float(avg), dim=not avg),
                    self._research_number(today, dim=today <= 0),
                    self._research_number(month, dim=month <= 0),
                ]
            )
        return build_simple_table(headers, rows, numeric_from=1)
