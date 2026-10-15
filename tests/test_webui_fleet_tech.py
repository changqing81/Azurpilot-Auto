"""舰队科技列表页视图纯逻辑回归：筛选、排序、分页、汇总与表格 HTML。"""
import unittest
from unittest.mock import patch

from module.webui import fleet_tech_view as view


def _tech(pt_get, pt_upgrage, pt_level, get_types, get_attr, get_value, level_types, level_attr, level_value):
    return {'pt_get': pt_get, 'pt_upgrage': pt_upgrage, 'pt_level': pt_level, 'max_star': 5,
            'add_get_shiptype': get_types, 'add_get_attr': get_attr, 'add_get_value': get_value,
            'add_level_shiptype': level_types, 'add_level_attr': level_attr, 'add_level_value': level_value}


def _catalog():
    """5 条覆盖：有科技/无科技、前排/后排、稀有度、阵营、联动 id。"""
    return {'updated_at': '2026-10-13', 'source_commit': 'x', 'max_points': 49203,
            'nation_points': {'1': 7089, '2': 5300, '3': 6908, '4': 8668},
            'nations': {},
            'ships': [
                {'group_type': 10102, 'code': 4, 'handbook_type': 0, 'blueprint': False,
                 'name': '杜威', 'names': {'cn': '杜威', 'en': 'Dewey', 'jp': 'デューイ', 'tw': '杜威'},
                 'nationality': 1, 'rarity': 'SSR', 'type': 1,
                 'technology': _tech(3, 7, 5, [1, 20, 21], 1, 1, [1, 20, 21], 1, 1)},
                {'group_type': 10103, 'code': 5, 'handbook_type': 0, 'blueprint': False,
                 'name': '卡辛', 'names': {'cn': '卡辛', 'en': 'Cassin', 'jp': 'カッシン', 'tw': '卡辛'},
                 'nationality': 1, 'rarity': 'R', 'type': 1,
                 'technology': _tech(2, 5, 4, [1], 1, 1, [1], 1, 1)},
                {'group_type': 29901, 'code': 20001, 'handbook_type': 2, 'blueprint': True,
                 'name': '方案舰', 'names': {'cn': '方案舰', 'en': 'Blueprint', 'jp': 'プラン', 'tw': '方案舰'},
                 'nationality': 2, 'rarity': 'PR', 'type': 4,
                 'technology': _tech(15, 30, 23, [4], 4, 1, [4], 4, 2)},
                {'group_type': 10000, 'code': 1, 'handbook_type': 0, 'blueprint': False,
                 'name': '无科技船', 'names': {'cn': '无科技船', 'en': 'Plain', 'jp': 'なし', 'tw': '無科技船'},
                 'nationality': 4, 'rarity': 'N', 'type': 22, 'technology': None},
                {'group_type': 40001, 'code': 500, 'handbook_type': 0, 'blueprint': False,
                 'name': '联动船', 'names': {'cn': '联动船', 'en': 'Collab', 'jp': 'コラボ', 'tw': '聯動船'},
                 'nationality': 105, 'rarity': 'SR', 'type': 7,
                 'technology': _tech(6, 12, 9, [7], 2, 3, [7], 2, 3)},
            ]}


def _report():
    return {'revision': 'r', 'nation_levels': {}, 'bonuses': {}, 'confirmed_points': 10,
            'completed': 1, 'pending_star': 2, 'ships': [
                {'group_type': 10102, 'owned': True, 'full_star': False, 'lv120': True, 'level': 125,
                 'observed_level': 125, 'status': 'complete', 'manual': False, 'points': 15},
                {'group_type': 10103, 'owned': True, 'full_star': None, 'lv120': False, 'level': 95,
                 'observed_level': 95, 'status': 'unfinished', 'manual': True, 'points': 2},
                {'group_type': 29901, 'owned': False, 'full_star': None, 'lv120': None, 'level': None,
                 'observed_level': None, 'status': 'unowned', 'manual': False, 'points': 0},
                {'group_type': 10000, 'owned': None, 'full_star': None, 'lv120': None, 'level': None,
                 'observed_level': None, 'status': 'pending', 'manual': False, 'points': 0},
                {'group_type': 40001, 'owned': None, 'full_star': None, 'lv120': None, 'level': None,
                 'observed_level': None, 'status': 'pending', 'manual': False, 'points': 0},
            ]}


