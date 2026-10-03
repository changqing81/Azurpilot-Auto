"""大世界行动力账本（ActionPointLedger）。

记录最近一次校准的行动力读数，并按自然恢复速率（每 10 分钟 +1，当前行动力上限 200）
推演"现在应该是多少"，供大世界任务在不弹窗的情况下预判行动力是否够开工。

设计要点（详见计划文档 .workbuddy/plans/stellar-beacon-lovelace-j94o0aU_.md）：
- 双口径：`current`（当前行动力，不含箱，顶栏可见）与 `total_with_box`（恒含箱总行动力，
  与统计/体力图同口径）；仪表盘与统计只吃含箱口径，顶栏读数绝不直接进统计。
- 校准来源分级：`popup`（行动力弹窗，精确，同时拿到箱子明细）> `map_bar`（顶栏 OCR，±2）>
  `persisted`（从配置恢复，最多 medium）。
- 回血推演只受自然上限约束：`estimate()` 的回血部分封顶 `cap`，`observe()` 对过门读数
  照单全收（含 >200，允许"一口气补过 1k"的玩法）。
- 本模块保持纯逻辑（不依赖 device），全部方法可离线单测。
"""

from datetime import datetime

from module.config.time_source import now as current_time
from module.logger import logger

# 大世界行动力每 10 分钟自然回复 1 点（唯一常量来源，防止行动力溢出任务从这里引用）。
ACTION_POINT_RECOVER_SECONDS = 600
# 大世界当前行动力自然上限。
NATURAL_ACTION_POINT_LIMIT = 200

# 校准来源
SOURCE_POPUP = 'popup'
SOURCE_MAP_BAR = 'map_bar'
SOURCE_PERSISTED = 'persisted'

# 弹窗校准后多久内视为高置信
HIGH_CONFIDENCE_SECONDS = 120
# 顶栏读数与推演的允许偏差（用户给定 ±2）
MAP_BAR_TOLERANCE = 2
# 顶栏读数允许的最大位数（≥5 位视为 OCR 拼接，如 154 + Lv.60 → 60154）
MAP_BAR_MAX_DIGITS = 4


class ApEstimate:
    """账本推演结果。字段可能为 None（账本为空时）。"""

    __slots__ = ('current', 'total_with_box', 'age', 'confidence')

    def __init__(self, current, total_with_box, age, confidence):
        self.current = current
        self.total_with_box = total_with_box
        self.age = age
        self.confidence = confidence

    def __repr__(self):
        return (f'ApEstimate(current={self.current}, total_with_box={self.total_with_box}, '
                f'age={self.age}, confidence={self.confidence!r})')


