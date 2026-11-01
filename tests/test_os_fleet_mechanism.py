"""双舰队机关解锁逻辑的回归测试（不连接游戏）。

背景：机关是相邻的两格，必须由两支不同的舰队各踩一格才会解锁。
旧实现有三个漏洞，本测试把它们逐条锁死：

1. 「踩上了」用 convert_radar_to_local((0, 0)) 反查舰队落点，该帧识别不到
   当前舰队时退化成「假设摄像机中心即当前舰队」，而摄像机中心正是刚点的格子，
   判定恒真 —— 属于自我证明。
2. 第二踩随便挑视野里可见的机关，不要求与第一踩相邻，踩完照样标记解锁。
3. 第二踩不校验舰队是否换了，同一支舰队连踩两格也算数。

全部用桩替换设备与地图交互，只验证分支编排。
"""

import unittest
from unittest.mock import Mock

from module.os.map import OSMap


class FakeGrid:
    """最小可用的格子桩：只有机关判定与坐标。"""

    def __init__(self, location, is_fleet_mechanism=False):
        self.location = location
        self.is_fleet_mechanism = is_fleet_mechanism
        self.is_current_fleet = False


class FakeView:
    """最小可用的视野桩：按局部坐标存格子，支持 in / [] / 迭代。"""

    def __init__(self, grids, center_loca=(0, 0)):
        self._grids = {tuple(g.location): g for g in grids}
        self.center_loca = center_loca

    def __iter__(self):
        return iter(self._grids.values())

    def __contains__(self, item):
        return tuple(item) in self._grids

    def __getitem__(self, item):
        return self._grids[tuple(item)]

    def select(self, **kwargs):
        """只支持 is_fleet_mechanism 过滤，够本测试用。"""
        flag = kwargs.get("is_fleet_mechanism", True)
        return [g for g in self._grids.values() if g.is_fleet_mechanism == flag]


def make_map(camera=(0, 0), center_loca=(0, 0), grids=None):
    """构造一个只挂了坐标换算所需状态的 OSMap。"""
    obj = OSMap.__new__(OSMap)
    obj.camera = camera
    obj.view = FakeView(grids or [], center_loca=center_loca)
    obj._solved_fleet_mechanism = False
    return obj


class TestCoordinateConvert(unittest.TestCase):
    """局部/全局坐标换算必须互逆。"""

    def test_round_trip(self):
        obj = make_map(camera=(4, 6), center_loca=(4, 4))
        for local in ((0, 0), (4, 2), (7, 3)):
            global_loc = obj._view_loc_to_global(local)
            self.assertEqual(obj._global_to_view_loc(global_loc), local)

    def test_global_uses_camera_not_local(self):
        # 摄像机在 (4,6)、视野中心 (4,4) 时，局部 (4,0) 对应全局 (4,2)
        obj = make_map(camera=(4, 6), center_loca=(4, 4))
        self.assertEqual(obj._view_loc_to_global((4, 0)), (4, 2))


class TestIsAdjacent(unittest.TestCase):
    """只有上下左右一格算相邻，斜对角与同格都不算。"""

    def test_adjacent(self):
        self.assertTrue(OSMap._is_adjacent((4, 2), (4, 3)))
        self.assertTrue(OSMap._is_adjacent((4, 2), (5, 2)))

    def test_not_adjacent(self):
        self.assertFalse(OSMap._is_adjacent((4, 2), (4, 2)))
        self.assertFalse(OSMap._is_adjacent((4, 2), (5, 3)))
        self.assertFalse(OSMap._is_adjacent((4, 2), (4, 4)))


class TestFindSecondMechanism(unittest.TestCase):
    """第二踩必须与第一踩相邻，不能随便挑可见机关。"""

    def test_returns_adjacent_cell(self):
        grids = [
            FakeGrid((0, 0), is_fleet_mechanism=True),
            FakeGrid((0, 1), is_fleet_mechanism=True),
        ]
        obj = make_map(grids=grids)
        self.assertEqual(obj._find_fleet_mechanism_second((0, 0)), (0, 1))

    def test_rejects_non_adjacent_cell(self):
        # 视野里只有一个远处机关：不能拿它当第二踩
        grids = [FakeGrid((5, 5), is_fleet_mechanism=True)]
        obj = make_map(grids=grids)
        self.assertIsNone(obj._find_fleet_mechanism_second((0, 0)))

    def test_rejects_itself(self):
        grids = [FakeGrid((0, 0), is_fleet_mechanism=True)]
        obj = make_map(grids=grids)
        self.assertIsNone(obj._find_fleet_mechanism_second((0, 0)))


class TestFindFirstMechanism(unittest.TestCase):
    """视野里只要有一格机关就能开踩，不要求同时看到两格。"""

    def test_single_cell_is_enough(self):
        grids = [FakeGrid((2, 3), is_fleet_mechanism=True)]
        obj = make_map(grids=grids)
        self.assertEqual(obj._find_fleet_mechanism_first(), (2, 3))

    def test_none_when_no_mechanism(self):
        obj = make_map(grids=[FakeGrid((0, 0))])
        self.assertIsNone(obj._find_fleet_mechanism_first())


