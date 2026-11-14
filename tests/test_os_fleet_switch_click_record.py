"""大世界换队点击记录清理的回归测试（不连接游戏）。

2026-11-11 17:24 事故：侵蚀一战后扫图，逐队尝试塞壬研究装置，7 次换队后
触发设备层 GameTooManyClickError「同一区域反复点击: 格子=(0, 2)」。

根因不是游戏卡住，而是设备层 4x3 网格检测把**正常换队**判成了点击洪水：
FLEET_CHOOSE 的点击中心是 (307, 667)，FLEET_BAR_INDEX_1/2/3 的点击中心是
(245, 594/541/488)，全部落在同一格 (0,2)。一次换队固定贡献 2 次同格点击，
而 map_rescan 一轮就要换 5 次队、装置路径再换 3~4 次，累计必然超过 12/15。

修复：换队**确认成功**后清掉本次换队产生的点击记录。失败路径必须保留累积，
因为 open()/click() 的重试路径唯一兜底就是点击频率检测。

本文件锁四件事：
1. 按钮名清单覆盖 FLEET_CHOOSE + 全部 INDEX 行（大世界 4 行、潜艇 5 行）
2. 清记录只清换队按钮，不误伤其它记录（如地图格子点击）
3. 端到端：按事故日志的 7 次换队重放，修复后不报错、且修复前必然报错
   （后者证明本测试不是空转）
4. 源码锁：清理只能在确认成功的分支，不能出现在 open() 的重试路径
"""

import inspect
import unittest
from collections import deque
from unittest.mock import patch

from module.base.timer import Timer as RealTimer
from module.device.device import Device
from module.exception import GameTooManyClickError
from module.os.map_fleet_selector import FleetSelector, StorageFleetSelector


class _AlwaysReachedTimer:
    """让状态循环里的节流计时器恒为「可以点」。

    真实 Timer(3, count=6) 首次 reached() 为真、之后要等 3 秒，直接把测试拖到
    40 秒以上。这里只关掉节流，不影响被测的「确认成功后清记录」逻辑。
    """

    def __init__(self, *args, **kwargs):
        pass

    def reached(self):
        return True

    def reset(self):
        return self

    def start(self):
        return self

    def clear(self):
        return self


class FakeDevice:
    """复用真实 Device 的点击频率检测逻辑，只替换底层点击动作。

    点击动作本身交给 owner（FakeMain）去做，因为「游戏是否响应」是地图状态，
    不属于设备层。
    """

    click_grid_add = Device.click_grid_add
    click_grid_check = Device.click_grid_check
    click_record_remove = Device.click_record_remove
    click_record_clear = Device.click_record_clear

    def __init__(self, owner, respond=True):
        self.owner = owner
        self.click_record = deque(maxlen=15)
        self.click_grid_record = deque(maxlen=15)
        self.click_grid_whitelist = []
        self.respond = respond
        self.clicks = []

    def stuck_record_clear(self):
        return None

    def handle_control_check(self, button):
        # 与 Device.handle_control_check 同序：先记名字与网格，再做两项检查
        self.stuck_record_clear()
        self.click_record_add_fake(button)
        self.click_record_check()
        self.click_grid_add(button)
        self.click_grid_check()

    click_record_add_fake = Device.click_record_add
    click_record_check = Device.click_record_check

    def click(self, button):
        name = str(button)
        self.clicks.append(name)
        self.handle_control_check(button)
        if self.respond:
            self.owner.apply_click(name)


class FakeMain:
    """大世界地图替身：负责「游戏响应点击」并驱动状态循环。"""

    def __init__(self, respond=True, current_fleet=1):
        self.current_fleet = current_fleet
        self.bar_open = False
        self.frames = 0
        self.device = FakeDevice(owner=self, respond=respond)

    def loop(self, **kwargs):
        # 上限只用于防止真实死循环把测试挂住；正常路径几步就 break
        while self.frames < 60:
            self.frames += 1
            yield None

    def handle_map_event(self):
        return False

    def apply_click(self, name):
        if name == 'FLEET_CHOOSE':
            self.bar_open = not self.bar_open
        elif '_INDEX_' in name:
            self.current_fleet = int(name.rsplit('_', 1)[1])
            self.bar_open = False


def make_selector(main, storage=False):
    selector = StorageFleetSelector(main=main) if storage else FleetSelector(main=main)
    selector.bar_opened = lambda: main.bar_open
    selector.get = lambda: main.current_fleet
    return selector


