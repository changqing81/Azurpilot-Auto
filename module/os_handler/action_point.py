"""大世界行动力管理模块。

处理大世界（Operation Siren）模式下的行动力（Action Point）管理。
包含行动力数值的 OCR 识别、适应性属性读取、药剂（AP Box）库存解析，
以及自动购买或使用补给品的交互逻辑。
"""
# 此文件处理大世界（Operation Siren）模式下的行动力（Action Point, AP）管理。
# 包含行动力数值 OCR 识别、药剂（AP Box）库存解析以及自动购买或使用补给的交互逻辑。
from datetime import datetime, timedelta

import module.config.server as server
from module.base.button import ButtonGrid
from module.base.timer import Timer
from module.base.utils import *
from module.config.time_source import now as current_time
from module.config.utils import get_server_next_update, server_time_offset
from module.config.deep import deep_get
from module.exception import RequestHumanTakeover
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
    SOURCE_MAP_BAR,
    SOURCE_POPUP,
    ActionPointLedger,
)

OCR_ACTION_POINT_REMAIN = Digit(ACTION_POINT_REMAIN, letter=(255, 219, 66), name='OCR_ACTION_POINT_REMAIN')
OCR_ACTION_POINT_REMAIN_OS = Digit(ACTION_POINT_REMAIN_OS, letter=(239, 239, 239),
                                   threshold=160, name='OCR_SHOP_YELLOW_COINS_OS')


