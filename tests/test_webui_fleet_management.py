"""舰队管理组导航回归：组入口与舰队科技工作台页已下线（入口迁入统计页）。

2026-10-16 调整：科技列表 / 心智计算器改由统计页的两个页签承载，
侧栏不再渲染舰队管理组，组入口直达的工作台页随之删除；组内任务
（舰队信息 / 舰队扫描）的恢复路由保持独立。
"""
import unittest
from types import SimpleNamespace

from module.webui.base import Frame


def _page_stub(**extra):
    """page_renderer 的命名页分支会整组取属性，按需给全桩。"""
    fields = dict(alas_overview=None, alas_set_stat=None, show_home=None, dev_set_menu=None,
                  dev_setting=None, dev_utils=None, dev_update=None, dev_remote=None)
    fields.update(extra)
    return SimpleNamespace(**fields)


class FleetManagementNavTest(unittest.TestCase):
    def test_group_entry_no_longer_renders_a_page(self):
        """组入口与工作台页已删除：断线恢复到这两个名字时回落总览。"""
        stub = _page_stub()
        stub.ALAS_MENU = {'FleetManagement': {'menu': 'collapse', 'page': 'setting',
                                              'tasks': ['FleetInfo', 'FleetScan']}}
        self.assertIsNone(Frame.page_renderer(stub, 'FleetManagement'))
        self.assertIsNone(Frame.page_renderer(stub, 'FleetTechnology'))

    def test_task_pages_still_restore_their_own_pages(self):
        """组内子任务的恢复路由不受影响（舰队信息 / 舰队扫描）。"""
        stub = _page_stub(fleet_info_page=object(), fleet_scan_page=object())
        stub.ALAS_MENU = {'FleetManagement': {'menu': 'collapse', 'page': 'setting',
                                              'tasks': ['FleetInfo', 'FleetScan']}}
        self.assertIs(Frame.page_renderer(stub, 'FleetInfo'), stub.fleet_info_page)
        self.assertIs(Frame.page_renderer(stub, 'FleetScan'), stub.fleet_scan_page)

    def test_highlight_alias_mechanism_is_gone(self):
        """组入口按钮不复存在，子页面高亮不再归并到别处。"""
        self.assertFalse(hasattr(Frame, 'MENU_HIGHLIGHT_ALIAS'))


if __name__ == '__main__':
    unittest.main()