_FAKE = {'Gui.FleetTechnology.Nation.1': '白鹰', 'Gui.FleetTechnology.Nation.2': '皇家',
         'Gui.FleetTechnology.Collaboration': '联动', 'Gui.FleetTechnology.Type.1': '驱逐',
         'Gui.FleetTechnology.Type.4': '战巡', 'Gui.FleetTechnology.Type.7': '航母',
         'Gui.FleetTechnology.Type.22': '风帆', 'Gui.FleetTechnology.Attr.1': '耐久',
         'Gui.FleetTechnology.Attr.2': '炮击', 'Gui.FleetTechnology.Attr.4': '防空',
         'Gui.FleetTechnology.All': '全部', 'Gui.FleetTechnology.Rarity.SSR': '超稀有'}


def fake_t(key, *args, **kwargs):
    return _FAKE.get(key, key)


class FleetTechViewTest(unittest.TestCase):
    def setUp(self):
        self.view = view
        self.catalog, self.report = _catalog(), _report()
        self.rows = view.combine_rows(self.catalog, self.report)
        patcher = patch.object(view, 't', side_effect=fake_t)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _names(self, **kwargs):
        params = dict(filters={}, query='', scope='all', stage='all', sort_key='code', sort_direction=1)
        params.update(kwargs)
        return [row['name'] for row in view.arrange_rows(self.rows, **params)]

    def test_combine_rows_pairs_catalog_with_progress(self):
        self.assertEqual(len(self.rows), 5)
        row = next(row for row in self.rows if row['group_type'] == 10102)
        self.assertEqual(row['status'], 'complete')
        self.assertEqual(row['technology']['pt_get'], 3)

    def test_type_filter_derives_vanguard_and_main(self):
        self.assertEqual(self._names(filters={'type': ['front']}), ['杜威', '卡辛'])
        self.assertEqual(self._names(filters={'type': ['back']}), ['联动船', '方案舰'])
        self.assertEqual(self._names(filters={'type': ['22']}), ['无科技船'])

    def test_rarity_nation_status_filters(self):
        self.assertEqual(self._names(filters={'rarity': ['SSR']}), ['杜威'])
        self.assertEqual(self._names(filters={'nation': ['2']}), ['方案舰'])
        self.assertEqual(self._names(filters={'status': ['complete']}), ['杜威'])
        # 「未完成」= 明确未获得 + 已获得但未满级
        self.assertEqual(self._names(filters={'status': ['incomplete']}), ['卡辛', '方案舰'])
        self.assertEqual(self._names(filters={'status': ['owned']}), ['杜威', '卡辛'])

    def test_scope_and_query(self):
        self.assertEqual(self._names(scope='none'), ['无科技船'])
        self.assertEqual(len(self._names(scope='tech')), 4)
        self.assertEqual(self._names(query='杜威'), ['杜威'])
        self.assertEqual(self._names(query='004'), ['杜威'])
        self.assertEqual(self._names(query='dewey'), ['杜威'])
        self.assertEqual(self._names(query='不存在'), [])

    def test_bonus_type_and_attr_require_technology(self):
        self.assertEqual(self._names(filters={'bonusType': ['1']}), ['杜威', '卡辛'])
        # 无科技船被排除；方案舰的加成目标是战巡(4)
        self.assertEqual(self._names(filters={'bonusType': ['4']}), ['方案舰'])
        self.assertEqual(self._names(filters={'attr': ['1']}), ['杜威', '卡辛'])
        self.assertNotIn('无科技船', self._names(filters={'attr': ['1']}))

    def test_bonus_stage_switches_between_unlock_and_level(self):
        rows = [dict(row) for row in self.rows]
        target = next(row for row in rows if row['group_type'] == 40001)
        target['technology'] = dict(target['technology'], add_get_attr=1, add_level_attr=2)
        # 联动船的解锁加成是耐久(1)、Lv.120 加成是炮击(2)；按阶段筛选要分开生效
        self.assertEqual(
            [row['name'] for row in view.arrange_rows(rows, {'attr': ['2']}, '', 'all', 'level', 'code', 1)],
            ['联动船'])
        self.assertEqual(
            [row['name'] for row in view.arrange_rows(rows, {'attr': ['2']}, '', 'all', 'get', 'code', 1)],
            [])

    def test_multiple_values_within_group_are_or(self):
        self.assertEqual(self._names(filters={'rarity': ['SSR', 'R']}), ['杜威', '卡辛'])

    def test_sort_orders_by_technique_points_with_missing_last_desc(self):
        by_total = self._names(sort_key='total', sort_direction=-1)
        self.assertEqual(by_total[0], '方案舰')
        self.assertEqual(by_total[-1], '无科技船')
        by_code = self._names(sort_key='code', sort_direction=-1)
        self.assertEqual(by_code, ['方案舰', '联动船', '卡辛', '杜威', '无科技船'])

    def test_sort_value_for_missing_technology_is_minus_one(self):
        plain = next(row for row in self.rows if row['group_type'] == 10000)
        self.assertEqual(view.sort_value(plain, 'total'), -1)
        self.assertEqual(view.sort_value(plain, 'get'), '—')

    def test_page_slice_clamps_and_counts_pages(self):
        rows = self.rows * 60  # 300 行
        shown, page, pages = view.page_slice(rows, 0)
        self.assertEqual((len(shown), page, pages), (50, 0, 6))
        shown, page, pages = view.page_slice(rows, 99)
        self.assertEqual((len(shown), page, pages), (300 - 250, 5, 6))
        shown, page, pages = view.page_slice([], 3)
        self.assertEqual((len(shown), page, pages), (0, 0, 1))

    def test_filter_options_first_item_is_all(self):
        options = view.filter_options('type', self.rows)
        self.assertEqual(options[0][0], '')
        self.assertEqual([value for value, _ in options][1:3], ['front', 'back'])
        self.assertIn(('22', '风帆'), options)
        nations = view.filter_options('nation', self.rows)
        self.assertEqual([value for value, _ in nations][1:], ['1', '2', '4', '105'])

    def test_nation_label_falls_back_to_collaboration(self):
        self.assertEqual(view.nation_label(1), '白鹰')
        self.assertEqual(view.nation_label(105), '联动 · 105')

    def test_display_name_follows_language(self):
        row = self.rows[0]
        self.assertEqual(view.display_name(row, 'zh-CN'), '杜威')
        self.assertEqual(view.display_name(row, 'en-US'), 'Dewey')
        self.assertEqual(view.display_name(row, 'ja-JP'), 'デューイ')
        self.assertEqual(view.display_name(row, 'zh-TW'), '杜威')

    def test_effect_text_lists_classes_with_dedup(self):
        row = next(row for row in self.rows if row['group_type'] == 10102)
        # add_get_shiptype [1, 20, 21] 都是驱逐（20/21 归并 1），只出现一次
        self.assertEqual(view.effect_text(row, 'get'), '驱逐 耐久+1')
        plain = next(row for row in self.rows if row['group_type'] == 10000)
        self.assertEqual(view.effect_text(plain, 'get'), '—')

    def test_summary_html_reports_points_and_versions(self):
        html = view.summary_html(self.catalog, self.report, self.rows)
        self.assertIn('49,203', html)
        self.assertIn('白鹰 7,089', html)
        self.assertIn('6,908', html)
        self.assertIn('1 / 4', html)
        self.assertIn('Lua · 2026-10-13', html)

    def test_table_html_has_double_header_and_row_states(self):
        page = self.rows[:5]
        html = view.table_html(page, 'code', 1, edit_mode=False)
        self.assertIn('rowspan="2"', html)
        self.assertIn('colspan="4"', html)
        self.assertIn('colspan="2"', html)
        self.assertIn('data-ft-sort="code"', html)
        self.assertIn('data-ft-sort="level"', html)
        self.assertIn('aria-sort="ascending"', html)
        self.assertIn('<tr class="ft-complete">', html)
        self.assertIn('<tr class="ft-pending">', html)
        self.assertIn('ft-rarity-SSR', html)
        self.assertIn('✎', html)
        self.assertIn('Lv.95', html)
        self.assertNotIn('data-ft-edit', html)

    def test_table_html_edit_mode_marks_rows(self):
        html = view.table_html(self.rows[:5], 'code', 1, edit_mode=True)
        self.assertIn('data-ft-edit="10102"', html)
        self.assertIn('data-ft-edit="10000"', html)

    def test_table_html_missing_technology_shows_dash(self):
        html = view.table_html([row for row in self.rows if row['group_type'] == 10000], 'code', 1, False)
        self.assertIn('—', html)
        self.assertIn('001', html)

    def test_bonus_grid_groups_by_ship_class(self):
        report = dict(self.report, bonuses={'1': {'1': 2, '2': 1}, '4': {'4': 1}})
        html = view.bonus_grid_html(report)
        self.assertIn('耐久+2', html)
        self.assertIn('炮击+1', html)
        self.assertIn('防空+1', html)

    def test_arrange_rows_falls_back_to_code_for_unknown_sort_key(self):
        self.assertEqual(self._names(sort_key='bogus'), ['无科技船', '杜威', '卡辛', '联动船', '方案舰'])


if __name__ == '__main__':
    unittest.main()
