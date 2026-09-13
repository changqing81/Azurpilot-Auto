"""WebUI 资源增减统计视图：净变化折线图、增减汇总表与消耗排行榜。

数据来自 module/statistics/resource_delta_stats.py 的资源增减事件库
（./config/resource_delta.db），事件由 LogRes 钩子在资源值变化时写入，
委托任务（Commission）不参与统计。
"""

from module.webui.app_dependencies import (
    datetime,
    json,
    put_buttons,
    put_html,
    put_text,
    run_js,
    t,
    timedelta,
    use_scope,
)

from module.webui.app_helpers import (
    build_muted_notice,
    read_webapp_template,
)


from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked


# 资源展示顺序与配色（与全资源趋势图保持一致）
RESOURCE_KEYS = [
    "Oil", "Coin", "Gem", "Pt", "Cube", "Core", "Medal",
    "Merit", "GuildCoin", "ActionPoint", "YellowCoin", "PurpleCoin",
]
RESOURCE_COLORS = {
    "Oil": "#ff8a65",
    "Coin": "#ffd54f",
    "Gem": "#ef5350",
    "Pt": "#4fc3f7",
    "Cube": "#4dd0e1",
    "Core": "#b0bec5",
    "Medal": "#ffd740",
    "Merit": "#ffab00",
    "GuildCoin": "#a1887f",
    "ActionPoint": "#64b5f6",
    "YellowCoin": "#ffa726",
    "PurpleCoin": "#ce93d8",
}

# 粒度 -> (分桶方式, 追溯天数)；total 用月度分桶 + 折线累计
PERIOD_BUCKETS = {
    "day": ("day", 30),
    "week": ("week", 84),
    "month": ("month", 366),
    "total": ("month", 3650),
}
PERIOD_KEYS = ["day", "week", "month", "total"]

# 任务名翻译缓存（缺翻译键时 t() 会向 stdout 打印提示，缓存避免重复刷屏）
_MENU_LABEL_CACHE = {}


def _iter_bucket_labels(start, end, bucket):
    """枚举 [start, end] 内的全部桶标签（升序），用于补齐无事件的空桶"""
    if bucket == "month":
        year, month = start.year, start.month
        labels = []
        while (year, month) <= (end.year, end.month):
            labels.append(f"{year:04d}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return labels
    if bucket == "week":
        cur = (start - timedelta(days=start.weekday())).date()
        end_monday = (end - timedelta(days=end.weekday())).date()
        labels = []
        while cur <= end_monday:
            labels.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=7)
        return labels
    cur = start.date()
    labels = []
    while cur <= end.date():
        labels.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return labels


def _bucket_display(label, bucket):
    """桶标签的图表显示形式"""
    if bucket == "day":
        return label[5:]
    if bucket == "week":
        return label[5:]
    return label


