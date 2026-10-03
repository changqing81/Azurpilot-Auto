"""大世界行动力管理模块。

处理大世界（Operation Siren）模式下的行动力（Action Point）管理。
包含行动力数值的 OCR 识别、适应性属性读取、药剂（AP Box）库存解析，
以及自动购买或使用补给品的交互逻辑。
"""
# 此文件处理大世界（Operation Siren）模式下的行动力（Action Point, AP）管理。
# 包含行动力数值 OCR 识别、药剂（AP Box）库存解析以及自动购买或使用补给的交互逻辑。
from datetime import timedelta

import module.config.server as server
from module.base.button import ButtonGrid
from module.base.timer import Timer
from module.base.utils import *
from module.config.time_source import now as current_time
from module.config.utils import get_server_next_update, server_time_offset
from module.logger import logger
from module.ocr.ocr import Digit, DigitCounter, Ocr
from module.os_handler.assets import *
from module.os_handler.map_event import MapEventHandler
from module.statistics.item import Item, ItemGrid
from module.ui.assets import OS_CHECK
from module.ui.ui import UI
from module.log_res import LogRes
from module.os_handler.action_point_ledger import (
    NATURAL_ACTION_POINT_LIMIT,
    SOURCE_POPUP,
    ActionPointLedger,
    HourlyQuota,
)

OCR_ACTION_POINT_REMAIN = Digit(ACTION_POINT_REMAIN, letter=(255, 219, 66), name='OCR_ACTION_POINT_REMAIN')
OCR_ACTION_POINT_REMAIN_OS = Digit(ACTION_POINT_REMAIN_OS, letter=(239, 239, 239),
                                   threshold=160, name='OCR_SHOP_YELLOW_COINS_OS')


class MapActionPointDigit(Digit):
    """大世界地图顶栏行动力数字 OCR（海域内顶栏，白色数字）。

    与弹窗内 OCR（OCR_ACTION_POINT_REMAIN，黄字）不同：
    - 顶栏数字为白色，沿用 ACTION_POINT_REMAIN_OS 资产的 letter/threshold；
    - 必须做位数门（≤4 位，防 OCR 把相邻元素拼进来，如行动力 154 与舰船等级 Lv.60 → 60154），
      非法读数返回 -1，由账本判定链（judge_map_bar_reading）拒绝。

    区域标定（2026-10-03 双截图离线实测）：现有资产区 (878,28,928,46) 与收紧框
    (894,26,934,48) 均稳定读出 154；先用现有资产，影子期发现误读再收紧。
    """

    def __init__(self, buttons, name=None):
        super().__init__(buttons, letter=(239, 239, 239), threshold=160, name=name)
        self.invalid_reason = None

    def after_process(self, result):
        # 跳过 Digit 的 I/D/S/B 宽容纠错：顶栏读数只接受纯数字串，
        # 混入任何非数字字符（如 Lv 等级连读）都按误读拒绝，位数门在这里执行
        result = Ocr.after_process(self, result)
        text = str(result).strip()
        if not text.isdigit() or len(text) > 4:
            self.invalid_reason = 'empty' if not text else ('len>4' if len(text) > 4 else 'non-digit')
            return -1
        self.invalid_reason = None
        return int(text)


MAP_ACTION_POINT_DIGIT = MapActionPointDigit(ACTION_POINT_REMAIN_OS, name='MAP_ACTION_POINT_DIGIT')

OCR_OS_ADAPTABILITY = Digit([
    OS_ADAPTABILITY_ATTACK,
    OS_ADAPTABILITY_DURABILITY,
    OS_ADAPTABILITY_RECOVER
], letter=(231, 235, 239), lang="cnocr", name='OCR_OS_ADAPTABILITY')


class ActionPointBuyCounter(DigitCounter):
    def after_process(self, result):
        result = super().after_process(result)

        # 可能的结果: 0/5, 05
        if result == '05':
            result = '0/5'

        return result


