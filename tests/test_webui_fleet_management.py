"""舰队管理组导航回归：菜单单入口直达舰队科技页签工作台。

上游导航把舰队管理收成一个入口、子模块在组内切换；老前端由舰队科技
页签工作台承接（科技列表 / 舰队信息 / 心智计算器），组入口按钮在
子页面共享高亮，断线恢复走同一路由。组内的独立页面（舰队信息 /
舰队扫描）仍可各自恢复。
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
    def test_menu_entry_routes_to_tech_tab_workbench(self):
        """菜单组入口的断线恢复直达舰队科技页签工作台。"""
        stub = _page_stub(fleet_technology_page=object())
        page = Frame.page_renderer(stub, 'FleetManagement')
        self.assertIs(page, stub.fleet_technology_page)

    def test_task_pages_still_restore_their_own_pages(self):
        """组内子任务的恢复路由不受影响（舰队信息 / 舰队扫描）。"""
        stub = _page_stub(fleet_info_page=object(), fleet_scan_page=object())
        stub.ALAS_MENU = {'FleetManagement': {'menu': 'collapse', 'page': 'setting',
                                              'tasks': ['FleetInfo', 'FleetScan']}}
        self.assertIs(Frame.page_renderer(stub, 'FleetInfo'), stub.fleet_info_page)
        self.assertIs(Frame.page_renderer(stub, 'FleetScan'), stub.fleet_scan_page)

    def test_menu_highlight_alias_covers_group_tasks(self):
        """子页面高亮归并到组入口按钮（菜单里没有它们的独立按钮）。"""
        for task in ('FleetInfo', 'FleetScan', 'FleetTechnology', 'FleetTechScan', 'MindCalculatorScan'):
            self.assertEqual(Frame.MENU_HIGHLIGHT_ALIAS.get(task), 'FleetManagement')


if __name__ == '__main__':
    unittest.main()
