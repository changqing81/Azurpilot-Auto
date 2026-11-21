"""自动搜索战斗管理器。

管理通关模式（快进模式）下的自动搜索战斗流程。

在通关模式下，游戏会自动进行地图探索和战斗。
此模块负责：
- 启动自动搜索（地图中的出击按钮）
- 等待自动搜索完成（检测回到关卡页面）
- 处理战斗期间的异常（退役、低情绪、撤退等）
- 检测停止条件（石油/物资限制、通关次数等）
- Boss 战后的关卡推进

继承自 MapOperation + Combat + CampaignStatus，
组合了地图操作、战斗系统和战役状态追踪的能力。
"""

from module.base.timer import Timer
from module.campaign.campaign_status import CampaignStatus
from module.combat.assets import *
from module.combat.combat import Combat
from module.combat.auto_search_recovery import AutoSearchRecovery
from module.exception import CampaignEnd, GameStuckError, ScriptEnd
from module.handler.assets import AUTO_SEARCH_MAP_OPTION_ON, GET_MISSION
from module.logger import logger
from module.map.assets import WITHDRAW, SWITCH_OVER, FLEET_WITHDRAW, FLEET_SWITCH_CONFIRM, FLEET_WITHDRAW_BOSS
from module.map.map_operation import MapOperation