if server.server != 'jp':
    # ACTION_POINT_BUY_REMAIN 中的字符不是碧蓝航线通常使用的数字字体
    OCR_ACTION_POINT_BUY_REMAIN = ActionPointBuyCounter(
        ACTION_POINT_BUY_REMAIN, letter=(148, 247, 99), lang='cnocr', name='OCR_ACTION_POINT_BUY_REMAIN')
else:
    # 日服中 ACTION_POINT_BUY_REMAIN 的数字颜色为白色，国服和国际服为浅绿色
    OCR_ACTION_POINT_BUY_REMAIN = ActionPointBuyCounter(
        ACTION_POINT_BUY_REMAIN, letter=(255, 255, 255), lang='cnocr', name='OCR_ACTION_POINT_BUY_REMAIN')


class ActionPointItem(Item):
    """大世界行动力物品。"""
    def predict_valid(self):
        return True


ACTION_POINT_GRID = ButtonGrid(
    origin=(323, 274), delta=(173, 0), button_shape=(115, 115), grid_shape=(4, 1), name='ACTION_POINT_GRID')

class GridSlice:
    """网格切片，用于构建物品网格。"""
    def __init__(self, buttons):
        self.buttons = buttons

OIL_ITEM = ItemGrid(GridSlice([ACTION_POINT_GRID.buttons[0]]), templates={}, amount_area=(43, 91, 111, 113))
OIL_ITEM.item_class = ActionPointItem

ACTION_POINT_ITEMS = ItemGrid(GridSlice(ACTION_POINT_GRID.buttons[1:]), templates={}, amount_area=(75, 91, 111, 113))
ACTION_POINT_ITEMS.item_class = ActionPointItem
ACTION_POINTS_COST = {
    1: 5,
    2: 10,
    3: 15,
    4: 20,
    5: 30,
    6: 40,
}
ACTION_POINTS_COST_OBSCURE = {
    1: 10,  # CL1 实际上没有隐秘海域
    2: 10,
    3: 20,
    4: 20,
    5: 40,
    6: 40,
}
ACTION_POINTS_COST_ABYSSAL = {
    1: 80,
    2: 80,
    3: 80,  # CL4 以下实际上没有深渊海域
    4: 80,
    5: 100,
    6: 100,
}
ACTION_POINTS_BUY = {
    1: 4000,
    2: 2000,
    3: 2000,
    4: 1000,
    5: 1000,
}
ACTION_POINT_BOX = {
    0: 0,
    1: 20,
    2: 50,
    3: 100,
}


class ActionPointLimit(Exception):
    """
    行动力不足异常。

    当行动力不足以进入目标海域时抛出。
    """
    def __init__(self, current=None, total=None, cost=None, preserve=None):
        super().__init__()
        self.current = current
        self.total = total
        self.cost = cost
        self.preserve = preserve

    @property
    def delay_minutes(self):
        """
        获取需要延迟的分钟数。

        Returns:
            int | None: 需要延迟的分钟数，如果无需延迟则返回 None。
        """
        if self.cost is None or self.current is None:
            return None

        missing = self.cost - self.current
        if missing <= 0:
            return None

        return missing * 10


