"""行动力账本（ActionPointLedger）的离线回归，不连接游戏。

覆盖：observe/spend/estimate 边界、回血封顶、置信度迁移、
need_popup 真值表（含 total ∈ [cost, preserve] 的保留值守卫缺口）、
顶栏读数门（位数/范围/变化率/连续一致）、状态持久化 round-trip。
"""

import unittest
from datetime import datetime, timedelta

from module.os_handler.action_point_ledger import (
    ACTION_POINT_RECOVER_SECONDS,
    NATURAL_ACTION_POINT_LIMIT,
    ActionPointLedger,
    ApEstimate,
    HourlyQuota,
)

T0 = datetime(2026, 10, 3, 12, 0, 0)


def minutes(n):
    return T0 + timedelta(minutes=n)


class TestObserveAndEstimate(unittest.TestCase):
    def setUp(self):
        self.ledger = ActionPointLedger()

    def test_empty_ledger_estimates_low(self):
        est = self.ledger.estimate(now=T0)
        self.assertIsNone(est.current)
        self.assertEqual(est.confidence, 'low')
        self.assertTrue(self.ledger.need_popup(cost=120, preserve=200))

    def test_popup_observe_is_high_confidence(self):
        self.ledger.observe(154, 354, source='popup', at=T0)
        est = self.ledger.estimate(now=T0)
        self.assertEqual(est.current, 154)
        self.assertEqual(est.total_with_box, 354)
        self.assertEqual(est.confidence, 'high')

    def test_estimate_recovers_over_time(self):
        self.ledger.observe(100, 200, source='popup', at=T0)
        # 30 分钟后：+3
        est = self.ledger.estimate(now=minutes(30))
        self.assertEqual(est.current, 103)
        self.assertEqual(est.total_with_box, 203)
        self.assertEqual(est.confidence, 'medium')

    def test_estimate_caps_at_natural_limit(self):
        self.ledger.observe(195, 195, source='popup', at=T0)
        # 1 小时后理论上 +6，但封顶 200
        est = self.ledger.estimate(now=minutes(60))
        self.assertEqual(est.current, NATURAL_ACTION_POINT_LIMIT)

    def test_cap_does_not_clamp_higher_observations(self):
        # "一口气补过 1k"：observe 照单全收，回血封顶不往下压
        self.ledger.observe(1000, 1500, source='popup', at=T0)
        est = self.ledger.estimate(now=T0)
        self.assertEqual(est.current, 1000)
        self.assertEqual(est.total_with_box, 1500)
        est = self.ledger.estimate(now=minutes(10))
        self.assertEqual(est.current, 1000)  # 已超上限，自然回血不再增加

    def test_observe_rejects_invalid(self):
        self.assertFalse(self.ledger.observe(None, None, source='popup', at=T0))
        self.assertFalse(self.ledger.observe('abc', source='popup', at=T0))
        self.assertEqual(self.ledger.estimate(now=T0).confidence, 'low')

    def test_map_bar_observe_keeps_box_value_and_medium_confidence(self):
        self.ledger.observe(154, 354, source='popup', at=T0)
        self.ledger.observe(150, source='map_bar', at=minutes(5))
        est = self.ledger.estimate(now=minutes(5))
        self.assertEqual(est.current, 150)
        # 顶栏只校准当前值，箱子价值不变：总量跟随修正为 150 + 200 = 350
        self.assertEqual(est.total_with_box, 350)
        self.assertEqual(est.confidence, 'medium')

    def test_stale_popup_beyond_full_recovery_is_low(self):
        self.ledger.observe(154, 354, source='popup', at=T0)
        # 超过理论上限回满时长（200 点 × 600s ≈ 33 小时）
        est = self.ledger.estimate(now=T0 + timedelta(hours=40))
        self.assertEqual(est.confidence, 'low')


class TestSpend(unittest.TestCase):
    def setUp(self):
        self.ledger = ActionPointLedger()
        self.ledger.observe(154, 354, source='popup', at=T0)

    def test_spend_battle_cost(self):
        self.assertTrue(self.ledger.spend(5, reason='battle_22', at=T0))
        est = self.ledger.estimate(now=T0)
        self.assertEqual(est.current, 149)
        self.assertEqual(est.total_with_box, 349)

    def test_spend_floors_at_zero_keeps_boxes(self):
        # 过度扣减时当前值封底为 0，但箱子价值保留（总量 = 0 + 200）
        self.ledger.spend(99999, at=T0)
        est = self.ledger.estimate(now=T0)
        self.assertEqual(est.current, 0)
        self.assertEqual(est.total_with_box, 200)

    def test_negative_spend_is_injection(self):
        self.ledger.spend(-100, reason='akashi_box', at=T0)
        est = self.ledger.estimate(now=T0)
        self.assertEqual(est.current, 254)
        self.assertEqual(est.total_with_box, 454)

    def test_zero_cost_ignored(self):
        self.assertFalse(self.ledger.spend(0, at=T0))


