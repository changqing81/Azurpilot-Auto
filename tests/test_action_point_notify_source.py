"""行动力推送取数的离线回归：账本优先 + 无效值不推送。

背景（2026-10-05 真机日志定位）：账本开启后大量轮次走「顶栏 OCR + 账本记录」
快路径跳过行动力弹窗（ap_checked=True），而 _action_point_total 只在
action_point_update() 里赋值，未弹窗时保持类属性默认 0。于是推送发出
「总行动力: 0 下跌 2470」，并把 0 写进对比基准，下一次推送又变成
「上涨 2794」的假暴涨。只有开了行动力账本才会踩到。
"""

import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

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
    _format_launcher_notification = CoinTaskMixin._format_launcher_notification

    def __init__(
        self,
        ledger,
        fallback_total=0,
        ledger_enabled=True,
        yellow_coin=None,
        yellow_coin_error=None,
        can_send=True,
    ):
        self._ledger = ledger
        self._ledger_enabled = ledger_enabled
        self._action_point_total = fallback_total
        self.pushed = []
        self.marked = []
        self.config = SimpleNamespace(config_name='test')
        self._yellow_coin = yellow_coin
        self._yellow_coin_error = yellow_coin_error
        self._can_send = can_send

    def _ap_ledger_enabled(self):
        return self._ledger_enabled

    def _get_ap_ledger(self):
        return self._ledger

    def _can_send_ap_notification(self, key):
        return self._can_send

    def _mark_ap_notification_sent(self, key):
        self.marked.append(key)

    def get_yellow_coins(self):
        if self._yellow_coin_error is not None:
            raise self._yellow_coin_error
        return self._yellow_coin

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


def _fake_cl1_db(previous):
    """用假模块顶替 cl1_database，避免测试碰真实 DB 文件。"""
    module = ModuleType('module.statistics.cl1_database')
    fake_db = MagicMock()
    fake_db.get_last_ap_notification.return_value = previous
    module.db = fake_db
    return module, fake_db


def _run_notify(previous, ap_total, yellow_coin, **stub_kwargs):
    module, fake_db = _fake_cl1_db(previous)
    stub = _NotifyStub(
        _ledger_with(126, ap_total), yellow_coin=yellow_coin, **stub_kwargs
    )
    with patch.dict('sys.modules', {'module.statistics.cl1_database': module}):
        stub.check_and_notify_action_point_threshold()
    return stub, fake_db


class TestYellowCoinInActionPointNotification(unittest.TestCase):
    """黄币行：与行动力同格式（总量 + 涨跌），且推送后落盘为下次基准。"""

    def test_title_empty_and_yellow_coin_delta_shown(self):
        stub, _ = _run_notify({'ap': 1000, 'yellow_coin': 5000}, 990, 5200)
        self.assertEqual(len(stub.pushed), 1)
        title, content = stub.pushed[0]
        self.assertEqual(title, '', '标题必须留空，消息体只保留内容')
        self.assertEqual(
            content, '总行动力: 990 下跌10行动力\n总黄币: 5200 上涨200黄币'
        )

    def test_yellow_coin_falling(self):
        stub, _ = _run_notify({'ap': 1000, 'yellow_coin': 5000}, 990, 4600)
        self.assertIn('总黄币: 4600 下跌400黄币', stub.pushed[0][1])

    def test_yellow_coin_without_baseline_shows_plain_value(self):
        # 首次推送（库里还没有黄币基准）：只显示数值，不伪造涨跌
        stub, _ = _run_notify({'ap': 1000}, 990, 5200)
        self.assertEqual(
            stub.pushed[0][1], '总行动力: 990 下跌10行动力\n总黄币: 5200'
        )

    def test_yellow_coin_unchanged_has_no_trend_word(self):
        stub, _ = _run_notify({'ap': 1000, 'yellow_coin': 5200}, 990, 5200)
        content = stub.pushed[0][1]
        self.assertIn('总黄币: 5200', content)
        self.assertNotIn('上涨', content)
        self.assertNotIn('下跌', content.split('\n')[1])

    def test_yellow_coin_read_failure_still_pushes_action_point(self):
        # 黄币读取失败不能拖垮行动力推送
        stub, _ = _run_notify(
            {'ap': 1000, 'yellow_coin': 5000},
            990,
            None,
            yellow_coin_error=RuntimeError('ocr timeout'),
        )
        self.assertEqual(len(stub.pushed), 1)
        self.assertEqual(stub.pushed[0][1], '总行动力: 990 下跌10行动力')

    def test_zero_yellow_coin_omits_line(self):
        # OCR 兜底可能返回 0，0 不写入推送也不写入基准
        stub, fake_db = _run_notify({'ap': 1000, 'yellow_coin': 5000}, 990, 0)
        self.assertNotIn('总黄币', stub.pushed[0][1])
        fake_db.async_set_last_ap_notification.assert_called_once_with(
            'test', 990, yellow_coin=None
        )

    def test_zero_baseline_degrades_to_plain_value(self):
        # 基准里残留 0（早期版本写入过）：必须退化成「无基准」，
        # 不能拿 0 做差发出「上涨几千黄币」的假信号
        stub, _ = _run_notify({'ap': 1000, 'yellow_coin': 0}, 990, 5200)
        self.assertEqual(
            stub.pushed[0][1], '总行动力: 990 下跌10行动力\n总黄币: 5200'
        )

    def test_yellow_coin_recorded_as_next_baseline(self):
        stub, fake_db = _run_notify({'ap': 1000, 'yellow_coin': 5000}, 990, 5200)
        fake_db.async_set_last_ap_notification.assert_called_once_with(
            'test', 990, yellow_coin=5200
        )
        self.assertEqual(stub.marked, ['_last_ap_notification_time'])

    def test_ap_unchanged_skips_push_entirely(self):
        stub, _ = _run_notify({'ap': 990, 'yellow_coin': 5000}, 990, 5200)
        self.assertEqual(stub.pushed, [])


class TestLauncherFormatWithEmptyTitle(unittest.TestCase):
    """启动器通道：空标题时不生成占位标题行，但窗口标题仍可读。"""

    def test_empty_title_uses_ap_caption_without_placeholder_line(self):
        stub = _NotifyStub(None)
        title, content = stub._format_launcher_notification(
            instance_name='test',
            title='',
            content='总行动力: 990 下跌10行动力\n总黄币: 5200 上涨200黄币',
        )
        self.assertEqual(title, 'test 行动力动了一下喵~')
        self.assertEqual(
            content, '总行动力: 990 下跌10行动力\n总黄币: 5200 上涨200黄币 喵~'
        )

    def test_non_empty_title_keeps_existing_mapping(self):
        stub = _NotifyStub(None)
        title, content = stub._format_launcher_notification(
            instance_name='test',
            title='[AzurPilot] 行动力出现变化！',
            content='总行动力: 990 下跌10行动力',
        )
        self.assertEqual(title, 'test 行动力动了一下喵~')
        self.assertIn('行动力出现变化', content)


if __name__ == '__main__':
    unittest.main()