class TestMechanismPressed(unittest.TestCase):
    """踩上判定不能用「现在不是机关」自证，必须有点击前的基线。"""

    def test_was_mechanism_now_gone(self):
        grids = [FakeGrid((0, 0), is_fleet_mechanism=False)]
        obj = make_map(grids=grids)
        self.assertTrue(obj._mechanism_pressed((0, 0), was_mechanism=True))

    def test_plain_sea_is_not_pressed(self):
        # 点击前就不是机关格（普通海面），点击后也不是——不能判成功
        grids = [FakeGrid((0, 0), is_fleet_mechanism=False)]
        obj = make_map(grids=grids)
        self.assertFalse(obj._mechanism_pressed((0, 0), was_mechanism=False))

    def test_still_mechanism_not_pressed(self):
        grids = [FakeGrid((0, 0), is_fleet_mechanism=True)]
        obj = make_map(grids=grids)
        self.assertFalse(obj._mechanism_pressed((0, 0), was_mechanism=True))


class TestUnlocked(unittest.TestCase):
    """两格描边都不见了才算解锁；有一格在视野外就不算。"""

    def _make(self, grids):
        obj = make_map(grids=grids)
        obj.device = Mock()
        obj.update_os = Mock()
        obj.view.predict = Mock()
        return obj

    def test_both_cleared(self):
        grids = [
            FakeGrid((0, 0), is_fleet_mechanism=False),
            FakeGrid((0, 1), is_fleet_mechanism=False),
        ]
        self.assertTrue(self._make(grids)._is_fleet_mechanism_unlocked((0, 0), (0, 1)))

    def test_one_still_visible(self):
        grids = [
            FakeGrid((0, 0), is_fleet_mechanism=False),
            FakeGrid((0, 1), is_fleet_mechanism=True),
        ]
        self.assertFalse(self._make(grids)._is_fleet_mechanism_unlocked((0, 0), (0, 1)))

    def test_out_of_view_is_not_unlocked(self):
        # 只有一格在视野内，判据缺失，不能宣布解锁
        grids = [FakeGrid((0, 0), is_fleet_mechanism=False)]
        self.assertFalse(self._make(grids)._is_fleet_mechanism_unlocked((0, 0), (9, 9)))


class TestHandleFleetMechanism(unittest.TestCase):
    """编排水位：第一踩记录坐标与队号，第二踩必须换队。"""

    def _make(self, second_fleet, current_fleet, second_ok=True):
        obj = make_map(grids=[FakeGrid((0, 1), is_fleet_mechanism=True)])
        obj._fleet_mechanism_second_fleet = second_fleet
        obj.get_second_fleet = Mock(return_value=second_fleet)
        obj.fleet_selector = Mock()
        obj.fleet_selector.get = Mock(return_value=current_fleet)
        obj.fleet_set = Mock()
        obj._press_fleet_mechanism = Mock(return_value=second_ok)
        obj._is_fleet_mechanism_unlocked = Mock(return_value=True)
        obj.os_auto_search_run = Mock()
        obj._solved_map_event = set()
        return obj

    def test_first_click_records_state(self):
        obj = self._make(second_fleet=1, current_fleet=3)
        obj._press_fleet_mechanism = Mock(return_value=True)
        obj._fleet_mechanism_first_global = None
        obj._fleet_mechanism_first_fleet = None

        self.assertTrue(obj._handle_fleet_mechanism((0, 0)))
        self.assertTrue(obj._solved_fleet_mechanism)
        self.assertEqual(obj._fleet_mechanism_first_global, (0, 0))
        self.assertEqual(obj._fleet_mechanism_first_fleet, 3)

    def test_second_click_refuses_same_fleet(self):
        # 第二队号与第一队相同：必须跳过，绝不换回同一支舰队
        obj = self._make(second_fleet=3, current_fleet=3)
        obj._solved_fleet_mechanism = True
        obj._fleet_mechanism_first_global = (0, 0)
        obj._fleet_mechanism_first_fleet = 3

        self.assertTrue(obj._handle_fleet_mechanism(None))
        obj._press_fleet_mechanism.assert_not_called()
        self.assertNotIn("is_fleet_mechanism", obj._solved_map_event)

    def test_second_click_switches_fleet_and_marks_solved(self):
        obj = self._make(second_fleet=1, current_fleet=1)
        obj._solved_fleet_mechanism = True
        obj._fleet_mechanism_first_global = (0, 0)
        obj._fleet_mechanism_first_fleet = 3

        self.assertTrue(obj._handle_fleet_mechanism(None))
        obj.fleet_set.assert_called_once_with(1)
        self.assertIn("is_fleet_mechanism", obj._solved_map_event)

    def test_not_marked_when_second_click_fails(self):
        obj = self._make(second_fleet=1, current_fleet=1, second_ok=False)
        obj._solved_fleet_mechanism = True
        obj._fleet_mechanism_first_global = (0, 0)
        obj._fleet_mechanism_first_fleet = 3

        self.assertTrue(obj._handle_fleet_mechanism(None))
        self.assertNotIn("is_fleet_mechanism", obj._solved_map_event)


if __name__ == "__main__":
    unittest.main()
