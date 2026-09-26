"""WebUI 舰船经验统计视图。"""

from module.webui.app_dependencies import (
    alas_instance,
    put_html,
    t,
    use_scope,
)

from module.webui.app_helpers import (
    build_chip_row,
    build_fold_block,
    build_muted_notice,
    build_simple_table,
)


from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked


class ShipExperienceStatisticsMixin(WebUIMixinBase):
    """WebUI 舰船经验统计视图。"""

    @render_locked
    def _render_ship_exp(self):
        try:
            from module.statistics.opsi_month import (
                get_opsi_stats as get_opsi_stats_func,
            )
            from module.statistics.ship_exp_stats import get_ship_exp_stats

            # 使用当前实例名称获取统计数据，确保不为空
            instance_name = getattr(self, "alas_name", None)
            if not instance_name:
                # 使用第一个可用的实例
                from module.config.utils import alas_instance

                all_instances = alas_instance()
                instance_name = all_instances[0] if all_instances else None
            stats = get_ship_exp_stats(instance_name=instance_name)
            if not stats.data or not stats.data.get("ships"):
                with use_scope("ship_exp_table", clear=True):
                    put_html(build_muted_notice(t("Gui.Stat.NoShipExpData")))
                return

            ships_data = stats.data.get("ships", [])
            target_level = stats.data.get("target_level", 125)
            last_check_time = stats.data.get("last_check_time", "-")

            current_battles = (
                get_opsi_stats_func(instance_name=instance_name)
                .summary()
                .get("total_battles", 0)
            )
            progress_list = [
                stats.calculate_progress(ship, target_level, current_battles)
                for ship in ships_data
            ]

            try:
                today = stats.get_today_stats() or {}
            except Exception:
                today = {}
            today_battles = int(today.get("battle_count", 0) or 0)
            today_exp = int(today.get("total_exp_gained", 0) or 0)
            today_run = int((today.get("total_run_time", 0) or 0) // 60)

            fmt = self._opsi_fmt_num
            second_unit = t("Gui.Stat.SecondUnit")
            avg_battle = stats.get_average_battle_time()
            avg_round = stats.get_average_round_time()
            exp_per_hour = stats.get_exp_per_hour()
            chips = [
                (
                    t("Gui.Stat.AvgBattleTimeHeader"),
                    f"{avg_battle:.1f}{second_unit}" if avg_battle > 0 else "-",
                ),
                (
                    t("Gui.Stat.ShipExpAvgRound"),
                    f"{avg_round:.1f}{second_unit}" if avg_round > 0 else "-",
                ),
                (
                    t("Gui.Stat.ExpEfficiencyHeader"),
                    f"{fmt(exp_per_hour)}/{t('Gui.Stat.HourUnit')}"
                    if exp_per_hour > 0
                    else "-",
                ),
                (
                    t("Gui.Stat.ShipExpToday"),
                    t(
                        "Gui.Stat.ShipExpTodayValue",
                        battles=fmt(today_battles),
                        exp=fmt(today_exp),
                        run=fmt(today_run),
                    ),
                ),
                (t("Gui.Stat.ShipExpLastCheck"), str(last_check_time)),
            ]

            exp_labels = [
                t("Gui.Stat.ShipSlot"),
                t("Gui.Stat.Level"),
                t("Gui.Stat.CurrentExpThisLevel"),
                t("Gui.Stat.TotalExp"),
                t("Gui.Stat.TargetExpRequired"),
                t("Gui.Stat.ExpToTarget"),
                t("Gui.Stat.SortiesNeeded"),
                t("Gui.Stat.EstimatedTime"),
            ]
            exp_rows = []
            for progress in progress_list:
                # 等级已达标的船：当前等级经验 / 目标需求无从谈起，剩余经验是 0，
                # 出战数与耗时都置空，改标注「已完成」。
                done = progress["exp_needed"] <= 0
                exp_rows.append(
                    [
                        t("Gui.Stat.ShipExpSlotValue", slot=progress["position"]),
                        fmt(progress["level"]),
                        "-" if done else fmt(progress["current_exp"]),
                        fmt(progress["total_exp"]),
                        "-" if done else fmt(progress["target_exp"]),
                        fmt(progress["exp_needed"]),
                        t("Gui.Stat.ShipExpDone")
                        if done
                        else f"≈ {fmt(progress['battles_needed'])}",
                        "-" if done else f"≈ {self._format_duration(progress['time_seconds'])}",
                    ]
                )

            digest = " · ".join(
                [
                    t("Gui.Stat.FoldShipDigest", n=len(exp_rows)),
                    t("Gui.Stat.ShipExpTargetShort", level=target_level),
                    t("Gui.Stat.ShipExpTodayShort", n=fmt(today_battles)),
                ]
            )
            with use_scope("ship_exp_table", clear=True):
                put_html(
                    build_fold_block(
                        t("Gui.Stat.ShipExpProgressTitle"),
                        build_chip_row(chips)
                        + build_simple_table(exp_labels, exp_rows, numeric_from=1),
                        digest=digest,
                    )
                )
        except Exception as e:
            with use_scope("ship_exp_table", clear=True):
                put_html(build_muted_notice(t("Gui.Stat.LoadShipExpFailed", e=e)))

    @staticmethod
    def _format_duration(seconds):
        """把秒数格式化成「X小时Y分钟」；单位走 i18n。

        数据层的 `_format_time` 是硬编码中文的，英文/日文界面下会串味，
        所以这里统一用 `time_seconds` 重新格式化。
        """
        if not seconds or seconds <= 0:
            return "-"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        if hours > 0:
            return (
                f"{hours}{t('Gui.Stat.HourUnit')}"
                f"{minutes}{t('Gui.Stat.MinuteUnit')}"
            )
        return f"{minutes}{t('Gui.Stat.MinuteUnit')}"
