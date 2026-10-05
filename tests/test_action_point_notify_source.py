"""行动力推送取数的离线回归：账本优先 + 无效值不推送。

背景（2026-10-05 真机日志定位）：账本开启后大量轮次走「顶栏 OCR + 账本记录」
快路径跳过行动力弹窗（ap_checked=True），而 _action_point_total 只在
action_point_update() 里赋值，未弹窗时保持类属性默认 0。于是推送发出
「总行动力: 0 下跌 2470」，并把 0 写进对比基准，下一次推送又变成
「上涨 2794」的假暴涨。只有开了行动力账本才会踩到。
"""

import unittest

from module.os.tasks.scheduling import CoinTaskMixin
from module.os_handler.action_point_ledger import (
    SOURCE_MAP_BAR,
    SOURCE_POPUP,
    ActionPointLedger,
)


class _NotifyStub:
    """只借 CoinTaskMixin 的推送取数方法，其余依赖用桩替代。"""

    _resolve_notify_ap_total = CoinTaskMixin._resolve_notify_ap_total
    check_and_notify_action_point_threshold = (
        CoinTaskMixin.check_and_notify_action_point_threshold
    )

    def __init__(self, ledger, fallback_total=0, ledger_enabled=True):
        self._ledger = ledger
        self._ledger_enabled = ledger_enabled
        self._action_point_total = fallback_total
        self.pushed = []

    def _ap_ledger_enabled(self):
        return self._ledger_enabled

    def _get_ap_ledger(self):
        return self._ledger

    def notify_push(self, title, content):
        self.pushed.append((title, content))
        return True


def _ledger_with(current, total=None, source=SOURCE_POPUP):
    ledger = ActionPointLedger()
    ledger.observe(current, total, source=source)
    return ledger


class TestResolveNotifyApTotal(unittest.TestCase):
    def test_ledger_total_wins(self):
        # 账本有含箱总量 → 用账本值，与当轮有没有弹窗无关
        stub = _NotifyStub(_ledger_with(126, 2986), fallback_total=0)
        self.assertEqual(stub._resolve_notify_ap_total(), 2986)

    def test_ledger_total_wins_over_stale_attribute(self):
        # 关键回归：类属性残留旧值（2470）时，账本新值（2986）必须赢
        stub = _NotifyStub(_ledger_with(126, 2986), fallback_total=2470)
        self.assertEqual(stub._resolve_notify_ap_total(), 2986)

    def test_total_unknown_falls_back_and_does_not_fake_total(self):
        # 只顶栏记录过（含箱总量未知）：不拿当前值冒充总量，
        # 兜底值维持原样，由后续弹窗建立总量
        ledger = _ledger_with(126, None, source=SOURCE_MAP_BAR)
        stub = _NotifyStub(ledger, fallback_total=0)
        self.assertEqual(stub._resolve_notify_ap_total(), 0)

    def test_fallback_when_ledger_disabled(self):
        # 账本关闭：保持原行为，直接用 _action_point_total
        stub = _NotifyStub(None, fallback_total=2470, ledger_enabled=False)
        self.assertEqual(stub._resolve_notify_ap_total(), 2470)


class TestInvalidReadingSkipsPush(unittest.TestCase):
    def test_ledger_on_but_no_reading_skips_push(self):
        # 账本开着但账本空、类属性还是 0：不允许推送
        stub = _NotifyStub(None, fallback_total=0, ledger_enabled=True)
        stub.check_and_notify_action_point_threshold()
        self.assertEqual(stub.pushed, [])

    def test_negative_reading_skips_push(self):
        stub = _NotifyStub(None, fallback_total=-1, ledger_enabled=True)
        stub.check_and_notify_action_point_threshold()
        self.assertEqual(stub.pushed, [])


if __name__ == '__main__':
    unittest.main()
