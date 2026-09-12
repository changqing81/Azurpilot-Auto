"""大世界跨月重置模块。

处理大世界每月重置前后的过渡逻辑，包括：
- 重置时间检测和倒计时管理
- 重置前 10 分钟内的紧急操作
- 跨月后复用月末清理循环消耗多余行动力（含塞壬要塞检查）

继承自 OpsiScheduling：
- 复用月末清理行动力循环（_run_month_end_cleanup_loop）
- 复用大世界推送通道（_send_opsi_notification）
- OSMap 大世界地图操作经由继承链获得
"""

from datetime import timedelta

from module.config.time_source import now as current_time
from module.config.utils import get_os_next_reset
from module.exception import ScriptError
from module.logger import logger
from module.os.tasks.scheduling import OpsiScheduling


class OpsiCrossMonth(OpsiScheduling):
    def os_cross_month_end(self):
        self.config.task_delay(target=get_os_next_reset() - timedelta(minutes=10))
        self.config.task_stop()

    def os_cross_month(self):
        self._os_cross_month()

    def _os_cross_month(self):
        next_reset = get_os_next_reset()
        now = current_time()
        logger.attr('大世界下次重置', next_reset)

        # 检查开始时间
        if next_reset < now:
            raise ScriptError(f'Invalid OpsiNextReset: {next_reset} < {now}')
        if next_reset - now > timedelta(days=3):
            logger.error('距离下次大世界重置超过 3 天，大世界可能已经重置，停止跨月每日')
            self.os_cross_month_end()
        if next_reset - now > timedelta(minutes=10):
            logger.error('距离下次大世界重置超过 10 分钟，距离跨月每日执行时间过早，停止任务')
            self.os_cross_month_end()

        # 距离大世界重置还有 10 分钟
        logger.hr('跨月每日等待大世界重置', level=1)
        logger.warning('AzurPilot 正在等待下次大世界重置，等待期间请不要操作游戏')
        while True:
            logger.info(f'等待到 {next_reset}')
            now = current_time()
            remain = (next_reset - now).total_seconds()
            if remain <= 0:
                break
            else:
                self.device.sleep(min(remain, 60))
                continue

        logger.hr('跨月每日处理大世界重置', level=3)

        def false_func(*args, **kwargs):
            return False

        self.is_in_opsi_explore = false_func
        self.config.override(_disable_task_switch=True)

        logger.hr('跨月每日清理大世界每日+', level=1)
        self.config.override(
            OpsiGeneral_DoRandomMapEvent=True,
            OpsiFleet_Fleet=self.config.cross_get('OpsiDaily.OpsiFleet.Fleet'),
            OpsiFleet_Submarine=False,
            # 每日任务
            OpsiDaily_SkipSirenResearchMission=False,
            OpsiDaily_KeepMissionZone=False,
        )
        count = 0
        empty_trial = 0
        while True:
            # 如果无法接收更多每日任务，先完成已有任务再重试
            success = self.os_mission_overview_accept()
            # 重新初始化区域名称
            # MISSION_ENTER 从右侧出现，需确认动画结束，否则会点击到 MAP_GOTO_GLOBE
            self.zone_init()
            if empty_trial >= 5:
                logger.warning('5 分钟内没有找到大世界每日+，停止等待')
                break
            count += self.os_finish_daily_mission()
            if not count:
                logger.warning('未接取到大世界每日+，可能游戏每日尚未刷新，等待 1 分钟')
                empty_trial += 1
                self.device.sleep(60)
                continue
            if success:
                break

        # 跨月每日完成后，清理多余行动力
        self._os_cross_month_clear_action_point()
        self.os_cross_month_end()

    def _os_cross_month_clear_action_point(self):
        """跨月每日完成后，检查并清理多余行动力。

        行动力口径为总行动力（当前行动力 + 行动力箱子折算），与月末清理一致。
        """
        logger.hr('跨月每日检查剩余行动力', level=1)
        if not self._config_enabled(keys='OpsiCrossMonth.OpsiCrossMonth.ActionPointCleanupEnable'):
            logger.info('跨月后清理行动力已关闭，跳过清理')
            return

        preserve = int(self.config.OpsiCrossMonth_ActionPointPreserve or 0)
        self.zone_init()
        total_ap, current_ap = self._get_scheduling_action_point(force_refresh=True)
        logger.attr('跨月后总行动力', total_ap)
        if total_ap <= preserve:
            logger.info(f'总行动力 {total_ap} 未超过保留值 {preserve}，无需清理')
            self._notify_cross_month(
                title='[AzurPilot] 跨月每日完成',
                content=(
                    '总行动力未超过保留值，无需清理，已切换下一个任务\n'
                    f'总行动力: {total_ap}\n'
                    f'当前行动力: {current_ap}\n'
                    f'保留值: {preserve}'
                ),
            )
            return

        self._run_cross_month_action_point_cleanup(preserve)

        total_ap, current_ap = self._get_scheduling_action_point(force_refresh=True)
        logger.attr('清理后总行动力', total_ap)
        self._notify_cross_month(
            title='[AzurPilot] 跨月每日完成',
            content=(
                '跨月任务剩余行动力结束，已切换下一个任务\n'
                f'剩余总行动力: {total_ap} (保留值 {preserve})\n'
                f'当前行动力: {current_ap}'
            ),
        )

    def _run_cross_month_action_point_cleanup(self, preserve):
        """复用月末清理循环消耗多余行动力。

        先检查塞壬要塞，再循环 隐秘海域 → 深渊坐标 → 耄耋相接，
        直到总行动力低于保留值、无可执行内容或行动力耗尽。

        Args:
            preserve (int): 清理保留值（总行动力口径）。
        """
        logger.hr(f'跨月每日清理多余行动力, 保留值={preserve}', level=1)
        self.config.override(
            OpsiGeneral_DoRandomMapEvent=True,
            OpsiGeneral_BuyActionPointLimit=0,
            OpsiGeneral_UseLogger=True,
            HOMO_EDGE_DETECT=True,
            STORY_OPTION=-2,
            # 隐秘海域 / 深渊坐标
            OpsiObscure_SkipHazard2Obscure=self.config.cross_get('OpsiObscure.OpsiObscure.SkipHazard2Obscure'),
            OpsiObscure_ForceRun=True,
            OpsiAbyssal_ForceRun=True,
            OpsiFleetFilter_Filter=self.config.cross_get('OpsiAbyssal.OpsiFleetFilter.Filter'),
            # 耄耋相接（清理全程沿用该舰队设置，与月末清理单舰队跑全部子任务的模式一致）
            OpsiFleet_Fleet=self.config.cross_get('OpsiMeowfficerFarming.OpsiFleet.Fleet'),
            OpsiFleet_Submarine=False,
            OpsiMeowfficerFarming_ActionPointPreserve=0,
            OpsiMeowfficerFarming_HazardLevel=self.config.cross_get(
                'OpsiMeowfficerFarming.OpsiMeowfficerFarming.HazardLevel'),
            OpsiMeowfficerFarming_TargetZone=self.config.cross_get(
                'OpsiMeowfficerFarming.OpsiMeowfficerFarming.TargetZone'),
            OpsiMeowfficerFarming_StayInZone=self.config.cross_get(
                'OpsiMeowfficerFarming.OpsiMeowfficerFarming.StayInZone'),
        )
        # 强制本轮必查一次塞壬要塞（新月要塞会刷新，不受月末清理历史状态影响）
        self._set_month_end_cleanup_first_run(True)
        # 清理数千行动力可能耗时数小时，看门狗临时放宽，防止被任务超时误杀
        backup = self.config.temporary(TaskFailureProtection_WatchdogTaskTimeout=360)
        # 标记消耗型上下文：清理期间子任务不得反向补充行动力
        self._month_end_cleanup_running = True
        try:
            self._run_month_end_cleanup_loop(preserve)
        finally:
            self._month_end_cleanup_running = False
            backup.recover()

    def _run_month_end_shop_purchase(self):
        """跨月清理不做月末商店购买（商店已在 1 日刷新，购买交给 OpsiShop 任务）。"""
        logger.info('[跨月每日] 跳过月末商店购买')

    def _notify_cross_month(self, title, content):
        """发送跨月任务推送，受 OpsiCrossMonth.PushNotify 开关控制。"""
        if not self._config_enabled(keys='OpsiCrossMonth.OpsiCrossMonth.PushNotify'):
            return False
        try:
            return self._send_opsi_notification(title, content, log_tag='[大世界-跨月每日]')
        except Exception as e:
            logger.error(f'跨月每日推送发送异常: {e}')
            return False
