"""
智能调度行动力保留双向同步的回归测试。

覆盖 save_callback 中值 > 0 才同步的规则：
短猫相接（OpsiScheduling.ActionPointPreserve ↔ OpsiMeowfficerFarming.ActionPointPreserve）
与侵蚀1（OpsiScheduling.Hazard1ActionPointPreserve ↔ OpsiHazard1Leveling.MinimumActionPointReserve）。
两侧 0 值语义不同（智能调度侧 0 = 不覆盖），因此 0 不得互相清零。
"""
import unittest

from module.config.config_updater import ConfigUpdater


SMART_AP = 'OpsiScheduling.OpsiScheduling.ActionPointPreserve'
MEOW_AP = 'OpsiMeowfficerFarming.OpsiMeowfficerFarming.ActionPointPreserve'
SMART_H1_AP = 'OpsiScheduling.OpsiScheduling.Hazard1ActionPointPreserve'
CL1_AP = 'OpsiHazard1Leveling.OpsiHazard1Leveling.MinimumActionPointReserve'


class TestActionPointPreserveSync(unittest.TestCase):

    def setUp(self):
        self.updater = ConfigUpdater()

    def test_smart_meow_ap_syncs_to_meow_task(self):
        r = list(self.updater.save_callback(SMART_AP, 1500))
        self.assertEqual(r, [(MEOW_AP, 1500)])

    def test_meow_task_ap_syncs_back_to_smart(self):
        r = list(self.updater.save_callback(MEOW_AP, 1200))
        self.assertEqual(r, [(SMART_AP, 1200)])

    def test_smart_ap_zero_does_not_clear_meow_task(self):
        # 智能调度侧 0 = 不覆盖沿用任务页，清零短猫任务页会破坏防呆
        r = list(self.updater.save_callback(SMART_AP, 0))
        self.assertEqual(r, [])

    def test_meow_task_ap_zero_not_synced(self):
        r = list(self.updater.save_callback(MEOW_AP, 0))
        self.assertEqual(r, [])

    def test_string_values_are_parsed(self):
        # WebUI 表单可能传字符串数字
        r = list(self.updater.save_callback(SMART_AP, '800'))
        self.assertEqual(r, [(MEOW_AP, '800')])

    def test_invalid_values_not_synced(self):
        self.assertEqual(list(self.updater.save_callback(SMART_AP, 'abc')), [])
        self.assertEqual(list(self.updater.save_callback(SMART_AP, None)), [])

    def test_smart_hazard1_ap_syncs_to_cl1_task(self):
        r = list(self.updater.save_callback(SMART_H1_AP, 300))
        self.assertEqual(r, [(CL1_AP, 300)])

    def test_cl1_task_ap_syncs_back_to_smart(self):
        r = list(self.updater.save_callback(CL1_AP, 260))
        self.assertEqual(r, [(SMART_H1_AP, 260)])

    def test_smart_hazard1_ap_zero_keeps_cl1_task_value(self):
        # 侵蚀1任务页的 0 是真实保留值（搏一搏玩法），不能被智能调度的 0 覆盖
        self.assertEqual(list(self.updater.save_callback(SMART_H1_AP, 0)), [])

    def test_negative_values_not_synced(self):
        self.assertEqual(list(self.updater.save_callback(SMART_AP, -5)), [])
        self.assertEqual(list(self.updater.save_callback(SMART_H1_AP, -5)), [])


if __name__ == '__main__':
    unittest.main()
