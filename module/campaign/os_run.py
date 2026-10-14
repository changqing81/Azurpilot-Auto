"""大世界战役运行模块。

管理大世界（Operation Siren）任务的执行流程。

大世界是一个独立于主线战役的开放世界系统，有自己的：
- 行动力（Action Point）机制
- 适应性（Adaptability）系统
- 净化装置（Purification Device）
- 港口商店和任务系统

此模块负责：
- 加载大世界配置和地图操作实例
- 处理行动力溢出保护
- 大世界任务的延迟和调度
- 行动力不足时的任务延迟

继承自 OSMapOperation，提供大世界地图操作能力。
"""

from module.config.utils import get_os_reset_remain
from module.logger import logger
from module.os.config import OSConfig
from module.os.map_operation import OSMapOperation
from module.os.operation_siren import OperationSiren
from module.os_handler.action_point import ActionPointLimit


class OSCampaignRun(OSMapOperation):
    """大世界战役运行器。

    管理大世界任务的执行，包括行动力保护和任务调度。

    Attributes:
        PREVENT_AP_OVERFLOW_TASK (str): 防止行动力溢出的任务名称。
    """
    PREVENT_AP_OVERFLOW_TASK = 'OpsiPreventActionPointOverflow'
    # 「guard 临时挂起」标记：guard 运行其他大世界任务期间置 True，结束时清除。
    # 若进程在 guard 运行期间被终止（如 WebUI 手动停止、崩溃），finally 不会执行，
    # 标记会残留；下次运行大世界任务时先清掉，避免防溢出任务被永久挂起。
    # 该标记只参与调度判据（见 `module.config.config.GUARD_SUSPEND_FLAG_PATHS`），
    # 绝不改写用户控制的 `Scheduler.Enable`。
    PREVENT_AP_OVERFLOW_GUARD_FLAG = (
        'OpsiPreventActionPointOverflow.Storage.Storage.GuardTempClosed'
    )

    def load_campaign(self, cls=OperationSiren):
        config = self.config.merge(OSConfig())
        campaign = cls(config=config, device=self.device)
        campaign.os_init()
        return campaign

    def delay_opsi_tasks_after_ap_limit(self, error):
        delay_minutes = getattr(error, 'delay_minutes', None)
        if delay_minutes is not None:
            logger.info(f'[大世界-运行] 延迟大世界行动力任务 {delay_minutes} 分钟直到行动力恢复')
        self.config.opsi_task_delay(ap_limit=True, ap_limit_minutes=delay_minutes)

    def _is_prevent_ap_overflow_guard_flag_set(self):
        """读取「guard 临时关闭」残留标记。"""
        return bool(self.config.cross_get(
            keys=self.PREVENT_AP_OVERFLOW_GUARD_FLAG, default=False))

    def _set_prevent_ap_overflow_guard_flag(self, value):
        """写入或清除「guard 临时关闭」残留标记。"""
        self.config.cross_set(
            keys=self.PREVENT_AP_OVERFLOW_GUARD_FLAG, value=bool(value))

    def heal_prevent_ap_overflow_guard_residual(self):
        """清除 guard 临时挂起后进程异常终止留下的残留标记。

        guard 依赖 finally 清除挂起标记；若进程在运行期间被终止（WebUI 手动
        停止、崩溃等），清除不会执行。下次运行大世界任务时先清掉，避免防溢出
        任务被永久挂起。

        挂起标记与用户控制的 `Scheduler.Enable` 已分离，本方法只清标记，
        绝不改动任务的启用状态，因此不存在「把用户主动关闭误当成残留恢复」。

        Returns:
            bool: 本次是否清除了残留标记。
        """
        if not self._is_prevent_ap_overflow_guard_flag_set():
            return False
        logger.info('[战役] 清除防止行动力溢出任务残留的临时挂起标记')
        self._set_prevent_ap_overflow_guard_flag(False)
        return True

    def _run_opsi_task_with_ap_overflow_guard(self, runner):
        """运行普通大世界任务时临时挂起防溢出任务，并在结束时恢复。

        「挂起」只写 guard 标记，不改 `Scheduler.Enable`；用户关闭的任务
        因此不会被 guard 重新启用。
        """
        # 先清掉上次异常终止（如手动停止）留下的临时挂起标记
        self.heal_prevent_ap_overflow_guard_residual()

        campaign = None
        prevent_enabled = self.config.is_task_enabled(self.PREVENT_AP_OVERFLOW_TASK)
        if prevent_enabled:
            logger.info('[战役] 临时挂起防止行动力溢出任务')
            self._set_prevent_ap_overflow_guard_flag(True)

        try:
            campaign = self.load_campaign()
            return runner(campaign)
        finally:
            if prevent_enabled:
                try:
                    if campaign is not None:
                        campaign.update_prevent_action_point_overflow_schedule()
                except Exception:
                    logger.debug('更新防止行动力溢出任务调度失败', exc_info=True)
                finally:
                    self._set_prevent_ap_overflow_guard_flag(False)

    def opsi_explore(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_explore())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_shop(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_shop())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_voucher(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_voucher())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_daily(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_daily())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_meowfficer_farming(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_meowfficer_farming())
        except ActionPointLimit:
            if get_os_reset_remain() > 0:
                self.config.task_delay(server_update=True)
                self.config.task_call('Reward', force_call=False)
            else:
                logger.info('[大世界-运行] 距离大世界重置不足1天，延迟2.5小时')
                self.config.task_delay(minute=150, server_update=True)

    def opsi_hazard1_leveling(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(
                lambda campaign: (campaign.os_check_leveling(), campaign.os_hazard1_leveling())
            )
        except ActionPointLimit:
            self.config.task_delay(server_update=True)

    def opsi_obscure(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_obscure())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_month_boss(self):
        if self.config.SERVER in ['tw']:
            logger.info(f'[大世界-运行] OpsiMonthBoss不支持服务器 {self.config.SERVER}，'
                        ' please contact server maintainers')
            self.config.task_delay(server_update=True)
            self.config.task_stop()
            return
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.clear_month_boss())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_abyssal(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_abyssal())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_archive(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_archive())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_stronghold(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_stronghold())
        except ActionPointLimit as e:
            self.delay_opsi_tasks_after_ap_limit(e)

    def opsi_scheduling(self):
        # 必须在 load_campaign() 前拦截，否则 os_init() 会执行首次自律寻敌。
        if self.is_in_opsi_explore():
            logger.info('[大世界-智能调度] 每月开荒正在运行，初始化前延期智能调度')
            self.config.task_delay(
                server_update=self.config.cross_get(
                    keys='OpsiScheduling.Scheduler.ServerUpdate',
                    default='00:00',
                ),
                task='OpsiScheduling',
            )
            self.config.task_stop()
            return

        self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.run_smart_scheduling())

    def opsi_prevent_action_point_overflow(self):
        campaign = self.load_campaign()
        campaign.os_prevent_action_point_overflow()

    def opsi_cross_month(self):
        try:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_cross_month())
        except ActionPointLimit:
            self._run_opsi_task_with_ap_overflow_guard(lambda campaign: campaign.os_cross_month_end())
