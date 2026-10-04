"""大世界港口商店模块。

执行大世界港口商店的补给物资购买，包括：
- 遍历所有友方港口购买补给
- 黄币和紫币余额检查
- 月度购买限制日期配置
- 港口间的自动导航和购买执行

继承自 OSMap，提供港口导航和商店购买的完整操作链路，
是大世界代币消耗的重要途径之一。
"""

import json
from datetime import datetime, timedelta

from module.config.deep import deep_get
from module.config.time_source import now as current_time
from module.config.utils import DEFAULT_TIME, get_server_next_update, get_os_reset_remain, get_os_next_reset
from module.exception import GameStuckError
from module.logger import logger
from module.os.map import OSMap
from module.os_shop.assets import OS_SHOP_CHECK


# 月度港口行动力购买开关的配置路径（对应 WebUI「大世界商店」页的开关）
CONFIG_PATH_BUY_PORT_ACTION_POINT = 'OpsiShop.OpsiShop.BuyActionPoint'
# 大世界商店任务的状态存储路径
CONFIG_PATH_SHOP_STATE = 'OpsiShop.Storage.Storage'
# 本月港口行动力购买状态键
STATE_KEY_ACTION_POINT_PURCHASE = 'ActionPointPurchase'


def monthly_explore_complete(config):
    """判断本月每月开荒是否已完成 100%。

    ExploreProgress 自身不带月份标记：完成时写入「已完成百分之100.00」，
    下月开荒任务重新运行时才归零。这里再用开荒任务的 Scheduler.NextRun 兜底：
    开荒完成会把 NextRun 推到下次大世界重置，因此 NextRun >= 下次重置
    即说明本月开荒已经跑完；跨月后 NextRun 落后于新的重置时间，判定自动失效。

    Args:
        config: 配置对象。

    Returns:
        bool: 本月每月开荒已完成返回 True。
    """
    if config.cross_get('OpsiExplore.OpsiExplore.ExploreProgress', default='') != '已完成百分之100.00':
        return False
    next_run = config.cross_get('OpsiExplore.Scheduler.NextRun', default=DEFAULT_TIME)
    return next_run >= get_os_next_reset()


