"""「非安全海域跳过扫图」门控的回归测试（不连接游戏）。

覆盖两件事：
1. ``OSMapOperation.should_run_post_search_scan()`` 本体：仅 CN、开关默认关闭时不
   做任何 OCR；开关开启时按安全海域判定，识别失败按「非安全」处理并有限次重试。
2. 四个调用点的编排：侵蚀1练级 ``_cl1_run_battle``、短猫相接的
   ``_meow_handle_traditional_zone`` / ``_meow_handle_stay_in_zone`` /
   ``_meow_handle_normal_search``。

全部用桩替换设备与地图交互，只验证分支编排。
"""

import unittest
from unittest.mock import MagicMock, Mock, patch

from module.os.map_operation import OSMapOperation
from module.os.tasks.hazard_leveling import OpsiHazard1Leveling
from module.os.tasks.meowfficer_farming import OpsiMeowfficerFarming


def make_gate(server='cn', switch=True, results=(True,)):
    """构造一个只挂了 config/device 的 OSMapOperation，用于测门控本体。

    Args:
        server (str): self.config.SERVER 的取值。
        switch (bool): OpsiGeneral_SkipScanOnUnsafeZone 的取值。
        results (tuple): 每次 get_zone_name() 的「是否安全」结果；元素可以是
            bool 或 Exception（抛出）。

    Returns:
        tuple: (obj, ocr_calls) —— ocr_calls 记录每次识别返回的结果。
    """
    obj = OSMapOperation.__new__(OSMapOperation)
    obj.config = Mock()
    obj.config.SERVER = server
    obj.config.OpsiGeneral_SkipScanOnUnsafeZone = switch
    obj.device = Mock()
    obj.is_zone_name_hidden = False

    ocr_calls = []

    def fake_get_zone_name():
        index = min(len(ocr_calls), len(results) - 1)
        result = results[index]
        ocr_calls.append(result)
        if isinstance(result, Exception):
            raise result
        obj.is_zone_name_hidden = bool(result)
        return 'NA海域西南B-安全海域' if result else '卡利比安海A'

    obj.get_zone_name = fake_get_zone_name
    return obj, ocr_calls


class TestShouldRunPostSearchScan(unittest.TestCase):
    """门控本体：服务器限定、开关默认关闭、重试与异常兜底。"""

    def test_non_cn_server_skips_check(self):
        for server in ('en', 'jp', 'tw'):
            with self.subTest(server=server):
                obj, ocr_calls = make_gate(server=server, switch=True, results=(False,))
                self.assertTrue(obj.should_run_post_search_scan())
                self.assertEqual(ocr_calls, [], '非 CN 不应产生任何 OCR')
                obj.device.screenshot.assert_not_called()

    def test_switch_off_skips_check(self):
        obj, ocr_calls = make_gate(server='cn', switch=False, results=(False,))
        self.assertTrue(obj.should_run_post_search_scan())
        self.assertEqual(ocr_calls, [], '开关关闭不应产生任何 OCR')
        obj.device.screenshot.assert_not_called()

    def test_safe_on_first_attempt(self):
        obj, ocr_calls = make_gate(server='cn', switch=True, results=(True,))
        self.assertTrue(obj.should_run_post_search_scan())
        self.assertEqual(ocr_calls, [True])
        self.assertEqual(obj.device.screenshot.call_count, 1)
        obj.device.sleep.assert_not_called()

    def test_retry_then_safe(self):
        obj, ocr_calls = make_gate(server='cn', switch=True, results=(False, True))
        self.assertTrue(obj.should_run_post_search_scan())
        self.assertEqual(ocr_calls, [False, True])
        self.assertEqual(obj.device.screenshot.call_count, 2)
        self.assertEqual(obj.device.sleep.call_count, 1)

    def test_all_attempts_unsafe_returns_false(self):
        obj, ocr_calls = make_gate(server='cn', switch=True, results=(False,))
        self.assertFalse(obj.should_run_post_search_scan(attempts=3))
        self.assertEqual(len(ocr_calls), 3)
        self.assertEqual(obj.device.screenshot.call_count, 3)
        self.assertEqual(obj.device.sleep.call_count, 2)

    def test_ocr_exception_treated_as_unsafe(self):
        obj, ocr_calls = make_gate(
            server='cn', switch=True, results=(RuntimeError('ocr boom'),)
        )
        self.assertFalse(obj.should_run_post_search_scan(attempts=2))
        self.assertEqual(len(ocr_calls), 2, '异常也应重试到 attempts 次')
        self.assertEqual(obj.device.screenshot.call_count, 2)

    def test_attempts_at_least_one(self):
        obj, ocr_calls = make_gate(server='cn', switch=True, results=(False,))
        self.assertFalse(obj.should_run_post_search_scan(attempts=0))
        self.assertEqual(len(ocr_calls), 1)


