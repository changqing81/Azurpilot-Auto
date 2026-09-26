"""WebUI 大世界统计视图。"""

from html import escape as html_escape

from module.webui.app_dependencies import (
    current_time,
    put_html,
    put_scope,
    put_text,
    t,
    time,
    use_scope,
)

from module.webui.app_helpers import (
    build_fold_block,
    build_muted_notice,
    build_title_block,
    read_webapp_template,
)


from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked


class OpsiStatisticsMixin(WebUIMixinBase):
    """WebUI 大世界统计视图。"""

    @render_locked
    def _render_opsi_stats(self):
        dependencies = self._load_opsi_stats_dependencies()
        if dependencies is None:
            return

        (
            instance_name,
            summary,
            cl1_db,
            compute_monthly_cl1_akashi_ap,
            get_ship_exp_stats,
        ) = dependencies
        net, cards = self._build_cl1_summary(
            instance_name,
            summary,
            compute_monthly_cl1_akashi_ap,
            get_ship_exp_stats,
        )
        meow_rows = self._build_meow_rows(cl1_db, instance_name)
        self._render_opsi_summary(net, cards, meow_rows)

    def _load_opsi_stats_dependencies(self):
        try:
            from module.statistics.opsi_month import (
                get_opsi_stats,
                compute_monthly_cl1_akashi_ap,
            )
            from module.statistics.cl1_database import db as cl1_db
            from module.statistics.ship_exp_stats import get_ship_exp_stats

            instance_name = getattr(self, "alas_name", None)
            if not instance_name:
                from module.config.utils import alas_instance

                all_instances = alas_instance()
                instance_name = all_instances[0] if all_instances else None
            summary = get_opsi_stats(instance_name=instance_name).summary()
        except Exception as e:
            with use_scope("opsi_stats", clear=True):
                put_text(t("Gui.Stat.LoadOpsiStatsFailed", e=e))
            return None

        return (
            instance_name,
            summary,
            cl1_db,
            compute_monthly_cl1_akashi_ap,
            get_ship_exp_stats,
        )

    @staticmethod
    def _opsi_fmt_num(value, decimals=0):
        """千分位格式化指标数值；"-" 之类非数值原样返回。"""
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            return f"{value:,.{decimals}f}" if decimals else f"{int(value):,}"
        return str(value)

    @staticmethod
    def _opsi_card(label, value, unit="", sub="", tone=""):
        """构造一张侵蚀一指标卡。

        数值居中加粗、单位降为小字、副值另起一行 —— 对齐 statistics-v2 参考稿的
        metric 卡做法，但把「次数 / 概率」这类成对指标合并进同一张卡。
        tone 只负责加类名，具体颜色交给 CSS：
        cost（支出）/ akashi（明石，借黄币色）/ crane（吊机，借紫币色）/ eff / idle。

        没有副值的卡补一个空副值行，保证同排卡片高度一致。
        """
        tone_cls = f" is-{tone}" if tone else ""
        # 无数据的 "-" 不跟单位，否则会渲染成「- m」这种断句。
        has_unit = bool(unit) and str(value).strip() not in ("", "-")
        unit_html = (
            f'<span class="opsi-card-unit"> {html_escape(unit)}</span>'
            if has_unit
            else ""
        )
        sub_html = (
            f'<div class="opsi-card-sub">{html_escape(sub)}</div>'
            if sub
            else '<div class="opsi-card-sub">&nbsp;</div>'
        )
        return (
            f'<div class="opsi-card{tone_cls}">'
            f'<div class="opsi-card-label">{html_escape(str(label))}</div>'
            f'<div class="opsi-card-value">{value}{unit_html}</div>'
            f"{sub_html}"
            "</div>"
        )

    def _build_cl1_summary(
        self,
        instance_name,
        summary,
        compute_monthly_cl1_akashi_ap,
        get_ship_exp_stats,
    ):
        month = summary.get("month", "-")
        total = summary.get("total_battles", "-")
        try:
            tb = int(total)
            rounds = (tb + 1) // 2
            sortie_cost = rounds * 5
        except Exception:
            tb = total
            rounds = "-"
            sortie_cost = "-"

        akashi = summary.get("akashi_encounters", 0)
        try:
            ak = int(akashi)
        except Exception:
            ak = akashi

        try:
            if isinstance(rounds, int) and rounds > 0:
                rate = float(ak) / float(rounds)
                akashi_rate = f"{rate * 100:.2f}%"
            else:
                akashi_rate = "-"
        except Exception:
            akashi_rate = "-"

        try:
            siren_research = int(summary.get("siren_research_devices", 0) or 0)
        except Exception:
            siren_research = 0

        try:
            if isinstance(rounds, int) and rounds > 0:
                siren_research_rate = f"{siren_research / float(rounds) * 100:.2f}%"
            else:
                siren_research_rate = "-"
        except Exception:
            siren_research_rate = "-"

        try:
            ap_bought = compute_monthly_cl1_akashi_ap(instance_name=instance_name)
        except Exception:
            ap_bought = "-"

        try:
            if isinstance(ap_bought, (int, float)) and isinstance(ak, int) and ak > 0:
                avg_ap = int(float(ap_bought) / ak + 0.5)
            else:
                try:
                    ap_tmp = int(ap_bought)
                    if isinstance(ak, int) and ak > 0:
                        avg_ap = int(ap_tmp / ak + 0.5)
                    else:
                        avg_ap = "-"
                except Exception:
                    avg_ap = "-"
        except Exception:
            avg_ap = "-"

        try:
            net_ap = int(ap_bought) - int(sortie_cost)
        except Exception:
            net_ap = "-"

        try:
            eff = int(net_ap) / int(sortie_cost) * 100
            loop_eff = f"{eff:.2f}%"
        except Exception:
            loop_eff = "-"

        # 获取侵蚀1的平均时长
        try:
            exp_stats = get_ship_exp_stats(instance_name=instance_name)
            avg_cl1_battle_time = exp_stats.get_average_battle_time()
            avg_cl1_round_time = exp_stats.get_average_round_time()
            exp_per_hour = exp_stats.get_exp_per_hour()
            today_stats = exp_stats.get_today_stats()

            # 今日统计
            if today_stats:
                today_battles = today_stats.get("battle_count", 0)
                today_exp = today_stats.get("total_exp_gained", 0)
                today_run_time = int(today_stats.get("total_run_time", 0) // 60)
                today_exp_str = f"{today_exp:,}"
                today_run_str = f"{today_run_time}{t('Gui.Stat.MinuteUnit')}"
            else:
                today_battles = 0
                today_run_time = "-"
                today_exp_str = "-"
                today_run_str = "-"

            avg_cl1_battle_str = f"{avg_cl1_battle_time:.1f}{t('Gui.Stat.SecondUnit')}"
            avg_cl1_round_str = f"{avg_cl1_round_time:.1f}{t('Gui.Stat.SecondUnit')}"
            exp_per_hour_str = f"{exp_per_hour:.0f}/{t('Gui.Stat.HourUnit')}"
        except Exception:
            # 卡片要自己把单位拆出来渲染小字，所以这里也必须给出数值形态的兜底值。
            avg_cl1_battle_time = "-"
            avg_cl1_round_time = "-"
            exp_per_hour = "-"
            today_battles = 0
            today_exp_str = "-"
            today_run_time = "-"

        # 净赚率：净赚体力占当月购买体力的比例。任一值不可用时降级成 "-"。
        try:
            net_ratio = f"{int(net_ap) / int(ap_bought) * 100:.1f}%"
        except Exception:
            net_ratio = "-"

        # 收支比例条：以当月购买体力为满格，出击消耗按占比取长度。
        try:
            cost_pct = min(100.0, float(sortie_cost) / float(ap_bought) * 100.0)
        except Exception:
            cost_pct = 0.0

        fmt = self._opsi_fmt_num
        net_ap_text = fmt(net_ap)
        if isinstance(net_ap, int) and net_ap >= 0:
            net_ap_text = f"+{net_ap_text}"

        net = {
            "label": t("Gui.Stat.NetAP"),
            "value": net_ap_text,
            "bought_label": t("Gui.Stat.OpsiCardBought"),
            "bought_value": fmt(ap_bought),
            "cost_label": t("Gui.Stat.OpsiCardSortieCost"),
            "cost_value": fmt(sortie_cost),
            "cost_pct": f"{cost_pct:.1f}",
            "loop_label": t("Gui.Stat.LoopEfficiency"),
            "loop_value": loop_eff,
            "ratio_label": t("Gui.Stat.OpsiCardNetRatio"),
            "ratio_value": net_ratio,
            "month": month,
            "source": "cl1_data.db",
        }

        # 右侧 12 张卡：把「次数 / 概率」「战斗 / 一轮」这类成对指标各合成一张，
        # 于是正好 3 列 × 4 行，不会像原来 17 张那样在最后一行留一张孤卡。
        rate_label = t("Gui.Stat.OpsiCardRate")
        count_unit = t("Gui.Stat.OpsiCardCount")
        battle_unit = t("Gui.Stat.OpsiCardBattleUnit")
        second_unit = t("Gui.Stat.SecondUnit")
        cards = [
            self._opsi_card(
                t("Gui.Stat.BattleCount"), fmt(tb), battle_unit
            ),
            self._opsi_card(
                t("Gui.Stat.BattleRounds"), fmt(rounds), t("Gui.Stat.RoundUnit")
            ),
            self._opsi_card(
                t("Gui.Stat.OpsiCardSortieCost"), fmt(sortie_cost), tone="cost"
            ),
            self._opsi_card(t("Gui.Stat.AverageAP"), fmt(avg_ap)),
            self._opsi_card(
                t("Gui.Stat.OpsiCardAkashi"),
                fmt(ak),
                count_unit,
                f"{rate_label} {akashi_rate}",
                tone="akashi",
            ),
            self._opsi_card(
                t("Gui.Stat.OpsiCardCrane"),
                fmt(siren_research),
                count_unit,
                f"{rate_label} {siren_research_rate}",
                tone="crane",
            ),
            self._opsi_card(
                t("Gui.Stat.LoopEfficiency"), str(loop_eff), tone="eff"
            ),
            self._opsi_card(
                t("Gui.Stat.ExpEfficiencyHeader"),
                fmt(exp_per_hour),
                f"/{t('Gui.Stat.HourUnit')}",
                tone="eff",
            ),
            self._opsi_card(
                t("Gui.Stat.OpsiCardBattleTime"),
                fmt(avg_cl1_battle_time, 1),
                second_unit,
                f"{t('Gui.Stat.OpsiCardRound')} "
                f"{avg_cl1_round_time:.1f}{second_unit}"
                if isinstance(avg_cl1_round_time, float)
                else "",
            ),
            self._opsi_card(
                t("Gui.Stat.TodayBattlesHeader"),
                fmt(today_battles),
                battle_unit,
                tone="idle",
            ),
            self._opsi_card(
                t("Gui.Stat.TodayExpHeader"), today_exp_str, tone="idle"
            ),
            self._opsi_card(
                t("Gui.Stat.TodayRunHeader"),
                fmt(today_run_time),
                t("Gui.Stat.MinuteUnit"),
                tone="idle",
            ),
        ]

        return net, cards

    @staticmethod
    def _meow_loot_labels():
        """耄耋相接的四个平均收益列名（本地累积统计口径）。

        原先直接借 `AzurStats.meowofficer_farming_labels` 里那四个中文列名，
        做成卡片后标签要跟着界面语言走，因此改走 i18n。
        顺序必须与 `_build_meow_rows` 输出的 row[9:13] 一致。
        """
        return [
            t("Gui.Stat.MeowLootYellowPerRound"),
            t("Gui.Stat.MeowLootPlatePerRound"),
            t("Gui.Stat.MeowLootAbyssalPerRound"),
            t("Gui.Stat.MeowLootObscurePerRound"),
        ]

    def _build_meow_rows(self, cl1_db, instance_name):
        # 平均收益列来自本地累积统计（azurstat_meowofficer_farming.csv），
        # 该数据没有月份维度，按侵蚀等级并入同一行展示。
        loot_by_hazard = {}
        try:
            from module.statistics.azurstats import AzurStats

            for row in AzurStats.load_meowofficer_farming():
                level = int(row[0])
                if float(row[2]) > 0:
                    loot_by_hazard[level] = [round(float(value), 4) for value in row[3:]]
        except Exception:
            loot_by_hazard = {}

        meow_rows = []
        try:
            now = current_time()
            # 侵蚀等级 1~6 都查一遍：3 / 5 是耄耋相接的常驻刷取等级，总是占位显示；
            # 其余等级只有真跑过、或本地累积有收益时才生成卡片（见下面的过滤）。
            # ⚠️ 分等级数据必须读 ``by_hazard`` 桶：get_meow_stats 只为 3/5 构建
            # 分等级数据，查 1/2/4/6 时顶层字段是全月总量（不是该等级的），
            # 直接读顶层会把总量重复挂到四张卡上。
            for hazard_level in range(1, 7):
                meow_data = cl1_db.get_meow_stats(
                    instance_name or "default",
                    now.year,
                    now.month,
                    hazard_level=hazard_level,
                )
                bucket = (meow_data.get("by_hazard") or {}).get(
                    str(hazard_level), {}
                )
                meow_effective_rounds = float(
                    bucket.get("effective_rounds", 0) or 0
                )
                meow_rounds = round(meow_effective_rounds, 1)
                if abs(meow_rounds - int(meow_rounds)) < 1e-6:
                    meow_rounds = int(meow_rounds)

                meow_avg_time = float(bucket.get("avg_round_time", 0.0) or 0)
                meow_avg_battle_time = float(
                    bucket.get("avg_battle_time", 0.0) or 0
                )
                avg_time_str = (
                    f"{meow_avg_time:.1f}{t('Gui.Stat.SecondUnit')}"
                    if meow_avg_time > 0
                    else "-"
                )
                avg_battle_time_str = (
                    f"{meow_avg_battle_time:.1f}{t('Gui.Stat.SecondUnit')}"
                    if meow_avg_battle_time > 0
                    else "-"
                )

                # 明石统计（按侵蚀等级，与侵蚀一表格口径一致）
                akashi_encounters = int(
                    bucket.get("akashi_encounters", 0) or 0
                )
                akashi_ap = int(bucket.get("akashi_ap", 0) or 0)
                akashi_rate_str = (
                    f"{akashi_encounters / meow_rounds * 100:.2f}%"
                    if meow_rounds > 0
                    else "-"
                )
                avg_ap_str = (
                    str(int(akashi_ap / akashi_encounters + 0.5))
                    if akashi_encounters > 0
                    else "-"
                )

                battle_count = int(bucket.get("battle_count", 0) or 0)
                # 非 3 / 5 的等级：本条既没跑过、本地也没有该等级的累积收益，
                # 就不生成卡片，免得页面被一排全 0 的空卡占满。
                if hazard_level not in (3, 5) and not (
                    battle_count
                    or meow_rounds
                    or akashi_encounters
                    or hazard_level in loot_by_hazard
                ):
                    continue

                meow_rows.append(
                    [
                        meow_data.get("month", "-"),
                        hazard_level,
                        battle_count,
                        meow_rounds,
                        akashi_encounters,
                        akashi_rate_str,
                        avg_ap_str,
                        avg_battle_time_str,
                        avg_time_str,
                    ]
                    + list(loot_by_hazard.get(hazard_level, ["-", "-", "-", "-"]))
                )
        except Exception:
            return []

        return meow_rows

    @staticmethod
    def _build_opsi_summary_html(net, cards):
        """把净赚主卡与 12 张指标卡填进 opsi_summary.html 模板。"""
        tpl = read_webapp_template("opsi_summary.html")
        return tpl.format(
            net_label=html_escape(str(net["label"])),
            net_value=net["value"],
            bought_label=html_escape(str(net["bought_label"])),
            bought_value=net["bought_value"],
            # 当月购买体力是收支条的满格基准
            bought_pct="100.0",
            cost_label=html_escape(str(net["cost_label"])),
            cost_value=net["cost_value"],
            cost_pct=net["cost_pct"],
            loop_label=html_escape(str(net["loop_label"])),
            loop_value=html_escape(str(net["loop_value"])),
            ratio_label=html_escape(str(net["ratio_label"])),
            ratio_value=html_escape(str(net["ratio_value"])),
            month=html_escape(str(net["month"])),
            source=html_escape(str(net["source"])),
            cards="".join(cards),
        )

    # 侵蚀等级的徽标配色（3 蓝 / 5 红，与 statistics-v2 参考稿一致）
    MEOW_BADGE_COLORS = {3: "#378ADD", 5: "#E24B4A"}

    @staticmethod
    def _split_unit_text(text, unit):
        """把 "12.9秒" 这类已经拼好单位的串拆回 (数值, 单位)。拆不动就原样返回。"""
        if isinstance(text, str) and unit and text.endswith(unit):
            return text[: -len(unit)].strip(), unit
        return text, ""

    def _build_meow_cards_html(self, rows):
        """把耄耋相接的行数据渲染成按侵蚀等级的卡片。

        与「侵蚀一」共用同一套卡片语言：左侧等级徽标 + 场次 / 轮次大字摘要，
        右侧指标格。指标做了合并 —— 明石次数带概率副值、战斗时间带一轮时长副值。

        **数据来源差异**（否则看着像 bug）：战斗数据来自 `cl1_data.db`，有月份维度；
        收益四列来自本地累积的 `azurstat_meowofficer_farming.csv`，**没有月份维度**，
        按侵蚀等级并入同一行。所以会出现「本月 0 场但收益有值」，
        那是该等级的历史平均，不是当月产出。
        """
        if not rows:
            return build_muted_notice(t("Gui.Stat.NoMeowDataNotice"))

        fmt = self._opsi_fmt_num
        tpl = read_webapp_template("opsi_meow_card.html")
        rate_label = t("Gui.Stat.OpsiCardRate")
        round_prefix = t("Gui.Stat.OpsiCardRound")
        count_unit = t("Gui.Stat.OpsiCardCount")
        second_unit = t("Gui.Stat.SecondUnit")
        loot_labels = self._meow_loot_labels()

        cards = []
        for row in rows:
            month, level = row[0], row[1]
            battles, rounds = row[2], row[3]
            akashi, akashi_rate, avg_ap = row[4], row[5], row[6]
            battle_text, round_text = row[7], row[8]
            loot = list(row[9:13])
            battle_value, battle_unit_text = self._split_unit_text(
                battle_text, second_unit
            )
            round_value, round_unit_text = self._split_unit_text(
                round_text, second_unit
            )
            round_sub = (
                f"{round_prefix} {round_value}{round_unit_text}"
                if round_value != "-"
                else ""
            )

            cells = [
                self._opsi_card(
                    t("Gui.Stat.OpsiCardAkashi"),
                    fmt(akashi),
                    count_unit,
                    f"{rate_label} {akashi_rate}",
                    tone="akashi",
                ),
                self._opsi_card(t("Gui.Stat.AverageAP"), fmt(avg_ap)),
                self._opsi_card(
                    t("Gui.Stat.OpsiCardBattleTime"),
                    battle_value if battle_value == "-" else fmt(float(battle_value), 1),
                    battle_unit_text,
                    round_sub,
                ),
            ]
            cells += [
                self._opsi_card(label, fmt(value, 2), tone="loot")
                for label, value in zip(loot_labels, loot)
            ]

            cards.append(
                tpl.format(
                    badge_bg=self.MEOW_BADGE_COLORS.get(level, "#888780"),
                    level=html_escape(str(level)),
                    level_label=html_escape(
                        t("Gui.Stat.MeowLootHazardColumn", level=level)
                    ),
                    month=html_escape(str(month)),
                    battles_label=html_escape(t("Gui.Stat.BattleCount")),
                    battles=fmt(battles),
                    rounds_label=html_escape(t("Gui.Stat.MeowRounds")),
                    rounds=fmt(rounds),
                    cells="".join(cells),
                )
            )
        return "".join(cards)

    def _render_opsi_summary(self, net, cards, meow_rows):
        with use_scope("opsi_stats", clear=True):
            put_html(build_title_block(t("Gui.Stat.OpsiDataCollectionTitle")))
            put_html(self._build_opsi_summary_html(net, cards))

            # 方案 F：收获总览条（meow_loot_scope）在 fold 之前，只占一行；
            # 明细表收进下方 fold 体内的次级折叠，默认不占高度。
            view = self._load_meow_loot_view()
            put_scope("meow_loot_scope")
            self._render_meowofficer_farming(view)

            meow_refresh_token = int(time.time() * 1000)

            put_html(f"<!-- meow-stats-refresh-token:{meow_refresh_token} -->")
            put_html(
                build_fold_block(
                    t("Gui.Stat.MeowDataCollectionTitle"),
                    self._build_meow_cards_html(meow_rows)
                    + self._build_meow_loot_fold_html(view),
                    digest=self._build_meow_fold_digest(meow_rows, view),
                )
            )
