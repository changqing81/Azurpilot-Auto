"""心智计算器页签的行为回归：渲染冒烟、清单过滤、编辑保存与冲突。

全部回调在无会话替身上执行；真 use_scope 在无会话线程会触发 pywebio 的
script 模式服务器 fallback 并永久阻塞（见 test_bridge_tab 的说明），
因此输出层与 use_scope 一律打桩。
"""
import inspect
import json
import unittest
from unittest.mock import MagicMock, patch

from pywebio import input as pywebio_input
from pywebio import output as pywebio_output
from pywebio import pin as pywebio_pin

from module.runtime import mind_calculator as mind_calc
from module.webui import mind_calculator_view as view
from module.webui.app_mind_calculator_tab import (
    MAX_LEVEL_PATH,
    MIN_LEVEL_PATH,
    MindCalculatorTabMixin,
)

MODULE = 'module.webui.app_mind_calculator_tab'


def _bound_input(name, real):
    """文件上传/动作按钮的桩：先按真签名校验，避免真构造在无会话线程
    触发 pywebio 的 script 模式 fallback（拉起本地服务器并开浏览器）而阻塞。"""

    def wrapper(*args, **kwargs):
        inspect.signature(real).bind(*args, **kwargs)
        return {'name': kwargs.get('name', name)}

    return wrapper


def _ships():
    """覆盖计入/待核对/排除/UR 与同名合并的清单样例（舰名取自真实目录）。"""
    return [
        {'name': '杜威', 'level': 125},
        {'name': '卡辛', 'level': 95},
        {'name': '贝利', 'level': 87, 'review': True},
        {'name': '拉菲', 'level': 100, 'excluded': True},
        {'name': '新泽西', 'level': 120},
    ]


