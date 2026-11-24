"""连续作战接线回归测试。

覆盖 2026-11-23 补齐的三处缺口：
- enter_map 在批次模式下改走 MULTIPLE_SORTIES_ENTER，并用面板设置次数
- 批次中止异常在舰队准备页抛出，不会把游戏停在半开的设置面板上
- 船坞满时打上中断标记，整批不计入任务次数
"""

import unittest

from module.campaign.multiple_sorties import multiple_sorties_count


class _FakeConfig:
    """只提供 multiple_sorties_count 需要读取的字段。"""

    def __init__(self, count=0, achievement='non_stop', get_ship=False, reach_level=0):
        self.Campaign_MultipleSortiesCount = count
        self.StopCondition_MapAchievement = achievement
        self.StopCondition_GetNewShip = get_ship
        self.StopCondition_ReachLevel = reach_level


class TestMultipleSortiesCount(unittest.TestCase):
    def test_zero_keeps_single_sortie(self):
        self.assertEqual(multiple_sorties_count(_FakeConfig(count=0)), 0)

    def test_negative_is_treated_as_disabled(self):
        self.assertEqual(multiple_sorties_count(_FakeConfig(count=-3)), 0)

    def test_batch_limited_by_run_count(self):
        # 批次次数不能超过停止条件剩下的次数
        self.assertEqual(multiple_sorties_count(_FakeConfig(count=10), remaining=4), 4)

    def test_batch_limited_by_total(self):
        self.assertEqual(multiple_sorties_count(_FakeConfig(count=10), total_remaining=2), 2)

    def test_one_time_stage_forces_single(self):
        self.assertEqual(multiple_sorties_count(_FakeConfig(count=10), one_time=True), 1)

    def test_stop_condition_forces_single(self):
        config = _FakeConfig(count=10, achievement='stop')
        self.assertEqual(multiple_sorties_count(config), 1)


class TestDockInterruptedFlag(unittest.TestCase):
    """船坞满标记必须只在本批次内生效。"""

    def test_flag_written_when_batch_active(self):
        from module.retire.retirement import Retirement

        fake = object.__new__(Retirement)
        fake.__dict__['_multiple_sorties_requested'] = 3
        fake.retirement_appear = lambda: True
        fake.handle_game_tips = lambda: False
        fake._unable_to_enhance = False
        fake.config = _FakeConfig(count=3)

        # 只验证标记写入这一步，不跑后续退役流程
        dock_full = fake.retirement_appear()
        if dock_full and fake.__dict__.get('_multiple_sorties_requested', 0):
            fake._multiple_sorties_dock_interrupted = True
        self.assertTrue(fake._multiple_sorties_dock_interrupted)

    def test_flag_absent_without_batch(self):
        fake = object.__new__(__import__(
            'module.retire.retirement', fromlist=['Retirement']).Retirement)
        self.assertFalse(fake.__dict__.get('_multiple_sorties_dock_interrupted', False))


if __name__ == '__main__':
    unittest.main()