class ResourceDeltaStatisticsMixin(WebUIMixinBase):
    """WebUI 资源增减统计视图。"""

    @render_locked
    def _render_resource_delta(self):
        self.cleanup_client_resources("__resourceDeltaChartCleanups")
        try:
            timeline, summary, ranking = self._load_delta_data()
        except Exception as e:
            with use_scope("resource_delta", clear=True):
                put_text(t("Gui.Stat.LoadDeltaDataFailed", e=e))
            return

        with use_scope("resource_delta", clear=True):
            self._render_delta_toolbar()
            if not timeline and not summary and not ranking:
                put_html(build_muted_notice(t("Gui.Stat.NoDeltaData")))
                return
            self._render_delta_chart(timeline)
            self._render_delta_tables(summary, ranking)

    # ---- 数据加载 ----

    def _get_delta_instance(self):
        instance_name = getattr(self, "alas_name", None)
        if not instance_name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance_name = all_instances[0] if all_instances else None
        return instance_name

    def _get_delta_period(self):
        return getattr(self, "_delta_period", "day")

    def _load_delta_data(self):
        """按当前粒度加载增减趋势、汇总与消耗排行数据。"""
        from module.statistics.resource_delta_stats import (
            get_consumption_ranking,
            get_delta_summary,
            get_delta_timeline,
        )

        instance_name = self._get_delta_instance()
        bucket, days = PERIOD_BUCKETS[self._get_delta_period()]
        start = (datetime.now() - timedelta(days=days)).isoformat()
        timeline = get_delta_timeline(instance_name, bucket=bucket, days=days)
        summary = get_delta_summary(instance_name, start_ts=start)
        ranking = get_consumption_ranking(instance_name, start_ts=start)
        return timeline, summary, ranking

    # ---- 粒度切换 ----

    def _render_delta_toolbar(self):
        current = self._get_delta_period()
        labels = {
            "day": t("Gui.Stat.DeltaPeriodDay"),
            "week": t("Gui.Stat.DeltaPeriodWeek"),
            "month": t("Gui.Stat.DeltaPeriodMonth"),
            "total": t("Gui.Stat.DeltaPeriodTotal"),
        }
        put_buttons(
            [
                {
                    "label": labels[key],
                    "value": key,
                    "color": "primary" if key == current else "off",
                }
                for key in PERIOD_KEYS
            ],
            onclick=self._on_delta_period_change,
        ).style("margin-bottom:8px;")

    def _on_delta_period_change(self, value):
        self._delta_period = value
        self._render_resource_delta()

    # ---- 折线图 ----

    def _render_delta_chart(self, timeline):
        labels, series_map = self._build_delta_series(timeline)
        if not labels:
            put_html(build_muted_notice(t("Gui.Stat.NoDeltaData")))
            return

        chart_id = f"rdc_{id(self)}"
        stats_html, legend_html = self._build_delta_chart_headers(series_map)
        html = read_webapp_template("resource_delta_chart.html").format(
            chart_id=chart_id,
            title=t("Gui.Stat.DeltaChartTitle"),
            stats_html=stats_html,
            legend_html=legend_html,
        )

        series_data = [
            {
                "key": key,
                "name": self._resource_label(key),
                "color": RESOURCE_COLORS.get(key, "#90a4ae"),
                "data": meta["data"],
            }
            for key, meta in series_map.items()
        ]
        js_code = (
            read_webapp_template("resource_delta_chart.js")
            .replace("__LABELS__", json.dumps(labels, ensure_ascii=False))
            .replace("__SERIES_DATA__", json.dumps(series_data, ensure_ascii=False))
            .replace("__CHART_ID__", chart_id)
            .replace("__CHART_TITLE__", t("Gui.Stat.DeltaChartTitle"))
        )
        put_html(html)
        run_js(js_code)

    def _build_delta_series(self, timeline):
        """把分桶净变化整理成折线序列，补齐无事件的空桶（记 0）。

        Returns:
            (labels, {resource: {"data": [..]}})，labels 为空表示无数据
        """
        if not timeline:
            return [], {}

        period = self._get_delta_period()
        bucket, days = PERIOD_BUCKETS[period]
        accumulate = period == "total"

        buckets = {item["bucket"]: item for item in timeline}
        now = datetime.now()
        # 时间窗从追溯起点开始，而不是最早事件，保证各粒度窗口长度稳定
        window_labels = _iter_bucket_labels(now - timedelta(days=days), now, bucket)
        first_existing = min(buckets)
        labels = [
            label for label in window_labels
            if label >= first_existing
        ] or [first_existing]
        display_labels = [_bucket_display(label, bucket) for label in labels]

        # 只保留出现过的资源，避免整条零值线
        resources = set()
        for item in buckets.values():
            for key in item:
                if key != "bucket":
                    resources.add(key)

        series_map = {}
        for key in sorted(resources, key=self._resource_sort_key):
            data = []
            for label in labels:
                value = buckets.get(label, {}).get(key, 0)
                data.append(int(value or 0))
            if accumulate:
                running = 0
                cumulative = []
                for value in data:
                    running += value
                    cumulative.append(running)
                data = cumulative
            series_map[key] = {"data": data}
        return display_labels, series_map

    @staticmethod
    def _resource_sort_key(key):
        # 无配色的新资源排在已定义顺序之后
        try:
            return RESOURCE_KEYS.index(key)
        except ValueError:
            return len(RESOURCE_KEYS)

    def _build_delta_chart_headers(self, series_map):
        """构造图表上方的区间合计统计与图例开关"""
        stats_html = ""
        legend_html = ""
        for idx, (key, meta) in enumerate(series_map.items()):
            data = meta["data"]
            name = self._resource_label(key)
            color = RESOURCE_COLORS.get(key, "#90a4ae")
            net = sum(v for v in data if v is not None)
            net_color = "#ef5350" if net >= 0 else "#26a69a"
            net_sign = "+" if net >= 0 else ""
            stats_html += (
                f'<span style="white-space:nowrap;">{name}: '
                f'<b style="color:{color}">{net_sign}{net:,}</b>'
                f'</span>'
            )
            legend_html += (
                f'<span class="rc-legend-item" data-series="{idx}" '
                f'style="display:flex;align-items:center;gap:4px;cursor:pointer;opacity:1;">'
                f'<span style="width:12px;height:3px;background:{color};border-radius:1px;"></span>'
                f"{name}</span>"
            )
        return stats_html, legend_html

    # ---- 汇总表与排行榜 ----

    def _render_delta_tables(self, summary, ranking):
        html = read_webapp_template("resource_delta_rank.html").format(
            summary_title=t("Gui.Stat.DeltaSummaryTitle"),
            summary_html=self._build_summary_html(summary),
            ranking_title=t("Gui.Stat.ConsumptionRankingTitle"),
            ranking_html=self._build_ranking_html(ranking),
        )
        put_html(html)

    def _build_summary_html(self, summary):
        """左侧增减汇总表：资源 | 增加 | 消耗 | 净变化"""
        rows = {r["resource"]: r for r in summary}
        parts = ['<table style="width:100%;border-collapse:collapse;font-size:12px;">']
        header_style = 'style="font-weight:400;color:#888;padding:4px 6px;"'
        parts.append(
            '<tr>'
            f'<th {header_style} style="text-align:left;">{t("Gui.Stat.DeltaResource")}</th>'
            f'<th {header_style} style="text-align:right;">{t("Gui.Stat.DeltaIncrease")}</th>'
            f'<th {header_style} style="text-align:right;">{t("Gui.Stat.DeltaDecrease")}</th>'
            f'<th {header_style} style="text-align:right;">{t("Gui.Stat.DeltaNet")}</th>'
            '</tr>'
        )
        for key in RESOURCE_KEYS:
            row = rows.get(key)
            if not row or not row["events"]:
                continue
            color = RESOURCE_COLORS.get(key, "#90a4ae")
            increase = int(row["increase"] or 0)
            decrease = int(row["decrease"] or 0)
            net = int(row["net"] or 0)
            net_color = "#ef5350" if net >= 0 else "#26a69a"
            net_sign = "+" if net >= 0 else ""
            parts.append(
                '<tr style="border-top:1px solid #2a2a3e;">'
                f'<td style="padding:4px 6px;color:{color};">{self._resource_label(key)}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:#26a69a;">+{increase:,}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:#ef5350;">-{decrease:,}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:{net_color};">{net_sign}{net:,}</td>'
                '</tr>'
            )
        parts.append('</table>')
        return "".join(parts)

    def _build_ranking_html(self, ranking):
        """右侧消耗排行榜：按资源分节，节内按任务消耗降序，CSS 横条显示占比"""
        if not ranking:
            return f'<div style="color:#666;font-size:12px;">{t("Gui.Stat.NoDeltaData")}</div>'

        groups = {}
        for row in ranking:
            groups.setdefault(row["resource"], []).append(row)
        ordered = [
            key for key in RESOURCE_KEYS if key in groups
        ] + sorted(key for key in groups if key not in RESOURCE_KEYS)

        parts = []
        for res in ordered:
            rows = sorted(
                groups[res], key=lambda r: int(r["consumed"] or 0), reverse=True
            )
            total = sum(int(r["consumed"] or 0) for r in rows)
            color = RESOURCE_COLORS.get(res, "#90a4ae")
            parts.append(
                '<div style="margin-bottom:14px;">'
                f'<div style="font-size:13px;font-weight:600;color:{color};margin-bottom:6px;">'
                f'{self._resource_label(res)}'
                f'<span style="color:#888;font-weight:400;font-size:12px;margin-left:8px;">'
                f'{t("Gui.Stat.DeltaTotalConsumed", total=f"{total:,}")}'
                f'</span></div>'
            )
            for row in rows[:10]:
                consumed = int(row["consumed"] or 0)
                times = int(row["times"] or 0)
                pct = consumed / total * 100 if total else 0
                source = row["source"]
                parts.append(
                    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
                    f'<div style="width:150px;font-size:12px;color:#bbb;text-align:right;'
                    f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
                    f'title="{source}">{self._source_label(source)}</div>'
                    '<div style="flex:1;background:#252540;border-radius:6px;height:14px;min-width:60px;">'
                    f'<div style="width:{pct:.1f}%;height:100%;background:{color};'
                    f'border-radius:6px;opacity:0.85;"></div>'
                    '</div>'
                    f'<div style="width:130px;font-size:12px;color:#ddd;">{consumed:,}'
                    f'<span style="color:#666;"> ×{times}</span></div>'
                    '</div>'
                )
            parts.append('</div>')
        return "".join(parts)

    # ---- 文案 ----

    @staticmethod
    def _resource_label(key):
        label_key = f"Gui.Dashboard.{key}"
        text = t(label_key)
        return key if text == label_key else text

    @staticmethod
    def _source_label(source):
        """任务命令名 -> 菜单翻译；无翻译时回退原命令名"""
        if source not in _MENU_LABEL_CACHE:
            label_key = f"Gui.Menu.{source}"
            text = t(label_key)
            _MENU_LABEL_CACHE[source] = source if text == label_key else text
        return _MENU_LABEL_CACHE[source]