class TestHazard1Gate(unittest.TestCase):
    """侵蚀1练级 _cl1_run_battle：非安全跳过扫图与强制移动。"""

    def make_task(self, safe=True, forced_move_level=2):
        task = OpsiHazard1Leveling.__new__(OpsiHazard1Leveling)
        task.config = Mock()
        task.device = Mock()
        task.run_strategic_search = Mock(return_value=True)
        task.should_run_post_search_scan = Mock(return_value=safe)
        task.map_rescan = Mock()
        task._forced_move_level = Mock(return_value=forced_move_level)
        task._execute_fixed_patrol_scan = Mock()
        task.handle_after_auto_search = Mock()
        return task

    def run_battle(self, task):
        with patch('module.base.debug_clip.cleanup_clips_if_due'), \
                patch('module.base.debug_clip.clip_recording', MagicMock()), \
                patch('module.os.tasks.hazard_leveling.record_cl1_akashi_encounter') as record:
            task._cl1_run_battle()
        return record

    def test_unsafe_skips_scan_and_forced_move(self):
        task = self.make_task(safe=False)
        self.run_battle(task)
        task.map_rescan.assert_not_called()
        task._execute_fixed_patrol_scan.assert_not_called()
        task.handle_after_auto_search.assert_called_once_with()

    def test_safe_keeps_original_behavior(self):
        task = self.make_task(safe=True)
        self.run_battle(task)
        task.map_rescan.assert_called_once_with()
        task._execute_fixed_patrol_scan.assert_called_once_with(ExecuteFixedPatrolScan=True)
        task.handle_after_auto_search.assert_called_once_with()

    def test_event_book_reset_even_when_skipped(self):
        """跳过扫图也必须重置事件簿，否则上一轮明石会被重复计数。"""
        task = self.make_task(safe=False)
        task._solved_map_event = {'is_akashi'}
        record = self.run_battle(task)
        self.assertEqual(task._solved_map_event, set())
        record.assert_not_called()

    def test_akashi_recorded_when_solved(self):
        task = self.make_task(safe=True)
        task.map_rescan = Mock(side_effect=lambda *a, **k: task._solved_map_event.add('is_akashi'))
        record = self.run_battle(task)
        record.assert_called_once_with(task.config)


class MeowStubMixin:
    """短猫相接三支处理函数的公共桩。"""

    def make_task(self, safe=True, search_completed=True):
        task = OpsiMeowfficerFarming.__new__(OpsiMeowfficerFarming)
        task.config = Mock()
        # 这些用例只关心扫图门控：显式关掉 AP 账本，收尾兜底识别不参与
        task.config.OpsiGeneral_ActionPointLedgerEnabled = False
        task.device = Mock()
        task.zone = Mock(zone_id=1)
        task.zones = Mock()
        task.is_zone_name_hidden = True
        task.globe_goto = Mock()
        task.fleet_set = Mock()
        task.os_order_execute = Mock()
        task.get_current_zone = Mock()
        task.run_strategic_search = Mock(return_value=search_completed)
        task.run_auto_search = Mock()
        task.should_run_post_search_scan = Mock(return_value=safe)
        task.clear_question = Mock()
        task.map_rescan = Mock()
        task.handle_after_auto_search = Mock()
        task._meow_fixed_patrol_scan = Mock()
        task._meow_record_akashi_if_solved = Mock()
        task.meow_search_metrics_start = Mock()
        task.meow_search_metrics_end = Mock()
        task.is_running_smart_scheduling_task = Mock(return_value=True)
        task.is_running_prevent_action_point_overflow_task = Mock(return_value=False)
        task.need_action_point_popup = Mock(return_value=False)
        return task


