"""心智清单批量编辑回归：目标范围、显式字段、事务冲突与渲染开关。

用例沿用 test_webui_mind_tab 的无会话替身（真 use_scope 在无会话线程会触发
pywebio 的 script 模式服务器 fallback 并永久阻塞），输出层一律打桩。
"""
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from module.webui import app_mind_batch as batch
from module.webui import mind_calculator_view as view

from test_webui_mind_tab import MODULE, _FakeOutput, _FakePin, _MindHarness, _ships

MODULE_V = 'module.webui.mind_calculator_view'
ROOT = Path(__file__).resolve().parents[1]


class _Recorder:
    """把输出层调用记下来，同时返回可当 context manager 用的替身。"""

    def __init__(self):
        self.items = []

    def __call__(self, name):
        def record(*args, **kwargs):
            self.items.append((name, args, kwargs))
            return _FakeOutput(name)

        return record

    def names(self):
        return [name for name, _, _ in self.items]


class MindViewMindFilterTest(unittest.TestCase):
    """满级筛选：只有计入行参与判定，未满级/已满级互补。"""

    def setUp(self):
        self.rows = view.calculate_rows(_ships())[1]

    def _names(self, mind_filter):
        return [row['name'] for _, row in view.filter_rows(
            self.rows, '', 'all', '', '', '', mind_filter)]

    def test_below_keeps_included_rows_that_still_need_mind(self):
        self.assertEqual(self._names('below'), ['卡辛'])

    def test_maxed_keeps_included_rows_at_level_cap(self):
        self.assertEqual(self._names('maxed'), ['杜威', '新泽西'])

    def test_all_and_unknown_filter_keep_everything(self):
        self.assertEqual(len(self._names('all')), 5)
        self.assertEqual(len(self._names('whatever')), 5)

    def test_checkbox_column_only_in_edit_mode(self):
        shown, _, _ = view.page_slice(self.rows, 0)
        with patch(f'{MODULE_V}.t', side_effect=lambda key, **kw: key):
            edit = view.ships_table_html(shown, None, True)
            plain = view.ships_table_html(shown, None, False)
        self.assertIn('data-mc-select="0"', edit)
        self.assertIn('data-mc-select-page', edit)
        self.assertNotIn('data-mc-select', plain)


