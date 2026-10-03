"""大世界行动力账本（ActionPointLedger）。

账本是一个**记录器，不是推演器**：只保存最近一次 OCR 观测到的行动力真值
（当前行动力 + 含箱总行动力），供当前任务与后续任务直接读取；
不做自然恢复推演，也不模拟战斗/进场消耗——消耗造成的偏差由下一次读数自然纠正。

数据流（用户定稿，2026-10-03）：
1. 顶栏 OCR（海域内，MAX 值旁的白色数字）→ 记录当前行动力；
2. 判定：记录的当前行动力低于任务所需 → 开窗补充；弹窗内同时读到
   当前行动力与总行动力（含箱），记录进账本完成同步；
3. 任务（当前与后续）从账本读取这两个值，无需各自弹窗读数。

抗干扰只保留 OCR 合法性门（位数门 ≤4 位、非数字拒绝）；
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

# 顶栏读数允许的最大位数（≥5 位视为 OCR 拼接，如 154 + Lv.60 → 60154）
MAP_BAR_MAX_DIGITS = 4


class ActionPointLedger:
    """行动力账本：记录（observe）→ 读取（current / total_with_box）→ 判定（need_popup）。"""

    def __init__(self):
        self.current = None
        self.total_with_box = None
        self.box = None
        # 箱子折算价值 = total_with_box - current；只在弹窗记录（开箱/买油）时变化，
        # 顶栏读数只更新当前值，箱子价值保持不变
        self.box_value = 0
        self.recorded_at = None
        self.source = None

    # -------------------- 记录 --------------------

    def observe(self, current, total_with_box=None, source=SOURCE_POPUP, box=None, at=None):
        """把一次读数记录进账本。

        Args:
            current (int): 当前行动力（不含箱）。
            total_with_box (int | None): 恒含箱总行动力；仅弹窗能精确给出。
            source (str): 'popup' | 'map_bar'。
            box (sequence | None): 弹窗读到的箱子明细（4 格）。
            at (datetime | None): 读数时间（服务器时间），默认当前时间。

        Returns:
            bool: 是否被采纳。
        """
        current = self._clean_value(current)
        if current is None or current < 0:
            logger.warning(f'[AP账本] 忽略非法读数 current={current!r} source={source}')
            return False
        at = at or current_time()
        previous_current = self.current

        self.current = current
        if total_with_box is not None:
            total = self._clean_value(total_with_box)
            if total is not None and total >= current:
                self.total_with_box = total
        elif self.total_with_box is not None and previous_current is not None:
            # 顶栏只记录当前值：总行动力跟随修正（箱子价值保持不变），
            # 避免 total_with_box - current 的口径漂移
            self.total_with_box = max(current, self.total_with_box - (previous_current - current))
        if self.total_with_box is None or self.total_with_box < current:
            # 没有可用的总行动力读数时，至少保证不低于当前值
            self.total_with_box = current
        self.box_value = max(0, self.total_with_box - self.current)
        self.box = tuple(box) if box is not None else self.box
        self.recorded_at = at
        self.source = source
        logger.attr('AP账本', f'记录({source}) 当前={self.current} 总(含箱)={self.total_with_box}')
        return True

    # -------------------- 判定 --------------------

    def need_popup(self, cost, preserve=0, top_up_ceiling=None):
        """判定是否需要打开行动力弹窗：账本记录值 vs 任务所需。

        Args:
            cost (int): 任务所需行动力（开工线，当前行动力需达到该值）。
            preserve (int): 保留值（含箱总行动力不许低于该值，沿用各任务现状）。
            top_up_ceiling (int | None): 补充上限（仅要塞 320），由补充循环消费；
                开工线超过该值时直接按弹窗处理。

        Returns:
            bool: True = 需要弹窗（读真值或补充）；False = 账本记录够用，直接开工。
        """
        if top_up_ceiling is not None and cost > top_up_ceiling:
            logger.warning(f'[AP账本] 开工线 {cost} 超过补充上限 {top_up_ceiling}，按弹窗处理')
            return True
        if self.current is None:
            # 账本还没有任何记录，无从判定
            return True
        if self.current < cost:
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

    # -------------------- 持久化 --------------------

    def to_state(self):
        """转成可写入配置 json 的紧凑 dict；账本为空时返回空 dict。"""
        if self.current is None:
            return {}
        return {
            'v': 1,
            'current': self.current,
            'total': self.total_with_box,
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
            ledger.total_with_box = total if (total is not None and total >= current) else current
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
