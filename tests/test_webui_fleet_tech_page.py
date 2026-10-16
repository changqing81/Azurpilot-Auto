"""舰队科技页交互回调的行为回归：筛选切换、点击桥、弹窗草稿与保存冲突。

全部回调都在无会话的替身上执行，PyWebIO 输出层整体打桩；
真实渲染由 GUI 实测覆盖，这里只锁定状态机与数据组装。

另有两道防复发闸门（2026-10-15 GUI 首跑事故后新增）：
- 全流程渲染的每个 PyWebIO 调用都经**真函数签名 bind 校验**
  （事故一：_ft_load 的 dict 重复 revision；事故二：put_select 不存在
   onchange 参数、use_scope 的容器参数名是 container_scope）；
- 页面用到的 i18n 键（静态 + 动态枚举域）必须全部存在于 zh-CN。
"""
import inspect
import json
import re
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from pywebio import output as pywebio_output
from pywebio import pin as pywebio_pin
from pywebio import session as pywebio_session

from module.webui import fleet_tech_view as view
from module.webui.app_fleet_management import FleetManagementMixin
from module.webui.app_fleet_technology import (
    BRIDGE_PIN,
    MAX_LEVEL_PATH,
    MIN_LEVEL_PATH,
    FleetTechnologyMixin,
    build_fleet_tech_bridge_script,
)

MODULE = 'module.webui.app_fleet_technology'
ROOT = Path(__file__).resolve().parents[1]


def _row(gid=10102, name='杜威', owned=None, full_star=None, lv120=None, level=None, status='pending'):
    return {'group_type': gid, 'code': 4, 'name': name, 'names': {'cn': name}, 'nationality': 1,
            'rarity': 'SSR', 'type': 1, 'technology': None, 'owned': owned, 'full_star': full_star,
            'lv120': lv120, 'level': level, 'status': status, 'manual': False}


class _Harness(FleetManagementMixin, FleetTechnologyMixin):
    """只带回调所需状态的最小替身；渲染函数记账不落真实输出。

    同时混入 FleetManagementMixin：扫描启动的两段式 helper
    （``_start_scan_task``）定义在那里，与产品里 AlasGUI 的 MRO 一致。
    """

    def __init__(self):
        self.alas_name = 'alas'
        self._ft_state()
        self._ft_rows = [_row(), _row(10103, '卡辛', level=95, status='unfinished')]
        self.render_log = []

    def init_menu(self, **kwargs):
        """页面外壳桩。"""

    def set_title(self, *args, **kwargs):
        """页面外壳桩。"""

    @property
    def task_handler(self):
        class _Handler:
            @staticmethod
            def add(*args, **kwargs):
                return None

        return _Handler()

    def _ft_render_filter_group(self, key):
        self.render_log.append(f'filter:{key}')

    def _ft_render_table(self):
        self.render_log.append('table')

    def _ft_render_functions(self):
        self.render_log.append('functions')

    def _ft_render_stage(self):
        self.render_log.append('stage')

    def _ft_render_bonuses(self):
        self.render_log.append('bonuses')

    def _ft_render_summary(self):
        self.render_log.append('summary')

    # FleetManagementMixin 的信息页依赖（页签框架复用它们）。
    def _fleet_info_html(self, result, record):
        self.render_log.append('fleet_info_html')
        return '<div class="fleet-info">fleet-info-html</div>'

    def _fleet_scan_running(self):
        return False

    def _fleet_scan_start(self):
        pass

    def _fleet_scan_running_click(self):
        pass

    @property
    def alas_config(self):
        class _Config:
            def __init__(self, data):
                self._data = data

            def read_file(self, name):
                return self._data

        return _Config(getattr(self, '_alas_config_data', {}))


