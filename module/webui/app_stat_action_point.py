"""WebUI 体力趋势图的数据装配和图表渲染。"""

from module.webui.app_dependencies import (
    current_time,
    datetime,
    json,
    put_button,
    put_buttons,
    put_html,
    put_row,
    put_text,
    t,
    use_scope,
)

from module.webui.app_helpers import (
    build_muted_notice,
    read_webapp_template,
)


from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked


class ActionPointStatisticsMixin(WebUIMixinBase):
    """WebUI 体力趋势图的数据装配和图表渲染。"""

    # 折线/详情视图；其余为聚合视图
    AP_LINE_VIEWS = ("line", "detail")
    # 支持的视图顺序即按钮顺序
    AP_CHART_VIEWS = ("line", "day", "month")
    # 辅助序列的固定配色（图例色点与指标行共用）
    AP_SERIES_COLORS = {
        "ap": "#64b5f6",
        "yellow": "#ffd54f",
        "purple": "#ce93d8",
        "asset": "#22d3ee",
        "distance": "#1565c0",
    }

    @staticmethod
    def _format_ap_metric(value, decimals=0):
        """按千分位格式化指标数值；资产等小数量保留指定小数位。"""
        if decimals:
            return f"{value:,.{decimals}f}"
        return f"{int(value):,}"

    @staticmethod
    def _ap_res_soft_color(hex_color, alpha=0.16):
        """把资源色转成低透明度的胶囊底色，供浅色/深色主题叠加使用。"""
        raw = hex_color.lstrip("#")
        red, green, blue = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        return f"rgba({red},{green},{blue},{alpha})"

    @classmethod
    def _build_ap_legend_item(cls, series_id, color, label, dashed=False):
        """构造一个图例项（色条 + 文案，点击由前端 JS 负责开关）。"""
        dash = f" border-top:1px dashed {color};" if dashed else ""
        return (
            f'<span class="ap-legend-item" data-series="{series_id}" '
            'style="display:flex; align-items:center; gap:4px;cursor:pointer;opacity:1;">'
            f'<span style="width:12px; height:2px; background:{color}; '
            f'border-radius:1px;{dash}"></span>'
            f"{label}</span>"
        )

    @classmethod
    def _build_ap_resource_row(
        cls, *, name, color, value, change, max_value, min_value, decimals=0
    ):
        """构造一行资源指标胶囊（对齐 statistics-v2 的 .chip）。

        布局：资源名 + 当前值合成一个带色点和淡色底的胶囊，
        变化 / 最高 / 最低 各一个中性胶囊；整行 flex-wrap，窄屏自动折行。
        原先这里是行内 grid（固定 150/100/90/90/90 px，第 5 列恒为空），
        窄屏会横向溢出。
        """
        fmt = cls._format_ap_metric
        sign = "+" if change >= 0 else "-"
        change_cls = "is-up" if change >= 0 else "is-down"
        change_text = f"{sign}{fmt(abs(change), decimals)}"
        soft = cls._ap_res_soft_color(color)
        label_change = t("Gui.Stat.MetricChange")
        label_max = t("Gui.Stat.MetricMax")
        label_min = t("Gui.Stat.MetricMin")
        return (
            '<div class="ap-res-row">'
            f'<span class="ap-res-tag" style="--ap-res-soft:{soft}">'
            f'<span class="ap-res-dot" style="background:{color}"></span>'
            f'<span class="ap-res-name">{name}</span>'
            f'<b class="ap-res-value">{fmt(value, decimals)}</b>'
            "</span>"
            f'<span class="ap-res-chip"><span class="ap-res-label">{label_change}</span>'
            f'<b class="ap-res-num {change_cls}">{change_text}</b></span>'
            f'<span class="ap-res-chip"><span class="ap-res-label">{label_max}</span>'
            f'<b class="ap-res-num">{fmt(max_value, decimals)}</b></span>'
            f'<span class="ap-res-chip"><span class="ap-res-label">{label_min}</span>'
            f'<b class="ap-res-num">{fmt(min_value, decimals)}</b></span>'
            "</div>"
        )

    def _load_ap_chart_timelines(self):
        """读取当前实例的行动力、凭证和资产时间线。"""
        from module.statistics.opsi_month import (
            get_ap_timeline,
            get_asset_timeline,
            get_coins_timeline,
        )

        instance_name = getattr(self, "alas_name", None)
        if not instance_name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance_name = all_instances[0] if all_instances else None
        timeline = get_ap_timeline(instance_name=instance_name)
        coins_timeline = get_coins_timeline(instance_name=instance_name)
        asset_timeline = get_asset_timeline(instance_name=instance_name)
        return timeline, coins_timeline, asset_timeline

    def _load_ap_chart_dataset(self, reuse: bool = False):
        """读取并规范化图表所需的数据集。

        切换视图（折线 / 按日 / 按月）只改变聚合口径，原始快照完全一样，
        因此 ``reuse=True`` 时直接复用上一次的结果，跳过三个数据源的
        文件读取与逐点时间解析——这是「切视图要等」的主要来源。
        刷新 / 首次进入页面走 ``reuse=False``，保证拿到最新数据。

        Returns:
            tuple: (raw_points, timeline, coins_timeline, asset_timeline)
        """
        if reuse:
            cached = getattr(self, "_ap_chart_dataset", None)
            if cached is not None:
                return cached

        timeline, coins_timeline, asset_timeline = self._load_ap_chart_timelines()
        raw_points = self._normalize_ap_chart_points(timeline)
        dataset = (raw_points, timeline, coins_timeline, asset_timeline)
        self._ap_chart_dataset = dataset
        return dataset

    @render_locked
    def _render_ap_chart(self, reuse_dataset: bool = False):
        self.cleanup_client_resources("__apChartCleanups")
        try:
            raw_points, timeline, coins_timeline, asset_timeline = (
                self._load_ap_chart_dataset(reuse=reuse_dataset)
            )
        except Exception as e:
            self._ap_chart_dataset = None
            with use_scope("ap_chart", clear=True):
                put_text(t("Gui.Stat.LoadApDataFailed", e=e))
            return

        if not timeline:
            with use_scope("ap_chart", clear=True):
                put_html(build_muted_notice(t("Gui.Stat.NoApData")))
            return

        if not raw_points:
            with use_scope("ap_chart", clear=True):
                put_html(build_muted_notice(t("Gui.Stat.NoValidApData")))
            return

        chart_data = self._build_ap_chart_series(raw_points)
        if chart_data is None:
            with use_scope("ap_chart", clear=True):
                put_html(build_muted_notice(t("Gui.Stat.CannotAggregateKline")))
                put_button(
                    t("Gui.Stat.ViewLineShort"),
                    onclick=lambda: (
                        setattr(self, "_ap_chart_view", "line"),
                        self._render_ap_chart(),
                    ),
                    color="off",
                )
            return

        auxiliary_data = self._build_ap_chart_auxiliary_data(
            timeline=timeline,
            coins_timeline=coins_timeline,
            asset_timeline=asset_timeline,
            chart_points=chart_data["chart_points"],
            current_view=chart_data["current_view"],
        )
        self._render_ap_chart_content(chart_data, auxiliary_data)

    @staticmethod
    def _normalize_ap_chart_points(timeline):
        """解析行动力快照并按时间排序。"""
        raw_points = []
        for pt in timeline:
            ts_raw = pt.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts_raw)
            except Exception:
                continue
            raw_points.append(
                {
                    "dt": dt,
                    "ap": int(pt.get("ap_total", pt.get("ap", 0))),
                    "source": pt.get("source", "-"),
                }
            )

        raw_points.sort(key=lambda p: p["dt"])
        return raw_points

    def _build_ap_chart_series(self, raw_points):
        """按当前视图构造折线或 K 线主序列及其摘要。"""
        current_view = getattr(self, "_ap_chart_view", "line")
        if current_view not in self.AP_CHART_VIEWS + ("detail",):
            current_view = "line"

        labels = []
        opens = []
        highs = []
        lows = []
        closes = []
        counts = []
        ap_list = []
        ap_ts = []
        detail_sources = []
        chart_points = []
        is_detail_mode = False

        today = current_time().date()
        today_points = [p for p in raw_points if p["dt"].date() == today]
        if not today_points and raw_points:
            last_date = raw_points[-1]["dt"].date()
            today_points = [p for p in raw_points if p["dt"].date() == last_date]
            today = last_date

        if current_view == "detail":
            is_detail_mode = True
            if today_points:
                for p in today_points:
                    labels.append(p["dt"].strftime("%H:%M"))
                    ap_list.append(p["ap"])
                    ap_ts.append(int(p["dt"].timestamp() * 1000))
                    detail_sources.append(p.get("source", "-"))
                    chart_points.append(p)
                view_title = t("Gui.Stat.DetailChartTitle")
            else:
                for p in raw_points:
                    labels.append(p["dt"].strftime("%m-%d %H:%M"))
                    ap_list.append(p["ap"])
                    ap_ts.append(int(p["dt"].timestamp() * 1000))
                    chart_points.append(p)
                view_title = t("Gui.Stat.ViewTitleLine")
                is_detail_mode = False
                current_view = "line"
        elif current_view == "line":
            for p in raw_points:
                labels.append(p["dt"].strftime("%m-%d %H:%M"))
                ap_list.append(p["ap"])
                ap_ts.append(int(p["dt"].timestamp() * 1000))
                chart_points.append(p)
            view_title = t("Gui.Stat.ViewTitleLine")
        else:
            candles = self._aggregate_ap_candles(
                raw_points,
                by_hour=current_view == "day",
                today_points=today_points,
            )
            if not candles:
                return None

            if current_view == "day":
                view_title = t("Gui.Stat.ViewTitleDay", day=today.strftime("%m-%d"))
            else:
                view_title = t("Gui.Stat.ViewTitleMonth")

            for key, value in candles.items():
                labels.append(key)
                opens.append(value["open"])
                highs.append(value["high"])
                lows.append(value["low"])
                closes.append(value["close"])
                counts.append(value["count"])

        all_ap = [p["ap"] for p in raw_points]
        ap_max = max(all_ap)
        ap_min = min(all_ap)
        ap_avg = int(sum(all_ap) / len(all_ap))
        ap_cur = all_ap[-1]
        if current_view in self.AP_LINE_VIEWS:
            ap_change = ap_list[-1] - ap_list[0] if len(ap_list) >= 2 else 0
            data_points_text = t("Gui.Stat.DataPointsCount", count=len(labels))
        else:
            ap_change = closes[-1] - opens[0] if len(closes) > 0 else 0
            data_points_text = t("Gui.Stat.CandlesCount", count=len(labels))
        change_color = "#ef5350" if ap_change >= 0 else "#26a69a"
        change_sign = "+" if ap_change >= 0 else ""

        return {
            "current_view": current_view,
            "labels": labels,
            "opens": opens,
            "highs": highs,
            "lows": lows,
            "closes": closes,
            "counts": counts,
            "ap_list": ap_list,
            "ap_ts": ap_ts,
            "detail_sources": detail_sources,
            "chart_points": chart_points,
            "is_detail_mode": is_detail_mode,
            "view_title": view_title,
            "ap_cur": ap_cur,
            "ap_change": ap_change,
            "ap_max": ap_max,
            "ap_min": ap_min,
            "ap_avg": ap_avg,
            "data_points_text": data_points_text,
            "change_color": change_color,
            "change_sign": change_sign,
        }

    @staticmethod
    def _aggregate_ap_candles(raw_points, by_hour, today_points):
        """把快照聚合为小时或日的 OHLC 序列。"""
        from collections import OrderedDict

        candles = OrderedDict()
        if by_hour:
            source = today_points if today_points else raw_points[:24]
            for p in source:
                key = p["dt"].strftime("%H:00")
                candle = candles.get(key)
                if candle is None:
                    candles[key] = {
                        "open": p["ap"],
                        "high": p["ap"],
                        "low": p["ap"],
                        "close": p["ap"],
                        "count": 1,
                    }
                else:
                    candle["high"] = max(candle["high"], p["ap"])
                    candle["low"] = min(candle["low"], p["ap"])
                    candle["close"] = p["ap"]
                    candle["count"] += 1
        else:
            for p in raw_points:
                key = p["dt"].strftime("%m-%d")
                candle = candles.get(key)
                if candle is None:
                    candles[key] = {
                        "open": p["ap"],
                        "high": p["ap"],
                        "low": p["ap"],
                        "close": p["ap"],
                        "count": 1,
                    }
                else:
                    candle["high"] = max(candle["high"], p["ap"])
                    candle["low"] = min(candle["low"], p["ap"])
                    candle["close"] = p["ap"]
                    candle["count"] += 1
        return candles


    @staticmethod
    def _align_ap_timeline(raw_points, chart_points):
        """按最近时间戳将辅助时间线对齐到图表点。"""
        raw_points.sort(key=lambda p: p["dt"])
        aligned_points = []
        point_idx = 0
        point_last = len(raw_points) - 1
        for chart_point in chart_points:
            while point_idx < point_last:
                cur_delta = abs(
                    (raw_points[point_idx]["dt"] - chart_point["dt"]).total_seconds()
                )
                next_delta = abs(
                    (
                        raw_points[point_idx + 1]["dt"] - chart_point["dt"]
                    ).total_seconds()
                )
                if next_delta > cur_delta:
                    break
                point_idx += 1
            aligned_points.append(raw_points[point_idx])
        return aligned_points

    def _build_ap_chart_auxiliary_data(
        self, timeline, coins_timeline, asset_timeline, chart_points, current_view
    ):
        """分别装配辅助序列，并按既有顺序组合图表载荷。"""
        distance_data = self._build_ap_chart_distance_data(
            timeline, chart_points, current_view
        )
        coins_data = self._build_ap_chart_coins_data(
            coins_timeline, chart_points, current_view
        )
        asset_data = self._build_ap_chart_asset_data(asset_timeline, current_view)
        return self._combine_ap_chart_auxiliary_data(
            coins_data, distance_data, asset_data
        )

    def _build_ap_chart_coins_data(self, coins_timeline, chart_points, current_view):
        """解析并对齐黄币、紫币时间线，构造对应统计和图例。"""
        yellow_coins_list = []
        purple_coins_list = []
        coins_sources_list = []
        show_coins = False
        stats_html = ""
        legend_html = ""
        digest_parts = []

        if coins_timeline and chart_points and current_view in ("line", "detail"):
            coins_raw_points = []
            for pt in coins_timeline:
                ts_raw = pt.get("ts", "")
                try:
                    dt = datetime.fromisoformat(ts_raw)
                except Exception:
                    continue
                coins_raw_points.append(
                    {
                        "dt": dt,
                        "yellow_coins": int(pt.get("yellow_coins", 0)),
                        "purple_coins": int(pt["purple_coins"])
                        if "purple_coins" in pt
                        else None,
                        "source": pt.get("source", "-"),
                    }
                )

            if coins_raw_points:
                for coins_point in self._align_ap_timeline(
                    coins_raw_points, chart_points
                ):
                    yellow_coins_list.append(coins_point["yellow_coins"])
                    purple_coins_list.append(coins_point["purple_coins"])
                    coins_sources_list.append(coins_point.get("source", "-"))

                valid_yellow_coins = [v for v in yellow_coins_list if v is not None]
                valid_purple_coins = [
                    v for v in purple_coins_list if v is not None and v > 0
                ]
                show_coins = bool(valid_yellow_coins or valid_purple_coins)

                if valid_yellow_coins:
                    yc_cur = valid_yellow_coins[-1]
                    yc_change = (
                        valid_yellow_coins[-1] - valid_yellow_coins[0]
                        if len(valid_yellow_coins) >= 2
                        else 0
                    )
                    yc_max = max(valid_yellow_coins)
                    yc_min = min(valid_yellow_coins)

                    stats_html += self._build_ap_resource_row(
                        name=t("Gui.Stat.SeriesYellowCoin"),
                        color=self.AP_SERIES_COLORS["yellow"],
                        value=yc_cur,
                        change=yc_change,
                        max_value=yc_max,
                        min_value=yc_min,
                    )
                    digest_parts.append(
                        (
                            t("Gui.Stat.SeriesYellowCoin"),
                            self._format_ap_metric(yc_cur, 0),
                        )
                    )
                    legend_html += self._build_ap_legend_item(
                        2,
                        self.AP_SERIES_COLORS["yellow"],
                        t("Gui.Stat.SeriesYellowCoin"),
                        dashed=True,
                    )

                if valid_purple_coins:
                    pc_cur = valid_purple_coins[-1]
                    pc_change = (
                        valid_purple_coins[-1] - valid_purple_coins[0]
                        if len(valid_purple_coins) >= 2
                        else 0
                    )
                    pc_max = max(valid_purple_coins)
                    pc_min = min(valid_purple_coins)

                    stats_html += self._build_ap_resource_row(
                        name=t("Gui.Stat.SeriesPurpleCoin"),
                        color=self.AP_SERIES_COLORS["purple"],
                        value=pc_cur,
                        change=pc_change,
                        max_value=pc_max,
                        min_value=pc_min,
                    )
                    digest_parts.append(
                        (
                            t("Gui.Stat.SeriesPurpleCoin"),
                            self._format_ap_metric(pc_cur, 0),
                        )
                    )
                    legend_html += self._build_ap_legend_item(
                        1,
                        self.AP_SERIES_COLORS["purple"],
                        t("Gui.Stat.SeriesPurpleCoin"),
                        dashed=True,
                    )

        return {
            "yellow_coins_list": yellow_coins_list,
            "purple_coins_list": purple_coins_list,
            "coins_sources_list": coins_sources_list,
            "show_coins": show_coins,
            "stats_html": stats_html,
            "legend_html": legend_html,
            "digest_parts": digest_parts,
        }

    def _build_ap_chart_distance_data(self, timeline, chart_points, current_view):
        """解析并对齐海里数时间线，构造对应统计和图例。"""
        distance_raw_points = []
        if current_view in ("line", "detail"):
            for pt in timeline:
                distance_val = pt.get("distance")
                if distance_val is not None:
                    ts_raw = pt.get("ts", "")
                    try:
                        distance_dt = datetime.fromisoformat(ts_raw)
                        distance_raw_points.append(
                            {
                                "dt": distance_dt,
                                "distance": int(distance_val),
                            }
                        )
                    except Exception:
                        continue

        distance_list = []
        stats_html = ""
        legend_html = ""
        digest_parts = []
        if distance_raw_points and chart_points and current_view in ("line", "detail"):
            for distance_point in self._align_ap_timeline(
                distance_raw_points, chart_points
            ):
                distance_list.append(distance_point["distance"])

            if distance_list:
                valid_distance = [v for v in distance_list if v is not None]
                if valid_distance:
                    d_cur = valid_distance[-1]
                    d_change = (
                        valid_distance[-1] - valid_distance[0]
                        if len(valid_distance) >= 2
                        else 0
                    )
                    d_max = max(valid_distance)
                    d_min = min(valid_distance)

                    stats_html += self._build_ap_resource_row(
                        name=t("Gui.Stat.SeriesDistance"),
                        color=self.AP_SERIES_COLORS["distance"],
                        value=d_cur,
                        change=d_change,
                        max_value=d_max,
                        min_value=d_min,
                    )
                    digest_parts.append(
                        (
                            t("Gui.Stat.SeriesDistance"),
                            self._format_ap_metric(d_cur, 0),
                        )
                    )
                    legend_html += self._build_ap_legend_item(
                        4,
                        self.AP_SERIES_COLORS["distance"],
                        t("Gui.Stat.SeriesDistance"),
                    )

        return {
            "distance_list": distance_list,
            "stats_html": stats_html,
            "legend_html": legend_html,
            "digest_parts": digest_parts,
        }

    def _build_ap_chart_asset_data(self, asset_timeline, current_view):
        """解析资产时间线，构造对应统计和图例。"""
        asset_list = []
        asset_ts_list = []
        if asset_timeline and current_view in ("line", "detail"):
            for pt in asset_timeline:
                ts_raw = pt.get("ts", "")
                if ts_raw:
                    try:
                        va_dt = datetime.fromisoformat(ts_raw)
                        asset_value = self._snapshot_float(pt, "asset")
                        if asset_value is None:
                            continue
                        asset_list.append(asset_value)
                        asset_ts_list.append(int(va_dt.timestamp() * 1000))
                    except TypeError, ValueError:
                        continue

        stats_html = ""
        legend_html = ""
        digest_parts = []
        if asset_list:
            valid_asset = [v for v in asset_list if v is not None]
            if valid_asset:
                a_cur = valid_asset[-1]
                a_change = (
                    valid_asset[-1] - valid_asset[0] if len(valid_asset) >= 2 else 0
                )
                a_max = max(valid_asset)
                a_min = min(valid_asset)

                stats_html += self._build_ap_resource_row(
                    name=t("Gui.Stat.SeriesAsset"),
                    color=self.AP_SERIES_COLORS["asset"],
                    value=a_cur,
                    change=a_change,
                    max_value=a_max,
                    min_value=a_min,
                    decimals=1,
                )
                digest_parts.append(
                    (
                        t("Gui.Stat.SeriesAsset"),
                        self._format_ap_metric(a_cur, 1),
                    )
                )
                legend_html += self._build_ap_legend_item(
                    3,
                    self.AP_SERIES_COLORS["asset"],
                    t("Gui.Stat.SeriesAsset"),
                )

        return {
            "asset_list": asset_list,
            "asset_ts_list": asset_ts_list,
            "stats_html": stats_html,
            "legend_html": legend_html,
            "digest_parts": digest_parts,
        }

    @staticmethod
    def _combine_ap_chart_auxiliary_data(coins_data, distance_data, asset_data):
        """按模板约定组合辅助序列、摘要 HTML 与图例。"""
        show_coins = coins_data["show_coins"]
        if not show_coins and (
            asset_data["asset_list"]
            or coins_data["yellow_coins_list"]
            or coins_data["purple_coins_list"]
            or distance_data["distance_list"]
        ):
            show_coins = True

        return {
            "yellow_coins_list": coins_data["yellow_coins_list"],
            "purple_coins_list": coins_data["purple_coins_list"],
            "coins_sources_list": coins_data["coins_sources_list"],
            "distance_list": distance_data["distance_list"],
            "asset_list": asset_data["asset_list"],
            "asset_ts_list": asset_data["asset_ts_list"],
            "show_coins": show_coins,
            "coins_stats_html": (
                coins_data["stats_html"]
                + distance_data["stats_html"]
                + asset_data["stats_html"]
            ),
            "coins_legend_html": (
                coins_data["legend_html"]
                + distance_data["legend_html"]
                + asset_data["legend_html"]
            ),
            # 折叠摘要：各序列当前值（黄币 72,366 · 紫币 2,838 · 资产 452,599.3）
            "aux_digest": " · ".join(
                f"{name} {value}"
                for name, value in (
                    coins_data["digest_parts"]
                    + distance_data["digest_parts"]
                    + asset_data["digest_parts"]
                )
            ),
        }

    @staticmethod
    def _snapshot_float(point, key):
        """将快照中的可选数值转换为浮点数。"""
        value = point.get(key)
        if value is None:
            return None
        return float(value)

    def _render_ap_chart_content(self, chart_data, auxiliary_data):
        """将已装配的数据填充到 HTML 和 JavaScript 模板。"""
        current_view = chart_data["current_view"]
        chart_id = f"ap_cv_{id(self)}"
        detail_controls_display = (
            "display:flex;" if current_view in self.AP_LINE_VIEWS else "display:none;"
        )

        html_tpl = read_webapp_template("ap_chart_panel.html")
        js_tpl = read_webapp_template("ap_chart.js")

        # 黄币 / 紫币 / 资产 统计行 + 图例收进默认折叠的 details（用户拍板），
        # 摘要行保留各序列当前值，收起时信息不丢。
        aux_fold_html = (
            '<details class="ap-aux-fold">'
            "<summary>"
            f'<span class="ap-aux-title">{t("Gui.Stat.AuxFoldTitle")}</span>'
            f'<span class="ap-aux-digest">{auxiliary_data["aux_digest"]}</span>'
            "</summary>"
            '<div class="ap-aux-body">'
            + auxiliary_data["coins_stats_html"]
            + '<div id="'
            + chart_id
            + '_legend" style="display:flex; flex-wrap:wrap; gap:12px; '
            'margin-top:10px; font-size:12px; color:#888;">'
            + self._build_ap_legend_item(
                0,
                self.AP_SERIES_COLORS["ap"],
                t("Gui.Stat.SeriesActionPoint"),
            )
            + auxiliary_data["coins_legend_html"]
            + "</div>"
            + "</div></details>"
        )

        html = html_tpl.format(
            chart_id=chart_id,
            ap_cur=chart_data["ap_cur"],
            change_color=chart_data["change_color"],
            change_sign=chart_data["change_sign"],
            ap_change=chart_data["ap_change"],
            ap_max=chart_data["ap_max"],
            ap_min=chart_data["ap_min"],
            ap_avg=chart_data["ap_avg"],
            data_points_text=chart_data["data_points_text"],
            detail_controls_display=detail_controls_display,
            aux_fold_html=aux_fold_html,
            # KPI 卡文案。原模板里这几个标签是硬编码中文，顺手补上 i18n。
            kpi_current_ap=t("Gui.Stat.KpiCurrentAp"),
            kpi_change=t("Gui.Stat.KpiChange"),
            kpi_range_max=t("Gui.Stat.KpiRangeMax"),
            kpi_range_min=t("Gui.Stat.KpiRangeMin"),
            kpi_average=t("Gui.Stat.KpiAverage"),
        )

        js_tpl = read_webapp_template("ap_chart.js")
        js_code = (
            js_tpl.replace(
                "__CHART_TYPE__",
                "line" if chart_data["is_detail_mode"] else current_view,
            )
            .replace("__LABELS__", json.dumps(chart_data["labels"], ensure_ascii=False))
            .replace("__OPENS__", json.dumps(chart_data["opens"]))
            .replace("__HIGHS__", json.dumps(chart_data["highs"]))
            .replace("__LOWS__", json.dumps(chart_data["lows"]))
            .replace("__CLOSES__", json.dumps(chart_data["closes"]))
            .replace("__COUNTS__", json.dumps(chart_data["counts"]))
            .replace("__AP__", json.dumps(chart_data["ap_list"]))
            .replace("__AP_TS__", json.dumps(chart_data["ap_ts"]))
            .replace("__AVG__", str(chart_data["ap_avg"]))
            .replace("__CHART_ID__", chart_id)
            .replace(
                "__IS_DETAIL_MODE__",
                "true" if chart_data["is_detail_mode"] else "false",
            )
            .replace(
                "__SOURCES__",
                json.dumps(
                    chart_data["detail_sources"]
                    if chart_data["is_detail_mode"]
                    else []
                ),
            )
            .replace("__YELLOW_COINS__", json.dumps(auxiliary_data["yellow_coins_list"]))
            .replace("__PURPLE_COINS__", json.dumps(auxiliary_data["purple_coins_list"]))
            .replace("__COINS_SOURCES__", json.dumps(auxiliary_data["coins_sources_list"]))
            .replace("__ASSET__", json.dumps(auxiliary_data["asset_list"]))
            .replace("__ASSET_TS__", json.dumps(auxiliary_data["asset_ts_list"]))
            .replace("__DISTANCE__", json.dumps(auxiliary_data["distance_list"]))
            .replace(
                "__SHOW_COINS__",
                "true" if auxiliary_data["show_coins"] else "false",
            )
        )
        from pywebio.session import run_js

        with use_scope("ap_chart", clear=True):
            self._render_ap_chart_view_switcher(chart_data["view_title"], current_view)
            put_html(html)
            run_js(js_code)

    def _render_ap_chart_view_switcher(self, view_title, current_view):
        """标题行：左侧标题，右侧视图切换分段控件。

        布局对齐 statistics-v2 的卡片头部（标题在左、粒度切换在右），
        控件外观由 statistics-alas.css 的 .btn-group 规则给定。
        """
        labels = {
            "line": t("Gui.Stat.ViewLineButton"),
            "day": t("Gui.Stat.ViewDayButton"),
            "month": t("Gui.Stat.ViewMonthButton"),
        }
        put_row(
            [
                put_html(
                    f'<div style="font-weight:600;font-size:14px;">'
                    f'{t("Gui.Stat.ApChartTitle")} - {view_title}</div>'
                ),
                None,
                put_buttons(
                    [
                        {
                            "label": labels[view],
                            "value": view,
                            "color": "primary" if view == current_view else "off",
                        }
                        for view in self.AP_CHART_VIEWS
                    ],
                    onclick=self._switch_ap_chart_view,
                    group=True,
                ),
            ],
            size="auto 1fr auto",
        ).style("align-items:center;margin-top:16px;margin-bottom:8px;")

    @render_locked
    def _switch_ap_chart_view(self, view) -> None:
        """切换体力图表视图并重绘。

        切换只改变聚合口径，原始快照不变，因此复用上一次的数据集，
        避免每次切视图都重读三个数据源。
        """
        if view not in self.AP_CHART_VIEWS:
            return
        if view == getattr(self, "_ap_chart_view", "line"):
            return
        self._ap_chart_view = view
        self._render_ap_chart(reuse_dataset=True)