class ActionPointHandler(UI, MapEventHandler):
    _action_point_box = [0, 0, 0, 0]
    _action_point_current = 0
    _action_point_total = 0
    _action_point_total_with_box = 0

    @staticmethod
    def _is_in_month_end_purchase_block_week():
        """
        判断当前是否处于月末购买封锁期。

        封锁区间：从包含下个服务器月第一天的自然周的周一 0 点开始，
        至下月 1 号 0 点（新月开始）结束——进入新月后购买立即可用。

        示例（31 号为周一、1 号为周二）：
            8/31 封锁，9/1 起恢复，不拖到下一周。

        Returns:
            bool: 是否处于月末封锁期。
        """
        diff = server_time_offset()
        server_now = current_time() - diff
        next_month = (server_now.replace(day=28) + timedelta(days=4)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        next_month_start = next_month.replace(day=1)
        # 封锁起点：包含下月 1 号的自然周的周一
        block_start = next_month_start.date() - timedelta(days=next_month_start.weekday())
        # 终点：下月 1 号 0 点（新月开始即恢复购买）
        return block_start <= server_now.date() < next_month_start.date()

    def _is_in_action_point(self):
        return self.appear(ACTION_POINT_USE, offset=(20, 20))

    def is_current_ap_visible(self):
        return self.match_template_color(CURRENT_AP_CHECK, offset=(40, 5), threshold=15)

    def action_point_use(self):
        prev = self._action_point_current
        self.interval_clear(ACTION_POINT_USE)
        for _ in self.loop():

            if self.appear_then_click(ACTION_POINT_USE, offset=(20, 20), interval=3):
                self.device.sleep(0.3)
                continue

            if self.handle_popup_confirm('ACTION_POINT_USE'):
                continue

            self.action_point_safe_get()
            if self._action_point_current > prev:
                break

    def action_point_update(self):
        """
        更新行动力信息。

        Returns:
            int: 总行动力，包括行动力药剂。
        """
        oil = OIL_ITEM.predict(self.device.image, name=False, amount=True)
        items = ACTION_POINT_ITEMS.predict(self.device.image, name=False, amount=True)
        box = [item.amount for item in oil] + [item.amount for item in items]
        current = OCR_ACTION_POINT_REMAIN.ocr(self.device.image)
        box_sum = np.sum(np.array(box) * tuple(ACTION_POINT_BOX.values()))
        total = current
        if self.config.OS_ACTION_POINT_BOX_USE:
            total += box_sum
        oil = box[0]

        LogRes(self.config).Oil = oil
        logger.info(f'[大世界-行动点] 行动点: {current}({total}), 石油: {oil}')
        # 统计口径的总行动力始终包含体力箱，不受 OS_ACTION_POINT_BOX_USE 临时关闭的影响
        # （防止行动力溢出任务会临时关闭该开关，导致统计快照丢箱、图表出现深坑）
        self._action_point_total_with_box = int(current + box_sum)
        self.config._action_point_total_with_box = self._action_point_total_with_box
        LogRes(self.config).ActionPoint = {'Value': current, 'Total': total}
        self.config.update()
        self._action_point_current = current
        self._action_point_box = box
        self._action_point_total = total
        # 处理超出上限的情况
        if total > 3000:
            self.config.override(OpsiGeneral_DoRandomMapEvent=False)
        # 行动力账本：弹窗是唯一精确校准点（同时拿到当前值与箱子明细）
        self.ap_observe_from_popup()

    # ==================== 行动力账本（AP Ledger） ====================

    AP_LEDGER_STATE_PATH = 'OpsiScheduling.Storage.Storage'

    def _ap_ledger_enabled(self):
        return bool(getattr(self.config, 'OpsiGeneral_ActionPointLedgerEnabled', False))

    def _get_ap_ledger(self):
        """惰性获取账本实例；首次从配置状态恢复，之后复用内存对象。"""
        ledger = self.__dict__.get('_ap_ledger_instance')
        if ledger is None:
            ledger = ActionPointLedger.from_state(self._load_ap_ledger_state())
            self.__dict__['_ap_ledger_instance'] = ledger
        return ledger

    def _load_ap_ledger_state(self):
        """读取账本状态；内存 data 缺键（多进程整档保存覆盖）时回退磁盘实例配置。"""
        try:
            state = deep_get(self.config.data, keys=self.AP_LEDGER_STATE_PATH, default={})
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        # 无条件与磁盘合并，防止其他状态键（BuyActionPointCount 等）被覆盖丢失
        disk = self._read_disk_storage_state()
        if disk:
            merged = dict(disk)
            merged.update(state)
            state = merged
        return state or {}

    def _read_disk_storage_state(self):
        """从磁盘实例配置读 OpsiScheduling.Storage.Storage（跨进程覆盖兜底）。"""
        try:
            from module.config.utils import filepath_config

            path = filepath_config(getattr(self.config, 'config_name', 'alas'))
            import json as _json
            with open(path, encoding='utf-8') as f:
                data = _json.load(f)
            state = deep_get(data, keys=self.AP_LEDGER_STATE_PATH, default={})
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}

    def _save_ap_ledger_state(self):
        """把账本写回 OpsiScheduling.Storage.Storage（与智能调度+ 状态同一持久化位置）。"""
        ledger = self.__dict__.get('_ap_ledger_instance')
        if ledger is None:
            return
        try:
            state = self._load_ap_ledger_state()
            state['ApLedgerState'] = ledger.to_state()
            self.config.modified[self.AP_LEDGER_STATE_PATH] = state
            self.config.save()
        except Exception:
            logger.warning('[AP账本] 状态持久化失败', exc_info=True)

    def ap_observe_from_popup(self):
        """弹窗校准：action_point_update() 末尾调用，是最精确的校准点。

        同时更新 current 与 total_with_box；写入账本状态但**不动任何统计快照**。
        """
        if not self._ap_ledger_enabled():
            return
        ledger = self._get_ap_ledger()
        if ledger is None:
            return
        try:
            total = getattr(self, '_action_point_total_with_box', None)
            if ledger.observe(self._action_point_current, total, source=SOURCE_POPUP, box=self._action_point_box):
                self._save_ap_ledger_state()
        except Exception:
            logger.exception('[AP账本] 弹窗校准失败')

    def ap_spend(self, cost, reason='', persist=False):
        """记一笔行动力增减（进图/指令等）。persist=True 时立即落盘。"""
        if not self._ap_ledger_enabled():
            return False
        ledger = self._get_ap_ledger()
        if ledger is None:
            return False
        try:
            ok = ledger.spend(cost, reason=reason)
        except Exception:
            logger.exception('[AP账本] 记账失败')
            return False
        if ok and persist:
            self._save_ap_ledger_state()
        return ok

    def ap_observe_from_map_bar(self, image=None, force_accept=False):
        """读顶栏行动力真值（仅海域内），过门后校准账本当前值。

        Args:
            image: 截图（默认用当前设备画面）。
            force_accept (bool): 强制采信读数——用于进图/任务切换/开工检查等
                关键时机，此时读数与推演的大幅偏差（drift）多为真实的战斗/进场
                消耗而非 OCR 误读，跳过变化率门直接采纳（位数/范围门仍生效）。

        任何情况下都不写 LogRes/统计快照。
        """
        if not self._ap_ledger_enabled():
            return None
        if not getattr(self.config, 'OpsiGeneral_ActionPointLedgerMapBarOcr', False):
            return None
        is_in_map = getattr(self, 'is_in_map', None)
        if not callable(is_in_map) or not is_in_map():
            return None
        ledger = self._get_ap_ledger()
        if ledger is None:
            return None
        try:
            raw = MAP_ACTION_POINT_DIGIT.ocr(image or self.device.image)
        except Exception as e:
            logger.warning(f'[AP账本] 顶栏读数 OCR 失败: {type(e).__name__}: {e}')
            return None
        est = ledger.estimate()
        last = self.__dict__.get('_ap_mapbar_last')
        verdict, value, reason = ActionPointLedger.judge_map_bar_reading(raw, est, last_value=last)
        if force_accept and verdict == 'suspect':
            # 强制时机：大幅 drift 视为真实消耗，采信 OCR 读数（OCR 已实测准确）
            verdict, reason = 'ok', f'force-accept {reason}'
        self.__dict__['_ap_mapbar_last'] = value if verdict in ('ok', 'pending') else None
        if verdict == 'suspect':
            # 可疑读数：标记待复核（额度内由 need_action_point_popup 安排弹窗校准）
            self.__dict__['_ap_suspect_pending'] = True
        logger.info(
            f'[AP账本] source=map_bar 读数={value if value is not None else raw} '
            f'推演={est.current} verdict={verdict} reason={reason}'
        )
        if verdict != 'ok' or value is None:
            return None
        # 采纳读数：只校准账本当前值（总量跟随修正），绝不写 LogRes/统计
        try:
            ledger.observe(value, source=SOURCE_MAP_BAR)
            self._save_ap_ledger_state()
        except Exception:
            logger.exception('[AP账本] 顶栏校准失败')
        return value

    def need_action_point_popup(self, cost, preserve=0, top_up_ceiling=None, now=None):
        """预判是否需要打开行动力弹窗（P2 决策入口）。

        - `ActionPointLedgerDecide` 关闭时恒返回 True，行为与现状完全一致；
        - 开启时由账本推演判定：当前值够开工且总行动力高于保留值 → 跳过弹窗；
        - 此前存在可疑读数（suspect）时，先在复核额度内弹一次窗读真值校准，
          再按校准后的账本判定；额度耗尽则按推演判定。

        Returns:
            bool: True = 需要弹窗（校准或补充）；False = 账本够用，直接开工。
        """
        if not self._ap_ledger_enabled():
            return True
        if not getattr(self.config, 'OpsiGeneral_ActionPointLedgerDecide', False):
            return True
        ledger = self._get_ap_ledger()
        if ledger is None:
            return True
        # 真值优先：开工检查等判定前先读一次顶栏 OCR（每次进图/任务切换时机），
        # 读到的真值直接作为判定基准，推演只作读不到时的兜底
        if getattr(self.config, 'OpsiGeneral_ActionPointLedgerMapBarOcr', False):
            is_in_map = getattr(self, 'is_in_map', None)
            if callable(is_in_map) and is_in_map():
                self.ap_observe_from_map_bar(force_accept=True)
        if self.__dict__.get('_ap_suspect_pending'):
            if self._ap_verify_by_popup():
                self.__dict__.pop('_ap_suspect_pending', None)
                logger.info('[AP账本] 可疑读数已复核校准')
            # 复核失败（额度耗尽/弹窗失败）时保留标记，下一个决策点再试（仍受额度限制）
        return ledger.need_popup(cost, preserve=preserve, top_up_ceiling=top_up_ceiling, now=now)

    def _get_verify_quota(self):
        """复核额度：上限取 OpsiGeneral_ActionPointLedgerConfirmLimit（0=永不复核）。"""
        quota = self.__dict__.get('_ap_verify_quota')
        if quota is None:
            try:
                limit = int(getattr(self.config, 'OpsiGeneral_ActionPointLedgerConfirmLimit', 3))
            except (TypeError, ValueError):
                limit = 3
            quota = HourlyQuota(limit)
            self.__dict__['_ap_verify_quota'] = quota
        return quota

    def _ap_verify_by_popup(self):
        """开一次行动力弹窗读真值并校准账本（受每小时复核额度限制）。

        Returns:
            bool: 是否完成了校准。
        """
        quota = self._get_verify_quota()
        if not quota.allow():
            logger.info('[AP账本] 复核额度已用尽，跳过弹窗校准')
            return False
        # 页面护栏：复核弹窗只允许发生在大世界界面，
        # 其他页面（或无设备上下文）直接放弃，避免 action_point_enter 空转
        if getattr(self, 'device', None) is None:
            logger.info('[AP账本] 无设备上下文，跳过复核弹窗')
            return False
        if not self.appear(OS_CHECK, offset=(20, 20)):
            logger.info('[AP账本] 当前不在大世界界面，跳过复核弹窗')
            return False
        quota.record()
        try:
            # 纯读取：进弹窗 → OCR（action_point_update 内会自动校准账本）→ 退出
            self.action_point_enter()
            self.action_point_safe_get()
            self.action_point_quit()
        except Exception:
            logger.exception('[AP账本] 复核弹窗失败')
            return False
        return True

    def action_point_safe_get(self):
        """
        安全获取行动力信息。

        等待行动力弹窗完全加载，并处理可能的地图事件。
        """
        timeout = Timer(3, count=6).start()
        for _ in self.loop():
            # 结束
            if self.is_current_ap_visible():
                break
            if timeout.reached():
                logger.warning('[大世界-行动点] 获取行动点超时')
                break
            # 处理行动力弹窗上方的强制地图事件
            if self.handle_map_event():
                timeout.reset()
                continue

        skip_first_screenshot = True
        timeout = Timer(1, count=2).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning('[大世界-行动点] 获取行动点超时')
                break
            # 处理行动力弹窗上方的强制地图事件
            if self.handle_map_event():
                timeout.reset()
                continue

            self.action_point_update()

            # 当前行动力过多，可能是 OCR 错误
            if self._action_point_current > 600:
                continue

            oil, boxes = self._action_point_box[0], self._action_point_box[1:]
            # 拥有药剂
            if sum(boxes) > 0:
                if oil > 100:
                    break
                else:
                    # [11, 0, 1, 0]
                    continue
            # 或者拥有石油
            # 页面未完全加载时可能为 0 或 1
            # [1, 0, 0, 0]
            if oil > 100:
                break

    @staticmethod
    def action_point_get_cost(zone, pinned):
        """
        获取进入指定海域所需的行动力消耗。

        Args:
            zone (Zone): 要进入的海域。
            pinned (str): 海域类型。可用类型: DANGEROUS, SAFE, OBSCURE, ABYSSAL, STRONGHOLD。

        Returns:
            int: 消耗的行动力。
        """
        if pinned == 'DANGEROUS':
            cost = ACTION_POINTS_COST[zone.hazard_level] * 2
        elif pinned == 'SAFE':
            cost = ACTION_POINTS_COST[zone.hazard_level]
        elif pinned == 'OBSCURE':
            cost = ACTION_POINTS_COST_OBSCURE[zone.hazard_level]
        elif pinned == 'ABYSSAL':
            cost = ACTION_POINTS_COST_ABYSSAL[zone.hazard_level]
        elif pinned == 'STRONGHOLD':
            cost = 200
        else:
            logger.warning(f'[大世界-行动点] 无法获取行动点消耗, 区域={zone}, 固定={pinned}，假设消耗40')
            cost = 40

        if zone.is_port:
            cost = 0

        return cost

    def action_point_get_active_button(self):
        """
        获取当前激活的行动力药剂按钮索引。

        Returns:
            int: 0 到 3。0 为石油，1 为 20 行动力药剂，2 为 50 行动力药剂，3 为 100 行动力药剂。
        """
        for index, item in enumerate(ACTION_POINT_GRID.buttons):
            area = item.area
            color = get_color(self.device.image, area=(area[0], area[3] + 5, area[2], area[3] + 10))
            # 激活的按钮会变蓝
            # 激活: 196, 未激活: 118 ~ 123
            if color[2] > 160:
                return index

        logger.warning('[大世界-行动点] 无法找到活动的行动点箱子按钮')
        return 1

    def action_point_set_button(self, index):
        """
        设置行动力药剂按钮。

        Args:
            index (int): 0 到 3。0 为石油，1 为 20 行动力药剂，2 为 50 行动力药剂，3 为 100 行动力药剂。

        Returns:
            bool: 是否成功。
        """
        for _ in self.loop(timeout=2):
            if self.action_point_get_active_button() == index:
                return True
            else:
                self.device.click(ACTION_POINT_GRID[index, 0])
                self.device.sleep(0.3)
        else:
            logger.warning('[大世界-行动点] 设置行动点按钮超时')
            return False

    def action_point_get_buy_remain(self):
        """
        获取行动力剩余购买次数。

        Returns:
            int: 剩余购买次数。

        Pages:
            in: ACTION_POINT_USE
        """
        current = 0
        for _ in self.loop(timeout=1):

            current, _, total = OCR_ACTION_POINT_BUY_REMAIN.ocr(self.device.image)

            # 可能的结果: 0/5, 05
            if total == 0:
                continue

            break
        else:
            logger.warning('[大世界-行动点] 获取行动点购买剩余超时')

        return current

    def action_point_buy(self, preserve=1000):
        """
        使用石油购买行动力。

        Args:
            preserve (int): 保留的石油量。

        Returns:
            bool: 是否购买成功。

        Pages:
            in: ACTION_POINT_USE
        """
        self.action_point_set_button(0)
        current = self.action_point_get_buy_remain()
        buy_max = 5  # 当前版本中，玩家每周可购买 5 次行动力
        buy_count = buy_max - current
        buy_limit = self.config.OpsiGeneral_BuyActionPointLimit
        # 注：BuyActionPointLimit <= 0 表示用户选择不购买（由调用方的 >0 条件拦截），
        # 不再做「临时覆盖残留」恢复——残留自愈由智能调度入口凭状态备份完成。
        if self._is_in_month_end_purchase_block_week():
            logger.info('[大世界-行动点] 跳过本周购买行动点，因为是月末封锁周')
            return False
        if buy_count >= buy_limit:
            logger.info('[大世界-行动点] 达到本周购买行动点上限')
            return False
        cost = ACTION_POINTS_BUY[current]
        oil = self._action_point_box[0]
        logger.info(f'[大世界-行动点] 购买行动点将消耗 {cost}, 当前石油: {oil}, 保留: {preserve}')
        if oil >= cost + preserve:
            self.action_point_use()
            return True
        else:
            logger.info('[大世界-行动点] 石油不足无法购买')
            return False

    def action_point_quit(self):
        """
        退出行动力弹窗。

        Pages:
            in: ACTION_POINT_USE
            out: page_os
        """
        for _ in self.loop():
            # 结束
            # 有时行动力弹窗没有黑色模糊背景
            # ACTION_POINT_CANCEL 和 OS_CHECK 同时出现
            if not self.appear(ACTION_POINT_CANCEL, offset=(20, 20)):
                if self.appear(OS_CHECK, offset=(20, 20)):
                    break
            # 点击
            if self.appear_then_click(ACTION_POINT_CANCEL, offset=(20, 20), interval=3):
                continue
            # 处理行动力弹窗上方的强制地图事件
            if self.handle_map_event():
                continue

    def handle_action_point(self, zone, pinned, cost=None, keep_current_ap=True, check_rest_ap=False):
        """
        处理行动力，包括购买和使用药剂。

        Args:
            zone (Zone): 要进入的海域。
            pinned (str): 海域类型。可用类型: DANGEROUS, SAFE, OBSCURE, ABYSSAL, STRONGHOLD。
            cost (int): 自定义行动力消耗值。
            keep_current_ap (bool): 是否先检查行动力，避免在不足时使用剩余行动力。
            check_rest_ap (bool): 如果当前行动力与今天可获得的剩余行动力之和超过 200，则跳过 keep_current_ap 检查。

        Returns:
            bool: 是否处理成功。

        Raises:
            ActionPointLimit: 行动力不足时抛出。

        Pages:
            in: ACTION_POINT_USE
        """
        if not self._is_in_action_point():
            return False

        # 行动力药剂有显示动画
        self.action_point_safe_get()
        if cost is None:
            cost = self.action_point_get_cost(zone, pinned)
        buy_checked = False

        # 检查剩余行动力
        if check_rest_ap:
            diff = get_server_next_update('00:00') - current_time()
            today_rest = int(diff.total_seconds() // 600)
            if self._action_point_current + today_rest >= NATURAL_ACTION_POINT_LIMIT:
                logger.info('[大世界处理-行动力] 当前行动力与今日可获得的剩余行动力之和超过 200，跳过行动力检查')
                logger.info(f'[大世界-行动点] 当前={self._action_point_current}  今日剩余={today_rest}')
                keep_current_ap = False

        # 先检查行动力
        if keep_current_ap:
            if self._action_point_total <= self.config.OS_ACTION_POINT_PRESERVE:
                logger.info(f'[大世界-行动点] 达到行动点上限, 保留={self.config.OS_ACTION_POINT_PRESERVE}')
                self.action_point_quit()
                raise ActionPointLimit(
                    current=self._action_point_current,
                    total=self._action_point_total,
                    preserve=self.config.OS_ACTION_POINT_PRESERVE,
                )

        for _ in range(12):
            # 拥有足够的行动力
            if self._action_point_current >= cost:
                logger.info('[大世界-行动点] 行动点充足')
                self.action_point_quit()
                return True

            # 购买行动力
            if self.config.OpsiGeneral_BuyActionPointLimit > 0 and not buy_checked:
                if self.action_point_buy(preserve=self.config.OpsiGeneral_OilLimit):
                    self.action_point_safe_get()
                    continue
                else:
                    buy_checked = True

            # 重新检查总行动力是否小于消耗
            # 如果是，则跳过使用药剂
            if self._action_point_total < cost:
                logger.info('[大世界-行动点] 行动点不足')
                self.action_point_quit()
                raise ActionPointLimit(
                    current=self._action_point_current,
                    total=self._action_point_total,
                    cost=cost,
                )

            # 排序行动力药剂
            box = []
            for index in [3, 2, 1]:
                if self._action_point_box[index] > 0:
                    if self._action_point_current + ACTION_POINT_BOX[index] >= NATURAL_ACTION_POINT_LIMIT:
                        box.append(index)
                    else:
                        box.insert(0, index)

            # 使用行动力药剂
            if len(box):
                if self._action_point_total > self.config.OS_ACTION_POINT_PRESERVE:
                    self.action_point_set_button(box[0])
                    self.action_point_use()
                    continue
                else:
                    logger.info(f'[大世界-行动点] 达到行动点上限, 保留={self.config.OS_ACTION_POINT_PRESERVE}')
                    self.action_point_quit()
                    raise ActionPointLimit(
                        current=self._action_point_current,
                        total=self._action_point_total,
                        preserve=self.config.OS_ACTION_POINT_PRESERVE,
                    )
            else:
                logger.info('[大世界-行动点] 没有更多行动点箱子')
                self.action_point_quit()
                raise ActionPointLimit(
                    current=self._action_point_current,
                    total=self._action_point_total,
                    cost=cost,
                )

        logger.warning('[大世界-行动点] 尝试12次后仍无法获取行动点')
        return False

    def action_point_enter(self):
        """
        进入行动力弹窗。

        Pages:
            in: OS_CHECK
            out: ACTION_POINT_USE
        """
        for _ in self.loop():
            if self.appear(ACTION_POINT_USE, offset=(20, 20)):
                break

            if self.appear(OS_CHECK, offset=(20, 20), interval=3):
                self.device.click(ACTION_POINT_REMAIN_OS)
                continue
            if self.handle_map_event():
                # 剧情是透明的，处理剧情时可能检测到 OS_CHECK
                self.interval_reset(OS_CHECK)
                continue
            if self.appear_then_click(AUTO_SEARCH_REWARD, offset=(50, 50)):
                continue

    def action_point_set(self, zone=None, pinned=None, cost=None, keep_current_ap=True, check_rest_ap=False):
        """
        设置行动力，进入行动力弹窗并处理。

        Args:
            zone (Zone): 要进入的海域。
            pinned (str): 海域类型。可用类型: DANGEROUS, SAFE, OBSCURE, ABYSSAL, STRONGHOLD。
            cost (int): 自定义行动力消耗值。
            keep_current_ap (bool): 是否先检查行动力，避免在不足时使用剩余行动力。
            check_rest_ap (bool): 如果当前行动力与今天可获得的剩余行动力之和超过 200，则跳过 keep_current_ap 检查。

        Returns:
            bool: 是否处理成功。

        Raises:
            ActionPointLimit: 行动力不足时抛出。
        """
        self.action_point_enter()
        if not self.handle_action_point(zone, pinned, cost, keep_current_ap, check_rest_ap):
            return False

        # 等待行动力弹窗关闭
        for _ in self.loop():
            if self.appear(IN_MAP, offset=(200, 5)):
                break

        return True

    def action_point_check(self, amount):
        """
        检查是否有足够的行动力。

        Args:
            amount (int): 需要检查的行动力数量。

        Returns:
            bool: 是否有足够的行动力。
        """
        self.action_point_enter()
        self.action_point_safe_get()

        enough = self._action_point_total > amount
        if enough:
            logger.info(f'[大世界-行动点] 拥有 {amount} 行动点')
        else:
            logger.info(f'[大世界-行动点] 没有 {amount} 行动点')

        self.action_point_quit()
        for _ in self.loop():
            if self.appear(IN_MAP, offset=(200, 5)):
                break

        return enough