class MindBatchTest(unittest.TestCase):
    def setUp(self):
        self.h = _MindHarness()
        self.h._mc_state()
        with patch(f'{MODULE}.load_config') as load_config:
            load_config.return_value.read_file.return_value = {
                'MindCalculatorScan': {'MindCalculator': {
                    'Result': {'ships': _ships(), 'updated_at': ''}}}}
            self.h._mc_load()
        self.h._mc_batch = True
        self.rec = _Recorder()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ('popup', 'put_html', 'put_select', 'put_input', 'put_buttons',
                     'close_popup', 'toast'):
            self.stack.enter_context(patch.object(batch, name, self.rec(name)))
        self.pins = _FakePin()
        self.stack.enter_context(patch.object(batch, 'pin', self.pins))
        self.save = self.stack.enter_context(patch.object(self.h, '_mc_save', return_value=None))
        self.after = self.stack.enter_context(patch.object(self.h, '_mc_render_after_change'))

    def open_batch(self, payload):
        self.h._mc_batch_open(payload)
        for name, args, kwargs in reversed(self.rec.items):
            if name == 'put_buttons':
                return kwargs['onclick']
        raise AssertionError('批量弹窗没有产出按钮')

    def test_bridge_reaches_batch_popup(self):
        with patch.object(self.h, '_mc_batch_open') as opened:
            self.h._mc_on_bridge('mbatch|[0,2]')
        opened.assert_called_once_with('[0,2]')

    def test_selected_rows_save_once_and_keep_other_ships(self):
        """只有被勾选的两行改状态，其余舰船原样保留。"""
        callback = self.open_batch('[0,2]')
        self.pins['mc_batch_status'] = 'excluded'
        callback('save')
        self.save.assert_called_once()
        saved = self.save.call_args[0][0]
        self.assertTrue(saved[0]['excluded'])
        self.assertTrue(saved[2]['excluded'])
        self.assertFalse(saved[1].get('excluded'))
        self.assertEqual(len(saved), 5)
        self.after.assert_called_once()

    def test_all_filtered_freezes_targets_against_later_filter_change(self):
        """「全部筛选结果」在打开弹窗时冻结目标，之后改筛选不扩大范围。"""
        callback = self.open_batch('all')
        self.h._mc_query = '卡'
        self.pins['mc_batch_level_mode'] = 'set'
        self.pins['mc_batch_level'] = '120'
        callback('save')
        saved = self.save.call_args[0][0]
        self.assertEqual([ship['level'] for ship in saved], [120] * 5)

    def test_selection_is_limited_to_current_page(self):
        """勾选只认当前页：第 2 页上的 0 号行不算选中。"""
        self.h._mc_ships = [{'name': '杜威', 'level': 120} for _ in range(60)]
        self.h._mc_page = 1
        self.h._mc_batch_open('[0]')
        self.assertEqual(self.rec.items, [])
        callback = self.open_batch('[59]')
        self.pins['mc_batch_status'] = 'excluded'
        callback('save')
        saved = self.save.call_args[0][0]
        self.assertTrue(saved[59]['excluded'])
        self.assertEqual(len(saved), 60)

    def test_rejects_invalid_payloads_and_closed_mode(self):
        for payload in ('[999]', '[true]', '{}', 'null', 'bad', '[]', '[0,"1"]'):
            with self.subTest(payload=payload):
                self.h._mc_batch_open(payload)
                self.assertEqual(self.rec.items, [])
        self.h._mc_batch = False
        self.h._mc_batch_open('all')
        self.assertEqual(self.rec.items, [])

    def test_noop_invalid_level_cancel_and_conflict_never_write(self):
        callback = self.open_batch('[0]')
        callback('save')
        self.save.assert_not_called()
        self.pins['mc_batch_level_mode'] = 'set'
        for value in ('', '0', '126', 'abc'):
            self.pins['mc_batch_level'] = value
            callback('save')
        self.save.assert_not_called()
        callback('cancel')
        self.assertIn('close_popup', self.rec.names())
        self.save.assert_not_called()
        self.after.assert_not_called()

    def test_stale_revision_or_other_instance_rejected(self):
        initial = self.h._mc_revision
        callback = self.open_batch('[0]')
        self.h._mc_revision = 'external-change'
        callback('save')
        self.save.assert_not_called()
        self.h._mc_revision = initial
        self.h.alas_name = 'other'
        callback('save')
        self.save.assert_not_called()

    def test_include_requires_known_level(self):
        self.h._mc_ships = [{'name': '贝利', 'level': 0, 'review': True}]
        callback = self.open_batch('[0]')
        self.pins['mc_batch_status'] = 'included'
        callback('save')
        self.save.assert_not_called()
        self.assertIn('toast', self.rec.names())

    def test_failed_save_keeps_popup_and_draft(self):
        callback = self.open_batch('[0]')
        self.pins['mc_batch_status'] = 'excluded'
        self.save.return_value = '清单已被其他操作更新'
        callback('save')
        self.assertNotIn('close_popup', self.rec.names())
        self.assertEqual(self.pins['mc_batch_status'], 'excluded')
        self.after.assert_not_called()

    def test_presets_only_fill_the_form(self):
        """快捷只填表；「设为120级」填的必须是 120，不是等级上限 125。

        2026-11-05 现场：这里把 MAX_LEVEL(125) 当目标等级用，批量点
        「快捷：设为120级」填出来的是 125。
        """
        self.assertEqual(batch.LEVEL_CAP, 120)
        self.assertNotEqual(batch.LEVEL_CAP, batch.MAX_LEVEL)
        self.open_batch('[0]')
        self.h._mc_batch_preset('level120')
        self.assertEqual(self.pins['mc_batch_level_mode'], 'set')
        self.assertEqual(self.pins['mc_batch_level'], '120')
        self.h._mc_batch_preset('excluded')
        self.assertEqual(self.pins['mc_batch_status'], 'excluded')
        self.assertEqual(self.pins['mc_batch_level_mode'], 'keep')

    def test_level_120_preset_saves_120_not_cap(self):
        """点「快捷：设为120级」再保存，落盘等级必须是 120。"""
        callback = self.open_batch('[0,1]')
        self.h._mc_batch_preset('level120')
        callback('save')
        saved = self.save.call_args[0][0]
        self.assertEqual([ship['level'] for ship in saved[:2]], [120, 120])


