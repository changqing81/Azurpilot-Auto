"""大世界行动力账本（ActionPointLedger）。

账本是一个**记录器，不是推演器**：只保存最近一次 OCR 观测到的行动力真值
（当前行动力 + 含箱总行动力），供当前任务与后续任务直接读取；
不做自然恢复推演，也不模拟战斗/进场消耗——消耗造成的偏差由下一次读数自然纠正。

数据流（2026-10-03）：
1. 顶栏 OCR（海域内，MAX 值旁的白色数字）→ 记录当前行动力；
2. 判定：记录的当前行动力低于任务所需 → 开窗补充；弹窗内同时读到
   当前行动力与总行动力（含箱），记录进账本完成同步；
3. 任务（当前与后续）从账本读取这两个值，无需各自弹窗读数。

入场静默识别（进海域后立即后台读一次顶栏）与明石商店同步读数（读黄币的
同一时刻同读一次行动力）也走这里的 observe()，只是来源不同；它们顺带记录
同一行顶栏的黄币读数（yellow_coins），供对账与排查使用。

抗干扰只保留 OCR 合法性门（位数门 ≤4 位、非数字拒绝；黄币为正数且 ≤8 位）；
读数与账本旧记录之间不做任何比对——读到的真值就是账本的新记录。

本模块保持纯逻辑（不依赖 device），全部方法可离线单测。
"""

from datetime import datetime

from module.config.time_source import now as current_time
from module.logger import logger

# 大世界行动力每 10 分钟自然回复 1 点（唯一常量来源，防止行动力溢出任务从这里引用）。
ACTION_POINT_RECOVER_SECONDS = 600
# 大世界当前行动力自然上限。
NATURAL_ACTION_POINT_LIMIT = 200

# 记录来源
SOURCE_POPUP = 'popup'
SOURCE_MAP_BAR = 'map_bar'
SOURCE_PERSISTED = 'persisted'
# 入场静默识别：进入海域地图后立即在后台读一次顶栏（行动力 + 黄币）
SOURCE_ENTRY = 'entry'
# 收尾兜底识别：事件/明石/领奖处理完毕时，本轮尚未记录过才现场补记一次
SOURCE_ENTRY_FALLBACK = 'entry_fallback'
# 明石商店：识别黄币的同一时刻同步读一次行动力
SOURCE_SHOP = 'shop'

# 顶栏读数允许的最大位数（≥5 位视为 OCR 拼接，如 154 + Lv.60 → 60154）
MAP_BAR_MAX_DIGITS = 4
# 顶栏黄币允许的最大位数（黄币为七位数量级，≥9 位视为 OCR 拼接）
COIN_MAX_DIGITS = 8


