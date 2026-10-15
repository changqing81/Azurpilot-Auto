"""舰队管理入口页（对齐上游单入口导航）的渲染与分发回归。

上游导航把舰队管理收成一个入口、子模块在组内切换；老前端以入口页承接：
四按钮分别进入舰队信息 / 舰队扫描 / 舰队科技 / 心智单元船坞扫描（配置页），
子页面在菜单栏共享组入口按钮的高亮。
"""
import inspect
import unittest
from unittest.mock import patch

from pywebio import output as pywebio_output

from module.webui.app_fleet_management import FleetManagementMixin

MODULE = 'module.webui.app_fleet_management'


class _FakeOutput:
    def style(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Harness(FleetManagementMixin):
    """记录分发目标；四个页面方法以记账桩替换。"""

    def __init__(self):
        self.alas_name = 'alas'
        self.calls = []

    def init_menu(self, **kwargs):
        self.calls.append(('init_menu', kwargs.get('name')))

    def set_title(self, *args, **kwargs):
        pass

    def fleet_info_page(self, task=None):
        self.calls.append(('page', 'FleetInfo'))

    def fleet_scan_page(self, task=None):
        self.calls.append(('page', 'FleetScan'))

    def fleet_technology_page(self, task=None):
        self.calls.append(('page', 'FleetTechnology'))

    def alas_set_group(self, task=None, expand_group=None):
        self.calls.append(('page', 'MindCalculatorScan'))


class FleetManagementEntranceTest(unittest.TestCase):
    def test_enter_dispatches_to_sub_pages(self):
        """四个入口各自进入对应页面；未知值不动作。"""
        harness = _Harness()
        for value in ('FleetInfo', 'FleetScan', 'FleetTechnology', 'MindCalculatorScan'):
            harness.calls.clear()
            harness._fleet_management_enter(value)
            self.assertEqual(harness.calls, [('page', value)])
        harness.calls.clear()
        harness._fleet_management_enter('bogus')
        self.assertEqual(harness.calls, [])

    def test_entrance_page_renders_four_menu_buttons(self):
        """入口页：组名高亮 + 四个 menu 色按钮（按真签名校验渲染调用）。"""
        harness = _Harness()
        buttons = []

        def fake_put_buttons(content, onclick=None, **kwargs):
            inspect.signature(pywebio_output.put_buttons).bind(content, onclick, **kwargs)
            buttons.extend(content)
            return _FakeOutput()

        with patch(f'{MODULE}.put_buttons', fake_put_buttons), \
                patch(f'{MODULE}.t', side_effect=lambda key, **kw: key):
            FleetManagementMixin.fleet_management_page.__wrapped__(harness)
        self.assertEqual(harness.calls, [('init_menu', 'FleetManagement')])
        self.assertEqual([item['value'] for item in buttons],
                         ['FleetInfo', 'FleetScan', 'FleetTechnology', 'MindCalculatorScan'])
        self.assertTrue(all(item['color'] == 'menu' for item in buttons))

    def test_menu_highlight_alias_covers_group_tasks(self):
        """子页面高亮归并到组入口按钮（菜单里没有它们的独立按钮）。"""
        from module.webui.base import Frame
        for task in ('FleetInfo', 'FleetScan', 'FleetTechnology', 'MindCalculatorScan'):
            self.assertEqual(Frame.MENU_HIGHLIGHT_ALIAS.get(task), 'FleetManagement')


if __name__ == '__main__':
    unittest.main()