class MapActionPointDigit(Digit):
    """大世界地图顶栏行动力数字 OCR（海域内顶栏，白色数字）。

    与弹窗内 OCR（OCR_ACTION_POINT_REMAIN，黄字）不同：
    - 顶栏数字为白色，沿用 ACTION_POINT_REMAIN_OS 资产的 letter/threshold；
    - 必须做位数门（≤4 位，防 OCR 把相邻元素拼进来，如行动力 154 与舰船等级 Lv.60 → 60154），
      非法读数返回 -1，账本记录前直接丢弃。

    区域标定（2026-10-03 双截图离线实测）：现有资产区 (878,28,928,46) 与收紧框
    (894,26,934,48) 均稳定读出 154；先用现有资产，误读率异常时再收紧。
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


class ActionPointUseUnconfirmed(ActionPointLimit):
    """
    USE 点击后 24 秒内未确认结果（库存/行动力都没有按预期变化）。

    故意继承 ActionPointLimit：所有捕获行动力不足的地方（智能调度+、
    月末清理、耄耋相接、各海域任务）会自然按「延后重跑」处理，
    走正常调度轨道，绝不进入 RequestHumanTakeover 的
    「可恢复错误 → 重启模拟器 → 不计失败限制」死循环轨道
    （2026-10-06 11 点真机日志：50 次 CRITICAL/66 秒一轮的教训）。

    结果未知是安全的：单发原则保证任何时刻最多一次未确认点击；
    任务延后重跑时会重新读数，行动力充足就不会再点。
    """

    def __init__(self, current=None, total=None, cost=None):
        super().__init__(current=current, total=total, cost=cost)
        # ActionPointLimit 不带消息文本（上层用 e.current/e.total 取值），
        # 这里补一条可读消息，方便 catch 处直接打日志。
        Exception.__init__(
            self,
            f'USE 已点但 24 秒内未确认（点击前当前={current}，档位={cost}），'
            '任务延后重跑，重新读数后再决定是否补充'
        )


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

    def action_point_use(self, selected_index):
        """每次最多点一次 USE；对应库存减少才算确认。

        弹窗可能十几秒不刷新。旧逻辑每 10 秒再点一次，23:13:32/43/54
        连续开了三个 100 箱；超时还会回到外层循环继续尝试。
        防「一口气开到 400 行动力」的三道闸（2026-10-06 收敛，撤掉
        ap-use-pending 哨兵文件——它只在点击后 24 秒内进程死掉时省
        一瓶箱子，却造成过残留死锁 + 66 秒一轮重启模拟器循环）：
        1. 单发：每次调用只允许一次实际点击，绝不 interval 重点；
        2. 确认：库存减少且行动力上涨才算数，确认前不许有下一次点击；
        3. 未确认即抛：当前任务立刻终止，绝不自动重试；任务重启后
           重新读数，行动力充足就不会再点。
        """
        if selected_index not in ACTION_POINT_BOX:
            # 调用方传错箱型属于代码缺陷：延后重跑不会自愈，保留人工轨道。
            raise RequestHumanTakeover(f'行动力补充选项无效: {selected_index}')
        prev = int(self._action_point_current)
        stock = int(self._action_point_box[selected_index])
        if stock <= 0:
            # 没有这个箱型的库存（用户用光/OCR 抖动）：这轮不补，延后重跑，
            # 走「行动力不足」轨道，与 handle_action_point 的无箱路径同语义。
            logger.warning(f'[大世界-行动点] 库存[{selected_index}]={stock}，无法补充，延后处理')
            raise ActionPointLimit(
                current=prev,
                total=self._action_point_total,
                cost=ACTION_POINT_BOX[selected_index],
            )

        if not self.appear(ACTION_POINT_USE, offset=(20, 20)):
            # USE 按钮没出现就没有点击，本轮作废延后重跑（未点不存在未确认）。
            logger.warning('[大世界-行动点] USE 按钮未出现，本轮补充作废，延后处理')
            raise ActionPointLimit(
                current=prev,
                total=self._action_point_total,
                cost=ACTION_POINT_BOX[selected_index],
            )
        # 不走 interval 重点：每次调用只允许一次实际点击；下次必须重新读数。
        self.device.click(ACTION_POINT_USE)
        self.device.sleep(0.3)

        timeout = Timer(24).start()
        while not timeout.reached():
            self.device.screenshot()
            if self.handle_popup_confirm('ACTION_POINT_USE'):
                continue
            if not self.is_current_ap_visible():
                continue
            self.action_point_update()
            # 当前值和库存必须同时更新；箱子只允许减少一个，防 OCR 噪声蒙混过关。
            # 石油购买不是按 1 扣库存，因此只检查石油减少及行动力上升。
            gained = self._action_point_current - prev
            stock_change = stock - self._action_point_box[selected_index]
            confirmed = (stock_change > 0 and gained > 0) if selected_index == 0 else (
                stock_change == 1 and gained >= ACTION_POINT_BOX[selected_index]
            )
            if confirmed:
                logger.info(
                    f'[大世界-行动点] USE 已确认：库存[{selected_index}] '
                    f'{stock}->{self._action_point_box[selected_index]}，'
                    f'当前={prev}->{self._action_point_current}'
                )
                # 每瓶确认即清一次该按钮的点击/网格记录：补药剂本来就要连点
                # USE（一轮最多补 5+ 瓶，等效允许连点 20 次以上），不清会被
                # 设备层 GameTooManyClickError 当成点击洪水打断（2026-10-06
                # 日志：第 6 瓶点 USE 时 SHIP_SWIPE×7+USE×6 触顶）。
                self.device.click_record_remove(ACTION_POINT_USE)
                return True

        logger.error(
            f'[大世界-行动点] USE 已点一次但 24 秒内未确认：'
            f'当前={prev}->{self._action_point_current}，'
            f'库存[{selected_index}]={stock}->{self._action_point_box[selected_index]}；'
            '禁止重复点击，请人工检查'
        )
        raise ActionPointUseUnconfirmed(
            current=prev,
            total=self._action_point_total,
            cost=ACTION_POINT_BOX[selected_index],
        )

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
    # 状态实体在 Storage.Storage 之下的 ApLedgerState 子键。
    # 读取必须取到这一层：取到上层包装字典 {'ApLedgerState': {...}} 会让
    # from_state 找不到 current 字段而总是返回空账本（2026-10-03 真机日志定位，
    # 该 bug 使持久化自上线起从未生效，每次任务重开都按空账本弹读数窗）。
    AP_LEDGER_STATE_KEY = 'OpsiScheduling.Storage.Storage.ApLedgerState'

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
        """读取账本状态；内存 data 缺键时回退磁盘实例配置（跨进程兜底）。"""
        try:
            state = deep_get(self.config.data, keys=self.AP_LEDGER_STATE_KEY, default={})
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        # 无条件与磁盘合并，防内存侧丢键（磁盘同名键优先级低于内存）
        disk = self._read_disk_storage_state()
        if disk:
            merged = dict(disk)
            merged.update(state)
            state = merged
        return state or {}

    def _read_disk_storage_state(self):
        """从磁盘实例配置读 OpsiScheduling.Storage.Storage.ApLedgerState（跨进程覆盖兜底）。"""
        try:
            from module.config.utils import filepath_config

            path = filepath_config(getattr(self.config, 'config_name', 'alas'))
            import json as _json
            with open(path, encoding='utf-8') as f:
                data = _json.load(f)
            state = deep_get(data, keys=self.AP_LEDGER_STATE_KEY, default={})
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}

    def _save_ap_ledger_state(self):
        """把账本写回 OpsiScheduling.Storage.Storage.ApLedgerState。

        按子键深路径写入，只动 ApLedgerState，不覆盖同组下
        买行动力计数等其他状态键（读-改-写整个 dict 在多进程下会互相覆盖）。
        """
        ledger = self.__dict__.get('_ap_ledger_instance')
        if ledger is None:
            return
        try:
            state = ledger.to_state()
            if not state:
                return
            self.config.modified[self.AP_LEDGER_STATE_PATH + '.ApLedgerState'] = state
            self.config.save()
        except Exception:
            logger.warning('[AP账本] 状态持久化失败', exc_info=True)

    def ap_observe_from_popup(self):
        """弹窗记录：action_point_update() 末尾调用，是最精确的记录点。

        同时记录 current 与 total_with_box（含箱子明细）；写入账本状态但**不动任何统计快照**。
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
            logger.exception('[AP账本] 弹窗记录失败')

    def ap_observe_from_map_bar(self, image=None):
        """读顶栏行动力真值（仅海域内）并记录进账本。

        顶栏读数只做 OCR 合法性门（位数门/非数字拒绝），通过即记录——
        读到的真值就是账本的新记录，不与旧记录比对。

        同时同步总览显示（Dashboard.ActionPoint 的当前值/总行动力/时间戳，
        总行动力未知时保持原值）。不写 LogRes/统计快照——行动力趋势与
        资源增减事件不混入顶栏读数。

        Args:
            image: 截图（默认用当前设备画面）。

        Returns:
            int | None: 记录成功返回读数，否则 None。
        """
        if not self._ap_ledger_enabled():
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
        value, reason = ActionPointLedger.sanitize_map_bar_value(raw)
        if value is None:
            logger.info(f'[AP账本] source=map_bar 读数={raw} 已丢弃({reason})')
            return None
        try:
            ledger.observe(value, source=SOURCE_MAP_BAR)
            # 本轮实例的行动力属性同步：跳过弹窗的轮次不会走 action_point_update()，
            # 若不在这里刷新，_action_point_total 会停留在类属性默认 0，
            # 导致行动力推送发出「总行动力: 0」，且最低保留检查误判行动力不足而推迟任务。
            self._action_point_current = int(value)
            if ledger.total_with_box is not None:
                total_with_box = int(ledger.total_with_box)
                self._action_point_total_with_box = total_with_box
                # 与 action_point_update() 同一口径：含箱开关关闭时总行动力不含箱
                self._action_point_total = (
                    total_with_box
                    if getattr(self.config, 'OS_ACTION_POINT_BOX_USE', False)
                    else int(value)
                )
            else:
                # 含箱总量未知（从没弹过窗）：至少不让本轮属性停在 0；
                # need_popup 会因总量未知强制弹窗补齐，不会长期停留在这个分支
                self._action_point_total = int(value)
            # 同步总览显示（Dashboard.ActionPoint，与弹窗口径一致）。
            # 直接写显示键而非 LogRes：行动力趋势快照与统计不混入顶栏读数
            self.config.modified['Dashboard.ActionPoint.Value'] = int(value)
            self.config.modified['Dashboard.ActionPoint.Record'] = \
                datetime.now().replace(microsecond=0)
            if ledger.total_with_box is not None:
                self.config.modified['Dashboard.ActionPoint.Total'] = \
                    int(ledger.total_with_box)
            self._save_ap_ledger_state()
        except Exception:
            logger.exception('[AP账本] 顶栏记录失败')
        return value

    def need_action_point_popup(self, cost, preserve=0):
        """判定是否需要打开行动力弹窗。

        - 账本关闭时恒返回 True，行为与现状完全一致；
        - 开启时：先读一次顶栏真值记录进账本（在海域内时），再用账本记录值
          与任务所需比较——当前行动力低于所需、总行动力会被保留值拦截、
          或账本还没有任何记录 → 弹窗。

        Returns:
            bool: True = 需要弹窗（读真值或补充）；False = 账本记录够用，直接开工。
        """
        if not self._ap_ledger_enabled():
            return True
        # 判定前先读一次顶栏真值并记录（在海域内时），判定永远基于最新记录
        self.ap_observe_from_map_bar()
        ledger = self._get_ap_ledger()
        if ledger is None:
            return True
        return ledger.need_popup(cost, preserve=preserve)

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
        buy_limit = self.config.OpsiGeneral_BuyActionPointLimit
        # 用户设置的购买上限（OpsiGeneral_BuyActionPointLimit，0-5）买满后，
        # 不再点击石油查看剩余次数（多余交互），直接按已达上限处理；
        # min(buy_limit, 5) 仅为防御手改配置超范围时按游戏每周 5 次兜底。
        # 跨周重置由 _get_buy_action_point_count 内部完成。
        # 非智能调度实例（无计数器方法）保持原 OCR 核对流程。
        if hasattr(self, '_get_buy_action_point_count'):
            stored_count = self._get_buy_action_point_count()
            if buy_limit <= 0 or stored_count >= min(buy_limit, 5):
                logger.info(
                    f'[大世界-行动点] 本地计数已购 {stored_count} 次'
                    f'（上限 {buy_limit}），跳过石油查看，不再购买'
                )
                return False
        if not self.action_point_set_button(0):
            # 没切到石油页签就没点击，本轮购买作废延后重跑（未点不存在未确认）。
            logger.warning('[大世界-行动点] 无法选择石油购买行动力，本轮购买作废，延后处理')
            raise ActionPointLimit(
                current=int(self._action_point_current),
                total=self._action_point_total,
            )
        current = self.action_point_get_buy_remain()
        buy_max = 5  # 当前版本中，玩家每周可购买 5 次行动力
        buy_count = buy_max - current
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
            self.action_point_use(selected_index=0)
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
                    # action_point_buy 内部的 action_point_use 已双重确认并重读
                    # 当前值与库存；不再 safe_get 重读，避免动画滞后的旧帧
                    # 覆盖已确认真值（与下方盒路径同一处理）。
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

            # 买行动力模式期间禁止使用行动力箱子：只允许石油购买行动力，
            # 行动力缺口抛 ActionPointLimit 交回买行动力主循环做中央购买
            if getattr(self, '_os_ap_box_forbidden', False):
                logger.info('[大世界-行动点] 买行动力模式期间禁止使用箱子，交回购买步骤')
                self.action_point_quit()
                raise ActionPointLimit(
                    current=self._action_point_current,
                    total=self._action_point_total,
                    cost=cost,
                )

            # 排序行动力药剂。
            # 分档选箱开关（默认关闭）开启时，按当前行动力定箱型：
            #   >=100 → 20 箱；80~99 → 50 箱；20~79 → 100 箱；
            #   对应档位没库存 → 回退上游排序；<20 → 维持上游排序。
            # 关闭时＝上游行为：不顶破自然上限（200）的箱子先用、档内小→大，
            # 防溢出、攒大箱应急。
            tier_index = None
            if getattr(self.config, 'OpsiGeneral_ActionPointTierBox', False):
                current = self._action_point_current
                if current >= 100:
                    tier_index = 1
                elif current >= 80:
                    tier_index = 2
                elif current >= 20:
                    tier_index = 3
            if tier_index is not None and self._action_point_box[tier_index] > 0:
                box = [tier_index]
            else:
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
                    if not self.action_point_set_button(box[0]):
                        # 没选中箱子就没点击 USE，本轮作废延后重跑（未点不存在未确认）。
                        logger.warning('[大世界-行动点] 无法选择行动力药剂，本轮补充作废，延后处理')
                        raise ActionPointLimit(
                            current=self._action_point_current,
                            total=self._action_point_total,
                            cost=cost,
                        )
                    self.action_point_use(selected_index=box[0])
                    # action_point_use 已在确认 USE 时重读当前值与库存；
                    # 不再额外重读一次，避免动画滞后的旧帧覆盖已确认真值。
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