class MindBatchRenderTest(unittest.TestCase):
    """批量模式才渲染工具栏与勾选列；关掉后一并消失。"""

    def test_toolbar_only_in_batch_mode(self):
        harness = _MindHarness()
        harness._mc_state()
        with patch(f'{MODULE}.load_config') as load_config:
            load_config.return_value.read_file.return_value = {
                'MindCalculatorScan': {'MindCalculator': {
                    'Result': {'ships': _ships(), 'updated_at': ''}}}}
            harness._mc_load()
        rec = _Recorder()
        outputs = []
        with patch(f'{MODULE}.use_scope', side_effect=lambda name, **kw: _FakeOutput(name)), \
                patch(f'{MODULE}.put_html',
                      side_effect=lambda html: outputs.append(html) or _FakeOutput('html')), \
                patch(f'{MODULE}.put_row', side_effect=rec('put_row')), \
                patch(f'{MODULE}.put_buttons', side_effect=rec('put_buttons')):
            harness._mc_batch = True
            harness._mc_render_list()
            html = ''.join(outputs)
            self.assertIn('data-mc-batch="selected"', html)
            self.assertIn('data-mc-select="0"', html)
            outputs.clear()
            harness._mc_batch = False
            harness._mc_render_list()
            html = ''.join(outputs)
            self.assertNotIn('data-mc-batch', html)
            self.assertNotIn('data-mc-select', html)

    def test_bridge_script_targets_mind_selectors_and_pin(self):
        script = batch.mind_batch_bridge_script('ft_bridge_pin')
        for token in ('ft_bridge_pin', 'ft_mind_list', 'data-mc-select', 'data-mc-batch', 'mbatch|'):
            self.assertIn(token, script)


class MindBatchFieldsTest(unittest.TestCase):
    """显式字段收集与整批应用。"""

    def test_collect_batch_fields(self):
        self.assertEqual(batch.collect_batch_fields({}), {})
        self.assertEqual(batch.collect_batch_fields({'status': 'excluded'}), {'status': 'excluded'})
        self.assertEqual(batch.collect_batch_fields({'level_mode': 'set', 'level': '120'}),
                         {'level': 120})
        for values in ({'status': 'merged'}, {'level_mode': 'set', 'level': ''},
                       {'level_mode': 'set', 'level': '0'}, {'level_mode': 'invalid'}):
            with self.assertRaises(ValueError):
                batch.collect_batch_fields(values)

    def test_apply_batch_touches_only_listed_ships(self):
        ships = [{'name': '杜威', 'level': 120}, {'name': '卡辛', 'level': 95}]
        result = batch.apply_batch(ships, [1], {'level': 120})
        self.assertEqual(result[1]['level'], 120)
        self.assertEqual(result[0], ships[0])
        self.assertEqual(ships[1]['level'], 95)
        with self.assertRaises(ValueError):
            batch.apply_batch([{'name': '贝利', 'level': 0}], [0], {'status': 'included'})

    def test_status_write_is_three_state(self):
        ships = [{'name': '杜威', 'level': 120, 'excluded': True, 'review': True}]
        included = batch.apply_batch(ships, [0], {'status': 'included'})
        self.assertFalse(included[0]['excluded'])
        self.assertFalse(included[0]['review'])
        review = batch.apply_batch(ships, [0], {'status': 'review'})
        self.assertTrue(review[0]['review'])
        self.assertFalse(review[0]['excluded'])


class MindBatchI18nTest(unittest.TestCase):
    def test_all_active_languages_cover_batch_labels(self):
        keys = ('MindBelow', 'MindMaxed', 'BatchEdit', 'BatchExit', 'SelectPage', 'Selected',
                'BatchSelected', 'BatchAll', 'SelectionHint', 'BatchTitle', 'BatchHint', 'Keep',
                'SetLevel', 'LevelPlaceholder', 'PresetIncluded', 'PresetExcluded', 'Preset120',
                'NoChanges', 'BatchLevelRequired')
        for path in (ROOT / 'module/config/i18n').glob('*.json'):
            data = json.loads(path.read_text(encoding='utf-8'))['Gui']['MindCalculator']
            for key in keys:
                self.assertIn(key, data, (path.name, key))
                self.assertNotEqual(data[key], f'Gui.MindCalculator.{key}', (path.name, key))


if __name__ == '__main__':
    unittest.main()