class OpsiShop(OSMap):
    def os_shop(self):
        """
        购买所有港口的补给物资。

        如果黄币或紫币不足，跳过下一个港口的补给购买。

        本月每月开荒完成后，若开启「购买港口行动力」，先执行一次港口行动力专购，
        再走普通补给购买流程。专购中断会中断本次流程，由上层重启后按状态自动续购。

        Pages:
            in: page_os, 大世界地图
            out: page_os, 大世界地图
        """
        logger.hr('大世界-大世界商店+', level=1)
        today = current_time().day
        limit = self.config.OpsiShop_DisableBeforeDate
        if today <= limit:
            logger.info(f'大世界商店+延迟运行，今日日期 {today} <= 限制日期 {limit}')
            self.config.task_delay(server_update=True)
            self.config.task_stop()

        if self.try_port_action_point_purchase():
            logger.info('大世界商店+：本月港口行动力购买已完成')

        not_empty = self.perform_port_shop_purchase()

        next_reset = self._os_shop_delay(not_empty)
        if not_empty:
            logger.info('大世界商店+已完成，延迟到下次重置')
        else:
            logger.warning('[大世界-商店] 港口中没有商店，跳到下个月')
        logger.attr('大世界商店下次重置', next_reset)

        self.config.task_delay(target=next_reset)
        self.config.task_stop()

    def perform_port_shop_purchase(self, action_point_only=False):
        """
        执行一次港口商店购买流程，不包含任务延迟和停止逻辑。

        供 os_shop 和智能调度+月末清理共用。前往最近友方港口，
        进入商店购买所有补给，购买完成后退出港口。

        Args:
            action_point_only (bool): 专购全部行动力箱，不更新普通商店购买状态，不再扫描核验其他商品。

        Returns:
            bool: 普通购买返回商店是否非空；专购正常完成流程即返回 True，已无可购行动力也算完成。

        Pages:
            in: page_os, 大世界地图
            out: page_os, 大世界地图
        """
        if not self.zone.is_azur_port:
            self.globe_goto(self.zone_nearest_azur_port(self.zone))

        self.port_enter()
        self.port_shop_enter()

        previous = getattr(self, '_opsi_action_point_purchase', False)
        self._opsi_action_point_purchase = action_point_only
        try:
            if self.appear(OS_SHOP_CHECK):
                not_empty = self.handle_port_supply_buy()
                if action_point_only:
                    # 专购只买行动力箱，不再复扫核验其他商品是否解锁或可识别；
                    # 正常走完购买流程即视为完成，中断续购时已无可购行动力也算完成。
                    not_empty = True
            else:
                not_empty = False
                logger.warning('[大世界-商店] 港口中没有商店')
                if action_point_only:
                    raise GameStuckError('未确认港口商店，无法完成行动力购买')
        finally:
            self._opsi_action_point_purchase = previous

        self.port_shop_quit()
        self.port_quit()
        return not_empty

    def _config_enabled(self, keys, default=False):
        """严格读取布尔配置，兼容 WebUI checkbox 历史值 [] / [True]。"""
        value = self.config.cross_get(keys=keys, default=default)
        if isinstance(value, list):
            return any(bool(item) for item in value)
        return value is True

    def _get_shop_state(self):
        """读取大世界商店任务的持久化运行状态。

        多进程场景（GUI 主进程与任务进程各持一份 data）下，其他进程的整档保存
        可能把本进程刚写入的键覆盖掉，导致读到空/缺键。此时回退读磁盘实例配置
        并合并（内存优先、磁盘补缺），避免月度购买标记丢失造成重复购买。
        """
        state = self.config.cross_get(keys=CONFIG_PATH_SHOP_STATE, default={})
        if not isinstance(state, dict):
            state = {}
        disk = self._read_disk_shop_state()
        if disk:
            merged = dict(disk)
            merged.update(state)
            state = merged
        return state

    def _read_disk_shop_state(self):
        """从磁盘实例配置读取状态（跨进程覆盖时的兜底数据源）。"""
        try:
            from module.config.utils import filepath_config

            path = filepath_config(getattr(self.config, 'config_name', 'alas'))
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            state = deep_get(data, keys=CONFIG_PATH_SHOP_STATE, default={})
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}

    def _get_shop_state_value(self, key, default=None):
        """读取单个大世界商店运行状态。"""
        return self._get_shop_state().get(key, default)

    def _set_shop_state_value(self, key, value):
        """写入单个大世界商店运行状态并立即持久化。

        按子键深路径写入：只动本键，不覆盖 Storage.Storage 下其他状态键。
        """
        state = self._get_shop_state()
        if state.get(key) == value:
            return
        self.config.modified[f'{CONFIG_PATH_SHOP_STATE}.{key}'] = value
        self.config.save()

    def try_port_action_point_purchase(self):
        """本月仅执行一轮大世界港口商店行动力购买；正常走完流程即标记完成，中断后可续购。

        随大世界商店任务一起触发，不再依赖智能调度+。仅本月每月开荒达到 100%
        后才允许购买。这里买的是港口商店里的行动力箱，不调用耗油的每日行动力购买。
        购买流程内部只挑行动力商品，不复扫其他商品。

        Returns:
            bool: 本轮已执行购买返回 True，未触发返回 False。
        """
        if not self._config_enabled(CONFIG_PATH_BUY_PORT_ACTION_POINT):
            return False
        if not monthly_explore_complete(self.config):
            return False
        reset = get_os_next_reset().isoformat()
        state = self._get_shop_state_value(STATE_KEY_ACTION_POINT_PURCHASE)
        if isinstance(state, dict) and state.get('reset') == reset and state.get('phase') == 'done':
            return False
        # 旧版「全商店复扫核验」的误报会留下 attempts 计数，不能再用它阻止本月续购。
        # 实际导航或购买异常仍由上层统一恢复；只在正常退出港口后记为完成。
        state = dict(reset=reset, phase='buying')
        self._set_shop_state_value(STATE_KEY_ACTION_POINT_PURCHASE, state)
        logger.hr('大世界商店：本月一次性购买港口行动力', level=1)
        self.perform_port_shop_purchase(action_point_only=True)
        if reset != get_os_next_reset().isoformat():
            raise GameStuckError('购买行动力期间跨月，停止旧月份流程')
        self._set_shop_state_value(STATE_KEY_ACTION_POINT_PURCHASE, dict(state, phase='done'))
        return True

    def _os_shop_delay(self, not_empty) -> datetime:
        """
        计算大世界商店+的延迟时间。

        根据商店是否为空和距月底重置的天数决定下次运行时间。

        Args:
            not_empty (bool): 商店是否非空。

        Returns:
            datetime: 下次商店重置时间。
        """
        next_reset = None

        if not_empty:
            next_reset = get_server_next_update(self.config.Scheduler_ServerUpdate)
        else:
            remain = get_os_reset_remain()
            next_reset = get_os_next_reset()
            if remain == 0:
                next_reset = get_server_next_update(self.config.Scheduler_ServerUpdate)
            elif remain < 7:
                next_reset = next_reset - timedelta(days=1)
            else:
                next_reset = (
                    get_server_next_update(self.config.Scheduler_ServerUpdate) +
                    timedelta(days=6)
                )
        return next_reset
