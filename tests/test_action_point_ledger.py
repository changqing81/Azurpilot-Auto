"""行动力账本（ActionPointLedger）的离线回归，不连接游戏。

账本是记录器不是推演器：只覆盖 记录（observe）边界、总行动力跟随修正、
need_popup 真值表（含保留值守卫）、顶栏读数位数门、状态持久化 round-trip。
"""

import unittest
from datetime import datetime

from module.os_handler.action_point_ledger import (
    ACTION_POINT_RECOVER_SECONDS,
    NATURAL_ACTION_POINT_LIMIT,
    SOURCE_MAP_BAR,
    SOURCE_POPUP,
    SOURCE_PERSISTED,
    ActionPointLedger,
)

T0 = datetime(2026, 10, 3, 12, 0, 0)


class TestObserve(unittest.TestCase):
    def setUp(self):
        self.ledger = ActionPointLedger()

    def test_empty_ledger_has_no_record(self):
        self.assertIsNone(self.ledger.current)
        self.assertIsNone(self.ledger.total_with_box)
        # 没有任何记录时，任何判定都要求弹窗
        self.assertTrue(self.ledger.need_popup(cost=120, preserve=200))

    def test_popup_records_current_and_total(self):
        self.assertTrue(self.ledger.observe(154, 354, source=SOURCE_POPUP, box=(0, 50, 100, 50), at=T0))
        self.assertEqual(self.ledger.current, 154)
        self.assertEqual(self.ledger.total_with_box, 354)
        self.assertEqual(self.ledger.box_value, 200)
        self.assertEqual(self.ledger.box, (0, 50, 100, 50))
        self.assertEqual(self.ledger.source, SOURCE_POPUP)

    def test_map_bar_records_current_and_total_follows(self):
        # 弹窗先建立 154/354（箱子 200），随后顶栏读到 134（战斗消耗 20）：
        # 箱子价值不变，总行动力跟随修正
        self.ledger.observe(154, 354, source=SOURCE_POPUP, at=T0)
        self.ledger.observe(134, source=SOURCE_MAP_BAR, at=T0)
        self.assertEqual(self.ledger.current, 134)
        self.assertEqual(self.ledger.total_with_box, 334)
        self.assertEqual(self.ledger.box_value, 200)

    def test_map_bar_upward_drift_keeps_box_value(self):
        # 外部补充（脚本外开箱）：当前值上涨，箱子价值同样保持不变
        self.ledger.observe(154, 354, source=SOURCE_POPUP, at=T0)
        self.ledger.observe(180, source=SOURCE_MAP_BAR, at=T0)
        self.assertEqual(self.ledger.current, 180)
        self.assertEqual(self.ledger.total_with_box, 380)
        self.assertEqual(self.ledger.box_value, 200)

    def test_map_bar_without_prior_total(self):
        # 从未弹窗过：顶栏读数只记录当前值，总行动力保持未知（None）——
        # 不能拿当前值冒充总行动力，否则保留值判定低估会误推迟
        self.ledger.observe(158, source=SOURCE_MAP_BAR, at=T0)
        self.assertEqual(self.ledger.current, 158)
        self.assertIsNone(self.ledger.total_with_box)
        # 无保留值诉求时按当前值判定即可
        self.assertFalse(self.ledger.need_popup(cost=120, preserve=0))
        # 涉及保留值判定而总量未知 → 弹窗（顺带建立含箱总量记录）
        self.assertTrue(self.ledger.need_popup(cost=120, preserve=200))

    def test_invalid_reading_ignored(self):
        self.assertFalse(self.ledger.observe(None, source=SOURCE_MAP_BAR, at=T0))
        self.assertFalse(self.ledger.observe(-1, source=SOURCE_MAP_BAR, at=T0))
        self.assertIsNone(self.ledger.current)

    def test_high_observation_accepted(self):
        # "一口气补过 1k"：记录值照单全收，没有上限压制
        self.ledger.observe(1000, 1500, source=SOURCE_POPUP, at=T0)
        self.assertEqual(self.ledger.current, 1000)
        self.assertEqual(self.ledger.total_with_box, 1500)


