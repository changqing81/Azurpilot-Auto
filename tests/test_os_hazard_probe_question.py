"""侵蚀 1 扫图前问号预检的行为锁。

线上背景（2026-10-21 16:25）：自律寻敌结束后先做全图重扫（均价 12.5s），
镜头被甩到最后一个扫描区域（E10），随后 clear_question 才发现视野里
没有当前舰队（舰队在 E5，视野 10x7 只覆盖 y=5..11，差一行），只好换队
1→2→1 重新对焦，再花 9.5s。而此刻雷达上正上方就有问号 (0,-1)。

改动：扫图前用当前舰队雷达做一次零换队预检，命中明石/记录塔/装置就
跳过全图重扫。未命中或预检异常时保持原有「扫图 + 强制移动」行为不变。

回归要点：
1. 预检命中目标事件时 map_rescan 不被调用；
2. 预检未命中时 map_rescan 照常调用一次（行为与改动前一致）；
3. 预检内部异常必须吞掉并回退原流程，不得让任务崩掉；
4. TaskEnd 等任务级异常必须穿透，不能被吞。
"""
import unittest
from unittest.mock import MagicMock, patch

import module.os.tasks.hazard_leveling as hazard


class ProbeBranches(unittest.TestCase):
    """_cl1_probe_question_before_scan 的分支行为。"""

    def _handler(self, has_zone=True, grid=None, predict_exc=None,
                 solved=None, clear_side_effect=None):
        handler = hazard.OpsiHazard1Leveling.__new__(hazard.OpsiHazard1Leveling)
        if has_zone:
            handler.zone = MagicMock(is_port=False)
        handler.device = MagicMock()
        handler.radar = MagicMock()
        if predict_exc is not None:
            handler.radar.predict_question.side_effect = predict_exc
        else:
            handler.radar.predict_question.return_value = grid
        handler._solved_map_event = set(solved or [])
        if clear_side_effect is not None:
            handler.clear_question = MagicMock(side_effect=clear_side_effect)
        else:
            handler.clear_question = MagicMock()
        return handler

    def test_no_zone_returns_false(self):
        handler = self._handler(has_zone=False)
        self.assertFalse(handler._cl1_probe_question_before_scan())
        handler.clear_question.assert_not_called()

    def test_no_question_returns_false(self):
        handler = self._handler(grid=None)
        self.assertFalse(handler._cl1_probe_question_before_scan())
        handler.clear_question.assert_not_called()

    def test_question_but_no_target_event_returns_false(self):
        def side_effect(drop=None):
            handler._solved_map_event.add('is_exploration_reward')

        handler = self._handler(grid=(0, -1), clear_side_effect=side_effect)
        self.assertFalse(handler._cl1_probe_question_before_scan())
        handler.clear_question.assert_called_once()

    def test_question_hit_akashi_returns_true(self):
        def side_effect(drop=None):
            handler._solved_map_event.add('is_akashi')

        handler = self._handler(grid=(0, -1), clear_side_effect=side_effect)
        self.assertTrue(handler._cl1_probe_question_before_scan())

    def test_radar_exception_falls_back(self):
        handler = self._handler(predict_exc=RuntimeError('boom'))
        self.assertFalse(handler._cl1_probe_question_before_scan())

    def test_clear_question_exception_falls_back(self):
        handler = self._handler(
            grid=(0, -1), clear_side_effect=RuntimeError('click failed'))
        self.assertFalse(handler._cl1_probe_question_before_scan())

    def test_task_end_propagates(self):
        from module.config.config import TaskEnd

        handler = self._handler(
            grid=(0, -1), clear_side_effect=TaskEnd('task end'))
        with self.assertRaises(TaskEnd):
            handler._cl1_probe_question_before_scan()


class RunBattleOrdering(unittest.TestCase):
    """_cl1_run_battle 里预检与全图重扫的先后顺序。"""

    def _run(self, probe_result):
        import contextlib

        handler = hazard.OpsiHazard1Leveling.__new__(hazard.OpsiHazard1Leveling)
        handler.config = MagicMock()
        handler.run_strategic_search = MagicMock(return_value=True)
        handler.should_run_post_search_scan = MagicMock(return_value=True)
        handler.map_rescan = MagicMock()
        handler.handle_after_auto_search = MagicMock()
        handler._forced_move_level = MagicMock(return_value=0)
        handler._cl1_probe_question_before_scan = MagicMock(return_value=probe_result)

        # 录屏相关函数在函数体内 import，只能从源模块打桩
        with patch('module.base.debug_clip.cleanup_clips_if_due', lambda cfg: None), \
                patch('module.base.debug_clip.clip_recording',
                      lambda *a, **k: contextlib.nullcontext()):
            hazard.OpsiHazard1Leveling._cl1_run_battle(handler)
        return handler

    def test_hit_skips_full_rescan(self):
        handler = self._run(True)
        handler._cl1_probe_question_before_scan.assert_called_once()
        handler.map_rescan.assert_not_called()
        handler.handle_after_auto_search.assert_called_once()

    def test_miss_keeps_full_rescan(self):
        handler = self._run(False)
        handler._cl1_probe_question_before_scan.assert_called_once()
        handler.map_rescan.assert_called_once()
        handler.handle_after_auto_search.assert_called_once()


if __name__ == '__main__':
    unittest.main()