class TestNeedPopup(unittest.TestCase):
    def make(self, current, total):
        ledger = ActionPointLedger()
        ledger.observe(current, total, source='popup', at=T0)
        return ledger

    def test_need_popup_when_current_below_cost(self):
        ledger = self.make(100, 300)
        self.assertTrue(ledger.need_popup(cost=120, preserve=200))

    def test_skip_popup_when_enough(self):
        ledger = self.make(154, 354)
        self.assertFalse(ledger.need_popup(cost=120, preserve=200))

    def test_preserve_guard_catches_total_gap(self):
        # 修复的缺口：total ∈ [cost, preserve] 时，即使当前值够开工也要弹窗
        # （现状 ap_checked 只看 current>=120，会跳过本该抛 ActionPointLimit 的弹窗）
        ledger = self.make(150, 150)
        self.assertTrue(ledger.need_popup(cost=120, preserve=200))

    def test_need_popup_on_low_confidence(self):
        ledger = ActionPointLedger()
        self.assertTrue(ledger.need_popup(cost=120, preserve=200))

    def test_fortress_cost(self):
        ledger = self.make(180, 400)
        self.assertTrue(ledger.need_popup(cost=200, preserve=200))
        ledger2 = self.make(200, 400)
        self.assertFalse(ledger2.need_popup(cost=200, preserve=200))

    def test_top_up_ceiling_does_not_change_decision(self):
        ledger = self.make(154, 354)
        self.assertFalse(ledger.need_popup(cost=120, preserve=200, top_up_ceiling=320))
        # 开工线超过补充上限时按弹窗处理并告警
        self.assertTrue(ledger.need_popup(cost=400, preserve=200, top_up_ceiling=320))


class TestMapBarGates(unittest.TestCase):
    def test_sanitize_digit_gates(self):
        sanitize = ActionPointLedger.sanitize_map_bar_value
        self.assertEqual(sanitize(154), (154, 'ok'))
        # 4 位合法（一口气补过 1k 的玩法）
        self.assertEqual(sanitize(1540), (1540, 'ok'))
        self.assertEqual(sanitize(9999), (9999, 'ok'))
        # ≥5 位丢弃（OCR 拼接：154 + Lv.60 → 60154）
        self.assertEqual(sanitize(60154), (None, 'len>4'))
        self.assertEqual(sanitize(-1), (None, 'negative'))
        self.assertEqual(sanitize(None), (None, 'empty'))

    def test_judge_without_baseline_is_pending(self):
        verdict, value, reason = ActionPointLedger.judge_map_bar_reading(154, None)
        self.assertEqual(verdict, 'pending')
        self.assertEqual(value, 154)

    def test_judge_within_tolerance(self):
        est = ApEstimate(152, 352, 60, 'medium')
        self.assertEqual(ActionPointLedger.judge_map_bar_reading(154, est)[0], 'ok')
        self.assertEqual(ActionPointLedger.judge_map_bar_reading(150, est)[0], 'ok')

    def test_judge_drift_is_suspect(self):
        est = ApEstimate(152, 352, 60, 'medium')
        verdict, value, reason = ActionPointLedger.judge_map_bar_reading(140, est)
        self.assertEqual(verdict, 'suspect')
        self.assertEqual(reason, 'drift=-12')

    def test_judge_consecutive_consistency(self):
        est = ApEstimate(152, 352, 60, 'medium')
        # 与上一帧不一致 → pending，等下一帧确认
        verdict, _, reason = ActionPointLedger.judge_map_bar_reading(152, est, last_value=154)
        self.assertEqual(verdict, 'pending')
        self.assertEqual(reason, 'not-consistent')
        # 连续一致 → ok
        verdict, _, _ = ActionPointLedger.judge_map_bar_reading(152, est, last_value=152)
        self.assertEqual(verdict, 'ok')

    def test_judge_rejects_garbage_without_consuming_quota(self):
        # 60154 这类明显垃圾在位数门就被拒，reason 可区分于变化率门
        verdict, value, reason = ActionPointLedger.judge_map_bar_reading(60154, ApEstimate(152, 352, 60, 'medium'))
        self.assertEqual(verdict, 'reject')
        self.assertIsNone(value)
        self.assertEqual(reason, 'len>4')