class AutoSearchCombat(AutoSearchRecovery, MapOperation, Combat, CampaignStatus):
    """自动搜索战斗执行器。

    在通关模式下编排自动搜索战斗流程，处理各种战斗异常和停止条件。

    Attributes:
        _auto_search_in_stage_timer (Timer): 关卡页面检测计时器。
        _auto_search_status_confirm (bool): 低心情战斗确认标志。
            置位后战斗结算走低心情分支，处理获得道具等额外结算界面；
            在自动搜索运行恢复或处理地图选项时重置。
        _withdraw (bool): 是否已执行撤退。
        _defeat_count (int): 战败次数。
        _shipwreck_emotion_reduced (bool): 沉船心情扣减是否已执行，防止重复扣减。
        _auto_search_emotion_reduce (bool): 当前战斗是否启用心情扣减。
        _auto_search_fleet_index (int): 当前战斗的舰队索引。
        auto_search_oil_limit_triggered (bool): 石油限制是否已触发。
        auto_search_coin_limit_triggered (bool): 物资限制是否已触发。
    """
    _auto_search_in_stage_timer = Timer(3, count=6)
    # 自律停滞唤醒：图标显示运行中但舰队长时间不动时，点一次自律开关把它唤回来。
    # 阈值依据 2026-11-20 日志中 201 次「自动搜索移动中」的实测分布：
    # 从进入移动阶段到出现加载条/战斗 p99 = 5.9s、最大 5.9s，段内最长无点击
    # 同样不超过 5.9s，也就是正常流程从不出现长时间无操作。取 120 秒是 20 倍余量。
    auto_search_stall_seconds = 120
    # 唤醒后的复查窗口。必须短于首判，否则「120 + 120 = 240 秒」反而比设备层
    # 195 秒长等待判死更慢；60 秒保证最坏 180 秒交回重启链路。
    auto_search_stall_retry_seconds = 60
    # 每个移动阶段最多唤醒一次：点一次仍不动说明不是同一类故障，
    # 继续点只会刷点击记录、拖长无进展时间。
    auto_search_stall_max_wake = 1
    # 停滞检测的运行时状态（auto_search_moving 入口由 _auto_search_stall_reset 重置）
    _auto_search_stall_clicks = 0
    _auto_search_stall_wake = 0
    _auto_search_stall_timer = None
    _auto_search_status_confirm = False
    _withdraw = False
    _defeat_count = 0
    _shipwreck_emotion_reduced = False
    _auto_search_emotion_reduce = False
    _auto_search_fleet_index = 1
    auto_search_oil_limit_triggered = False
    auto_search_coin_limit_triggered = False

    def _handle_auto_search_menu_missing(self):
        """
        Sometimes game is bugged, auto search menu is not shown.
        After BOSS battle, it enters campaign directly.
        To handle this, if game in campaign for a certain time, it means auto search ends.

        Returns:
            bool: If triggered
        """
        if self.is_in_stage():
            if self._auto_search_in_stage_timer.reached():
                logger.info('捕获自动搜索菜单缺失')
                return True
        else:
            self._auto_search_in_stage_timer.reset()

        return False

    def map_offensive_auto_search(self, skip_first_screenshot=True):
        """
        Pages:
            in: in_map, MAP_OFFENSIVE
            out: is_combat_loading
        """
        self.interval_reset(AUTO_SEARCH_MAP_OPTION_ON)
        # 清空上一流程残留的 OFF 确认，确保本流程内重新积累
        self._auto_search_off_frame = None
        for _ in self.loop():

            if self.handle_auto_search_map_option():
                self.interval_reset(AUTO_SEARCH_MAP_OPTION_ON)
                continue
            # To handle a bug in Azur Lane game client.
            # Auto search icon shows it's running but it's doing nothing
            # when Alas exited from retirement and turned it on immediately.
            # Monkey clicker, disable auto search every 3s, beginning not included
            if not self.__dict__.get('_multiple_sorties_stop_requested', False) \
                    and not self.__dict__.get('_restart_resume_guard', False) \
                    and self.appear(AUTO_SEARCH_MAP_OPTION_ON, offset=self._auto_search_offset, interval=3) \
                    and self.appear_then_click(AUTO_SEARCH_MAP_OPTION_ON):
                continue
            if self.handle_combat_low_emotion():
                # 退役后重新出击的低心情战斗，结算同样会包含获得道具界面
                self._auto_search_status_confirm = True
                continue
            if self.handle_retirement():
                continue

            # Break
            if self.is_combat_loading():
                break

    def auto_search_watch_fleet(self, checked=False):
        """
        Watch fleet index and ship level.

        Args:
            checked (bool): Watchers are only executed or logged once during fleet moving.
                            Set True to skip executing again.

        Returns:
            bool: If executed.
        """
        prev = self.fleet_current_index
        self.get_fleet_show_index()
        self.get_fleet_current_index()
        if self.fleet_current_index == prev:
            # Same as current, only print once
            if not checked:
                logger.info(f'[自动搜索-舰队] 舰队: {self.fleet_show_index}, 当前舰队索引: {self.fleet_current_index}')
                checked = True
                self.lv_get(after_battle=True)
        else:
            # Fleet changed
            logger.info(f'[自动搜索-舰队] 舰队: {self.fleet_show_index}, 当前舰队索引: {self.fleet_current_index}')
            checked = True
            self.lv_get(after_battle=False)

        return checked

    def auto_search_watch_oil(self, checked=False):
        """
        Watch oil.
        This will set auto_search_oil_limit_triggered.
        """
        if not checked:
            oil = self.get_oil()
            if oil == 0:
                logger.warning('未找到石油')
            else:
                if oil < max(500, self.config.StopCondition_OilLimit):
                    logger.info('达到石油上限')
                    self.auto_search_oil_limit_triggered = True
                else:
                    if self.auto_search_oil_limit_triggered:
                        logger.warning('[自动搜索-石油] 石油限制已触发但石油已恢复，'
                                       '可能是因为之前的OCR结果错误')
                    self.auto_search_oil_limit_triggered = False
                checked = True

        return checked

    def auto_search_watch_coin(self, checked=False):
        """
        Watch coin.
        This will set auto_search_coin_limit_triggered.
        """
        if not checked:
            limit = self.config.TaskBalancer_CoinLimit
            coin = self.get_coin()
            if coin == 0:
                logger.warning('未找到物资')
            else:
                if self.is_balancer_task():
                    if coin < limit:
                        logger.info('达到物资上限')
                        self.auto_search_coin_limit_triggered = True
                    else:
                        # Enough coin
                        self.auto_search_coin_limit_triggered = False
                else:
                    if self.auto_search_coin_limit_triggered:
                        logger.warning('auto_search_coin_limit_triggered but coin recovered, '
                                       'probably because of wrong OCR result before')
                    self.auto_search_coin_limit_triggered = False
                checked = True

        return checked

    def _wait_until_in_map(self, skip_first_screenshot=True):
        """
        To handle a bug in Azur Lane game client.
        Auto search icon shows it's running but it's doing nothing
        when Alas exited from retirement and turned it on immediately.

        Pages:
            in: Exiting from retirement or enhancement
            out: in_map()
        """
        timeout = Timer(3, count=6).start()
        for _ in self.loop():

            if self.is_in_map():
                break
            if timeout.reached():
                logger.warning('[自动搜索-地图] 等待退役后进入地图超时，假设已在地图中')
                break

    def _auto_search_stall_reset(self):
        """进入新的移动阶段时复位停滞检测。

        每场战斗开始前都会重新进入 auto_search_moving，计数必须在入口归零；
        否则上一场的点击计数会被带进来，第一帧就误判为停滞。
        """
        self._auto_search_stall_clicks = getattr(self.device, 'click_count', 0)
        self._auto_search_stall_timer = Timer(self.auto_search_stall_seconds).start()
        self._auto_search_stall_wake = 0

    def _auto_search_stall_detected(self):
        """判定自律是否已停滞：图标在跑、但既没进战斗也没动过。

        三个条件必须同时成立：
        1. 距离上一次真正点击/滑动超过阈值（读 device.click_count，滑动也算）；
        2. 自律图标仍显示为运行中（颜色 + 模板双重确认，避免误判）；
        3. 当前没有战斗加载、战斗执行、结算菜单这些"正常推进中"的画面。

        守护模式（DaemonBase 把 _combat_status_timeout 置 None）直接跳过：
        用户手动暂停时不该被脚本抢着点。

        Returns:
            bool: 停滞需要唤醒返回 True。
        """
        if self._combat_status_timeout is None:
            return False
        if self._auto_search_stall_timer is None:
            # 未经过 auto_search_moving 入口（如外部直接复用了检测方法），无计时器可用
            return False
        # 先做纯内存判断：正常流程每帧只会走完这三步，不额外增加画面识别开销。
        # 昂贵的历史判断（战斗加载 / 战斗执行 / 结算菜单 / 自律图标）只在计时器
        # 真的到期之后才执行，避免每帧重复调用十几个模板匹配。
        current = getattr(self.device, 'click_count', 0)
        if current != self._auto_search_stall_clicks:
            # 有任何点击/滑动都会让 click_count 自增，等价于"游戏被操作过"
            self._auto_search_stall_clicks = current
            self._auto_search_stall_timer.reset()
            return False
        if not self._auto_search_stall_timer.reached():
            return False

        # 计时器到期后确认画面：推进中的画面不是停滞。
        # 这里必须重置计时器：否则到期状态会保持，下一帧又跑一轮十几个模板匹配，
        # 在"确实在推进但推进很慢"的画面（如慢速加载）上变成每帧重复识别。
        if self.is_combat_loading() or self.is_combat_executing() \
                or self.is_in_auto_search_menu():
            self._auto_search_stall_timer.reset()
            return False

        if self._auto_search_stall_wake >= self.auto_search_stall_max_wake:
            # 已经关过一次自律，复查窗口内仍然没有动静：不是同一类故障，
            # 继续点只会刷点击记录。主动报错交回重启链路，比干等设备层 195 秒
            # 更早、且日志能直接说明是自律没响应而不是画面卡死。
            logger.warning(
                f'[自动搜索-停滞] 关闭自律后 {self.auto_search_stall_retry_seconds}s 仍未恢复移动，'
                '判定游戏侧自律失效')
            raise GameStuckError('[自动搜索-停滞] 自律图标运行中但舰队长时间不动，唤醒无效')

        running = self.match_template_color(
            AUTO_SEARCH_MAP_OPTION_ON, offset=self._auto_search_offset)
        if not running:
            # 图标不在（已进入战斗或已停），同样重置计时器避免逐帧重复识别
            self._auto_search_stall_timer.reset()
        return running

    def _auto_search_stall_wake_up(self):
        """点一次自律开关把它关掉，交回地图循环唤醒游戏。

        只点一次关闭、不在这里连点开启：下一帧由 handle_auto_search_map_option
        的两帧确认逻辑重新点亮，既避免连点触发点击洪水检测，也复用已有的
        "确认 OFF 后才点击"保护。

        Returns:
            bool: 已发出唤醒点击返回 True。
        """
        logger.warning(
            f'[自动搜索-停滞] 自律图标显示运行中但超过 {self.auto_search_stall_seconds}s 无任何操作，'
            f'尝试关闭自律唤醒（第 {self._auto_search_stall_wake + 1}/{self.auto_search_stall_max_wake} 次）')
        self.device.click(AUTO_SEARCH_MAP_OPTION_ON)
        self.interval_reset(AUTO_SEARCH_MAP_OPTION_ON)
        # 计数与计时必须在点击之后记录：device.click 自身会让 click_count 自增，
        # 先记录的话下一帧会把它当成"又操作过"，复查窗口被无限重置，永远等不到报错。
        self._auto_search_stall_wake += 1
        self._auto_search_stall_timer = Timer(self.auto_search_stall_retry_seconds).start()
        self._auto_search_stall_clicks = getattr(self.device, 'click_count', 0)
        return True

    def auto_search_moving(self, skip_first_screenshot=True):
        """
        Pages:
            in: map
            out: is_combat_loading()
        """
        logger.info('自动搜索移动中')
        self.device.stuck_record_clear()
        # 清空上一流程残留的 OFF 确认，确保本流程内重新积累
        self._auto_search_off_frame = None
        self._auto_search_stall_reset()
        checked_fleet = False
        checked_oil = False
        checked_coin = False
        for _ in self.loop():

            if self.is_auto_search_running():
                checked_fleet = self.auto_search_watch_fleet(checked_fleet)
                if not checked_oil or not checked_coin:
                    checked_oil = self.auto_search_watch_oil(checked_oil)
                    checked_coin = self.auto_search_watch_coin(checked_coin)
            # 连续作战：原生批次结算出现时整批结束，由外层关闭结算界面
            if (self.__dict__.get('_multiple_sorties_requested', 0)
                    and self.multiple_sorties_result_appear()):
                raise CampaignEnd
            if self.handle_multiple_sorties_stop():
                continue
            if self.handle_retirement():
                self.map_offensive_auto_search()
                # Map offensive ends at is_combat_loading
                break
            if self.handle_auto_search_map_option():
                continue
            if self.handle_combat_low_emotion():
                self._auto_search_status_confirm = True
                continue
            if self.handle_story_skip():
                continue
            if self.handle_map_cat_attack():
                continue
            if self.handle_vote_popup():
                continue
            # 停滞唤醒：图标在跑但舰队长时间不动（游戏客户端 bug / 网络掉线后
            # 自律指令被吞），点一次开关让游戏重新下发移动指令。
            if self._auto_search_stall_detected():
                self._auto_search_stall_wake_up()
                continue

            # End
            if self.is_combat_loading():
                break
            if self.is_combat_executing():
                logger.info('[自动搜索-战斗] 战斗执行中')
                break
            if self.is_in_auto_search_menu() or self._handle_auto_search_menu_missing():
                raise CampaignEnd

    def auto_search_combat_execute(self, emotion_reduce, fleet_index, battle=None, expected_end=None):
        """
        Args:
            emotion_reduce (bool):
            fleet_index (int):
            expected_end (callable):

        Pages:
            in: is_combat_loading()
            out: combat status
        """
        logger.info('自动搜索战斗加载中')
        self.device.stuck_record_clear()
        self.device.click_record_clear()
        self.device.screenshot_interval_set('combat')
        # 清空上一流程残留的 OFF 确认，确保本流程内重新积累
        self._auto_search_off_frame = None
        for _ in self.loop():

            if self.handle_combat_automation_confirm():
                continue
            if self.handle_story_skip():
                continue
            if self.handle_vote_popup():
                continue
            # 加载期间出现的红脸（低心情）弹窗同样没有任何其他处理器会认，
            # 不处理会一直等到 GameStuckError。ignore 模式下点「确定」继续出击
            if self.handle_combat_low_emotion():
                # 确认后游戏直接进入战斗（不回地图），结算会包含获得道具界面
                self._auto_search_status_confirm = True
                continue
            # 过图期间弹出的「船坞已满」弹窗没有任何其他处理器会认，
            # 不处理会一直等到 GameStuckError。处理完退役/强化后重新开启
            # 自动搜索，等待游戏自己再次进入战斗加载
            if self.handle_retirement():
                # 退役流程在地图上进行，先恢复普通截图间隔
                self.device.screenshot_interval_set()
                self.map_offensive_auto_search()
                self.device.screenshot_interval_set('combat')
                continue

            # End
            if self.is_in_auto_search_menu() or self._handle_auto_search_menu_missing():
                raise CampaignEnd
            pause = self.is_combat_executing()
            if pause:
                logger.attr('战斗UI', pause)
                break

        logger.info('[自动搜索-战斗] 战斗执行')
        self._restart_resume_guard = False
        self.submarine_call_reset()
        submarine_mode = 'do_not_use'
        if self.config.Submarine_Fleet:
            submarine_mode = self.config.Submarine_Mode
        force_call = battle[0] == battle[1] - 1 if battle is not None else False
        self.combat_auto_reset()
        self.combat_manual_reset()
        self.device.stuck_record_clear()
        self.device.click_record_clear()
        if emotion_reduce:
            self.emotion.reduce(fleet_index)
        auto = self.config.Fleet_Fleet1Mode if fleet_index == 1 else self.config.Fleet_Fleet2Mode

        confirm_timer = Timer(10)
        confirm_timer.start()
        while 1:
            self.device.screenshot()

            if self.handle_submarine_call(submarine_mode, call=force_call):
                continue
            if self.handle_combat_auto(auto):
                continue
            if self.handle_combat_manual(auto):
                continue
            if auto != 'combat_auto' and self.auto_mode_checked and self.is_combat_executing():
                if self.handle_combat_weapon_release():
                    continue
            # bunch of popup handlers
            if self.handle_popup_confirm('AUTO_SEARCH_COMBAT_EXECUTE'):
                continue
            if not self._withdraw and self.handle_urgent_commission():
                continue
            if self.handle_story_skip():
                continue
            if self.handle_guild_popup_cancel():
                continue
            if self.handle_vote_popup():
                continue
            if self.handle_mission_popup_ack():
                continue

            # End
            if self.is_in_auto_search_menu() or self._handle_auto_search_menu_missing():
                self.device.screenshot_interval_set()
                raise CampaignEnd
            if self.is_combat_executing():
                confirm_timer.reset()
                continue
            if self.handle_get_ship():
                continue
            if self.appear_then_click(OPTS_INFO_D, offset=(30, 30), interval=2):
                if emotion_reduce and not self._shipwreck_emotion_reduced:
                    self.emotion.reduce(fleet_index, shipwreck=True)
                    self._shipwreck_emotion_reduced = True
                self._withdraw = True
                break
            # D评价结算界面（BATTLE_STATUS_D / EXP_INFO_D）
            # S/A/B评价的动画过渡帧可能短暂误匹配D评价模板，
            # 但只有真正的沉船才会出现OPTS_INFO_D弹窗。
            # 此处不设置 _withdraw，让后续S/A/B评价条件覆盖误匹配。
            # 真正的D评价会先被上方OPTS_INFO_D捕获。
            if self.appear(BATTLE_STATUS_D) or self.appear(EXP_INFO_D):
                break
            if confirm_timer.reached():
                # 结算确认超时：不扣心情、不盲目点击OPTS_INFO_D
                # 只设置_withdraw让status处理，status中检测到OPTS_INFO_D才扣心情
                logger.warning('[自动搜索-战斗] 结算确认超时，进入status处理')
                self._withdraw = True
                confirm_timer.reset()
                break
            if self.appear(BATTLE_STATUS_A) or self.appear(BATTLE_STATUS_B) \
                    or self.appear(EXP_INFO_A) or self.appear(EXP_INFO_B):
                if emotion_reduce:
                    self.emotion.reduce(fleet_index, shipwreck=True)
                break
            if self.appear(BATTLE_STATUS_S) or self.appear(EXP_INFO_S) \
                    or self.appear(GET_MISSION) or self.is_auto_search_running():
                self.device.screenshot_interval_set()
                break
            if callable(expected_end):
                if expected_end():
                    self.device.screenshot_interval_set()
                    break
            

    def _wait_withdraw_stable(self, withdraw_stable_timer):
        """
        等待WITHDRAW按钮稳定出现，防止界面过渡动画导致误判。

        Args:
            withdraw_stable_timer (Timer): WITHDRAW按钮稳定计时器

        Returns:
            bool: True表示WITHDRAW按钮已稳定出现，可以点击；
                  False表示按钮尚未出现或不稳定，需要继续等待。
        """
        withdraw_appear = self.appear(WITHDRAW, offset=(30, 30))
        if withdraw_appear:
            if not withdraw_stable_timer.reached():
                return False
            return True
        else:
            withdraw_stable_timer.reset()
            return False

    def _handle_fleet_switch_over(self):
        """
        处理舰队切换操作：仅撤退当前战败舰队，切换到另一队继续战斗。
        包含超时保护，避免UI异常时无限循环。

        Returns:
            bool: True表示切换成功，False表示超时。
        """
        timeout = Timer(10, count=20).start()
        while 1:
            self.device.screenshot()
            if self.appear_then_click(FLEET_WITHDRAW, offset=(30, 30)):
                break
            if self.appear(FLEET_WITHDRAW_BOSS, offset=(30, 30)):
                self.withdraw()
                break
            if self.appear_then_click(SWITCH_OVER, interval=2):
                continue
            if timeout.reached():
                logger.warning('舰队切换超时，改为撤退')
                self.withdraw()
                break
        self.fleet_alive_multiple = False
        return True

    def auto_search_combat_status(self):
        """
        Pages:
            in: any
            out: is_auto_search_running()
        """
        logger.info('[自动搜索-结算] 战斗结算')
        # 清空上一流程残留的 OFF 确认，确保本流程内重新积累
        self._auto_search_off_frame = None
        self.device.stuck_record_clear()
        self.device.click_record_clear()
        exp_info = False  # This is for the white screen bug in game
        withdraw_stable_timer = Timer(2)
        # 结算阶段整体超时：与 combat_status 的阶段超时对称。
        # 结算点击会反复重置设备层计时器，动画循环卡死需在此兜底。
        # 守护模式（DaemonBase）下 _combat_status_timeout 为 None，禁用超时。
        combat_status_timer = None
        if self._combat_status_timeout is not None:
            combat_status_timer = Timer(self._combat_status_timeout, count=0)

        for _ in self.loop():
            if combat_status_timer is not None:
                combat_status_timer.start()
                if combat_status_timer.reached():
                    logger.warning(f'[自动搜索-结算] 结算阶段超过 {combat_status_timer.limit}s 未完成，判定卡死')
                    raise GameStuckError('[自动搜索-结算] 结算阶段超时')

            # End
            if self.is_auto_search_running():
                self._auto_search_status_confirm = False
                # 战斗正常结束（非战败），重置连续战败计数
                if self._defeat_count > 0:
                    logger.info('战斗胜利，重置失败计数')
                    self._defeat_count = 0
                break
            if self.is_in_auto_search_menu() or self._handle_auto_search_menu_missing():
                raise CampaignEnd

            # Withdraw
            if self._withdraw:
                # 先处理战斗结算界面（D评价、经验信息、获得舰船等），
                # 结算完成后才会出现FLEET_SWITCH_CONFIRM或WITHDRAW按钮
                # 沉船D评价流程：OPTS_INFO_D → BATTLE_STATUS_D → EXP_INFO_D → OPTS_INFO_D(再次出现) → FLEET_SWITCH_CONFIRM
                if self.appear_then_click(OPTS_INFO_D, offset=(30, 30), interval=2):
                    continue
                if self.handle_battle_status():
                    continue
                if self.handle_exp_info():
                    continue
                if self.handle_get_ship():
                    continue
                if self.handle_get_items():
                    continue
                if self.handle_popup_confirm('combat_status'):
                    continue

                defeat_withdraw = self.config.Campaign_DefeatWithdraw
                if defeat_withdraw == 'withdraw_continue' or defeat_withdraw == 'withdraw_stop':
                    # 撤退后继续任务 / 撤退后关闭任务：
                    # 点击FLEET_SWITCH_CONFIRM仅关闭弹窗，不取消撤退
                    # 游戏在舰队战败后弹出FLEET_SWITCH_CONFIRM，点击后才能看到WITHDRAW按钮
                    if self.appear_then_click(FLEET_SWITCH_CONFIRM, offset=(30, 30)):
                        continue
                    if self.handle_popup_confirm('WITHDRAW'):
                        continue
                    if not self._wait_withdraw_stable(withdraw_stable_timer):
                        continue
                    self._withdraw = False
                    if defeat_withdraw == 'withdraw_stop':
                        # 撤退后关闭任务：连续3次战败才关闭任务
                        self._defeat_count += 1
                        logger.attr('战败计数', f'{self._defeat_count}/3')
                        if self._defeat_count >= 3:
                            # 连续3次战败，关闭任务
                            # withdraw()内部抛出CampaignEnd，
                            # 需要捕获后转换为ScriptEnd以终止任务
                            try:
                                self.withdraw()
                            except CampaignEnd:
                                raise ScriptEnd('DefeatWithdraw=withdraw_stop')
                        else:
                            # 未满3次，撤退后继续任务
                            self.withdraw()
                            break
                    else:
                        self.withdraw()
                    break
                elif defeat_withdraw == 'switch_fleet':
                    # 切换队伍继续出击：尝试切换另一队继续战斗
                    if self.appear_then_click(FLEET_SWITCH_CONFIRM, offset=(30, 30)):
                        self.fleet_alive_multiple = False
                        self._withdraw = False
                        continue
                    if not self._wait_withdraw_stable(withdraw_stable_timer):
                        continue

                    self._withdraw = False
                    if not self.fleet_alive_multiple:
                        self.withdraw()
                        break
                    else:
                        self._handle_fleet_switch_over()
                        continue

            # Combat status
            if self.handle_get_ship():
                continue
            if not self._withdraw and self.handle_auto_search_map_option():
                self._auto_search_status_confirm = False
                continue
            # bunch of popup handlers
            if self.handle_popup_confirm('AUTO_SEARCH_COMBAT_STATUS'):
                # 中途低心情弹窗：确认后游戏直接进入下一场战斗（不回地图），
                # 下一场战斗的结算会包含获得道具界面，需置位标志让低心情分支处理
                self._auto_search_status_confirm = True
                continue
            if self.handle_urgent_commission():
                continue
            # 连续作战：周回中检测到停止请求时关自律，由游戏结算当前批次
            if self.handle_multiple_sorties_stop():
                continue
            if self.handle_story_skip():
                continue
            if self.handle_guild_popup_cancel():
                continue
            if self.handle_vote_popup():
                continue
            if self.handle_mission_popup_ack():
                continue
            # 点「退出战斗」后游戏会弹「信息 INFORMATION / 知道了」说明弹窗。
            # 这条是自动搜索的结算循环，与 interrupt_auto_search 一样没有外层
            # 战斗循环兜底，不处理会空转到 GameStuckError（同 2026-11-11 事故机制）。
            if self.handle_combat_automation_confirm():
                continue

            # 兜底点击获得道具结算界面：低心情弹窗可能在地图选项之前被确认
            # （如战斗结算中途中确认、退役后重出击），此时标志位可能未置位，
            # 若不点击会在获得道具界面卡死（GameStuckError）
            if self.handle_get_items():
                continue
            # 处理战斗结算界面——SABC评价在自动搜索中可能快速自动过渡，
            # 若截图恰好捕获到结算画面则点击推进并记录评价
            # D评价点击BATTLE_STATUS_D后，会出现OPTS_INFO_D沉船弹窗
            if self.handle_battle_status():
                continue
            if self.handle_exp_info():
                continue
            # 检测D评价（沉船）弹窗——这是沉船的确认性标志（二次确认）
            # 只有OPTS_INFO_D出现才确认是真正的D评价并扣心情
            # S/A/B/C转场误匹配BATTLE_STATUS_D不会出现OPTS_INFO_D，不会扣心情
            if self.appear(OPTS_INFO_D, offset=(30, 30)):
                logger.info('[自动搜索-结算] 检测到沉船弹窗，进入撤退处理')
                if self._auto_search_emotion_reduce and not self._shipwreck_emotion_reduced:
                    self.emotion.reduce(self._auto_search_fleet_index, shipwreck=True)
                    self._shipwreck_emotion_reduced = True
                self._withdraw = True
                continue

            # Handle low emotion combat
            # Combat status
            if self._auto_search_status_confirm:
                if not exp_info and self.handle_get_ship():
                    continue
                if self.handle_get_items():
                    continue
                if self.handle_battle_status():
                    continue
                if self.handle_popup_confirm('combat_status'):
                    continue
                if self.handle_exp_info():
                    exp_info = True
                    continue

    def auto_search_combat(self, emotion_reduce=None, fleet_index=1, battle=None):
        """
        Execute a combat.

        Note that fleet index == 1 is mob fleet, 2 is boss fleet.
        It's not the fleet index in fleet preparation or auto search setting.
        """
        emotion_reduce = emotion_reduce if emotion_reduce is not None else self.emotion.is_calculate

        self._auto_search_emotion_reduce = emotion_reduce
        self._auto_search_fleet_index = fleet_index
        self._shipwreck_emotion_reduced = False
        self.auto_search_combat_execute(emotion_reduce=emotion_reduce, fleet_index=fleet_index, battle=battle)
        self.auto_search_combat_status()

        logger.info('[自动搜索-战斗] 战斗结束')