class TestNeedPopup(unittest.TestCase):
    def make(self, current, total=None):
        ledger = ActionPointLedger()
        ledger.observe(current, total, source=SOURCE_POPUP, at=T0)
        return ledger

    def test_no_record_needs_popup(self):
        self.assertTrue(ActionPointLedger().need_popup(cost=120))

    def test_below_start_line_needs_popup(self):
        self.assertTrue(self.make(97, 1197).need_popup(cost=120, preserve=200))

    def test_enough_and_above_preserve_skips_popup(self):
        self.assertFalse(self.make(158, 1158).need_popup(cost=120, preserve=200))

    def test_preserve_guard_forces_popup(self):
        # 总行动力会被保留值拦截：即使当前值够开工也弹窗（走 ActionPointLimit 正常延后）
        self.assertTrue(self.make(130, 180).need_popup(cost=120, preserve=200))

    def test_top_up_ceiling(self):
        self.assertTrue(self.make(500, 500).need_popup(cost=800, top_up_ceiling=320))
        self.assertFalse(self.make(158, 1158).need_popup(cost=120, top_up_ceiling=320))


class TestSanitizeMapBarValue(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(ActionPointLedger.sanitize_map_bar_value(154), (154, 'ok'))
        self.assertEqual(ActionPointLedger.sanitize_map_bar_value('5'), (5, 'ok'))

    def test_four_digits_allowed(self):
        # 一口气补过 1k 的玩法：顶栏读数不预设 200 上限
        self.assertEqual(ActionPointLedger.sanitize_map_bar_value(1540), (1540, 'ok'))
        self.assertEqual(ActionPointLedger.sanitize_map_bar_value(9999), (9999, 'ok'))

    def test_five_digits_rejected(self):
        # OCR 拼接：行动力 154 与舰船等级 Lv.60 连读 → 60154
        self.assertEqual(ActionPointLedger.sanitize_map_bar_value(60154), (None, 'len>4'))

    def test_reject_sentinel_and_garbage(self):
        self.assertEqual(ActionPointLedger.sanitize_map_bar_value(-1), (None, 'negative'))
        self.assertEqual(ActionPointLedger.sanitize_map_bar_value(None), (None, 'empty'))
        self.assertEqual(ActionPointLedger.sanitize_map_bar_value('abc'), (None, 'empty'))


class TestStateRoundTrip(unittest.TestCase):
    def test_round_trip(self):
        ledger = ActionPointLedger()
        ledger.observe(154, 354, source=SOURCE_POPUP, at=T0)
        state = ledger.to_state()
        self.assertEqual(state['current'], 154)
        self.assertEqual(state['total'], 354)
        restored = ActionPointLedger.from_state(state)
        self.assertEqual(restored.current, 154)
        self.assertEqual(restored.total_with_box, 354)
        self.assertEqual(restored.source, SOURCE_PERSISTED)

    def test_empty_state_gives_empty_ledger(self):
        self.assertEqual(ActionPointLedger().to_state(), {})
        self.assertIsNone(ActionPointLedger.from_state({}).current)

    def test_corrupted_state_gives_empty_ledger(self):
        self.assertIsNone(ActionPointLedger.from_state({'current': 'oops'}).current)
        # 时间字段解析失败 → 整体回空账本（调用方自动走旧路径弹窗）
        self.assertIsNone(ActionPointLedger.from_state({'current': '154', 'at': 'not-a-date'}).current)

    def test_total_invalid_stays_unknown(self):
        # 状态里的总行动力非法（<当前值）→ 保持未知，由后续弹窗重建
        ledger = ActionPointLedger.from_state({'current': 200, 'total': 100, 'at': T0.isoformat()})
        self.assertEqual(ledger.current, 200)
        self.assertIsNone(ledger.total_with_box)
        self.assertTrue(ledger.need_popup(cost=120, preserve=200))

    def test_round_trip_with_unknown_total(self):
        # 只有顶栏记录（总量未知）也能持久化与恢复
        ledger = ActionPointLedger()
        ledger.observe(158, source=SOURCE_MAP_BAR, at=T0)
        restored = ActionPointLedger.from_state(ledger.to_state())
        self.assertEqual(restored.current, 158)
        self.assertIsNone(restored.total_with_box)


class TestConstantsSingleSource(unittest.TestCase):
    def test_recover_constants_are_the_single_source(self):
        # 防止出现第三份"10 分钟 +1 / 上限 200"副本：
        # prevent_action_point_overflow.py 应从账本模块引用同名常量
        from module.os.tasks import prevent_action_point_overflow as prevent

        self.assertIs(prevent.ACTION_POINT_RECOVER_SECONDS, ACTION_POINT_RECOVER_SECONDS)
        self.assertIs(prevent.NATURAL_ACTION_POINT_LIMIT, NATURAL_ACTION_POINT_LIMIT)
        self.assertEqual(ACTION_POINT_RECOVER_SECONDS, 600)
        self.assertEqual(NATURAL_ACTION_POINT_LIMIT, 200)


if __name__ == '__main__':
    unittest.main()