class TestMeowTraditionalGate(MeowStubMixin, unittest.TestCase):
    """短猫相接 ①传统指定海域。"""

    def test_unsafe_skips_scan(self):
        task = self.make_task(safe=False)
        with patch.object(task, '_meow_debug_clip', MagicMock()):
            task._meow_handle_traditional_zone(Mock(zone_id=1))
        task.clear_question.assert_not_called()
        task.map_rescan.assert_not_called()
        task.handle_after_auto_search.assert_called_once_with()

    def test_safe_scans(self):
        task = self.make_task(safe=True)
        with patch.object(task, '_meow_debug_clip', MagicMock()):
            task._meow_handle_traditional_zone(Mock(zone_id=1))
        task.clear_question.assert_called_once_with()
        task.map_rescan.assert_called_once_with()
        task.handle_after_auto_search.assert_called_once_with()

    def test_not_completed_short_circuits(self):
        """战略搜索未完成时不应调用门控（避免多余截图/OCR）。"""
        task = self.make_task(safe=True, search_completed=False)
        with patch.object(task, '_meow_debug_clip', MagicMock()):
            task._meow_handle_traditional_zone(Mock(zone_id=1))
        task.should_run_post_search_scan.assert_not_called()
        task.clear_question.assert_not_called()
        task.map_rescan.assert_not_called()
        task.handle_after_auto_search.assert_called_once_with()


class TestMeowStayInZoneGate(MeowStubMixin, unittest.TestCase):
    """短猫相接 ②循环出击指定海域（StayInZone），含强制移动。"""

    def test_unsafe_skips_scan_and_patrol(self):
        task = self.make_task(safe=False)
        with patch.object(task, '_meow_debug_clip', MagicMock()):
            task._meow_handle_stay_in_zone(Mock(zone_id=1))
        task.clear_question.assert_not_called()
        task.map_rescan.assert_not_called()
        task._meow_fixed_patrol_scan.assert_not_called()
        task.handle_after_auto_search.assert_called_once_with()

    def test_safe_scans_and_patrols(self):
        task = self.make_task(safe=True)
        with patch.object(task, '_meow_debug_clip', MagicMock()):
            task._meow_handle_stay_in_zone(Mock(zone_id=1))
        task.clear_question.assert_called_once_with()
        task.map_rescan.assert_called_once_with()
        task._meow_fixed_patrol_scan.assert_called_once_with()
        task.handle_after_auto_search.assert_called_once_with()


class TestMeowNormalSearchGate(MeowStubMixin, unittest.TestCase):
    """短猫相接 ④普通搜索。"""

    def make_normal_task(self, safe=True):
        task = self.make_task(safe=safe)
        zone = Mock(zone_id=7)
        chain = Mock()
        chain.delete.return_value = chain
        chain.sort_by_clock_degree.return_value = [zone]
        task.zone_select = Mock(return_value=chain)
        return task

    def test_unsafe_skips_scan(self):
        task = self.make_normal_task(safe=False)
        with patch.object(task, '_meow_debug_clip', MagicMock()):
            task._meow_handle_normal_search()
        task.clear_question.assert_not_called()
        task.map_rescan.assert_not_called()
        task.handle_after_auto_search.assert_called_once_with()

    def test_safe_scans(self):
        task = self.make_normal_task(safe=True)
        with patch.object(task, '_meow_debug_clip', MagicMock()):
            task._meow_handle_normal_search()
        task.clear_question.assert_called_once_with()
        task.map_rescan.assert_called_once_with()
        task.handle_after_auto_search.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