class TestStateRoundTrip(unittest.TestCase):
    def test_round_trip(self):
        ledger = ActionPointLedger()
        ledger.observe(154, 354, source='popup', at=T0)
        state = ledger.to_state()
        self.assertTrue(state)

        restored = ActionPointLedger.from_state(state)
        self.assertEqual(restored.current, 154)
        self.assertEqual(restored.total_with_box, 354)
        self.assertEqual(restored.calibrated_at, T0)
        self.assertEqual(restored.confidence, 'medium')  # 从盘恢复最多 medium
        self.assertEqual(restored.source, 'persisted')

    def test_empty_state_gives_empty_ledger(self):
        ledger = ActionPointLedger.from_state({})
        self.assertIsNone(ledger.current)
        self.assertEqual(ledger.estimate(now=T0).confidence, 'low')

    def test_corrupted_state_gives_empty_ledger(self):
        ledger = ActionPointLedger.from_state({'current': 'abc', 'at': 'not-a-date'})
        self.assertIsNone(ledger.current)
        self.assertTrue(ledger.need_popup(cost=120, preserve=200))

    def test_total_below_current_is_clamped(self):
        ledger = ActionPointLedger.from_state({'current': 154, 'total': 100, 'at': T0.isoformat()})
        self.assertEqual(ledger.total_with_box, 154)


class TestHourlyQuota(unittest.TestCase):
    def test_limit_three(self):
        quota = HourlyQuota(3)
        t = T0
        for _ in range(3):
            self.assertTrue(quota.allow(now=t))
            quota.record(now=t)
        self.assertFalse(quota.allow(now=t))

    def test_zero_limit_never_allows(self):
        quota = HourlyQuota(0)
        self.assertFalse(quota.allow(now=T0))

    def test_window_slides(self):
        quota = HourlyQuota(1)
        self.assertTrue(quota.allow(now=T0))
        quota.record(now=T0)
        self.assertFalse(quota.allow(now=T0 + timedelta(minutes=30)))
        # 1 小时后额度恢复
        self.assertTrue(quota.allow(now=T0 + timedelta(hours=1, seconds=1)))


class TestConstantsSingleSource(unittest.TestCase):
    def test_recover_constants_are_the_single_source(self):
        # 防止出现第三份"10 分钟 +1 / 上限 200"副本：
        # prevent_action_point_overflow.py 应从账本模块引用同名常量
        from module.os.tasks import prevent_action_point_overflow as prevent

        self.assertIs(prevent.ACTION_POINT_RECOVER_SECONDS, ACTION_POINT_RECOVER_SECONDS)
        self.assertIs(prevent.NATURAL_ACTION_POINT_LIMIT, NATURAL_ACTION_POINT_LIMIT)
        self.assertEqual(ACTION_POINT_RECOVER_SECONDS, 600)
        self.assertEqual(NATURAL_ACTION_POINT_LIMIT, 200)


class TestHandlerNeedPopup(unittest.TestCase):
    """handler 级 need_action_point_popup 开关语义（SimpleNamespace 桩，不连游戏）。"""

    def make_handler(self, decide=True, confirm_limit=3, state=None):
        from types import SimpleNamespace

        from module.os_handler.action_point import ActionPointHandler

        handler = ActionPointHandler.__new__(ActionPointHandler)
        handler.config = SimpleNamespace(
            OpsiGeneral_ActionPointLedgerEnabled=True,
            OpsiGeneral_ActionPointLedgerDecide=decide,
            OpsiGeneral_ActionPointLedgerConfirmLimit=confirm_limit,
            data={'OpsiScheduling': {'Storage': {'Storage': state or {}}}},
        )
        return handler

    def test_decide_off_always_popup(self):
        handler = self.make_handler(decide=False)
        self.assertTrue(handler.need_action_point_popup(cost=120, preserve=200))

    def test_decide_on_ledger_enough_skips_popup(self):
        handler = self.make_handler()
        handler._get_ap_ledger().observe(154, 354, source='popup', at=T0)
        self.assertFalse(handler.need_action_point_popup(cost=120, preserve=200))

    def test_decide_on_insufficient_pops(self):
        handler = self.make_handler()
        handler._get_ap_ledger().observe(100, 300, source='popup', at=T0)
        self.assertTrue(handler.need_action_point_popup(cost=120, preserve=200))

    def test_suspect_pending_triggers_verify_then_skips(self):
        handler = self.make_handler()
        handler._get_ap_ledger().observe(154, 354, source='popup', at=T0)
        handler.__dict__['_ap_suspect_pending'] = True
        verified = []
        handler._ap_verify_by_popup = lambda: verified.append(1) or True
        self.assertFalse(handler.need_action_point_popup(cost=120, preserve=200))
        self.assertTrue(verified, 'suspect 标记应触发一次复核')
        self.assertNotIn('_ap_suspect_pending', handler.__dict__, '复核成功后标记应清除')

    def test_verify_quota_exhausted_falls_back_to_projection(self):
        handler = self.make_handler(confirm_limit=0)
        handler._get_ap_ledger().observe(154, 354, source='popup', at=T0)
        handler.__dict__['_ap_suspect_pending'] = True
        # 额度为 0：不复核，直接按推演判定（推演值够 → 跳过弹窗）
        self.assertFalse(handler.need_action_point_popup(cost=120, preserve=200))
        self.assertIn('_ap_suspect_pending', handler.__dict__, '复核未成功时标记保留')


if __name__ == '__main__':
    unittest.main()