def switch_round(main, selector, index):
    """驱动一次完整换队：展开下拉 -> 选行 -> 确认。"""
    with patch('module.os.map_fleet_selector.Timer', _AlwaysReachedTimer):
        selector.click(index)


class TestSwitchClickRecordNames(unittest.TestCase):
    def test_world_fleet_names_cover_choose_and_all_rows(self):
        selector = make_selector(FakeMain())
        self.assertEqual(
            selector.switch_click_record_names(),
            ['FLEET_CHOOSE', 'FLEET_BAR_INDEX_1', 'FLEET_BAR_INDEX_2',
             'FLEET_BAR_INDEX_3', 'FLEET_BAR_INDEX_4'],
            '换队产生的按钮必须全部覆盖，漏一个就挡不住累积',
        )

    def test_storage_fleet_names_cover_submarine_row(self):
        selector = make_selector(FakeMain(), storage=True)
        names = selector.switch_click_record_names()
        self.assertEqual(names[0], 'STORAGE_FLEET_CHOOSE')
        self.assertEqual(len(names), 6, '仓库界面的舰队选择有 5 行（含潜艇）')


class TestClearOnlySwitchRecords(unittest.TestCase):
    def test_clear_removes_switch_records_and_keeps_others(self):
        main = FakeMain()
        selector = make_selector(main)
        device = main.device
        for button in (selector._choose, selector.get_button(2), selector.get_button(4)):
            device.handle_control_check(button)
        device.handle_control_check(SimpleGrid('G6'))

        selector.clear_switch_click_record()

        self.assertEqual(
            list(device.click_record), ['G6'],
            '只清换队按钮，地图格子等其它点击必须保留',
        )
        self.assertEqual(len(device.click_grid_record), 1, '网格记录同步清理')


class SimpleGrid:
    """带点击区域的地图格子替身（str() 即格子名）。"""

    def __init__(self, name, area=(1160, 480, 1200, 540)):
        self.name = name
        self.button = area

    def __str__(self):
        return self.name


class TestFleetSwitchFloodRegression(unittest.TestCase):
    """按 2026-11-11 事故的换队次数重放。"""

    SWITCHES = [(FakeMain, 2), (None, 3), (None, 4), (None, 1), (None, 2), (None, 3), (None, 4)]

    def replay(self, clear_switch_record):
        main = FakeMain(current_fleet=1)
        selector = make_selector(main)
        if not clear_switch_record:
            selector.clear_switch_click_record = lambda: None
        for _, index in self.SWITCHES:
            switch_round(main, selector, index)
        return main

    def test_seven_switches_do_not_trip_flood_guard(self):
        main = self.replay(clear_switch_record=True)
        self.assertEqual(main.current_fleet, 4, '最后一次换队应落到 4 队')
        self.assertLessEqual(
            len(main.device.click_grid_record), 2,
            f'确认成功后应清掉换队记录，实际残留 {list(main.device.click_grid_record)}',
        )

    def test_without_clearing_the_same_sequence_floods(self):
        """反证：不清记录时同一串换队必然报错，证明上面的测试不是空转。"""
        with self.assertRaises(GameTooManyClickError):
            self.replay(clear_switch_record=False)


class TestClearLocationSourceLock(unittest.TestCase):
    def test_clear_happens_on_confirmed_success_branch(self):
        source = inspect.getsource(FleetSelector.click)
        self.assertIn('self.clear_switch_click_record()', source)
        # 必须紧跟在 >= get() == index 的确认分支里，不能无条件执行
        success_at = source.index('if self.get() == index:')
        clear_at = source.index('self.clear_switch_click_record()')
        click_at = source.index('main.device.click(button)')
        self.assertLess(success_at, clear_at, '清理必须在确认换队成功的分支内')
        self.assertLess(clear_at, click_at, '清理应在成功分支，不能混进重试点行路径')

    def test_retry_paths_never_clear(self):
        """open()/click() 的重试路径唯一兜底是点击频率检测，清记录会让真死循环失控。"""
        for func in (FleetSelector.open, FleetSelector.close):
            self.assertNotIn(
                'click_record_clear' , inspect.getsource(func).replace(' ', ''),
                '重试路径禁止清点击记录',
            )
        click_source = inspect.getsource(FleetSelector.click)
        self.assertNotIn('click_record_clear()', click_source)


if __name__ == '__main__':
    unittest.main()
