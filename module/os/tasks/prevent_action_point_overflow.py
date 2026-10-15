"""行动力溢出保护模块。

防止大世界行动力（AP）因自然回复而溢出浪费，包括：
- 监控行动力自然回复速率（每 10 分钟回复 1 点，上限 200）
- 当行动力即将达到上限时触发其他大世界任务消耗行动力
- 可配置上下限阈值控制触发时机

继承自 OpsiScheduling，与其他大世界任务协调调度，
确保行动力资源得到充分利用。
"""

from module.config.config import TaskEnd
from module.config.time_source import now as current_time
from module.logger import logger
from module.os.tasks.scheduling import OpsiScheduling
from module.os_handler.action_point import ActionPointLimit
from module.os_handler.action_point_ledger import ACTION_POINT_RECOVER_SECONDS, NATURAL_ACTION_POINT_LIMIT

# 大世界行动力每 10 分钟自然回复 1 点、当前行动力自然上限 200：
# 常量唯一定义在行动力账本模块（action_point_ledger），此处按原名引用保持兼容。


class OpsiPreventActionPointOverflow(OpsiScheduling):
    """防止当前行动力溢出的任务。"""

    CONFIG_PATH_PREVENT_AP_OVERFLOW_TASK = 'OpsiPreventActionPointOverflow.OpsiPreventActionPointOverflow.Task'
    CONFIG_PATH_PREVENT_AP_OVERFLOW_UPPER = 'OpsiPreventActionPointOverflow.OpsiPreventActionPointOverflow.ActionPointUpperbound'
    CONFIG_PATH_PREVENT_AP_OVERFLOW_LOWER = 'OpsiPreventActionPointOverflow.OpsiPreventActionPointOverflow.ActionPointLowerbound'
    TASK_NAME_PREVENT_AP_OVERFLOW = 'OpsiPreventActionPointOverflow'

    # 防溢出读数采纳账本记录的最大年龄。行动力每 10 分钟自然回复 1 点，
    # 30 分钟最多漂移 3 点（换算排程误差 ≤30 分钟）可以接受；更旧的记录
    # 回退现场读弹窗，避免拿过期读数排程。
    PREVENT_AP_OVERFLOW_LEDGER_MAX_AGE_SECONDS = ACTION_POINT_RECOVER_SECONDS * 3

    def is_prevent_action_point_overflow_enabled(self):
        """判断防止行动力溢出任务是否启用。"""
        return self.config.is_task_enabled(self.TASK_NAME_PREVENT_AP_OVERFLOW)

    def _get_prevent_action_point_overflow_thresholds(self):
        """读取防止行动力溢出任务的当前行动力上下界。"""
        upper = self.config.cross_get(
            keys=self.CONFIG_PATH_PREVENT_AP_OVERFLOW_UPPER,
            default=NATURAL_ACTION_POINT_LIMIT,
        )
        lower = self.config.cross_get(
            keys=self.CONFIG_PATH_PREVENT_AP_OVERFLOW_LOWER,
            default=0,
        )
        try:
            upper = int(upper)
        except (TypeError, ValueError):
            upper = NATURAL_ACTION_POINT_LIMIT
        try:
            lower = int(lower)
        except (TypeError, ValueError):
            lower = 0

        upper = max(1, min(NATURAL_ACTION_POINT_LIMIT, upper))
        lower = max(0, min(upper, lower))
        return upper, lower

    def _get_prevent_action_point_overflow_task(self):
        """读取防止行动力溢出任务要代跑的一轮任务。"""
        task = self.config.cross_get(
            keys=self.CONFIG_PATH_PREVENT_AP_OVERFLOW_TASK,
            default=self.TASK_NAME_SCHEDULING,
        )
        if task not in (
            self.TASK_NAME_SCHEDULING,
            self.TASK_NAME_HAZARD1_LEVELING,
            self.TASK_NAME_MEOWFFICER_FARMING,
        ):
            logger.warning(f'[大世界-防止行动力溢出] 防止行动力溢出的执行任务无效: {task}，回退到智能调度')
            task = self.TASK_NAME_SCHEDULING
        return task

    def _read_action_point_from_ledger_for_overflow(self):
        """从行动力账本取防止行动力溢出用的行动力读数。

        账本是记录器，只读最近一次 OCR 真值、不做自然回复推演；
        账本关闭 / 无记录 / 记录过期 / 时间戳缺失时返回 None。

        Returns:
            int | None: 当前行动力；None 表示账本读数不可用。
        """
        if not self._ap_ledger_enabled():
            return None
        try:
            ledger = self._get_ap_ledger()
        except Exception:
            logger.debug('[大世界-防止行动力溢出] 读取行动力账本失败', exc_info=True)
            return None
        if ledger is None or ledger.current is None:
            return None
        recorded_at = ledger.recorded_at
        if recorded_at is None:
            logger.info('[大世界-防止行动力溢出] 账本记录缺少时间戳，按过期处理，改现场读弹窗')
            return None
        try:
            age_seconds = (current_time() - recorded_at).total_seconds()
        except Exception:
            logger.debug('[大世界-防止行动力溢出] 账本记录时间戳解析失败，按过期处理', exc_info=True)
            return None
        if age_seconds > self.PREVENT_AP_OVERFLOW_LEDGER_MAX_AGE_SECONDS:
            logger.info(
                f'[大世界-防止行动力溢出] 账本记录已过期（{age_seconds / 60:.0f} 分钟前），改现场读弹窗'
            )
            return None

        current = int(ledger.current)
        # 与顶栏读数路径（ap_observe_from_map_bar）同一口径同步本轮实例属性，
        # 供日志与后续消费方使用
        self._action_point_current = current
        total_with_box = ledger.total_with_box
        if total_with_box is not None:
            self._action_point_total_with_box = int(total_with_box)
            self._action_point_total = (
                int(total_with_box)
                if getattr(self.config, 'OS_ACTION_POINT_BOX_USE', False)
                else current
            )
        else:
            self._action_point_total = current
        logger.info(
            f'[大世界-防止行动力溢出] 防止行动力溢出检查：当前行动力={current}, '
            f'总行动力={self._action_point_total}（账本记录，{age_seconds / 60:.0f} 分钟前）'
        )
        return current

    def _read_action_point_live_for_overflow(self):
        """现场读行动力弹窗取防止行动力溢出用的读数；读不到有效值返回 None。"""
        self.action_point_enter()
        if not self.action_point_safe_get():
            # 弹窗没出现：说明当前画面不含行动力数据。仍走一次 quit 兜底
            # （极端情况下弹窗其实开着但没识别到），但绝不拿未读到的值排程。
            self.action_point_quit()
            logger.warning('[大世界-防止行动力溢出] 行动力弹窗未出现，本轮读数作废')
            return None
        current = int(getattr(self, '_action_point_current', 0) or 0)
        total = int(getattr(self, '_action_point_total', 0) or 0)
        self.action_point_quit()
        logger.info(f'[大世界-防止行动力溢出] 防止行动力溢出检查：当前行动力={current}, 总行动力={total}')
        return current

    def _get_current_action_point_for_overflow(self):
        """读取当前真实行动力，仅供防止行动力溢出功能使用。

        读取顺序（2026-10-15 事故后调整）：
        1. 行动力账本最近的记录（记录足够新时）——防溢出检查常在任务收尾
           运行，此时游戏可能已被任务切换带离大世界，现场弹窗读不到；
           账本恰好新鲜（任务运行中持续记录顶栏真值），且不会误读整屏；
        2. 账本不可用/过期时现场读弹窗（弹窗没出现则本轮作废）；
        3. 都读不到时返回 None，调用方保持原排程，绝不用垃圾值排程。

        Returns:
            int | None: 当前行动力；None 表示未取得有效读数。
        """
        current = self._read_action_point_from_ledger_for_overflow()
        if current is not None:
            return current
        return self._read_action_point_live_for_overflow()

    def update_prevent_action_point_overflow_schedule(self, current_ap=None):
        """
        按当前真实行动力更新防止行动力溢出任务下次运行时间。

        只写 `NextRun`，**不写 `Scheduler.Enable`**：任务是否启用完全由用户
        决定，避免把用户主动关闭的任务重新启用（2026-10-14 线上自激问题）。

        Args:
            current_ap (int | None): 当前真实行动力。为 None 时优先读行动力账本、
                再现场读弹窗；仍读不到时保持原排程不写入。
        """
        if current_ap is None:
            current_ap = self._get_current_action_point_for_overflow()
        if current_ap is None:
            # 读数失败时保持原排程：2026-10-15 事故中弹窗超时读出的 0/10
            # 被用来排程，把防溢出任务推迟到 31 小时后，保护形同虚设。
            logger.warning('[大世界-防止行动力溢出] 未取得有效行动力读数，保持原排程，不更新下次运行时间')
            return
        try:
            current_ap = int(current_ap)
        except (TypeError, ValueError):
            current_ap = 0
        current_ap = max(0, min(NATURAL_ACTION_POINT_LIMIT, current_ap))

        upper, _ = self._get_prevent_action_point_overflow_thresholds()
        if current_ap >= upper:
            delay_minutes = 1
        else:
            delay_minutes = max(1, (upper - current_ap) * ACTION_POINT_RECOVER_SECONDS / 60)

        logger.info(
            f'按当前行动力更新防止行动力溢出任务：当前={current_ap}, 上限={upper}, '
            f'{delay_minutes:.0f} 分钟后运行'
        )
        self.config.task_delay(
            minute=delay_minutes,
            task=self.TASK_NAME_PREVENT_AP_OVERFLOW,
        )

    def os_prevent_action_point_overflow(self):
        """防止行动力溢出任务入口。"""
        self.run_prevent_action_point_overflow()

    def _run_with_prevent_action_point_overflow_context(self, task_name, func, *args, **kwargs):
        """以防止行动力溢出任务上下文代跑一轮大世界子任务。"""
        previous_context = getattr(self, '_prevent_action_point_overflow_context', None)
        previous_config_context = getattr(self.config, '_prevent_action_point_overflow_context', None)
        self._prevent_action_point_overflow_context = True
        self.config._prevent_action_point_overflow_context = True
        try:
            return self._run_with_opsi_task_context(task_name, func, *args, **kwargs)
        finally:
            if previous_context is None:
                if hasattr(self, '_prevent_action_point_overflow_context'):
                    delattr(self, '_prevent_action_point_overflow_context')
            else:
                self._prevent_action_point_overflow_context = previous_context

            if previous_config_context is None:
                if hasattr(self.config, '_prevent_action_point_overflow_context'):
                    delattr(self.config, '_prevent_action_point_overflow_context')
            else:
                self.config._prevent_action_point_overflow_context = previous_config_context

    def _run_scheduled_coin_task_once(self, task_name, ap_preserve, ensure_budget=False):
        """由防止行动力溢出上下文直接执行一轮补黄币任务。"""
        if self.is_running_prevent_action_point_overflow_task():
            # 防止行动力溢出以消耗行动力为目标，强制不做预算补充，避免反向回补行动力
            ensure_budget = False
            logger.info(f'[大世界-防止行动力溢出] 直接执行一轮{self.TASK_NAMES.get(task_name, task_name)}')
        return super()._run_scheduled_coin_task_once(task_name, ap_preserve, ensure_budget=ensure_budget)

    def _run_prevent_action_point_overflow_target_once(self, task_name, lowerbound):
        """按配置执行一轮防止行动力溢出目标任务。"""
        with self.config.temporary(OS_ACTION_POINT_BOX_USE=False), \
                self._suspended_buy_action_point_limit():
            if task_name == self.TASK_NAME_SCHEDULING:
                self._run_with_prevent_action_point_overflow_context(
                    self.TASK_NAME_SCHEDULING,
                    self.run_smart_scheduling_once,
                )
            elif task_name == self.TASK_NAME_HAZARD1_LEVELING:
                self._run_with_prevent_action_point_overflow_context(
                    self.TASK_NAME_HAZARD1_LEVELING,
                    self.run_hazard1_leveling_once,
                    ap_preserve=lowerbound,
                )
            elif task_name == self.TASK_NAME_MEOWFFICER_FARMING:
                self._run_with_prevent_action_point_overflow_context(
                    self.TASK_NAME_MEOWFFICER_FARMING,
                    self.run_meowfficer_farming_once,
                    ap_preserve=lowerbound,
                )
            else:
                raise ValueError(f'未知防止行动力溢出目标任务: {task_name}')

    def run_prevent_action_point_overflow(self):
        """防止当前行动力溢出。"""
        logger.hr('大世界-防止行动力溢出', level=1)
        upperbound, lowerbound = self._get_prevent_action_point_overflow_thresholds()
        task_name = self._get_prevent_action_point_overflow_task()
        logger.attr('行动力上限', upperbound)
        logger.attr('行动力下限', lowerbound)
        logger.attr('溢出任务', task_name)

        started = False
        while True:
            current_ap = self._get_current_action_point_for_overflow()
            if current_ap is None:
                logger.warning('[大世界-防止行动力溢出] 未取得有效行动力读数，延迟到服务器刷新后重试')
                self.config.task_delay(server_update=True)
                self.config.task_stop()
            if not started:
                if current_ap < upperbound:
                    logger.info(f'[大世界-防止行动力溢出] 当前行动力未达到上限 ({current_ap} < {upperbound})，更新下次运行时间')
                    self.update_prevent_action_point_overflow_schedule(current_ap=current_ap)
                    self.config.task_stop()
            elif current_ap < lowerbound:
                logger.info(f'[大世界-防止行动力溢出] 当前行动力已低于下限 ({current_ap} < {lowerbound})，停止防止行动力溢出任务')
                self.update_prevent_action_point_overflow_schedule(current_ap=current_ap)
                self.config.task_stop()

            started = True
            try:
                self._run_prevent_action_point_overflow_target_once(task_name, lowerbound)
            except ActionPointLimit as e:
                current = getattr(e, 'current', None)
                if current is None:
                    current = current_ap
                logger.info(f'[大世界-防止行动力溢出] 当前行动力无法进入目标海域，停止防止行动力溢出任务: current={current}, error={e}')
                self.update_prevent_action_point_overflow_schedule(current_ap=current)
                self.config.task_stop()
            except TaskEnd:
                delay_request = getattr(self, self.RUNTIME_ATTR_PREVENT_OVERFLOW_DELAY, None)
                if hasattr(self, self.RUNTIME_ATTR_PREVENT_OVERFLOW_DELAY):
                    delattr(self, self.RUNTIME_ATTR_PREVENT_OVERFLOW_DELAY)
                if delay_request is not None:
                    args, kwargs = delay_request
                    logger.info('[大世界-防止行动力溢出] 应用代理子任务请求的延迟时间')
                    self.config.task_delay(
                        *args,
                        task=self.TASK_NAME_PREVENT_AP_OVERFLOW,
                        **kwargs,
                    )
                    raise
                try:
                    refreshed = self._get_current_action_point_for_overflow()
                except Exception:
                    refreshed = None
                    logger.debug('[大世界-防止行动力溢出] 防止行动力溢出任务结束后刷新当前行动力失败，使用运行前数值', exc_info=True)
                if refreshed is not None:
                    current_ap = refreshed
                self.update_prevent_action_point_overflow_schedule(current_ap=current_ap)
                raise
