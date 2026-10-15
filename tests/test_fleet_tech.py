"""舰队科技目录、扫描累计与人工校正回归；全部使用本地 runtime，不依赖已剪除的 module.api。"""
import copy
import unittest
from unittest.mock import patch

from module.runtime import fleet_tech as tech


def ship(name='杜威', level=125, **kwargs):
    return dict(name=name, level=level, review=False, excluded=False, **kwargs)


class FleetTechRuntimeTests(unittest.TestCase):
    def test_catalog_integrity_and_known_points(self):
        data = tech.public_catalog()
        self.assertGreaterEqual(len(data['ships']), 897)
        self.assertEqual(len(data['ships']), len({row['group_type'] for row in data['ships']}))
        self.assertEqual(len(data['ships']), len({(row['handbook_type'], row['code']) for row in data['ships']}))
        total = sum(sum(row['technology'][key] for key in ('pt_get', 'pt_upgrage', 'pt_level'))
                    for row in data['ships'] if row['technology'])
        self.assertEqual(data['max_points'], total)
        self.assertEqual(sum(data['nation_points'].values()), total)
        dewey = next(row for row in data['ships'] if row['name'] == '杜威')
        self.assertEqual(dewey['code'], 4)
        self.assertEqual(dewey['technology']['pt_get'], 3)
        for levels in data['nations'].values():
            for effects in levels:
                for types, attr, value in effects:
                    self.assertTrue(types)
                    self.assertIsInstance(attr, int)
                    self.assertIsInstance(value, int)

    def test_unknown_is_distinct_from_not_obtained(self):
        row = tech.report({})['ships'][0]
        self.assertIsNone(row['owned'])
        self.assertEqual(row['status'], 'pending')

    def test_partial_scan_retirement_and_125_keep_history(self):
        record = tech.merge_mind({}, [ship(), ship('卡辛', 95)])
        record = tech.merge_mind(record, [ship('杜威', 100)])
        record = tech.merge_mind(record, [])
        self.assertEqual(record['observed']['10102'], 125)
        row = next(row for row in tech.report(record)['ships'] if row['group_type'] == 10102)
        self.assertEqual(row['status'], 'complete')
        self.assertIsNone(row['full_star'])
        self.assertEqual(row['points'], 8)

    def test_uncertain_unknown_and_zero_are_ignored_but_cost_exclusion_is_not(self):
        record = tech.merge_mind({}, [dict(ship(), review=True), ship('未来舰'), ship('卡辛', 0),
                                      dict(ship('唐斯', 95), excluded=True)])
        self.assertEqual(record['observed'], {'10104': 95})

    def test_retrofit_merges_but_ii_and_meta_are_independent(self):
        record = tech.merge_mind({}, [ship('拉菲', 95), ship('拉菲.改', 120), ship('拉菲II'), ship('海伦娜·META')])
        self.assertEqual(len(record['observed']), 3)
        self.assertEqual(record['observed']['10117'], 120)

    def test_manual_override_survives_sync_and_can_restore(self):
        record = {'manual': {'10102': {'owned': False}}}
        original = copy.deepcopy(record)
        merged = tech.merge_mind(record, [ship()])
        row = next(row for row in tech.report(merged)['ships'] if row['group_type'] == 10102)
        self.assertFalse(row['owned'])
        self.assertEqual(record, original)
        merged['manual'].pop('10102')
        self.assertEqual(tech.report(merged)['completed'], 1)

    def test_multiple_target_subtypes_do_not_triple_bonus(self):
        record = tech.merge_mind({}, [ship()])
        result = tech.report(record)
        self.assertEqual(result['bonuses']['1']['1'], 2)
        record['nations'] = {'1': 1}
        faction = tech.report(record)
        self.assertEqual(faction['bonuses']['7']['5'], 1)
        record['nations']['1'] = 2
        self.assertEqual(tech.report(record)['bonuses']['7']['5'], 1)

    def test_new_catalog_ships_start_unknown_without_mutating_records(self):
        record = tech.merge_mind({}, [ship()])
        original = copy.deepcopy(record)
        data = copy.deepcopy(tech.catalog())
        row = dict(data['ships'][0], group_type=99999999)
        data['ships'].append(row)
        with patch.object(tech, 'catalog', return_value=data):
            self.assertEqual(tech.report(record)['ships'][-1]['status'], 'pending')
        self.assertEqual(record, original)


