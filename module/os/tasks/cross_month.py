"""大世界跨月重置模块。

处理大世界每月重置前后的过渡逻辑，包括：
- 重置时间检测和倒计时管理
- 重置前 10 分钟内的紧急操作
- 跨月后复用月末清理循环消耗多余行动力（含塞壬要塞检查）
- 失败时推送并自动交接，不再卡死等待人工

继承自 OpsiScheduling：
- 复用月末清理行动力循环（_run_month_end_cleanup_loop）
- 复用大世界推送通道（_send_opsi_notification）
- OSMap 大世界地图操作经由继承链获得
"""

from contextlib import contextmanager
from datetime import timedelta

from module.config.config import TaskEnd
from module.config.time_source import now as current_time
from module.config.utils import get_os_next_reset
from module.exception import RequestHumanTakeover, ScriptEnd, ScriptError
from module.logger import logger
from module.os_handler.action_point import ActionPointLimit
from module.os.tasks.scheduling import OpsiScheduling


class OpsiCrossMonth(OpsiScheduling):
    def os_cross_month_end(self):
        self.config.task_delay(target=get_os_next_reset() - timedelta(minutes=10))
        self.config.task_stop()

    @contextmanager
    def _os_cross_month_guard(self):
        """跨月流程的 override 快照保护。

        本任务及子任务链路（clear_obscure / handle_action_point / os_init 等）
        会写入大量 config.override 强制覆盖：它按属性名全局生效、进程内不会
        自动恢复，不清理会泄漏给同进程后续任务（如耄耋相接保留值被归零、
        买行动力上限被清零、舰队设置被替换）。退出时统一恢复快照。
        """
        overridden_backup = dict(self.config.overridden)
        try:
            yield
        finally:
            try:
                if self.config.overridden != overridden_backup:
                    self.config.overridden.clear()
                    self.config.overridden.update(overridden_backup)
                    # _disable_task_switch 不是配置路径，bind 不会恢复，需手动复位
                    self.config._disable_task_switch = False
                    self.config.bind(self.config.task)
                    logger.info('[跨月每日] 已恢复 override 强制覆盖快照，避免泄漏到后续任务')
            except Exception as restore_e:
                logger.warning(f'[跨月每日] 恢复 override 快照失败: {restore_e}')

    def _consume_rehearsal_request(self):
        """读取并清除开发者工具写入的预演请求。

        Returns:
            str | None: 'cleanup' 仅清理 / 'full' 完整流程 / None 无请求。
        """
        mode = self.config.cross_get(
            keys='OpsiCrossMonth.OpsiCrossMonth.RehearsalDebug', default='off')
        if mode not in ('cleanup', 'full'):
            return None
        self.config.cross_set(
            keys='OpsiCrossMonth.OpsiCrossMonth.RehearsalDebug', value='off')
        logger.info(f'[跨月每日] 检测到开发者工具预演请求: {mode}')
        return mode

    def _os_cross_month_rehearsal(self, mode):
        """开发者工具触发的预演：真机执行调试流程后按常规重新规划调度。

        Args:
            mode (str): 'cleanup' 跳过每日+ / 'full' 完整流程。
        """
        try:
            with self._os_cross_month_guard():
                self.os_cross_month_debug(skip_daily=(mode == 'cleanup'))
        except TaskEnd:
            logger.info('[跨月每日] 预演结束')
        except Exception as e:
            logger.exception(e)
            self._notify_cross_month_failed(e)
        # 无论成败都按常规重新规划到下次重置前 10 分钟，不影响真实跨月执行
        self.os_cross_month_end()

    def os_cross_month(self):
        mode = self._consume_rehearsal_request()
        if mode is not None:
            self._os_cross_month_rehearsal(mode)
            return
        try:
            with self._os_cross_month_guard():
                self._os_cross_month()
        except TaskEnd:
            # 正常结束（os_cross_month_end / task_stop），放行给调度器
            raise
        except ActionPointLimit:
            # 行动力耗尽属正常结束路径，交给 os_run 包装层收尾
            raise
        except (RequestHumanTakeover, ScriptEnd):
            # 需要人工接管 / 开发期中断，保持原有语义
            raise
        except Exception as e:
            # 跨月失败：推送并自动交接，不让异常触发 Sensitive 退出整个调度器
            logger.exception(e)
            self._notify_cross_month_failed(e)
            self._cross_month_fail_handover()

    def _cross_month_fail_handover(self):
        """跨月失败后的交接：规划下次运行时间并结束本任务，交给调度器继续。"""
        next_reset = get_os_next_reset()
        now = current_time()
        if next_reset - now > timedelta(days=3):
            # 重置已过，本次跨月已结束，直接规划到下月
            logger.info('跨月每日失败交接：本次重置已过，规划到下月重置前 10 分钟')
            self.os_cross_month_end()
        else:
            # 仍在重置前等待窗口：推迟到重置后，由既有的"超过 3 天"分支规划到下月
            logger.info('跨月每日失败交接：仍在等待窗口，推迟到重置后再规划')
            self.config.task_delay(target=next_reset + timedelta(minutes=10))
            self.config.task_stop()

    def os_cross_month_debug(self, skip_daily=False):
        """调试预演入口（由 module.debug.cross_month_debug 调用）。

        把今天当成跨月时刻：跳过时间检查与等待重置，真机执行
        大世界初始化 → 每日+（可选）→ 塞壬要塞 → 隐秘/深渊/耄耋清理 → 推送。
        预演不修改任务调度：不写 NextRun、不调用 task_stop。

        Args:
            skip_daily (bool): True 跳过每日+阶段，只验证塞壬要塞及后续清理。
        """
        logger.hr('跨月每日调试预演', level=1)
        logger.warning('预演将真实操作游戏：可能消耗行动力/仓库道具，并按推送开关发送真实通知')
        try:
            with self._os_cross_month_guard():
                self.os_init()
                self._os_cross_month_enter_context()
                if skip_daily:
                    logger.info('按参数跳过大世界每日+阶段')
                else:
                    self._os_cross_month_daily()
                self._os_cross_month_clear_action_point(force=True)
        except TaskEnd:
            logger.info('[跨月每日] 预演结束（捕获 task_stop）')
        logger.hr('跨月每日调试预演结束，任务调度未受影响', level=1)

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

        self._os_cross_month_enter_context()
        # 每日+阶段内部会接续完成行动力清理，并以 task_stop 结束本任务
        self._os_cross_month_daily()

    def _os_cross_month_enter_context(self):
        """进入跨月处理上下文：屏蔽每月开荒判断，禁用任务切换检查。"""
        def false_func(*args, **kwargs):
            return False

        self.is_in_opsi_explore = false_func
        self.config.override(_disable_task_switch=True)

    def _os_cross_month_daily(self):
        """阶段一：接取并完成大世界每日+。"""
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

    def _os_cross_month_clear_action_point(self, force=False):
        """跨月每日完成后，检查并清理多余行动力。

        行动力口径为总行动力（当前行动力 + 行动力箱子折算），与月末清理一致。

        Args:
            force (bool): 调试预演用，忽略清理开关强制执行；生产流程保持 False。
        """
        logger.hr('跨月每日检查剩余行动力', level=1)
        if not force and not self._config_enabled(keys='OpsiCrossMonth.OpsiCrossMonth.ActionPointCleanupEnable'):
            logger.info('跨月后清理行动力已关闭，跳过清理')
            return

        try:
            preserve = int(self.config.OpsiCrossMonth_ActionPointPreserve or 0)
        except (TypeError, ValueError):
            logger.warning('跨月清理行动力保留值配置无效，回退默认值 50')
            preserve = 50
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

    # ==================== 推送 ====================

    def _notify_cross_month(self, title, content):
        """发送跨月任务推送，受 OpsiCrossMonth.PushNotify 开关控制。

        整体兜底异常：推送自身失败不允许打断失败交接流程。
        """
        try:
            if not self._config_enabled(keys='OpsiCrossMonth.OpsiCrossMonth.PushNotify'):
                return False
            return self._send_opsi_notification(title, content, log_tag='[大世界-跨月每日]')
        except Exception as e:
            logger.error(f'跨月每日推送发送异常: {e}')
            return False

    def _notify_cross_month_failed(self, e):
        """推送跨月每日失败通知。"""
        self._notify_cross_month(
            title='[AzurPilot] 跨月每日失败',
            content=(
                '跨月每日任务失败，已交给调度器继续后续任务\n'
                f'原因: {type(e).__name__}: {e}'
            ),
        )
