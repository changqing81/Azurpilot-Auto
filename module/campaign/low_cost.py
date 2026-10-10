"""低耗检测模块。

在滑动时间窗口内累计石油消耗，超过阈值即判定为「未在低耗 / 换错了队伍 / 低耗异常」。

设计要点：
- 只统计「石油下降」事件：石油随时间自然恢复造成的上升不计入，
  因此度量的是游戏实际扣除的石油总量，而非窗口首尾差值。
- 状态由调用方（``CampaignRun``）持有，随任务实例一起销毁，**不做持久化**。
  任务被抢占或结束后实例回收，恢复时重新建立基线，从而避免把
  「其它任务消耗的石油」误算到本任务头上（详见计划文档「三之三」）。
"""

from collections import deque
from datetime import timedelta


class LowCostChecker:
    """滑动窗口石油消耗统计器。

    Attributes:
        window_seconds (int): 滑动窗口长度（秒）。
        oil_limit (int): 窗口内允许的最大消耗量。
        oil_cap (int | None): 石油上限，用于过滤 OCR 误读；None 表示不校验。
        last_oil (int | None): 上一次有效读数。
        events (deque): 石油下降事件队列，元素为 ``(datetime, int)``。
        consumed (int): 当前窗口内累计消耗。
    """

    def __init__(self, window_seconds, oil_limit, oil_cap=None):
        self.window_seconds = max(1, int(window_seconds))
        self.oil_limit = int(oil_limit)
        self.oil_cap = (
            int(oil_cap) if isinstance(oil_cap, int) and not isinstance(oil_cap, bool)
            and oil_cap > 0 else None
        )
        self.last_oil = None
        self.events = deque()
        self.consumed = 0

    def update(self, oil, now):
        """记录一次石油读数，并返回窗口内累计消耗是否超过阈值。

        Args:
            oil (int): 本次读到的石油量。
            now (datetime.datetime): 本次读数的时间。

        Returns:
            bool: 窗口内累计消耗是否超过阈值。
        """
        # 无效读数（OCR 失败 / 非正整数）直接跳过，不污染状态
        if not isinstance(oil, int) or isinstance(oil, bool) or oil <= 0:
            return False
        # 读数超过石油上限视为 OCR 误读，跳过本帧（不更新基线）
        if self.oil_cap is not None and oil > self.oil_cap:
            return False

        # 只记录下降量；上升（自然恢复）与持平不计入
        if self.last_oil is not None and oil < self.last_oil:
            self.events.append((now, self.last_oil - oil))
        self.last_oil = oil

        # 淘汰滑出窗口的旧事件
        cutoff = now - timedelta(seconds=self.window_seconds)
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

        self.consumed = sum(amount for _, amount in self.events)
        return self.consumed > self.oil_limit