class _FakeOutput:
    def __init__(self, name='out'):
        self.name = name

    def style(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSwitchButton:
    def __init__(self, **kwargs):
        pass

    def g(self):
        return _FakeOutput('switch')


class MindViewTest(unittest.TestCase):
    """mind_calculator_view 纯函数。"""

    def setUp(self):
        self.rows = view.calculate_rows(_ships())[1]

    def test_calculate_rows_keeps_original_index(self):
        result, rows = view.calculate_rows(_ships())
        self.assertEqual([index for index, _ in rows], [0, 1, 2, 3, 4])
        by_index = dict(rows)
        self.assertEqual(by_index[0]['status'], 'included')
        self.assertEqual(by_index[2]['status'], 'review')
        self.assertEqual(by_index[3]['status'], 'excluded')

    def test_filter_rows_by_query_status_and_level(self):
        got = [row['name'] for _, row in view.filter_rows(
            self.rows, '卡', 'all', '', '', '')]
        self.assertEqual(got, ['卡辛'])
        got = [row['name'] for _, row in view.filter_rows(
            self.rows, '', 'review', '', '', '')]
        self.assertEqual(got, ['贝利'])
        got = [row['name'] for _, row in view.filter_rows(
            self.rows, '', 'all', '', '100', '')]
        self.assertEqual(got, ['杜威', '拉菲', '新泽西'])
        got = [row['name'] for _, row in view.filter_rows(
            self.rows, '不存在', 'all', '', '', '')]
        self.assertEqual(got, [])

    def test_page_slice_clamps(self):
        shown, page, pages = view.page_slice(self.rows, 5)
        self.assertEqual((page, pages), (0, 1))

    def test_stats_and_summary_html(self):
        result = mind_calc.calculate(_ships())
        with patch('module.webui.mind_calculator_view.t', side_effect=lambda key, **kw: key):
            html = view.stats_html(result)
            self.assertIn('ft-stat', html)
            summary = view.summary_table_html(result)
        self.assertIn('Gui.MindCalculator.Rarity.UR', summary)
        self.assertIn('Gui.MindCalculator.Rarity.R', summary)
        self.assertIn('Gui.MindCalculator.Stage5', summary)

    def test_ships_table_marks_rows_and_hooks(self):
        result, rows = view.calculate_rows(_ships())
        shown, _, _ = view.page_slice(rows, 0)
        html = view.ships_table_html(shown, result)
        self.assertIn('data-mc-edit="2"', html)
        self.assertIn('data-mc-del="0"', html)
        self.assertIn('ft-mind-review', html)
        self.assertIn('Gui.MindCalculator.Status.Review', html)


class MindTabTest(unittest.TestCase):
    def setUp(self):
        self.harness = _MindHarness()

    def test_render_mind_tab_smoke(self):
        """页签渲染：统计/汇总/清单/过滤/扫描开关全部产出且过签名校验。"""
        record = []
        with patch(f'{MODULE}.use_scope', side_effect=lambda name, **kw: _FakeOutput(name)) as use_scope, \
                patch(f'{MODULE}.put_html', side_effect=lambda html: _FakeOutput(html)) as put_html, \
                patch(f'{MODULE}.put_scope', side_effect=lambda name, **kw: _FakeOutput(name)) as put_scope, \
                patch(f'{MODULE}.put_input', side_effect=lambda *a, **kw: _FakeOutput('input')), \
                patch(f'{MODULE}.put_select', side_effect=lambda *a, **kw: _FakeOutput('select')), \
                patch(f'{MODULE}.put_row', side_effect=lambda *a, **kw: _FakeOutput('row')), \
                patch(f'{MODULE}.put_buttons', side_effect=lambda *a, **kw: _FakeOutput('buttons')), \
                patch(f'{MODULE}.BinarySwitchButton', _FakeSwitchButton), \
                patch(f'{MODULE}.pin_on_change'), \
                patch(f'{MODULE}.load_config') as load_config:
            load_config.return_value.read_file.return_value = {
                'MindCalculatorScan': {'MindCalculator': {
                    'MinLevel': 95, 'MaxLevel': 125,
                    'Result': {'ships': _ships(), 'updated_at': '2026-10-16T00:00:00'}}}}
            self.harness._ft_render_mind_tab()
        names = [call.args[0] for call in put_scope.call_args_list if call.args]
        for required in ('ft_mind_toolbar', 'ft_mind_filters', 'ft_mind_list', 'ft_mind_add',
                         'ft_mind_scan_button'):
            self.assertIn(required, names)
        htmls = [call.args[0] for call in put_html.call_args_list if call.args]
        self.assertTrue(any('ft-stats' in text for text in htmls))
        self.assertTrue(any('ft-mind-intro' in text for text in htmls))
        use_scope.assert_any_call('ft_mind', clear=True)

    def test_render_list_uses_filters_and_pagination(self):
        """过滤与分页：搜索/状态命中与每页 50 切片。"""
        self.harness._mc_state()
        with patch(f'{MODULE}.load_config') as load_config:
            load_config.return_value.read_file.return_value = {
                'MindCalculatorScan': {'MindCalculator': {
                    'Result': {'ships': _ships(), 'updated_at': '2026-10-16T00:00:00'}}}}
            self.harness._mc_load()
        self.harness._mc_query = '杜'
        with patch(f'{MODULE}.use_scope', side_effect=lambda name, **kw: _FakeOutput(name)), \
                patch(f'{MODULE}.put_html', side_effect=lambda html: _FakeOutput(html)) as put_html, \
                patch(f'{MODULE}.put_scope', side_effect=lambda name, **kw: _FakeOutput(name)) as put_scope, \
                patch(f'{MODULE}.put_row', side_effect=lambda *a, **kw: _FakeOutput('row')), \
                patch(f'{MODULE}.put_buttons', side_effect=lambda *a, **kw: _FakeOutput('buttons')):
            self.harness._mc_render_list()
        htmls = [call.args[0] for call in put_html.call_args_list if call.args]
        self.assertTrue(any('杜威' in text for text in htmls))
        self.assertFalse(any('卡辛' in text for text in htmls))

    def test_save_conflict_and_success(self):
        """保存：revision 一致才写盘；不一致提示重载。"""
        harness = _MindHarness()
        harness._mc_state()
        harness._mc_load()
        with patch(f'{MODULE}.config_transaction'), \
                patch(f'{MODULE}.load_config') as load_config, \
                patch(f'{MODULE}.deep_get', return_value={}), \
                patch(f'{MODULE}.deep_set') as deep_set, \
                patch(f'{MODULE}.State') as state:
            load_config.return_value.read_file.return_value = {}
            harness._mc_revision = 'stale'
            error = harness._mc_save(_ships())
            self.assertIn('Gui.MindCalculator.SaveConflict', error)
            deep_set.assert_not_called()
            state.config_updater.write_file.assert_not_called()
            harness._mc_revision = mind_calc.revision([])
            error = harness._mc_save(_ships())
            self.assertIsNone(error)
            deep_set.assert_called_once()
            state.config_updater.write_file.assert_called_once()

    def test_popup_save_rejects_invalid_level(self):
        """计入状态要求等级 1-125，否则错误且不写盘。"""
        harness = _MindHarness()
        harness._mc_state()
        harness._mc_load()
        scope = _FakeOutput('popup')
        values = {'mc_level_0': '0', 'mc_status_0': 'included'}
        with patch(f'{MODULE}.pin', _FakePin(values)), \
                patch(f'{MODULE}.config_transaction'), \
                patch(f'{MODULE}.load_config'), \
                patch(f'{MODULE}.deep_get', return_value={}), \
                patch(f'{MODULE}.deep_set'), \
                patch(f'{MODULE}.State'), \
                patch(f'{MODULE}.use_scope', side_effect=lambda name, **kw: _FakeOutput(name)), \
                patch(f'{MODULE}.put_error') as put_error, \
                patch.object(harness, '_mc_save') as save:
            harness._mc_popup_save(0, scope)
        put_error.assert_called_once()
        save.assert_not_called()


class _FakePin:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get(self, name, default=None):
        return self._values.get(name, default)

    def __getitem__(self, name):
        return self._values.get(name)

    def __setitem__(self, name, value):
        self._values[name] = value


class _MindHarness(MindCalculatorTabMixin):
    """无会话替身：配置读取与任务控制打桩。"""

    def __init__(self):
        self.alas_name = 'alas'
        self.render_log = []

    @property
    def task_handler(self):
        class _Handler:
            @staticmethod
            def add(*args, **kwargs):
                return None

        return _Handler()

    @property
    def alas(self):
        override = getattr(self, '_alas_override', None)
        if override is not None:
            return override

        class _Alas:
            alive = False

            def start(self, task):
                self.started = task

        return _Alas()

    @alas.setter
    def alas(self, value):
        """允许用例注入可断言的替身（默认仍是活的 _Alas 桩）。"""
        self._alas_override = value

    @property
    def alas_config(self):
        class _Config:
            def read_file(self, name):
                return {'MindCalculatorScan': {'MindCalculator': {
                    'MinLevel': 95, 'MaxLevel': 125,
                    'Result': {'ships': _ships(), 'updated_at': '2026-10-16T00:00:00'}}}}

        return _Config()

    def _fleet_scan_running(self):
        return False

    def _start_scan_task(self, task):
        """与 FleetManagementMixin 同一契约的替身：用例必须显式打桩。

        真实现是「worker 存活时先停（后台等待收尾）再以扫描任务启动」的
        两段式；心智页不得绕过它直接 ``alas.start``（worker 存活时那是
        no-op，会静默不启动还弹「已启动」）。
        """
        raise AssertionError('用例必须打桩 _start_scan_task')

    def _ft_write_config(self, changes):
        pass


class MindIoTest(unittest.TestCase):
    """导入解析与导出生成的纯函数。"""

    def test_parse_import_json_and_csv(self):
        ships = view.parse_import('list.json', json.dumps({'ships': [
            {'name': '杜威', 'level': 125}, {'name': '', 'level': 1}]}))
        self.assertEqual([s['name'] for s in ships], ['杜威'])
        self.assertEqual(ships[0]['level'], 125)
        ships = view.parse_import('list.csv', 'name,level,base_rarity\n卡辛,95,R\n')
        self.assertEqual(ships, [{'name': '卡辛', 'level': 95, 'rarity': '', 'base_rarity': 'R',
                                  'excluded': False, 'review': False, 'source': ''}])

    def test_parse_import_rejects_unknown_and_empty(self):
        for name, content in (('list.xlsx', ''), ('list.json', '不是 json'),
                              ('list.json', '[]'), ('list.txt', 'x')):
            with self.assertRaises(ValueError):
                view.parse_import(name, content)

    def test_export_content_json_and_csv(self):
        result = mind_calc.calculate(_ships())
        name, _mime, text = view.export_content(result, 'json')
        self.assertTrue(name.endswith('.json'))
        self.assertIn('杜威', text)
        name, _mime, text = view.export_content(result, 'csv')
        self.assertTrue(name.endswith('.csv'))
        self.assertTrue(text.startswith('\ufeff'))
        self.assertIn('name,level,rarity,base_rarity,status,mind,gold,source', text)
        self.assertIn('杜威', text)


class MindIoCallbackTest(unittest.TestCase):
    """导入/导出回调：上传 → 解析 → 合并保存；取消与非法文件不落盘。"""

    def setUp(self):
        self.harness = _MindHarness()

    def _load(self, harness):
        harness._mc_state()
        with patch(f'{MODULE}.load_config') as load_config:
            load_config.return_value.read_file.return_value = {
                'MindCalculatorScan': {'MindCalculator': {
                    'Result': {'ships': _ships(), 'updated_at': ''}}}}
            harness._mc_load()

    def test_import_list_merges_and_saves(self):
        h = self.harness
        self._load(h)
        upload = {'filename': 'list.json',
                  'content': json.dumps([{'name': '杜威', 'level': 125},
                                         {'name': 'Z23', 'level': 100}]).encode()}
        with patch(f'{MODULE}.input_group', return_value={'action': 'confirm', 'file': upload}), \
                patch(f'{MODULE}.file_upload', _bound_input('file', pywebio_input.file_upload)), \
                patch(f'{MODULE}.actions', _bound_input('action', pywebio_input.actions)), \
                patch(f'{MODULE}.toast'), \
                patch.object(h, '_mc_save', return_value=None) as save, \
                patch.object(h, '_mc_render_list'):
            h._mc_import_list()
        saved = save.call_args[0][0]
        names = [ship['name'] for ship in saved]
        self.assertIn('杜威', names)
        self.assertIn('Z23', names)
        self.assertEqual(len(saved), len(_ships()) + 1)

    def test_import_list_cancel_and_failure(self):
        h = self.harness
        self._load(h)
        with patch(f'{MODULE}.input_group', return_value=None), \
                patch(f'{MODULE}.file_upload', _bound_input('file', pywebio_input.file_upload)), \
                patch(f'{MODULE}.actions', _bound_input('action', pywebio_input.actions)), \
                patch.object(h, '_mc_save') as save:
            h._mc_import_list()
        save.assert_not_called()
        with patch(f'{MODULE}.input_group',
                   return_value={'action': 'confirm',
                                 'file': {'filename': 'x.txt', 'content': b'x'}}), \
                patch(f'{MODULE}.file_upload', _bound_input('file', pywebio_input.file_upload)), \
                patch(f'{MODULE}.actions', _bound_input('action', pywebio_input.actions)), \
                patch(f'{MODULE}.toast') as toast, \
                patch.object(h, '_mc_save') as save:
            h._mc_import_list()
        toast.assert_called_once()
        save.assert_not_called()

    def test_export_downloads(self):
        """导出：pywebio 的 download() 只收 bytes（曾把 str 直接传入 → 点击即 TypeError）。"""
        h = self.harness
        self._load(h)
        with patch(f'{MODULE}.download') as download, patch(f'{MODULE}.toast'):
            h._mc_export('csv')
        download.assert_called_once()
        filename, payload = download.call_args[0]
        self.assertTrue(filename.endswith('.csv'))
        self.assertIsInstance(payload, bytes)
        # CSV 的 UTF-8 BOM 必须随编码保留，Excel 打开才不乱码。
        self.assertTrue(payload.startswith(b'\xef\xbb\xbf'))
        self.assertIn('杜威', payload.decode('utf-8-sig'))
        with patch(f'{MODULE}.download') as download, patch(f'{MODULE}.toast'):
            h._mc_export('json')
        filename, payload = download.call_args[0]
        self.assertTrue(filename.endswith('.json'))
        self.assertIsInstance(payload, bytes)
        self.assertIn('杜威', [row['name'] for row in json.loads(payload)['ships']])

    def test_add_popup_accepts_button_value(self):
        """put_buttons 的回调会带上按钮 value（'add'）：签名必须吃得下。

        现场：2026-10-16 14:32 点击「添加舰船」报
        ``_mc_add_popup() takes 1 positional argument but 2 were given``。
        """
        h = self.harness
        with patch(f'{MODULE}.popup', side_effect=lambda *a, **kw: _FakeOutput('popup')), \
                patch(f'{MODULE}.put_input', side_effect=lambda *a, **kw: _FakeOutput('input')), \
                patch(f'{MODULE}.put_select', side_effect=lambda *a, **kw: _FakeOutput('select')), \
                patch(f'{MODULE}.put_scope', side_effect=lambda name, **kw: _FakeOutput(name)), \
                patch(f'{MODULE}.put_buttons', side_effect=lambda *a, **kw: _FakeOutput('buttons')) as put_buttons:
            h._mc_add_popup('add')  # 真按 put_buttons 的调用约定传值
            h._mc_add_popup()  # 直接调用同样可用（默认参数）
        self.assertEqual(put_buttons.call_count, 2)


    def test_filter_row_labels_level_inputs(self):
        """等级筛选输入框必须带文案。

        现场：2026-10-16 用户截图——搜索行右侧出现两个无标签的空盒子，
        因为 mc_min/mc_max 两个 put_input 连 label/placeholder 都没给。
        """
        h = _MindHarness()
        h._mc_state()
        captured = []
        with patch(f'{MODULE}.t', side_effect=lambda key, **kw: key), \
                patch(f'{MODULE}.use_scope', side_effect=lambda name, **kw: _FakeOutput(name)), \
                patch(f'{MODULE}.put_input',
                      side_effect=lambda *a, **kw: captured.append(kw) or _FakeOutput('input')), \
                patch(f'{MODULE}.put_select', side_effect=lambda *a, **kw: _FakeOutput('select')):
            h._mc_render_filters()
        labels = {item['name']: item.get('label') for item in captured if 'name' in item}
        self.assertEqual(labels.get('mc_min'), 'Gui.MindCalculator.FilterMin')
        self.assertEqual(labels.get('mc_max'), 'Gui.MindCalculator.FilterMax')


    def test_edit_popup_reads_computed_status(self):
        """弹窗初值必须来自计算行：原始记录只有 excluded/review，没有 status。

        现场：2026-10-16 17:44 用户点「编辑」→ KeyError('status')（桥修好后
        第一次真正走到弹窗）。同时校验三态映射：待核对/排除/计入。
        """
        h = _MindHarness()
        h._mc_state()
        h._mc_ships = _ships()  # 贝利 review=True、拉菲 excluded=True
        captured = []
        with patch(f'{MODULE}.popup', side_effect=lambda *a, **kw: _FakeOutput('popup')), \
                patch(f'{MODULE}.put_input', side_effect=lambda *a, **kw: _FakeOutput('input')), \
                patch(f'{MODULE}.put_select',
                      side_effect=lambda *a, **kw: captured.append(kw) or _FakeOutput('select')), \
                patch(f'{MODULE}.put_scope', side_effect=lambda name, **kw: _FakeOutput(name)), \
                patch(f'{MODULE}.put_buttons', side_effect=lambda *a, **kw: _FakeOutput('buttons')):
            h._mc_edit_popup(2)  # 贝利：review
            h._mc_edit_popup(3)  # 拉菲：excluded
            h._mc_edit_popup(0)  # 杜威：included
        self.assertEqual([item['value'] for item in captured], ['review', 'excluded', 'included'])


class MindScanStartTest(unittest.TestCase):
    """扫描按钮：写等级范围 → 走通用两段式启动（worker 存活时先停后启）。"""

    def setUp(self):
        self.h = _MindHarness()
        self.h.alas = MagicMock()
        self.h.alas.alive = False
        self.h.alas._ui_action_running = False

    def test_scan_start_writes_range_and_delegates_to_start_scan_task(self):
        """2026-10-16 修复：原实现直接 alas.start（worker 存活时 no-op 还弹已启动）。"""
        written = []
        with patch(f'{MODULE}.pin', _FakePin({'mc_min_level': '96', 'mc_max_level': '120'})), \
                patch.object(self.h, '_ft_write_config',
                             side_effect=lambda changes: written.extend(changes)), \
                patch.object(self.h, '_start_scan_task', return_value=True) as start_task, \
                patch(f'{MODULE}.toast'):
            self.h._mc_scan_start()
        self.assertEqual(written, [(MIN_LEVEL_PATH, 96), (MAX_LEVEL_PATH, 120)])
        start_task.assert_called_once_with('MindCalculatorScan')
        # 不得绕过两段式直接启动：worker 存活时那会静默失败。
        self.h.alas.start.assert_not_called()

    def test_scan_start_busy_instance_warns_without_starting(self):
        """两段式启动被拒（已有实例操作在进行）时只提示，不二次启动。"""
        with patch(f'{MODULE}.pin', _FakePin({'mc_min_level': '96', 'mc_max_level': '120'})), \
                patch.object(self.h, '_ft_write_config'), \
                patch.object(self.h, '_start_scan_task', return_value=False), \
                patch(f'{MODULE}.toast') as toast:
            self.h._mc_scan_start()
        toast.assert_called_once_with('Gui.FleetManagement.ScanAlreadyRunning', color='warn')
        self.h.alas.start.assert_not_called()

    def test_scan_start_rejects_invalid_range(self):
        """非法范围（倒挂）只提示，不写配置不启动。"""
        with patch(f'{MODULE}.pin', _FakePin({'mc_min_level': '120', 'mc_max_level': '95'})), \
                patch.object(self.h, '_ft_write_config') as write, \
                patch.object(self.h, '_start_scan_task') as start_task, \
                patch(f'{MODULE}.toast') as toast:
            self.h._mc_scan_start()
        write.assert_not_called()
        start_task.assert_not_called()
        toast.assert_called_once_with('Gui.MindCalculator.InvalidRange', color='error')


if __name__ == '__main__':
    unittest.main()