class FleetTechApplyChangesTests(unittest.TestCase):
    """人工校正的一致性规则，对应上游接口 patch 的本地纯函数形态。"""

    GID = 10102  # 杜威

    def row(self, record):
        return next(row for row in tech.report(record)['ships'] if row['group_type'] == self.GID)

    def test_milestone_implies_owned(self):
        record = tech.apply_changes({}, [{'group_type': self.GID, 'full_star': True}])
        row = self.row(record)
        self.assertTrue(row['full_star'])
        self.assertTrue(row['owned'])
        self.assertIsNone(row['lv120'])
        record = tech.apply_changes({}, [{'group_type': self.GID, 'level': 100}])
        row = self.row(record)
        self.assertTrue(row['owned'])
        self.assertFalse(row['lv120'])
        self.assertEqual(row['status'], 'unfinished')

    def test_owned_false_conflicts_with_milestones(self):
        with self.assertRaises(tech.FleetTechChangeError):
            tech.apply_changes({}, [{'group_type': self.GID, 'owned': False, 'level': 100}])
        with self.assertRaises(tech.FleetTechChangeError):
            tech.apply_changes({}, [{'group_type': self.GID, 'owned': False, 'full_star': True}])

    def test_owned_false_clears_fields(self):
        base = tech.apply_changes({}, [{'group_type': self.GID, 'level': 120, 'full_star': True}])
        record = tech.apply_changes(base, [{'group_type': self.GID, 'owned': False}])
        row = self.row(record)
        self.assertIsNone(row['level'])
        self.assertFalse(row['lv120'])
        self.assertFalse(row['full_star'])
        self.assertEqual(row['status'], 'unowned')

    def test_owned_unknown_clears_milestones(self):
        base = tech.apply_changes({}, [{'group_type': self.GID, 'level': 120, 'full_star': True}])
        record = tech.apply_changes(base, [{'group_type': self.GID, 'owned': None}])
        row = self.row(record)
        self.assertIsNone(row['level'])
        self.assertIsNone(row['lv120'])
        self.assertIsNone(row['full_star'])
        self.assertEqual(row['status'], 'pending')

    def test_level_without_lv120_falls_back_to_derivation(self):
        base = tech.apply_changes({}, [{'group_type': self.GID, 'lv120': False}])
        self.assertFalse(self.row(base)['lv120'])
        record = tech.apply_changes(base, [{'group_type': self.GID, 'level': 120}])
        row = self.row(record)
        self.assertTrue(row['lv120'])
        self.assertEqual(row['status'], 'complete')

    def test_restore_removes_manual_and_rejects_mix(self):
        base = tech.apply_changes({}, [{'group_type': self.GID, 'owned': False}])
        restored = tech.apply_changes(base, [{'group_type': self.GID, 'restore': True}])
        self.assertNotIn('10102', restored.get('manual', {}))
        self.assertIsNone(self.row(restored)['owned'])
        with self.assertRaises(tech.FleetTechChangeError):
            tech.apply_changes(base, [{'group_type': self.GID, 'restore': True, 'owned': True}])
        with self.assertRaises(tech.FleetTechChangeError):
            tech.apply_changes(base, [{'group_type': self.GID, 'restore': True, 'owned': None}])

    def test_invalid_inputs_rejected_without_partial_change(self):
        record = {'manual': {'10102': {'owned': True}}}
        original = copy.deepcopy(record)
        for change in (
            {'group_type': 99999999, 'owned': True},
            {'group_type': self.GID, 'level': 0},
            {'group_type': self.GID, 'level': 126},
            {'group_type': self.GID, 'level': True},
            {'group_type': self.GID, 'owned': 'yes'},
            {'group_type': self.GID, 'owned': 1},
        ):
            with self.assertRaises(tech.FleetTechChangeError):
                tech.apply_changes(record, [change])
            self.assertEqual(record, original)
        with self.assertRaises(tech.FleetTechChangeError):
            tech.apply_changes(record, [{'group_type': self.GID, 'owned': True},
                                        {'group_type': self.GID, 'owned': False}])
        self.assertEqual(record, original)

    def test_nations_validation_and_write(self):
        record = tech.apply_changes({}, [], {'1': 2})
        self.assertEqual(record['nations']['1'], 2)
        record = tech.apply_changes(record, [], {'1': None})
        self.assertIsNone(record['nations']['1'])
        for bad in ({'999': 1}, {'1': 999}, {'1': True}):
            with self.assertRaises(tech.FleetTechChangeError):
                tech.apply_changes({}, [], bad)

    def test_apply_does_not_mutate_input_record(self):
        record = tech.merge_mind({}, [ship()])
        original = copy.deepcopy(record)
        tech.apply_changes(record, [{'group_type': self.GID, 'owned': False}], {'1': 1})
        self.assertEqual(record, original)


if __name__ == '__main__':
    unittest.main()
