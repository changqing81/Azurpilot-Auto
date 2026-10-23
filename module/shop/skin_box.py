"""军火商外观箱的本轮槽位账本，不依赖名称 OCR 或临时模板编号。"""

import re


DEFAULT_SKIN_BOX_LIMITS = '1.1.1.1.1.1.1.1.1.1'
SKIN_BOX_LIMITS_PATTERN = r'[0-9]+(?:\.[0-9]+){0,9}'


class SkinBoxLedger:
    """用固定的十个网格槽位隔离同名、同图商品，并按交易确认计数。

    counts 只保存成功次数；pending 是尚未确认的交易，不能当作成功。
    finished 保存达到目标或已经售罄的槽位，不参与已购种类数统计。
    """

    def __init__(self, value=DEFAULT_SKIN_BOX_LIMITS):
        """解析一个点号分隔输入框，缺少的尾部槽位按零处理。"""
        if not isinstance(value, str) or re.fullmatch(SKIN_BOX_LIMITS_PATTERN, value) is None:
            raise ValueError('外观箱购买次数须为英文句点分隔的 1～10 个非负整数')
        limits = tuple(int(part) for part in value.split('.'))
        self.limits = limits + (0,) * (10 - len(limits))
        self.counts = {}
        self.finished = set()
        self.pending = None

    def reset(self):
        """只在确认刷新成功时清除本轮进度，保留用户设置的目标次数。"""
        self.counts.clear()
        self.finished.clear()
        self.pending = None

    def can_buy(self, slot):
        """同槽位未确认的交易不能重入，已达目标的槽位不能再次购买。"""
        return (slot is not None and 0 <= slot < 10
                and self.pending is None and slot not in self.finished
                and self.counts.get(slot, 0) < self.limits[slot])

    def begin(self, slot):
        """锁定一笔待确认交易；重复扫描不会创建第二笔交易。"""
        if not self.can_buy(slot):
            return False
        self.pending = slot
        return True

    def confirm(self, slot, sold_out=False):
        """每笔交易只入账一次，同一成功弹窗持续多帧也不会重复累计。"""
        if self.pending is None or self.pending != slot:
            return False
        self.counts[slot] = self.counts.get(slot, 0) + 1
        self.pending = None
        if sold_out or self.counts[slot] >= self.limits[slot]:
            self.finished.add(slot)
        return True

    def cancel(self, slot):
        """确认没有成交后释放待确认状态，保留再次尝试的资格。"""
        if self.pending == slot:
            self.pending = None

    @property
    def kinds(self):
        """已购类别数按有成功记录的独立槽位计算，不按购买次数累加。"""
        return len(self.counts)

    @property
    def total(self):
        """本轮实际购买总数，与独立槽位种类数分开统计。"""
        return sum(self.counts.values())