class ActionPointLedger:
    """行动力账本：记录（observe）→ 读取（current / total_with_box）→ 判定（need_popup）。"""

    def __init__(self):
        self.current = None
        self.total_with_box = None
        self.box = None
        # 箱子折算价值 = total_with_box - current；只在弹窗记录（开箱/买油）时变化，
        # 顶栏读数只更新当前值，箱子价值保持不变
        self.box_value = 0
        # 黄币读数：只在入场静默识别与明石商店同步读数时提供，
        # 弹窗/顶栏行动力读数不带黄币，此时保留上一有效值
        self.yellow_coins = None
        self.recorded_at = None
        self.source = None

    # -------------------- 记录 --------------------

    def observe(self, current, total_with_box=None, source=SOURCE_POPUP, box=None, at=None, yellow_coins=None):
        """把一次读数记录进账本。

        Args:
            current (int): 当前行动力（不含箱）。
            total_with_box (int | None): 恒含箱总行动力；仅弹窗能精确给出。
            source (str): 'popup' | 'map_bar' | 'entry' | 'entry_fallback' | 'shop'。
            box (sequence | None): 弹窗读到的箱子明细（4 格）。
            at (datetime | None): 读数时间（服务器时间），默认当前时间。
            yellow_coins (int | None): 同一行顶栏的黄币读数（入场静默识别 /
                明石商店同步读数为读数时刻提供）；非法或未提供时保留上一有效值。

        Returns:
            bool: 是否被采纳。
        """
        current = self._clean_value(current)
        if current is None or current < 0:
            logger.warning(f'[AP账本] 忽略非法读数 current={current!r} source={source}')
            return False
        at = at or current_time()
        previous_current = self.current
        previous_total = self.total_with_box
        previous_coins = self.yellow_coins

        self.current = current
        if total_with_box is not None:
            total = self._clean_value(total_with_box)
            if total is not None and total >= current:
                self.total_with_box = total
        elif self.total_with_box is not None and previous_current is not None:
            # 顶栏只记录当前值：总行动力跟随修正（箱子价值保持不变），
            # 避免 total_with_box - current 的口径漂移
            self.total_with_box = max(current, self.total_with_box - (previous_current - current))
        if self.total_with_box is not None and self.total_with_box < current:
            # 当前值超过了记录的总行动力（脚本外开箱/买油）：总量至少跟随到当前值
            self.total_with_box = current
        # 注意：账本还没有含箱总量记录（从没弹过窗）时，total_with_box 保持 None——
        # 顶栏读不到箱子，不能拿当前值冒充总行动力，否则保留值判定会低估而误推迟。
        # 此时由 need_popup/决策读安排一次弹窗建立总量。
        self.box_value = max(0, (self.total_with_box or 0) - self.current)
        self.box = tuple(box) if box is not None else self.box
        if yellow_coins is not None:
            coins, reason = self.sanitize_coin_value(yellow_coins)
            if coins is None:
                # 黄币读数非法：不覆盖上一有效值（黄币只是随行记录，不影响行动力记录）
                logger.debug(f'[AP账本] 记录({source}) 黄币读数={yellow_coins!r} 已忽略({reason})')
            else:
                self.yellow_coins = coins
        self.recorded_at = at
        self.source = source
        detail = f'当前={self.current} 总(含箱)={self.total_with_box}'
        if self.yellow_coins is not None:
            detail += f' 黄币={self.yellow_coins}'
        # 同一读数被反复记录（顶栏/弹窗复核、跨轮重复读）时不再刷屏：
        # 只有值真正变化才记 info，重复记录降为 debug 备查。
        if (self.current, self.total_with_box, self.yellow_coins) != (previous_current, previous_total, previous_coins):
            logger.attr('AP账本', f'记录({source}) {detail}')
        else:
            logger.debug(f'[AP账本] 记录({source}) 与上次一致：{detail}')
        return True

    # -------------------- 判定 --------------------

    def need_popup(self, cost, preserve=0):
        """判定是否需要打开行动力弹窗：账本记录值 vs 任务所需。

        Args:
            cost (int): 任务所需行动力（开工线，当前行动力需达到该值）。
            preserve (int): 保留值（含箱总行动力不许低于该值，沿用各任务现状）。

        Returns:
            bool: True = 需要弹窗（读真值或补充）；False = 账本记录够用，直接开工。
        """
        if self.current is None:
            # 账本还没有任何记录，无从判定
            return True
        if self.current < cost:
            return True
        if preserve > 0 and self.total_with_box is None:
            # 总行动力（含箱）未知：保留值无法判定，老实弹窗读真值
            # （顺带建立含箱总量记录，之后顶栏快路径即可接管）
            return True
        if self.total_with_box is not None and self.total_with_box <= preserve:
            # 保留值守卫：总行动力会被保留值拦截时，老实弹窗（走 ActionPointLimit 正常延后）
            return True
        return False

    # -------------------- 顶栏读数门 --------------------

    @staticmethod
    def sanitize_map_bar_value(value):
        """顶栏读数的 OCR 合法性门（位数门 + 非负门）。

        Args:
            value (int): 顶栏 OCR 的原始整数结果（含 -1 这类哨兵）。

        Returns:
            tuple[int | None, str]: (清洗后的值或 None, 原因)。
                原因: 'empty' | 'negative' | 'len>4' | 'ok'
        """
        if value is None:
            return None, 'empty'
        try:
            v = int(value)
        except (TypeError, ValueError):
            return None, 'empty'
        if v < 0:
            return None, 'negative'
        if len(str(v)) > MAP_BAR_MAX_DIGITS:
            return None, 'len>4'
        return v, 'ok'

    @staticmethod
    def sanitize_coin_value(value):
        """顶栏黄币读数的 OCR 合法性门（正数门 + 位数门）。

        黄币与行动力不同，0 也是非法读数：界面未加载、被弹窗遮挡或
        与相邻元素连读时都可能读到 0 或超长数字串。

        Args:
            value (int): 顶栏 OCR 的原始整数结果（含 -1 这类哨兵）。

        Returns:
            tuple[int | None, str]: (清洗后的值或 None, 原因)。
                原因: 'empty' | 'non-positive' | 'len>8' | 'ok'
        """
        if value is None:
            return None, 'empty'
        try:
            v = int(value)
        except (TypeError, ValueError):
            return None, 'empty'
        if v <= 0:
            return None, 'non-positive'
        if len(str(v)) > COIN_MAX_DIGITS:
            return None, 'len>8'
        return v, 'ok'

    # -------------------- 持久化 --------------------

    def to_state(self):
        """转成可写入配置 json 的紧凑 dict；账本为空时返回空 dict。"""
        if self.current is None:
            return {}
        return {
            'v': 1,
            'current': self.current,
            'total': self.total_with_box,
            'coins': self.yellow_coins,
            'at': self.recorded_at.isoformat(sep=' ', timespec='seconds') if self.recorded_at else None,
            'source': self.source,
        }

    @classmethod
    def from_state(cls, state):
        """从配置 json 恢复账本；解析失败返回空账本（调用方自动走旧路径弹窗）。"""
        ledger = cls()
        if not state:
            return ledger
        try:
            current = cls._clean_value(state.get('current'))
            if current is None:
                return ledger
            ledger.current = current
            total = cls._clean_value(state.get('total'))
            # 总行动力缺失或非法时保持 None（未知）：由后续弹窗建立，
            # 不拿当前值冒充总行动力（会让保留值判定低估而误推迟）
            ledger.total_with_box = total if (total is not None and total >= current) else None
            coins, reason = cls.sanitize_coin_value(state.get('coins'))
            # 黄币非法/缺失时保持 None（未知），不参与任何判定
            ledger.yellow_coins = coins
            if coins is None and state.get('coins') is not None:
                logger.debug(f'[AP账本] 状态里的黄币={state.get("coins")!r} 已丢弃({reason})')
            at = state.get('at')
            if at:
                ledger.recorded_at = datetime.fromisoformat(str(at))
            ledger.source = SOURCE_PERSISTED
        except Exception as e:
            logger.warning(f'[AP账本] 状态恢复失败，使用空账本: {type(e).__name__}: {e}')
            return cls()
        return ledger

    # -------------------- 内部 --------------------

    @staticmethod
    def _clean_value(value):
        """把 OCR/配置里可能的脏值清洗成 int 或 None。"""
        if value is None:
            return None
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value
