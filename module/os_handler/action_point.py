"""大世界行动力管理模块。

处理大世界（Operation Siren）模式下的行动力（Action Point）管理。
包含行动力数值的 OCR 识别、适应性属性读取、药剂（AP Box）库存解析，
以及自动购买或使用补给品的交互逻辑。
"""
# 此文件处理大世界（Operation Siren）模式下的行动力（Action Point, AP）管理。
# 包含行动力数值 OCR 识别、药剂（AP Box）库存解析以及自动购买或使用补给的交互逻辑。
import copy
import time
from datetime import datetime, timedelta

import module.config.server as server
from module.base.async_executor import async_executor
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
from module.os_handler.os_status import OCR_SHOP_YELLOW_COINS
from module.statistics.item import Item, ItemGrid
from module.ui.assets import OS_CHECK
from module.ui.ui import UI
from module.log_res import LogRes
from module.os_handler.action_point_ledger import (
    NATURAL_ACTION_POINT_LIMIT,
    SOURCE_AUTO_SEARCH,
    SOURCE_ENTRY,
    SOURCE_ENTRY_FALLBACK,
    SOURCE_MAP_BAR,
    SOURCE_POPUP,
    SOURCE_SHOP,
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
# 静默读数专用实例：与主线程共用的 OCR 对象分开，避免 invalid_reason 等实例状态
# 跨线程互相覆盖，并关掉 Ocr 自带的耗时日志（后台静默读数不该在日志里刷屏，
# 读到的值由 AP 账本自己记一行）
SILENT_ACTION_POINT_DIGIT = MapActionPointDigit(ACTION_POINT_REMAIN_OS, name='SILENT_ACTION_POINT_DIGIT')
SILENT_ACTION_POINT_DIGIT.SHOW_LOG = False
# 静默读数的黄币 OCR：复制商店黄币 OCR 的取色参数（含日服分支），同样关掉耗时日志
SILENT_YELLOW_COIN_OCR = copy.copy(OCR_SHOP_YELLOW_COINS)
SILENT_YELLOW_COIN_OCR.SHOW_LOG = False

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

    故意继承 ActionPointLimit：所有捕获行动力不足的地方（智能调度、
    月末清理、短猫相接、各海域任务）会自然按「延后重跑」处理，
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
    # USE 按钮点击前的重试等待秒数：覆盖弹窗刷新动画/网络波动导致的
    # 按钮短暂消失（2026-10-14 真机：上一瓶确认后 2ms 内尝试下一瓶，
    # USE 单帧检测失败即抛「行动力不足」，含箱 6477 被判不足推迟到次日）。
    AP_USE_RETRY_SECONDS = 5
    # 进入/退出行动力弹窗的等待上限（秒）。界面不匹配时必须退出，否则会一直
    # 截图空转，只能等设备层 `stuck_timer_long`（195s）兜底抛 GameStuckError
    # （2026-10-14 线上事故：194 秒零日志、零点击、零文件写入）。
    ACTION_POINT_ENTER_TIMEOUT = 15
    ACTION_POINT_QUIT_TIMEOUT = 15

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

        # USE 按钮可能因弹窗刷新动画/网络波动短暂消失（2026-10-14 真机日志：
        # 上一瓶确认后 2ms 内尝试下一瓶，USE 单帧检测失败即抛「行动力不足」，
        # 把含箱 6477 判成不足并推迟到次日）。点击前等待重试：
        # 未点击就不存在未确认状态，不违反单发原则。
        timeout = Timer(self.AP_USE_RETRY_SECONDS).start()
        while 1:
            if self.appear(ACTION_POINT_USE, offset=(20, 20)):
                break
            if timeout.reached():
                # USE 按钮没出现就没有点击，本轮作废延后重跑（未点不存在未确认）。
                logger.warning(
                    f'[大世界-行动点] USE 按钮未出现（已重试 {self.AP_USE_RETRY_SECONDS} 秒），'
                    f'本轮补充作废，延后处理'
                )
                raise ActionPointLimit(
                    current=prev,
                    total=self._action_point_total,
                    cost=ACTION_POINT_BOX[selected_index],
                )
            self.device.screenshot()
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
    # 账本落盘会连带写入的键（全是脚本内部状态）：待写集合不超过这些键时，
    # 保存日志降为 debug，避免按轮刷「保存配置」行
    AP_LEDGER_SAVE_KEYS = (
        'OpsiScheduling.Storage.Storage.ApLedgerState',
        'Dashboard.ActionPoint.Value',
        'Dashboard.ActionPoint.Record',
        'Dashboard.ActionPoint.Total',
    )
    # 账本新鲜窗口：窗口内的记录就是「这一刻」的真值，决策读直接沿用、不再 OCR。
    # 行动力只在出击时变化，一次战斗十余秒，窗口取一分钟足够覆盖决策时点。
    AP_LEDGER_FRESH_SECONDS = 60

    def _ap_ledger_enabled(self):
        return bool(getattr(self.config, 'OpsiGeneral_ActionPointLedgerEnabled', False))

    def _ap_ledger_is_fresh(self):
        """账本记录是否还在新鲜窗口内（用于免去重复的顶栏读数）。"""
        ledger = self._get_ap_ledger()
        if ledger is None or ledger.current is None or ledger.recorded_at is None:
            return False
        age = (current_time() - ledger.recorded_at).total_seconds()
        return 0 <= age <= self.AP_LEDGER_FRESH_SECONDS

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

        待写键全是账本与总览显示这类内部状态时用静默保存（日志降 debug）：
        账本每轮/按周期落盘，INFO 记一行会把日志区刷满；一旦混入其它改动，
        照旧走 INFO，不掩盖别人的保存。
        """
        ledger = self.__dict__.get('_ap_ledger_instance')
        if ledger is None:
            return
        try:
            state = ledger.to_state()
            if not state:
                return
            self.config.modified[self.AP_LEDGER_STATE_PATH + '.ApLedgerState'] = state
            quiet = set(self.config.modified) <= set(self.AP_LEDGER_SAVE_KEYS)
            self.config.save(quiet=quiet)
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

        账本记录还在新鲜窗口内（AP_LEDGER_FRESH_SECONDS）时直接沿用账本值、
        不重复 OCR：静默识别与明石商店同步刚刚读过的话，决策读不必再读一次。

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
        if image is None and self._ap_ledger_is_fresh():
            # 账本刚被静默识别/商店同步刷新过：这一刻的真值已在账本里，不再重复读数
            logger.debug(
                f'[AP账本] 顶栏读数跳过：账本记录于 {ledger.recorded_at}，仍在新鲜窗口内'
            )
            return ledger.current
        try:
            raw = MAP_ACTION_POINT_DIGIT.ocr(image or self.device.image)
        except Exception as e:
            logger.warning(f'[AP账本] 顶栏读数 OCR 失败: {type(e).__name__}: {e}')
            return None
        value, reason = ActionPointLedger.sanitize_map_bar_value(raw)
        if value is None:
            logger.info(f'[AP账本] source=map_bar 读数={raw} 已丢弃({reason})')
            return None
        if self._ap_apply_ledger_reading(ledger, value, source=SOURCE_MAP_BAR):
            return value
        return None

    def _ap_apply_ledger_reading(self, ledger, value, source, coins_raw=None):
        """把一条顶栏读数写进账本，并同步本轮行动力属性与总览显示。

        行动力属性同步：跳过弹窗的轮次不会走 action_point_update()，若不在这里
        刷新，_action_point_total 会停留在类属性默认 0，导致行动力推送发出
        「总行动力: 0」，且最低保留检查误判行动力不足而推迟任务。

        只写 Dashboard 显示键、不写 LogRes：顶栏读数与静默读数都不混入
        行动力趋势快照与统计。

        Args:
            ledger (ActionPointLedger): 账本实例。
            value (int): 已过合法性门的顶栏行动力读数。
            source (str): 账本记录来源（map_bar / entry / entry_fallback / shop）。
            coins_raw (int | None): 同一行顶栏的黄币读数，随行记录；无则为 None。

        Returns:
            bool: 是否已写入账本。
        """
        try:
            if not ledger.observe(value, source=source, yellow_coins=coins_raw):
                return False
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
            self.config.modified['Dashboard.ActionPoint.Value'] = int(value)
            self.config.modified['Dashboard.ActionPoint.Record'] = \
                datetime.now().replace(microsecond=0)
            if ledger.total_with_box is not None:
                self.config.modified['Dashboard.ActionPoint.Total'] = \
                    int(ledger.total_with_box)
            self._save_ap_ledger_state()
        except Exception:
            logger.exception(f'[AP账本] {source} 记录失败')
            return False
        return True

    # ==================== 入场静默识别（进海域立即后台读 AP + 黄币） ====================
    # 进海域后立即在后台线程读一次顶栏（行动力 + 同一行黄币）并写进 AP 账本：
    # 主流程不等 OCR 结果，识别没能在进入战斗前完成即本轮作废，等下一轮重新识别。
    # 自动搜索（跑图）期间继续按周期静默读数，账本跟着消耗走，读数同样在后台完成。
    # 全部由 AP 账本开关（OpsiGeneral.ActionPointLedgerEnabled）控制，
    # 开关关闭时这些方法首行返回，不产生任何额外 OCR、写盘与日志。

    # 首帧读数非法时最多再等这么久，等主流程换一帧新画面重试（进图动画 /
    # 加载界面下顶栏数值可能尚未渲染完成）。轮询只在后台线程内进行。
    AP_ENTRY_SCAN_REFIRE_TIMEOUT = 1.5
    # 后台等新画面时的轮询间隔
    AP_ENTRY_SCAN_POLL_INTERVAL = 0.1
    # 自动搜索期间的读数周期：一场战斗十余秒、单点消耗十几到几十点，
    # 二十秒一次约合每场战斗一次读数，既不漏掉消耗也不挤占主流程的 OCR 队列
    AP_ENTRY_SCAN_PERIOD = 20

    def ap_entry_scan_start(self):
        """入场静默识别：进入海域地图后立即触发一次后台读数，不等待结果。

        同时布防「自动搜索期间周期读数」，让账本在跑图过程中持续更新。

        - AP 账本开关关闭：直接返回（完全退回原有行为，零额外 OCR 与日志）；
        - 开关开启：复制当前帧交给后台执行器，主流程继续进图逻辑；
        - 每次入场自增 token：上一次未消费的结果作废，连续快速进出海域时
          上一次的作废结果不会污染下一次识别。

        入场读数由 ap_entry_scan_commit_before_battle() 在开战前取回写入账本；
        没取到就由 ap_entry_scan_fallback() 在收尾时兜底补记。
        """
        if not self._ap_ledger_enabled():
            return
        # 新的一轮：本轮「是否已记录」清零（本轮先成功写入的读数为准）
        self.__dict__['_ap_entry_scan_recorded'] = False
        self.__dict__['_ap_entry_scan_periodic'] = True
        self.__dict__['_ap_entry_scan_next_at'] = time.monotonic() + self.AP_ENTRY_SCAN_PERIOD
        self._ap_entry_scan_dispatch(source=SOURCE_ENTRY, require_map=False)

    def _ap_entry_scan_dispatch(self, source, require_map):
        """派发一次后台读数（不等待结果）。

        Args:
            source (str): 账本来源标签（entry / auto_search）。
            require_map (bool): 是否要求帧是海域地图画面才采纳读数。
                自动搜索期间的周期读数开着它：跑图时画面会经过战斗/加载/弹窗，
                那些画面上的数字不是顶栏行动力。
        """
        image = getattr(self.device, 'image', None)
        if image is None:
            logger.debug('[AP账本] 静默识别跳过：当前没有可用截图')
            return
        # 复制帧快照：主流程随后会继续截图覆盖 device.image，后台线程只能对副本
        # 做 OCR（设备对象归主线程所有）；复制本身不到 1ms，不阻塞跑图
        try:
            frame = np.array(image)
        except Exception:
            logger.debug('[AP账本] 静默识别跳过：当前帧无法复制')
            return
        token = self.__dict__.get('_ap_entry_scan_token', 0) + 1
        self.__dict__['_ap_entry_scan_token'] = token
        previous = self.__dict__.get('_ap_entry_scan_state')
        if previous is not None:
            logger.debug(f"[AP账本] 上一次静默识别结果未消费(token={previous.get('token')})，丢弃")
        # 后台任务只派发、不等待；派发失败（执行器异常）同样不能影响进图
        try:
            future = async_executor.submit(self._ap_entry_scan_worker, frame, image, require_map)
        except Exception as e:
            logger.warning(f'[AP账本] 静默识别派发失败: {type(e).__name__}: {e}')
            return
        self.__dict__['_ap_entry_scan_state'] = {
            'token': token,
            'source': source,
            'future': future,
        }
        logger.debug(f'[AP账本] 静默识别已派发({source} token={token})')

    def ap_entry_scan_tick(self):
        """自动搜索（跑图）期间的静默读数：取回上一次结果，必要时派发下一次。

        由 handle_map_event() 覆写在每个地图画面上调用；每次调用只做两次属性
        判断和一次时间比较，取回结果时才写盘（主线程），派发时只复制一帧，
        全程不等待 OCR，因此不会让跑图停下来等识别。
        """
        if not self._ap_ledger_enabled():
            return
        if not self.__dict__.get('_ap_entry_scan_periodic', False):
            return
        state = self.__dict__.get('_ap_entry_scan_state')
        if state is not None:
            future = state.get('future')
            if future is None or not future.done():
                # 上一次还没回来：先不派发新的，避免排队堆积
                return
            self.__dict__.pop('_ap_entry_scan_state', None)
            self._ap_entry_scan_commit_result(state)
        if time.monotonic() < self.__dict__.get('_ap_entry_scan_next_at', 0):
            return
        if self._ap_ledger_is_fresh():
            # 账本刚被商店同步 / 入场识别刷新过：这一刻的真值已在账本里，
            # 跑图期间也不重复读数；账本过期后下一个地图画面机会自动补读
            return
        self.__dict__['_ap_entry_scan_next_at'] = time.monotonic() + self.AP_ENTRY_SCAN_PERIOD
        self._ap_entry_scan_dispatch(source=SOURCE_AUTO_SEARCH, require_map=True)

    def handle_map_event(self, drop=None):
        """覆盖地图事件处理：每次上下文顺手看一眼顶栏（AP 账本开启时才真的读）。

        ActionPointHandler 在 MRO 中先于 MapEventHandler，因此这个覆写对所有
        大世界任务生效；返回值与 super() 完全一致，不改变事件处理语义。
        """
        result = super().handle_map_event(drop=drop)
        self.ap_entry_scan_tick()
        return result

    def _ap_entry_scan_commit_result(self, state):
        """取回一次已完成的静默识别并写账本（主线程）。

        Args:
            state (dict): _ap_entry_scan_state 的实体。

        Returns:
            bool: 是否写入账本。
        """
        source = state.get('source', SOURCE_ENTRY)
        future = state.get('future')
        try:
            ap_raw, coins_raw = future.result(timeout=0)
        except Exception as e:
            logger.info(f'[AP账本] {source} token={state.get("token")} 识别失败({type(e).__name__}: {e})')
            return False
        if ap_raw is None and coins_raw is None:
            # 后台判定该帧不是海域地图（战斗/加载/其它页面）或 OCR 失败
            logger.debug(f'[AP账本] {source} token={state.get("token")} 无有效读数，跳过')
            return False
        return self._ap_entry_scan_apply(ap_raw, coins_raw, source=source)

    def _ap_entry_scan_worker(self, frame, origin, require_map=False):
        """后台线程：对帧做行动力 + 黄币 OCR，返回 (行动力, 黄币)。

        Args:
            frame: 触发时刻的帧副本（后台线程只读）。
            origin: 触发时刻 device.image 的对象引用，用于判断是否出现了新帧。
            require_map: 帧必须是海域地图画面才读数（自动搜索期间的周期读数用）。

        Returns:
            tuple[int | None, int | None]: OCR 原始读数；失败时为 None。
                合法性门由主线程的 _ap_entry_scan_apply() 统一执行。

        只做纯 OCR：不碰配置、不写账本——配置与账本写盘一律留在主线程。
        """
        if require_map and not self._ap_entry_scan_frame_in_map(frame):
            return None, None

        def read(target):
            try:
                ap = SILENT_ACTION_POINT_DIGIT.ocr(target)
            except Exception as e:
                logger.debug(f'[AP账本] 静默识别行动力 OCR 失败: {type(e).__name__}: {e}')
                return None, None
            return ap, self._ap_entry_scan_ocr_coins(target)

        ap, coins = read(frame)
        if not require_map and ActionPointLedger.sanitize_map_bar_value(ap)[0] is None:
            # 入场首帧读数非法（顶栏还没渲染出来）：等一帧新画面再试一次。
            # 等待只发生在后台线程内，主流程不等它。
            retry = self._ap_entry_scan_next_frame(origin)
            if retry is not None:
                ap, coins = read(retry)
        return ap, coins

    @staticmethod
    def _ap_entry_scan_frame_in_map(image):
        """只按帧判断画面是不是海域地图（后台线程不能碰设备对象）。

        跑图期间画面会经过战斗、加载、弹窗等非地图画面，那些画面上的数字不是
        顶栏行动力，必须丢弃。模板匹配只读传入的帧，不访问 device。
        """
        try:
            return bool(IN_MAP.match_luma(image, offset=(200, 5)))
        except Exception as e:
            logger.debug(f'[AP账本] 帧内海域判定失败: {type(e).__name__}: {e}')
            return False

    def _ap_entry_scan_next_frame(self, origin):
        """后台等一帧新画面（最多 AP_ENTRY_SCAN_REFIRE_TIMEOUT 秒）。

        Args:
            origin: 触发时刻 device.image 的对象引用。

        Returns:
            image | None: 新帧（只读，不做复制）；超时仍没有新帧返回 None。
        """
        deadline = time.monotonic() + self.AP_ENTRY_SCAN_REFIRE_TIMEOUT
        while time.monotonic() < deadline:
            current = getattr(self.device, 'image', None)
            if current is not None and current is not origin:
                return current
            time.sleep(self.AP_ENTRY_SCAN_POLL_INTERVAL)
        return None

    @staticmethod
    def _ap_entry_scan_ocr_coins(image):
        """读同一行顶栏的黄币读数（大世界顶栏常驻，海域内与商店内都在）。

        用静默实例（SILENT_YELLOW_COIN_OCR，无耗时日志）；黄币随行动力一起写进
        账本，读数非法时返回 None（账本保留上一有效值）。
        """
        try:
            return int(SILENT_YELLOW_COIN_OCR.ocr(image))
        except Exception as e:
            logger.debug(f'[AP账本] 静默识别黄币 OCR 失败: {type(e).__name__}: {e}')
            return None

    def ap_entry_scan_commit_before_battle(self):
        """开战前取回入场识别结果并写入账本（非阻塞，不等待 OCR）。

        取回点刻意安排在发起战略搜索 / 自律寻敌之前，因此「结果还没就绪」
        在结构上等价于「识别完成前已经进入战斗」——本轮入场读数作废，
        不写账本，等下一轮入场重新识别（收尾兜底仍会在轮末补记一次）。

        Returns:
            bool: 是否写入账本。
        """
        if not self._ap_ledger_enabled():
            return False
        if self.__dict__.get('_ap_entry_scan_recorded', False):
            logger.debug('[AP账本] 本轮已记录，忽略开战前的入场识别结果')
            return False
        state = self.__dict__.pop('_ap_entry_scan_state', None)
        if state is None:
            return False
        token = state.get('token')
        future = state.get('future')
        if future is None or not future.done():
            logger.info(f'[AP账本] entry token={token} 识别未在进入战斗前完成，本轮作废')
            return False
        return self._ap_entry_scan_commit_result(state)

    def ap_entry_scan_fallback(self, reason=''):
        """收尾兜底识别：事件/明石/领奖处理完毕时，本轮尚未成功记录过才补记。

        用当前帧直接读一次顶栏（行动力 + 黄币），同步调用、不派后台任务：
        收尾节点允许这点读数开销，换来的是入场识别作废的轮次不空记录。
        当前不在大世界地图上时直接跳过，避免把别的画面上的数字当成读数。

        Args:
            reason (str): 收尾节点名称，仅用于日志。

        Returns:
            bool: 是否写入账本。
        """
        # 本轮结束：撤防自动搜索期间的周期读数，未消费的识别结果一并丢弃
        self.__dict__['_ap_entry_scan_periodic'] = False
        self.__dict__.pop('_ap_entry_scan_state', None)
        if not self._ap_ledger_enabled():
            return False
        if self.__dict__.get('_ap_entry_scan_recorded', False):
            return False
        is_in_map = getattr(self, 'is_in_map', None)
        if not callable(is_in_map):
            return False
        logger.debug(f'[AP账本] 兜底识别触发({reason or "收尾"})')
        # 兜底识别是辅助功能：读数失败一律放弃本轮回退原有流程，
        # 不让设备侧异常冒泡进任务收尾
        try:
            if not is_in_map():
                logger.debug('[AP账本] 兜底识别跳过：当前不在大世界地图')
                return False
            ap_raw = SILENT_ACTION_POINT_DIGIT.ocr(self.device.image)
            coins_raw = self._ap_entry_scan_ocr_coins(self.device.image)
        except Exception as e:
            logger.info(f'[AP账本] 兜底识别({reason or "收尾"}) 读数异常: {type(e).__name__}: {e}')
            return False
        return self._ap_entry_scan_apply(ap_raw, coins_raw, source=SOURCE_ENTRY_FALLBACK)

    def ap_entry_scan_shop_sync(self, yellow_coins=None):
        """明石商店：与黄币识别同刻读一次顶栏行动力，两项一并写进账本。

        由 CoinTaskMixin 覆写的 os_shop_get_coins() 在明石商店流程内调用：
        黄币直接复用刚刚（同一帧或相邻帧）确认过的读数，不再重复 OCR。

        Args:
            yellow_coins (int | None): 黄币读数；缺省取商店流程刚写入的
                self._shop_yellow_coins。

        Returns:
            bool: 是否写入账本。
        """
        if not self._ap_ledger_enabled():
            return False
        if yellow_coins is None:
            yellow_coins = getattr(self, '_shop_yellow_coins', None)
        try:
            ap_raw = SILENT_ACTION_POINT_DIGIT.ocr(self.device.image)
        except Exception as e:
            logger.info(f'[AP账本] 明石商店同步读数 OCR 失败: {type(e).__name__}: {e}')
            return False
        recorded = self.__dict__.get('_ap_entry_scan_recorded', False)
        if not self._ap_entry_scan_apply(ap_raw, yellow_coins, source=SOURCE_SHOP):
            return False
        if recorded:
            logger.debug('[AP账本] 明石商店读数覆盖本轮已有记录（先到先得，不重复计数）')
        return True

    def _ap_entry_scan_apply(self, ap_raw, coins_raw, source):
        """门禁 + 入账：把一次静默识别的读数写进账本（只在主线程调用）。

        Args:
            ap_raw (int | None): 顶栏行动力 OCR 原始读数。
            coins_raw (int | None): 同一行顶栏黄币 OCR 原始读数（可为 None）。
            source (str): 账本记录来源（entry / entry_fallback / shop）。

        Returns:
            bool: 是否写入账本。
        """
        if not self._ap_ledger_enabled():
            return False
        ledger = self._get_ap_ledger()
        if ledger is None:
            return False
        value, reason = ActionPointLedger.sanitize_map_bar_value(ap_raw)
        if value is None:
            logger.info(f'[AP账本] {source} 读数={ap_raw!r} 已丢弃({reason})')
            return False
        if not self._ap_apply_ledger_reading(ledger, value, source=source, coins_raw=coins_raw):
            return False
        # 本轮已有记录：先成功写入的读数为准
        self.__dict__['_ap_entry_scan_recorded'] = True
        return True

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

        等待行动力弹窗完全加载，并处理可能的地图事件。弹窗没有出现时本轮
        直接作废：不能按弹窗坐标去 OCR 无关画面，否则整屏垃圾数字会被当成
        真值落盘（2026-10-15 事故：任务被抢占退出大世界后仍读，读出
        0/1/2/7/22 并污染 Dashboard、统计图与行动力账本）。

        Returns:
            bool: True = 弹窗出现且数值已读到；False = 弹窗未出现或数值
                始终未加载，本轮读数作废（不更新行动力属性）。
        """
        timeout = Timer(3, count=6).start()
        popup_visible = False
        for _ in self.loop():
            # 结束
            if self.is_current_ap_visible():
                popup_visible = True
                break
            if timeout.reached():
                logger.warning('[大世界-行动点] 获取行动点超时')
                break
            # 处理行动力弹窗上方的强制地图事件
            if self.handle_map_event():
                timeout.reset()
                continue

        if not popup_visible:
            logger.warning('[大世界-行动点] 行动力弹窗未出现，本轮读数作废')
            return False

        skip_first_screenshot = True
        timeout = Timer(1, count=2).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning('[大世界-行动点] 获取行动点超时')
                return False
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
                    return True
                else:
                    # [11, 0, 1, 0]
                    continue
            # 或者拥有石油
            # 页面未完全加载时可能为 0 或 1
            # [1, 0, 0, 0]
            if oil > 100:
                return True

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

        界面不匹配时按 `ACTION_POINT_QUIT_TIMEOUT` 超时退出，避免无超时循环
        截图空转到设备层 195s 卡死兜底。

        Pages:
            in: ACTION_POINT_USE
            out: page_os
        """
        for _ in self.loop(timeout=self.ACTION_POINT_QUIT_TIMEOUT):
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
        else:
            logger.warning(
                f'[大世界-行动点] 退出行动力弹窗超时（{self.ACTION_POINT_QUIT_TIMEOUT}s）'
            )

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

        界面不匹配时按 `ACTION_POINT_ENTER_TIMEOUT` 超时退出：原先的无超时
        循环会一直截图空转，只能等设备层 195s 卡死兜底（2026-10-14 线上事故）。

        Pages:
            in: OS_CHECK
            out: ACTION_POINT_USE
        """
        for _ in self.loop(timeout=self.ACTION_POINT_ENTER_TIMEOUT):
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
            # 作战情报页面未关闭时先关闭，避免在错误页面上等待导致卡死
            if self.appear_then_click(MISSION_QUIT, offset=(20, 20), interval=2):
                continue
        else:
            logger.warning(
                f'[大世界-行动点] 进入行动力弹窗超时（{self.ACTION_POINT_ENTER_TIMEOUT}s），'
                f'当前页面可能不在大世界'
            )

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
        for _ in self.loop(timeout=self.ACTION_POINT_QUIT_TIMEOUT):
            if self.appear(IN_MAP, offset=(200, 5)):
                break
        else:
            logger.warning(
                f'[大世界-行动点] 等待行动力弹窗关闭超时（{self.ACTION_POINT_QUIT_TIMEOUT}s）'
            )

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
        for _ in self.loop(timeout=self.ACTION_POINT_QUIT_TIMEOUT):
            if self.appear(IN_MAP, offset=(200, 5)):
                break
        else:
            logger.warning(
                f'[大世界-行动点] 等待回到大世界地图超时（{self.ACTION_POINT_QUIT_TIMEOUT}s）'
            )

        return enough