class _FakeOutput:
    """哑输出：容忍链式 style 调用，并可作为上下文管理器。"""

    def __init__(self, name='out'):
        self.name = name

    def style(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSwitchButton:
    """BinarySwitchButton 替身（构造参数忽略）。"""

    def __init__(self, **kwargs):
        pass

    def g(self):
        return _FakeOutput('switch')


class _FakePin:
    """与 PyWebIO Pin 对象同契约：仅支持 [] 读取（缺失为 None）。

    刻意不提供字典式 get：页面代码若写 ``pin.get``，真机上会被 Pin 的
    __getattr__ 兜底成"读取名为 get 的控件"并返回 None（调用即 TypeError），
    在替身上则以 AttributeError 当场暴露。
    """

    def __init__(self, values=None):
        self._values = dict(values or {})

    def __getitem__(self, name):
        return self._values.get(name)

    def __setitem__(self, name, value):
        self._values[name] = value


_CHECKED_API = {
    'put_html': pywebio_output.put_html,
    'put_row': pywebio_output.put_row,
    'put_scope': pywebio_output.put_scope,
    'put_buttons': pywebio_output.put_buttons,
    'put_input': pywebio_pin.put_input,
    'put_select': pywebio_pin.put_select,
    'use_scope': pywebio_output.use_scope,
    'run_js': pywebio_session.run_js,
    'toast': pywebio_output.toast,
    'pin_on_change': pywebio_pin.pin_on_change,
    'clear': pywebio_output.clear,
    'popup': pywebio_output.popup,
    'close_popup': pywebio_output.close_popup,
}


def _checked(name, real, record):
    """包装输出函数：先按真签名校验调用，再记录并返回哑输出。"""

    def wrapper(*args, **kwargs):
        if name == 'use_scope':
            # use_scope 的具名参数（clear/create_scope）之外的部分才透传给 set_scope
            forwarded = {key: value for key, value in kwargs.items()
                         if key not in ('clear', 'create_scope')}
            inspect.signature(pywebio_output.set_scope).bind('probe', if_exist=None, **forwarded)
        else:
            inspect.signature(real).bind(*args, **kwargs)
        record.append((name, args, kwargs))
        return _FakeOutput(name)

    return wrapper


class FleetTechPageTest(unittest.TestCase):
    def setUp(self):
        self.harness = _Harness()

    # ---------- 筛选 ----------

    def test_filter_toggles_multi_select_and_clear(self):
        h = self.harness
        h._ft_on_filter('type|front')
        self.assertEqual(h._ft_filters, {'type': ['front']})
        h._ft_on_filter('type|back')
        # 选择集保持排序，重绘顺序稳定
        self.assertEqual(h._ft_filters, {'type': ['back', 'front']})
        h._ft_on_filter('type|front')
        self.assertEqual(h._ft_filters, {'type': ['back']})
        h._ft_on_filter('type|')
        self.assertNotIn('type', h._ft_filters)

    def test_filter_ignores_unknown_group(self):
        self.harness._ft_on_filter('bogus|x')
        self.assertEqual(self.harness._ft_filters, {})

    def test_filter_resets_page_and_repaints_group_and_table(self):
        h = self.harness
        h._ft_page = 3
        h._ft_on_filter('rarity|SSR')
        self.assertEqual(h._ft_page, 0)
        self.assertEqual(h.render_log, ['filter:rarity', 'table'])

    # ---------- 数据装载 ----------

    def test_load_assembles_report_from_instance_config(self):
        """实例配置与真 tech.report 的装配：revision 由 report 自带，不重复传参。"""
        from module.runtime import fleet_tech as real_tech
        fake = {'FleetTechScan': {'FleetTech': {
            'MinLevel': 96, 'MaxLevel': 120, 'Record': {'observed': {'10102': 125}}}}}
        with patch(f'{MODULE}.load_config') as load_config:
            load_config.return_value.read_file.return_value = fake
            catalog, report = self.harness._ft_load(force=True)
        self.assertEqual(report['min_level'], 96)
        self.assertEqual(report['max_level'], 120)
        self.assertEqual(report['revision'], real_tech.revision({'observed': {'10102': 125}}))
        self.assertEqual(report['instance'], 'alas')
        row = next(row for row in self.harness._ft_rows if row['group_type'] == 10102)
        self.assertEqual(row['status'], 'complete')
        self.assertGreaterEqual(len(catalog['ships']), 897)

    # ---------- 点击桥（排序 / 编辑入口） ----------

    def test_bridge_sort_toggles_same_key_and_switches_new_key(self):
        h = self.harness
        h._ft_sort_key, h._ft_sort_direction = 'code', 1
        h._ft_on_bridge('sort|code')
        self.assertEqual((h._ft_sort_key, h._ft_sort_direction), ('code', -1))
        h._ft_on_bridge('sort|total')
        self.assertEqual((h._ft_sort_key, h._ft_sort_direction), ('total', 1))
        h._ft_page = 2
        h._ft_on_bridge('sort|total')
        self.assertEqual((h._ft_page, h._ft_sort_direction), (0, -1))

    def test_bridge_ignores_unknown_sort_and_bad_gid(self):
        h = self.harness
        h._ft_on_bridge('sort|not_a_column')
        self.assertEqual(h._ft_sort_key, 'code')
        h._ft_on_bridge('edit|not_an_int')
        self.assertEqual(h.render_log, [])
        h._ft_on_bridge('')

    def test_bridge_edit_opens_popup(self):
        h = self.harness
        with patch.object(h, '_ft_edit_popup') as popup:
            h._ft_on_bridge('edit|10102')
        popup.assert_called_once_with(10102)

    def test_bridge_script_targets_hooks_and_pin(self):
        script = build_fleet_tech_bridge_script('ft_bridge_pin')
        self.assertIn('data-ft-sort', script)
        self.assertIn('data-ft-edit', script)
        self.assertIn('ft_bridge_pin', script)
        self.assertIn('__alasFleetTechClick', script)

    # ---------- 开关与翻页 ----------

    def test_toggle_flips_switch_and_repaints(self):
        h = self.harness
        h._ft_on_toggle('edit')
        self.assertTrue(h._ft_edit)
        h._ft_on_toggle('bonuses')
        self.assertTrue(h._ft_bonuses)
        h._ft_on_toggle('edit')
        self.assertFalse(h._ft_edit)
        h._ft_on_toggle('bogus')
        self.assertFalse(hasattr(h, '_ft_bogus'))
        self.assertIn('table', h.render_log)

    def test_page_buttons_adjust_and_clamp_slice(self):
        h = self.harness
        h._ft_page = 0
        h._ft_on_page('prev')
        self.assertEqual(h._ft_page, -1)  # page_slice 在渲染时收敛
        h._ft_on_page('next')
        h._ft_on_page('bogus')
        self.assertEqual(h._ft_page, 0)

    # ---------- 弹窗草稿（仅提交改动字段） ----------

    def _collect(self, values, row=None):
        with patch(f'{MODULE}.pin', _FakePin(values)):
            return self.harness._ft_collect_change(10102, row or _row())

    def test_collect_change_only_reports_differences(self):
        row = _row(owned=True, full_star=None, lv120=True, level=125)
        # 原封不动：空提交
        change = self._collect({'ft_owned_10102': 'true', 'ft_full_star_10102': '',
                                'ft_lv120_10102': 'true', 'ft_level_10102': '125'}, row)
        self.assertEqual(change, {'group_type': 10102})
        # 只改满星
        change = self._collect({'ft_owned_10102': 'true', 'ft_full_star_10102': 'true',
                                'ft_lv120_10102': 'true', 'ft_level_10102': '125'}, row)
        self.assertEqual(change, {'group_type': 10102, 'full_star': True})

    def test_collect_change_level_and_tri_state(self):
        change = self._collect({'ft_owned_10102': '', 'ft_full_star_10102': '', 'ft_lv120_10102': '',
                                'ft_level_10102': ''})
        self.assertEqual(change, {'group_type': 10102})
        change = self._collect({'ft_owned_10102': 'false', 'ft_full_star_10102': '', 'ft_lv120_10102': '',
                                'ft_level_10102': '100'})
        self.assertEqual(change, {'group_type': 10102, 'owned': False, 'level': 100})

    def test_collect_change_rejects_invalid_level(self):
        for raw in ('0', '126', 'abc'):
            with self.assertRaises(ValueError):
                self._collect({'ft_owned_10102': '', 'ft_full_star_10102': '',
                               'ft_lv120_10102': '', 'ft_level_10102': raw})

    # ---------- 保存（冲突 / 成功） ----------

    def _save(self, revision, updated=None, error=None):
        h = self.harness
        h._ft_revision = 'expected'
        with patch(f'{MODULE}.config_transaction'), \
                patch(f'{MODULE}.load_config') as load_config, \
                patch(f'{MODULE}.deep_get', return_value={}), \
                patch(f'{MODULE}.deep_set') as deep_set, \
                patch(f'{MODULE}.State') as state, \
                patch(f'{MODULE}.tech') as tech_mod, \
                patch.object(h, '_ft_load'):
            tech_mod.revision.return_value = revision
            if error is not None:
                tech_mod.apply_changes.side_effect = error
            else:
                tech_mod.apply_changes.return_value = {'manual': {}}
            result = h._ft_save_changes([{'group_type': 10102, 'owned': True}])
        return result, deep_set, state

    def test_save_rejects_stale_revision(self):
        result, deep_set, state = self._save('stale')
        self.assertIn('Gui.FleetTechnology.Conflict', result)
        deep_set.assert_not_called()
        state.config_updater.write_file.assert_not_called()

    # ---------- 扫描启动 ----------

    def test_scan_start_reads_pin_writes_range_and_runs(self):
        """扫描按钮：读 pin 输入的等级范围 → 写配置 → 启动心智扫描任务。"""
        h = self.harness
        h.alas = MagicMock()
        h.alas.alive = False  # 同步启动路径（存活时走两段式，另行测试）
        h.alas._ui_action_running = False
        written = []
        with patch(f'{MODULE}.pin', _FakePin({'ft_min_level': '96', 'ft_max_level': '120'})), \
                patch.object(h, '_ft_write_config', side_effect=lambda changes: written.extend(changes)), \
                patch(f'{MODULE}.toast'):
            h._ft_scan_start()
        self.assertEqual(written, [(MIN_LEVEL_PATH, 96), (MAX_LEVEL_PATH, 120)])
        h.alas.start.assert_called_once_with('FleetTechScan')

    def test_scan_start_rejects_invalid_range(self):
        """非法范围（越界/倒挂/非数字/空）只提示，不写配置不启动。"""
        h = self.harness
        h.alas = MagicMock()
        h.alas.alive = False  # 同步启动路径（存活时走两段式，另行测试）
        h.alas._ui_action_running = False
        written = []
        for values in ({'ft_min_level': '0', 'ft_max_level': '125'},
                       {'ft_min_level': '120', 'ft_max_level': '95'},
                       {'ft_min_level': 'abc', 'ft_max_level': '125'},
                       {}):
            with patch(f'{MODULE}.pin', _FakePin(values)), \
                    patch.object(h, '_ft_scan_running', return_value=False), \
                    patch.object(h, '_ft_write_config', side_effect=lambda changes: written.extend(changes)), \
                    patch(f'{MODULE}.toast') as toast:
                h._ft_scan_start()
            toast.assert_called_once()
            h.alas.start.assert_not_called()
        self.assertEqual(written, [])

    def test_scan_start_ignores_duplicate_within_window(self):
        """运行窗内再次触发只提示，不重复启动（真实时间窗判定）。"""
        h = self.harness
        h.alas = MagicMock()
        h.alas.alive = False  # 同步启动路径（存活时走两段式，另行测试）
        h.alas._ui_action_running = False
        h._ft_scan_started_at = time.monotonic()
        with patch(f'{MODULE}.pin', _FakePin({'ft_min_level': '95', 'ft_max_level': '125'})), \
                patch.object(h, '_ft_write_config') as write, \
                patch(f'{MODULE}.toast') as toast:
            h._ft_scan_start()
        h.alas.start.assert_not_called()
        write.assert_not_called()
        toast.assert_called_once()

    def test_scan_running_uses_local_window(self):
        """运行窗只看本会话点击时间，与 alas 有没有别的任务在跑无关。"""
        h = self.harness
        h.alas = MagicMock()  # 即便 alas 一直存活
        h.alas._ui_action_running = False
        h._ft_scan_started_at = time.monotonic()
        self.assertTrue(h._ft_scan_running())
        h._ft_scan_started_at = time.monotonic() - 601
        self.assertFalse(h._ft_scan_running())

    def test_scan_running_false_when_never_started_on_fresh_boot(self):
        """未启动过扫描时必须判为未运行——即便开机不足窗口秒数（CI runner）。

        现场：2026-10-16 CI unittest——`getattr(..., '_ft_scan_started_at', 0.0)`
        配 `time.monotonic()`（开机以来的秒数），runner 开机不足 600 秒窗口时
        `monotonic() - 0.0 < 600` 恒真，扫描按钮被判成「扫描中」直接早退，
        既不写等级范围也不启动任务。
        """
        h = self.harness
        h.alas = MagicMock()
        h.alas.alive = False
        h.alas._ui_action_running = False
        written = []
        with patch(f'{MODULE}.time.monotonic', return_value=42.0), \
                patch(f'{MODULE}.pin', _FakePin({'ft_min_level': '96', 'ft_max_level': '120'})), \
                patch.object(h, '_ft_write_config', side_effect=lambda changes: written.extend(changes)), \
                patch(f'{MODULE}.toast'):
            self.assertFalse(h._ft_scan_running())
            h._ft_scan_start()
        self.assertEqual(written, [(MIN_LEVEL_PATH, 96), (MAX_LEVEL_PATH, 120)])
        h.alas.start.assert_called_once_with('FleetTechScan')

    def test_scan_start_uses_stop_then_start_when_alive(self):
        """实例存活时扫描走两段式：停（后台等收尾）→ 以扫描任务启动。"""
        h = self.harness
        h.alas = MagicMock()
        captured = []

        def fake_spawn(pm, action, pending):
            captured.append((action, pending))
            return True

        with patch('module.webui.app_fleet_management.spawn_instance_action',
                   side_effect=fake_spawn):
            h.alas.alive = True
            accepted = h._start_scan_task('FleetScan')
            h.alas.alive = False  # 收尾完成
        self.assertTrue(accepted)
        self.assertEqual(len(captured), 1)
        action, pending = captured[0]
        self.assertEqual(pending, 'start')
        action()
        h.alas.stop_by_user.assert_called_once()
        h.alas.start.assert_called_once_with('FleetScan')

    def test_save_applies_and_writes_on_match(self):
        result, deep_set, state = self._save('expected')
        self.assertIsNone(result)
        deep_set.assert_called_once()
        state.config_updater.write_file.assert_called_once()

    def test_save_reports_change_error(self):
        h = self.harness
        h._ft_revision = 'expected'
        with patch(f'{MODULE}.config_transaction'), \
                patch(f'{MODULE}.load_config'), \
                patch(f'{MODULE}.deep_get', return_value={}), \
                patch(f'{MODULE}.deep_set'), \
                patch(f'{MODULE}.State'), \
                patch(f'{MODULE}.tech') as tech_mod, \
                patch.object(h, '_ft_load'):
            tech_mod.revision.return_value = 'expected'
            tech_mod.FleetTechChangeError = ValueError
            tech_mod.apply_changes.side_effect = ValueError('舰船群组不存在或重复')
            result = h._ft_save_changes([{'group_type': 1}])
        self.assertEqual(result, '舰船群组不存在或重复')

    def test_bridge_tab_switches_and_keeps_state(self):
        """页签切换：重绘页签行与内容；同页签不重绘；未知页签忽略。

        拦截 _ft_render_tab_content 而非子方法：真 use_scope 在无会话线程
        会触发 pywebio 的 script 模式服务器 fallback 并永久阻塞（见
        session/__init__.py get_current_session 的回退逻辑）。
        """
        h = self.harness
        h._ft_render_tabs = lambda: h.render_log.append('tabs')

        def fake_content():
            h.render_log.append(f'tab:{h._ft_tab}')

        h._ft_render_tab_content = fake_content
        h._ft_on_bridge('tab|info')
        self.assertEqual(h._ft_tab, 'info')
        self.assertEqual(h.render_log, ['tabs', 'tab:info'])
        h._ft_on_bridge('tab|info')
        self.assertEqual(h.render_log, ['tabs', 'tab:info'])
        h._ft_on_bridge('tab|tech')
        self.assertEqual(h._ft_tab, 'tech')
        h._ft_on_bridge('tab|bogus')
        self.assertEqual(h._ft_tab, 'tech')

    def test_info_tab_renders_scan_toggle_and_empty_hint(self):
        """舰队信息页签（无扫描结果）：扫描开关 scope + 空结果提示。

        use_scope 同样需要桩（真实现会走 script 模式 fallback 卡死）。
        """
        h = self.harness
        with patch(f'{MODULE}.put_html', side_effect=lambda html: _FakeOutput(html)) as put_html, \
                patch(f'{MODULE}.put_scope', side_effect=lambda name, **kw: _FakeOutput(name)) as put_scope, \
                patch(f'{MODULE}.use_scope', side_effect=lambda name, **kw: _FakeOutput(name)), \
                patch(f'{MODULE}.BinarySwitchButton', _FakeSwitchButton):
            h._ft_render_info_tab()
        texts = [call.args[0] for call in put_html.call_args_list if call.args]
        self.assertTrue(any('Gui.FleetManagement.NoResult' in text for text in texts))
        scopes = [call.args[0] for call in put_scope.call_args_list if call.args]
        self.assertIn('ft_info_scan_button', scopes)

    def test_info_tab_renders_fleets_with_result(self):
        """舰队信息页签（有扫描结果）：渲染编队展示 HTML。"""
        h = self.harness
        h._alas_config_data = {'FleetInfo': {'FleetInfo': {
            'Result': {'vanguard': {'1': [{'name': '杜威', 'level': 120}]}},
            'Record': '2026-10-15 18:35'}}}
        with patch(f'{MODULE}.put_html', side_effect=lambda html: _FakeOutput(html)) as put_html, \
                patch(f'{MODULE}.put_scope', side_effect=lambda name, **kw: _FakeOutput(name)) as put_scope, \
                patch(f'{MODULE}.use_scope', side_effect=lambda name, **kw: _FakeOutput(name)), \
                patch(f'{MODULE}.BinarySwitchButton', _FakeSwitchButton):
            h._ft_render_info_tab()
        texts = [call.args[0] for call in put_html.call_args_list if call.args]
        self.assertTrue(any('fleet-info-html' in text for text in texts))
        self.assertTrue(any('Gui.FleetManagement.LastScan' in text or 'fleet-info' in text
                            for text in texts))


    def test_data_refresh_detects_external_writes(self):
        """周期刷新：基线建立后记录/清单变化才重绘，无变化不重绘。"""
        h = self.harness
        h._ft_state()
        base = {'FleetTechScan': {'FleetTech': {'Record': {'observed': {'10102': 125}}}},
                'MindCalculatorScan': {'MindCalculator': {'Result': {'ships': []}}}}
        changed = {'FleetTechScan': {'FleetTech': {'Record': {'observed': {'10102': 125,
                                                                          '10103': 95}}}},
                   'MindCalculatorScan': {'MindCalculator': {'Result': {'ships': []}}}}
        with patch(f'{MODULE}.load_config') as load_config, \
                patch.object(h, '_ft_refresh_render') as render:
            load_config.return_value.read_file.return_value = base
            h._ft_data_refresh()
            render.assert_not_called()
            h._ft_data_refresh()
            render.assert_not_called()
            load_config.return_value.read_file.return_value = changed
            h._ft_data_refresh()
            render.assert_called_once()


class FleetTechRenderSmokeTest(unittest.TestCase):
    """全流程渲染冒烟：每个 PyWebIO 调用都通过真函数签名校验。"""

    FAKE_DATA = {'FleetTechScan': {'FleetTech': {
        'MinLevel': 95, 'MaxLevel': 125}},
        'FleetInfo': {'FleetInfo': {
            'Result': {'vanguard': {'1': [{'name': '杜威', 'level': 120}]}}}}}

    def test_full_render_passes_real_api_signatures(self):
        from module.webui import app_fleet_technology as module
        harness = _Harness()
        record = []
        with ExitStack() as stack:
            for name, real in _CHECKED_API.items():
                stack.enter_context(patch.object(module, name, _checked(name, real, record)))
            stack.enter_context(patch.object(module, 'BinarySwitchButton', _FakeSwitchButton))
            stack.enter_context(patch.object(module, 'pin', _FakePin()))
            stack.enter_context(patch.object(module, 't', side_effect=lambda key, **kw: key))
            stack.enter_context(patch('module.webui.fleet_tech_view.t',
                                      side_effect=lambda key, **kw: key))
            load_config = stack.enter_context(patch.object(module, 'load_config'))
            load_config.return_value.read_file.return_value = self.FAKE_DATA
            harness._alas_config_data = self.FAKE_DATA  # 舰队信息页签的编队数据

            raw = FleetTechnologyMixin.fleet_technology_page.__wrapped__
            raw(harness)                                  # 初次渲染（科技列表页签）
            raw(harness, 'FleetManagement')               # 组入口进入（标题用组名）
            harness._ft_on_bridge('tab|info')             # 页签切换：舰队信息
            harness._ft_on_bridge('tab|tech')             # 切回科技列表
            harness._ft_edit = True                       # 编辑列 + 加成视图 + 阶段选择
            harness._ft_bonuses = True
            harness._ft_extra = True
            raw(harness)                                  # 重入渲染
            FleetTechnologyMixin._ft_edit_popup(harness, 10102)        # 弹窗表单

        names = [item[0] for item in record]
        for required in ('put_scope', 'put_buttons', 'put_select', 'put_input',
                         'pin_on_change', 'run_js', 'popup'):
            self.assertIn(required, names)
        bridges = [kwargs for name, _, kwargs in record
                   if name == 'put_input' and kwargs.get('name') == BRIDGE_PIN]
        self.assertTrue(bridges, '点击桥 pin 必须渲染')
        tabs = [args for name, args, kwargs in record
                if name == 'put_html' and args and 'data-ft-tab' in str(args[0])]
        self.assertTrue(tabs, '页签行必须渲染')
        self.assertIn('fleet_info_html', harness.render_log)


class FleetTechI18nTest(unittest.TestCase):
    """页面用到的翻译键必须全部存在（静态 + 动态枚举域）。"""

    def test_i18n_covers_page_keys(self):
        from module.webui.app_fleet_technology import FILTER_TITLES
        src = ''
        for name in ('module/webui/app_fleet_technology.py', 'module/webui/fleet_tech_view.py'):
            src += (ROOT / name).read_text(encoding='utf-8')
        used = {key for key in re.findall(r'Gui\.FleetTechnology\.[A-Za-z0-9_.]+', src)
                if not key.endswith('.')}
        for value in view.TYPE_ORDER:
            used.add(f'Gui.FleetTechnology.Type.{value}')
        for value in view.ATTR_ORDER:
            used.add(f'Gui.FleetTechnology.Attr.{value}')
        for value in view.RARITY_ORDER:
            used.add(f'Gui.FleetTechnology.Rarity.{value}')
        for value in view.STATUS_ORDER:
            used.add(f'Gui.FleetTechnology.Status.{value}')
        for title in FILTER_TITLES.values():
            used.add(f'Gui.FleetTechnology.{title}')
        for name in ('Edit', 'Bonuses', 'Extra'):
            used.add(f'Gui.FleetTechnology.{name}')
        data = json.loads((ROOT / 'module/config/i18n/zh-CN.json').read_text(encoding='utf-8'))
        have = {f'Gui.FleetTechnology.{key}' for key in data['Gui']['FleetTechnology']}
        missing = sorted(used - have)
        self.assertEqual(missing, [], f'缺少翻译键: {missing}')


if __name__ == '__main__':
    unittest.main()
