"""批量编辑回归：目标范围、显式字段、事务冲突与真实输出签名。"""
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from module.runtime import fleet_tech as tech
from module.webui import app_fleet_tech_batch as batch
from module.webui import fleet_tech_view as view
from module.webui.app_fleet_technology import FleetTechnologyMixin
from test_webui_fleet_tech_page import (
    MODULE, ROOT, _CHECKED_API, _FakePin, _Harness, _checked, _row,
)


class FleetTechBatchTest(unittest.TestCase):
    def setUp(self):
        self.h = _Harness()
        self.h._ft_edit = True
        self.h._ft_revision = 'initial'
        self.outputs = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, real in _CHECKED_API.items():
            if hasattr(batch, name):
                self.stack.enter_context(patch.object(batch, name, _checked(name, real, self.outputs)))
        self.pins = _FakePin()
        self.stack.enter_context(patch.object(batch, 'pin', self.pins))
        self.save = self.stack.enter_context(patch.object(self.h, '_ft_save_changes', autospec=True,
                                                        return_value=None))
        self.after = self.stack.enter_context(patch.object(self.h, '_ft_after_save', autospec=True))

    def open_and_callback(self, payload):
        self.h._ft_batch_open(payload)
        return next(kwargs['onclick'] for name, args, kwargs in reversed(self.outputs)
                    if name == 'put_buttons')

    def test_bridge_reaches_batch_popup(self):
        with patch.object(self.h, '_ft_batch_open', autospec=True) as opened:
            self.h._ft_on_bridge('batch|[10102]')
        opened.assert_called_once_with('[10102]')

    def test_real_table_renderer_mounts_batch_toolbar_only_in_edit_mode(self):
        from module.webui import app_fleet_technology as page
        output = []
        with ExitStack() as stack:
            for name, real in _CHECKED_API.items():
                stack.enter_context(patch.object(page, name, _checked(name, real, output)))
            FleetTechnologyMixin._ft_render_table(self.h)
            html = ''.join(args[0] for name, args, _ in output if name == 'put_html')
            self.assertIn('data-ft-batch="selected"', html)
            self.assertIn('data-ft-select="10102"', html)
            output.clear()
            self.h._ft_edit = False
            FleetTechnologyMixin._ft_render_table(self.h)
            html = ''.join(args[0] for name, args, _ in output if name == 'put_html')
            self.assertNotIn('data-ft-batch', html)
            self.assertNotIn('data-ft-select', html)

    def test_selected_saves_once_and_keeps_unspecified_fields(self):
        callback = self.open_and_callback('[10102,10103,10102]')
        self.h._ft_batch_preset('lv120')
        callback('save')
        self.save.assert_called_once_with([
            {'group_type': 10102, 'owned': True, 'lv120': True},
            {'group_type': 10103, 'owned': True, 'lv120': True},
        ], expected_revision='initial')
        self.after.assert_called_once()

    def test_all_filtered_crosses_page_boundary_and_freezes_targets(self):
        self.h._ft_rows = [_row(10000 + i, name='目标' if i < 55 else '其他') for i in range(60)]
        self.h._ft_query = '目标'
        callback = self.open_and_callback('all')
        self.h._ft_rows = []
        self.h._ft_revision = 'external-change'
        callback('restore')
        changes = self.save.call_args.args[0]
        self.assertEqual([c['group_type'] for c in changes], list(range(10000, 10055)))
        self.assertTrue(all(c['restore'] for c in changes))
        self.assertEqual(self.save.call_args.kwargs, {'expected_revision': 'initial'})

    def test_rejects_non_page_ids_and_invalid_payloads(self):
        for payload in ('[99999]', '[true]', '{}', 'null', 'bad', '[]', '[10102,"10103"]'):
            with self.subTest(payload=payload):
                self.h._ft_batch_open(payload)
                self.assertEqual(self.outputs, [])
        self.h._ft_edit = False
        self.h._ft_batch_open('all')
        self.assertEqual(self.outputs, [])

    def test_selection_is_limited_to_current_page(self):
        self.h._ft_rows = [_row(10000 + i) for i in range(60)]
        self.h._ft_page = 1
        self.h._ft_batch_open('[10000]')
        self.assertEqual(self.outputs, [])
        callback = self.open_and_callback('[10059]')
        callback('restore')
        self.save.assert_called_once_with([{'group_type': 10059, 'restore': True}],
                                          expected_revision='initial')

    def test_noop_invalid_level_and_cancel_never_write(self):
        callback = self.open_and_callback('[10102]')
        callback('save')
        self.save.assert_not_called()
        self.pins['ft_batch_level_mode'] = 'set'
        for value in ('', '0', '126', '1.5', 'abc'):
            self.pins['ft_batch_level'] = value
            callback('save')
            self.save.assert_not_called()
        callback('cancel')
        self.save.assert_not_called()
        self.after.assert_not_called()

    def test_changed_instance_rejected(self):
        callback = self.open_and_callback('[10102]')
        self.h.alas_name = 'other'
        callback('restore')
        self.save.assert_not_called()
        self.after.assert_not_called()

    def test_failed_save_keeps_popup_and_draft(self):
        callback = self.open_and_callback('[10102]')
        self.h._ft_batch_preset('owned')
        self.save.return_value = '记录冲突'
        self.outputs.clear()
        callback('save')
        self.assertNotIn('close_popup', [name for name, _, _ in self.outputs])
        self.assertEqual(self.pins['ft_batch_owned'], 'true')
        self.after.assert_not_called()

    def test_reopening_discards_draft(self):
        self.open_and_callback('[10102]')
        self.h._ft_batch_preset('lv120')
        self.open_and_callback('[10103]')
        self.assertEqual(self.pins['ft_batch_lv120'], 'keep')

    def test_explicit_unknown_and_level_modes(self):
        self.assertEqual(batch.collect_batch_fields({}), {})
        self.assertEqual(batch.collect_batch_fields({'full_star': 'unknown', 'level_mode': 'unknown'}),
                         {'full_star': None, 'level': None})
        self.assertEqual(batch.collect_batch_fields({'level_mode': 'set', 'level': '125'}), {'level': 125})
        for fields in ({'owned': 'unexpected'}, {'level_mode': 'invalid'}):
            with self.assertRaises(ValueError):
                batch.collect_batch_fields(fields)

    def test_backend_applies_batch_without_losing_other_ship_records(self):
        original = {'observed': {'10102': 125, '10103': 95},
                    'manual': {'10103': {'full_star': False}, '10104': {'owned': True}}}
        result = tech.apply_changes(original, [{'group_type': gid, 'lv120': True} for gid in (10102, 10103)])
        self.assertEqual(tech.progress(result, 10102)['level'], 125)
        self.assertEqual(tech.progress(result, 10103)['level'], 120)
        self.assertFalse(tech.progress(result, 10103)['full_star'])
        self.assertEqual(result['manual']['10104'], original['manual']['10104'])
        self.assertNotIn('10102', original['manual'])
        with self.assertRaises(tech.FleetTechChangeError):
            tech.apply_changes(original, [{'group_type': 10102, 'owned': True},
                                          {'group_type': 10103, 'owned': False, 'lv120': True}])
        self.assertNotIn('10102', original['manual'])

    def test_expected_revision_survives_background_reload(self):
        self.h._ft_revision = 'external-change'
        with patch(f'{MODULE}.config_transaction'), patch(f'{MODULE}.load_config') as load, \
                patch(f'{MODULE}.tech.revision', return_value='external-change'), \
                patch(f'{MODULE}.State') as state:
            load.return_value.read_file.return_value = {}
            error = FleetTechnologyMixin._ft_save_changes(
                self.h, [{'group_type': 10102, 'owned': True}], expected_revision='initial')
        self.assertTrue(error)
        state.config_updater.write_file.assert_not_called()

    def test_table_selection_only_in_edit_mode_and_escaped(self):
        row = _row(name='舰船"<标签>')
        edit = view.table_html([row], 'code', 1, True)
        self.assertIn('data-ft-select="10102"', edit)
        self.assertIn('data-ft-select-page', edit)
        self.assertIn('&quot;&lt;标签&gt;', edit)
        self.assertNotIn('data-ft-select', view.table_html([row], 'code', 1, False))

    def test_all_active_languages_cover_batch_labels(self):
        import re
        source = (ROOT / 'module/webui/app_fleet_tech_batch.py').read_text(encoding='utf-8-sig')
        keys = set(re.findall(r'Gui\.FleetTechnology\.([A-Za-z0-9]+)', source)) | {'SelectPage'}
        for path in (ROOT / 'module/config/i18n').glob('*.json'):
            data = json.loads(path.read_text(encoding='utf-8'))['Gui']['FleetTechnology']
            for key in keys:
                self.assertIn(key, data, (path.name, key))
                self.assertNotEqual(data[key], f'Gui.FleetTechnology.{key}')


if __name__ == '__main__':
    unittest.main()
