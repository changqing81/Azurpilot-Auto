"""WebUI 资源增减统计视图：任务资源时间轴、增减汇总表与消耗排行榜。

数据来自 module/statistics/resource_delta_stats.py 的资源增减事件库
（./config/resource_delta.db），事件由 LogRes 钩子在资源值变化时写入，
委托任务（Commission）不参与统计。

时间轴按用户手绘稿设计：任务沿水平主线按时间排列，节点上方标注增加（涨了）、
下方标注消耗（消耗了）；消耗排行榜采用发光横条样式，资源按总消耗降序分节。
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

# 粒度 -> 追溯天数：日=最近1天、周=最近7天、月=最近30天、总=全部
PERIOD_DAYS = {
    "day": 1,
    "week": 7,
    "month": 30,
    "total": 3650,
}
PERIOD_KEYS = ["day", "week", "month", "total"]

# 时间轴节点上方/下方最多直接标注的条目数，其余进悬浮详情
_TIMELINE_TOP_N = 3

# 任务名翻译缓存（缺翻译键时 t() 会向 stdout 打印提示，缓存避免重复刷屏）
_MENU_LABEL_CACHE = {}


def _hex_to_rgba(color, alpha):
    """'#rrggbb' -> 'rgba(r,g,b,a)'；非法输入回退灰蓝色，保证横条辉光不失效"""
    try:
        value = str(color).lstrip("#")
        if len(value) != 6:
            raise ValueError(color)
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"
    except (ValueError, AttributeError):
        return f"rgba(144,164,174,{alpha})"


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
            self._render_delta_timeline(timeline, summary)
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
        """按当前粒度的时间窗加载任务时间轴、增减汇总与消耗排行数据。"""
        from module.statistics.resource_delta_stats import (
            get_consumption_ranking,
            get_delta_summary,
            get_task_delta_timeline,
        )

        instance_name = self._get_delta_instance()
        days = PERIOD_DAYS[self._get_delta_period()]
        start = (datetime.now() - timedelta(days=days)).isoformat()
        timeline = get_task_delta_timeline(instance_name, start_ts=start)
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

    # ---- 任务资源时间轴 ----

    def _render_delta_timeline(self, timeline, summary):
        """任务沿主线按时间排列：节点上方标注增加、下方标注消耗。"""
        if not timeline:
            put_html(build_muted_notice(t("Gui.Stat.NoDeltaData")))
            return

        chart_id = f"rdt_{id(self)}"
        html = read_webapp_template("resource_delta_timeline.html").format(
            chart_id=chart_id,
            title=t("Gui.Stat.DeltaChartTitle"),
            stats_html=self._build_delta_stats_html(summary),
            legend_html=self._build_delta_legend_html(),
        )

        tasks = [self._build_timeline_task(item) for item in timeline]
        js_code = (
            read_webapp_template("resource_delta_timeline.js")
            .replace("__TASKS__", json.dumps(tasks, ensure_ascii=False))
            .replace("__CHART_ID__", chart_id)
            .replace("__TXT_GAIN__", t("Gui.Stat.DeltaIncrease"))
            .replace("__TXT_LOSS__", t("Gui.Stat.DeltaDecrease"))
            .replace("__TXT_TIMES__", t("Gui.Stat.DeltaTaskTimes"))
            .replace("__TXT_MORE__", t("Gui.Stat.DeltaMore"))
        )
        put_html(html)
        run_js(js_code)

    def _build_timeline_task(self, item):
        """把按任务聚合的增减数据整理成前端节点结构。

        gains/losses 按数额降序，超出 _TIMELINE_TOP_N 的部分由前端折叠进悬浮详情。
        """
        gains, losses = [], []
        for key, meta in item["resources"].items():
            color = RESOURCE_COLORS.get(key, "#90a4ae")
            increased = int(meta.get("increased") or 0)
            consumed = int(meta.get("consumed") or 0)
            if increased > 0:
                gains.append(
                    {"name": self._resource_label(key), "color": color, "value": increased}
                )
            if consumed > 0:
                losses.append(
                    {"name": self._resource_label(key), "color": color, "value": consumed}
                )
        gains.sort(key=lambda e: e["value"], reverse=True)
        losses.sort(key=lambda e: e["value"], reverse=True)
        return {
            "name": self._source_label(item["source"]),
            "fullname": item["source"],
            "time": self._format_ts(item.get("last_ts")),
            "events": int(item.get("events") or 0),
            "gains": gains,
            "losses": losses,
        }

    @staticmethod
    def _format_ts(iso_ts):
        """ISO 时间 -> 'MM-DD HH:MM' 展示"""
        text = str(iso_ts or "").replace("T", " ")
        return text[5:16] if len(text) >= 16 else text

    def _build_delta_stats_html(self, summary):
        """时间轴上方的时间窗净变化汇总：资源: ±净变化"""
        rows = {r["resource"]: r for r in summary}
        parts = []
        for key in RESOURCE_KEYS:
            row = rows.get(key)
            if not row or not row["events"]:
                continue
            net = int(row["net"] or 0)
            color = RESOURCE_COLORS.get(key, "#90a4ae")
            sign = "+" if net >= 0 else ""
            parts.append(
                f'<span style="white-space:nowrap;">{self._resource_label(key)}: '
                f'<b style="color:{color}">{sign}{net:,}</b></span>'
            )
        return "".join(parts)

    @staticmethod
    def _build_delta_legend_html():
        """时间轴语义说明：上方为增加、下方为消耗"""
        gain = t("Gui.Stat.DeltaIncrease")
        loss = t("Gui.Stat.DeltaDecrease")
        hint = t("Gui.Stat.DeltaTimelineHint")
        return (
            '<span style="display:flex;align-items:center;gap:4px;">'
            f'<span style="color:#26a69a;">▲</span>{gain}</span>'
            '<span style="display:flex;align-items:center;gap:4px;">'
            f'<span style="color:#ef5350;">▼</span>{loss}</span>'
            f'<span style="opacity:0.7;">{hint}</span>'
        )

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
        """右侧消耗排行榜：资源按总消耗降序分节，节内按任务消耗降序，发光横条显示占比"""
        if not ranking:
            return f'<div style="color:#666;font-size:12px;">{t("Gui.Stat.NoDeltaData")}</div>'

        groups = {}
        for row in ranking:
            groups.setdefault(row["resource"], []).append(row)
        # 资源按总消耗降序排列，体现"榜"的语义
        totals = {
            res: sum(int(r["consumed"] or 0) for r in rows)
            for res, rows in groups.items()
        }
        ordered = sorted(groups, key=lambda res: totals[res], reverse=True)

        track_bg = "rgba(255,255,255,0.06)"
        parts = []
        for res in ordered:
            rows = sorted(
                groups[res], key=lambda r: int(r["consumed"] or 0), reverse=True
            )
            total = totals[res]
            color = RESOURCE_COLORS.get(res, "#90a4ae")
            # 横条样式：左浅右实的渐变 + 资源色辉光，与参考样式对齐
            fill = f"linear-gradient(90deg, {_hex_to_rgba(color, 0.45)}, {color})"
            glow = f"0 0 10px {_hex_to_rgba(color, 0.35)}"
            max_value = max(int(r["consumed"] or 0) for r in rows) or 1
            parts.append(
                '<div style="margin-bottom:18px;">'
                f'<div style="font-size:14px;font-weight:600;color:{color};margin-bottom:8px;">'
                f'{self._resource_label(res)}'
                f'<span style="color:#888;font-weight:400;font-size:12px;margin-left:8px;">'
                f'{t("Gui.Stat.DeltaTotalConsumed", total=f"{total:,}")}'
                f'</span></div>'
            )
            for row in rows[:10]:
                consumed = int(row["consumed"] or 0)
                times = int(row["times"] or 0)
                # 节内归一化到最大值，首条横条占满整行
                pct = consumed / max_value * 100
                source = row["source"]
                parts.append(
                    '<div style="display:flex;align-items:center;gap:10px;margin-bottom:7px;">'
                    f'<div style="width:150px;font-size:12px;color:#b8c2cc;text-align:right;'
                    f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
                    f'title="{source}">{self._source_label(source)}</div>'
                    f'<div style="flex:1;background:{track_bg};border-radius:8px;height:16px;'
                    f'min-width:60px;overflow:hidden;">'
                    f'<div style="width:{pct:.1f}%;height:100%;background:{fill};'
                    f'border-radius:8px;box-shadow:{glow};"></div>'
                    '</div>'
                    f'<div style="width:130px;font-size:12px;color:#fff;white-space:nowrap;">'
                    f'<b>{consumed:,}</b>'
                    f'<span style="color:#666;margin-left:6px;">×{times}</span></div>'
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
