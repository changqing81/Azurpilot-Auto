"""地图续接的识别和点击保护；来源：3e9407a7、d9acf237，不改变本地心情流程。"""

from module.base.timer import Timer
from module.exception import CampaignEnd, GameStuckError
from module.handler.assets import AUTO_SEARCH_MAP_OPTION_OFF, AUTO_SEARCH_MAP_OPTION_ON
from module.logger import logger
from module.map.assets import WITHDRAW


class AutoSearchRecovery:
    """只恢复已确认的地图；战斗与结算继续使用原有入口。"""

    def auto_search_recovery_state(self):
        """识别重启后的原地图，加载和战斗画面不能作为地图凭据。"""
        if self.is_combat_loading() or self.is_combat_executing():
            return None
        running = self.match_template_color(
            AUTO_SEARCH_MAP_OPTION_ON, offset=self._auto_search_offset, threshold=10)
        if (running or self.is_in_map()) and self.appear(WITHDRAW, offset=(30, 30)):
            return 'map'
        return None

    def prepare_auto_search_recovery(self, state):
        """记录地图恢复起点，之后通过截图循环等待两秒再开自律。"""
        if state != 'map':
            return False
        self._withdraw = False
        self._auto_search_status_confirm = False
        self._restart_resume_timer = Timer(30, count=60).start()
        self._restart_resume_click_timer = Timer(2).start()
        self._restart_resume_guard = True
        self._auto_search_in_stage_timer.reset()
        logger.info('[战役续接] 已确认原地图，等待界面稳定后恢复自律')
        return True

    def handle_auto_search_map_option(self):
        """续接时使用颜色校验开关，并在加载或战斗画面禁止点击。"""
        if not self.__dict__.get('_restart_resume_guard', False):
            return super().handle_auto_search_map_option()
        if self._restart_resume_timer.reached():
            raise GameStuckError('[战役续接] 自律寻敌未在期限内进入战斗')
        if self.is_combat_loading() or self.is_combat_executing():
            return False
        if self.match_template_color(AUTO_SEARCH_MAP_OPTION_ON,
                                     offset=self._auto_search_offset, threshold=10):
            return False
        if not self._restart_resume_click_timer.reached():
            return False
        if self.match_template_color(AUTO_SEARCH_MAP_OPTION_OFF,
                                     offset=self._auto_search_offset, threshold=10):
            return super().handle_auto_search_map_option()
        return False

    def withdraw_after_restart(self):
        """停止条件已触发时先关闭自律，再确认撤退，不额外开启新战斗。

        Pages:
            in: 已恢复的原地图
            out: 选关页
        """
        timeout = Timer(30, count=60).start()
        for _ in self.loop():
            if self.is_in_stage():
                raise CampaignEnd('Withdraw')
            if timeout.reached():
                raise GameStuckError('[战役续接] 停止条件触发后撤退超时')
            if self.handle_popup_confirm('WITHDRAW'):
                continue
            if self.match_template_color(AUTO_SEARCH_MAP_OPTION_ON,
                                         offset=self._auto_search_offset, threshold=10):
                if self.appear_then_click(AUTO_SEARCH_MAP_OPTION_ON,
                                          offset=self._auto_search_offset, interval=2):
                    continue
            if self.match_template_color(AUTO_SEARCH_MAP_OPTION_OFF,
                                         offset=self._auto_search_offset, threshold=10):
                if self.appear_then_click(WITHDRAW, offset=(30, 30), interval=5):
                    continue
            if self.handle_auto_search_exit():
                continue