class ActionPointLedger:
    """行动力账本：校准（observe）→ 记账（spend）→ 推演（estimate）→ 预判（need_popup）。"""

    def __init__(self, cap=NATURAL_ACTION_POINT_LIMIT, recover_seconds=ACTION_POINT_RECOVER_SECONDS):
        self.cap = int(cap)
        self.recover_seconds = int(recover_seconds)
        self.current = None
        self.total_with_box = None
        self.box = None
        # 箱子折算价值 = total_with_box - current；只在弹窗校准/注入时变化，
        # 战斗消耗只扣当前值，箱子不会消失
        self.box_value = 0
        self.calibrated_at = None
        self.source = None
        self.confidence = 'low'

    # -------------------- 校准 --------------------

    def observe(self, current, total_with_box=None, source=SOURCE_POPUP, box=None, at=None):
        """用一次读数校准账本。

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
            # 顶栏只校准当前值：总行动力跟随修正（箱子价值保持不变），
            # 避免 total_with_box - current 的口径漂移
            self.total_with_box = max(current, self.total_with_box - (previous_current - current))
        if self.total_with_box is None or self.total_with_box < current:
            # 没有可用的总行动力读数时，至少保证不低于当前值
            self.total_with_box = current
        self.box_value = max(0, self.total_with_box - self.current)
        self.box = tuple(box) if box is not None else self.box
        self.calibrated_at = at
        self.source = source
        self.confidence = 'high' if source == SOURCE_POPUP else 'medium'
        logger.attr('AP账本', f'校准({source}) 当前={self.current} 总(含箱)={self.total_with_box}')
        return True

    # -------------------- 记账 --------------------

    def spend(self, cost, reason='', at=None):
        """记一笔行动力增减。

        Args:
            cost (int): 消耗为正、补充为负（外部注入，如明石商店买箱）。
            reason (str): 记账原因，仅用于日志。
            at (datetime | None): 未使用，保留参数位以对齐 observe。

        Returns:
            bool: 是否记账成功（账本为空或 cost 为 0 时忽略）。
        """
        if self.current is None:
            return False
        try:
            cost = int(cost)
        except (TypeError, ValueError):
            return False
        if cost == 0:
            return False
        # 战斗消耗只扣当前值，箱子价值不变；过度扣减时当前值封底为 0，箱子保留
        self.current = max(0, self.current - cost)
        if self.total_with_box is not None:
            self.total_with_box = self.current + self.box_value
        logger.info(
            f'[AP账本] 记账 {reason or "spend"}: 变动 {-cost:+d} '
            f'→ 当前={self.current} 总(含箱)={self.total_with_box}'
        )
        return True

    # -------------------- 推演 --------------------

    def estimate(self, now=None):
        """推演当前时刻的行动力。

        回血部分封顶：自然恢复到 `cap`（默认 200）就不再上涨，
        但不会把 `observe()` 收到的更高观测值往下压。
        """
        now = now or current_time()
        if self.current is None or self.calibrated_at is None:
            return ApEstimate(None, None, None, 'low')
        age = max(0.0, (now - self.calibrated_at).total_seconds())
        recovered = int(age // self.recover_seconds) if age > 0 else 0
        current = min(self.current + recovered, max(self.cap, self.current))
        total = None
        if self.total_with_box is not None:
            # 总行动力的封顶 = 自然上限 + 箱子价值（含箱口径可以超过 200）
            total = min(
                self.total_with_box + recovered,
                max(self.cap + self.box_value, self.total_with_box),
            )
            total = max(total, current)
        return ApEstimate(int(current), int(total) if total is not None else None, age, self._confidence(age))

    def _confidence(self, age):
        if self.current is None or self.calibrated_at is None:
            return 'low'
        if self.source == SOURCE_POPUP:
            if age <= HIGH_CONFIDENCE_SECONDS:
                return 'high'
            if age > self.cap * self.recover_seconds:
                # 超过理论满回血时长（默认 200 点 ≈ 33 小时），读数已不可信
                return 'low'
            return 'medium'
        if self.source == SOURCE_MAP_BAR:
            return 'medium' if age <= self.cap * self.recover_seconds else 'low'
        # persisted：恢复时最多 medium，由 from_state 决定
        return self.confidence if self.confidence in ('medium', 'low') else 'medium'

    # -------------------- 预判 --------------------

    def need_popup(self, cost, preserve=0, top_up_ceiling=None, now=None):
        """预判是否需要打开行动力弹窗。

        Args:
            cost (int): 开工线（当前行动力需达到该值）。
            preserve (int): 保留值（含箱总行动力不许低于该值，沿用各任务现状）。
            top_up_ceiling (int | None): 补充上限（仅要塞 320），弹窗判定本身不受其影响，
                该值由补充循环消费；这里只做参数合法性保护。

        Returns:
            bool: True = 需要弹窗（校准或补充）；False = 账本够用，直接开工。
        """
        est = self.estimate(now=now)
        if est.confidence == 'low' or est.current is None:
            return True
        if top_up_ceiling is not None and cost > top_up_ceiling:
            logger.warning(f'[AP账本] 开工线 {cost} 超过补充上限 {top_up_ceiling}，按弹窗处理')
            return True
        if est.current < cost:
            return True
        if est.total_with_box is not None and est.total_with_box <= preserve:
            # 保留值守卫：总行动力会被保留值拦截时，老实弹窗（走 ActionPointLimit 正常延后）
            return True
        return False

    # -------------------- 顶栏读数门 --------------------

    @staticmethod
    def sanitize_map_bar_value(value):
        """位数门 + 范围门。

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

    @classmethod
    def judge_map_bar_reading(cls, value, estimate, tolerance=MAP_BAR_TOLERANCE, last_value=None):
        """对一次顶栏读数做完整判定（位数门 → 范围门 → 变化率门 → 连续一致门）。

        Args:
            value (int): OCR 原始整数（-1 等哨兵也会被处理）。
            estimate (ApEstimate | None): 当前推演值。
            tolerance (int): 变化率门容差（±2）。
            last_value (int | None): 上一帧已通过位数门的读数，用于连续一致门。

        Returns:
            tuple[str, int | None, str]: (verdict, 清洗值或 None, 原因)。
                verdict: 'reject'（明显垃圾，直接丢）| 'ok'（采纳）
                         | 'pending'（单帧，等下一帧确认）| 'suspect'（疑似误读/外部变动）
        """
        cleaned, reason = cls.sanitize_map_bar_value(value)
        if cleaned is None:
            return 'reject', None, reason
        if estimate is None or estimate.current is None:
            return 'pending', cleaned, 'no-baseline'
        if abs(cleaned - estimate.current) > tolerance:
            return 'suspect', cleaned, f'drift={cleaned - estimate.current:+d}'
        if last_value is not None and last_value != cleaned:
            return 'pending', cleaned, 'not-consistent'
        return 'ok', cleaned, 'within-tolerance'

    # -------------------- 持久化 --------------------

    def to_state(self):
        """转成可写入配置 json 的紧凑 dict；账本为空时返回空 dict。"""
        if self.current is None:
            return {}
        return {
            'v': 1,
            'current': self.current,
            'total': self.total_with_box,
            'at': self.calibrated_at.isoformat(sep=' ', timespec='seconds') if self.calibrated_at else None,
            'source': self.source,
        }

    @classmethod
    def from_state(cls, state, cap=NATURAL_ACTION_POINT_LIMIT, recover_seconds=ACTION_POINT_RECOVER_SECONDS):
        """从配置 json 恢复账本；解析失败返回空账本（调用方自动走旧路径弹窗）。"""
        ledger = cls(cap=cap, recover_seconds=recover_seconds)
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
                ledger.calibrated_at = datetime.fromisoformat(str(at))
            ledger.source = SOURCE_PERSISTED
            # 从盘恢复的读数最多 medium：重启期间可能发生脚本外的行动力变动
            ledger.confidence = 'medium'
        except Exception as e:
            logger.warning(f'[AP账本] 状态恢复失败，使用空账本: {type(e).__name__}: {e}')
            return cls(cap=cap, recover_seconds=recover_seconds)
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


class HourlyQuota:
    """按小时滑动窗口限流的复核额度。

    只统计"因读数存疑而主动弹窗复核"的次数；
    补充行动力等正常业务弹窗不经过这里，不受影响。
    """

    def __init__(self, limit, window_seconds=3600):
        self.limit = int(limit)
        self.window_seconds = int(window_seconds)
        self.events = []

    def _prune(self, now):
        cutoff = now.timestamp() - self.window_seconds
        self.events = [t for t in self.events if t >= cutoff]

    def allow(self, now=None):
        """当前是否还有额度。"""
        if self.limit <= 0:
            return False
        now = now or current_time()
        self._prune(now)
        return len(self.events) < self.limit

    def record(self, now=None):
        """记一次复核。"""
        now = now or current_time()
        self._prune(now)
        self.events.append(now.timestamp())
