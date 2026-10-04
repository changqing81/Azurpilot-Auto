"""大世界收获「合计条点击联动明细表」的回归测试。

覆盖 2026-10-04 用户裁定的联动行为：
- 合计条六个贵重物合计项可点击，点击后明细表只显示对应口径的行，再点一次取消
- 金菜/彩图纸/金机密联动**类别列**：彩图纸给全部图纸（SSR T4 + UR T5）、
  金机密给全部机密报告（T1~T4）——合计数字是头部口径，不必与行求和一致
- 隐蔽/深渊坐标与金猫箱是单一物品家族，按模板名前缀过滤（类别列会把
  隐蔽+深渊、猫箱T2+T3 合并到一起）
- 「其他」项不显示合计数字，点击筛出六项贵重物口径之外的掉落
  （货币/材料/其他类别、猫箱T2、数字编号的未知物品）
- 合计数字永远按全量 items 计算，不受点击筛选影响
- 筛选后无行的空态提示与「其余 N 项」计数都按筛选后的列表算
"""

import unittest
from unittest.mock import patch

from module.statistics.opsi_item_names import item_info
from module.webui.app_stat_opsi_export import (
    MEOW_LOOT_ITEMS,
    MEOW_STRIP_FILTERS,
    OpsiExportMixin,
)


def _items():
    """构造一份覆盖全部类别的明细 items（名字与 ops_item_names.json 对齐）。"""
    return [
        {"name": "PlateGeneralT4", "amount": 20, "count": 2, "avg": 10.0},
        {"name": "PlatePlaneT4", "amount": 40, "count": 4, "avg": 10.0},
        {"name": "GearDesignPlanGunT4", "amount": 2, "count": 1, "avg": 2.0},
        {"name": "GearDesignPlanGunT5", "amount": 1, "count": 1, "avg": 1.0},
        {"name": "OrdnanceTestingReportT2", "amount": 5, "count": 2, "avg": 2.5},
        {"name": "OrdnanceTestingReportT4", "amount": 2, "count": 1, "avg": 2.0},
        {"name": "CoordinateObscure", "amount": 6, "count": 6, "avg": 1.0},
        {"name": "CoordinateAbyssal", "amount": 1, "count": 1, "avg": 1.0},
        {"name": "CatT2", "amount": 1, "count": 1, "avg": 1.0},
        {"name": "CatT3", "amount": 1, "count": 1, "avg": 1.0},
        {"name": "OperationCoin", "amount": 170221, "count": 29, "avg": 5869.7},
        {"name": "SpecialGearPrototype", "amount": 530, "count": 32, "avg": 16.6},
        {"name": "44", "amount": 156, "count": 7, "avg": 22.3},
    ]


class _FakeChip:
    """替身 put_html Output：记录内容与 onclick 回调，供断言与触发。"""

    def __init__(self, html):
        self.html = html
        self.click_callback = None

    def onclick(self, callback):
        self.click_callback = callback
        return self


class _OpsiDropHarness(OpsiExportMixin):
    """只带大世界收获渲染所需状态的测试替身。"""

    def __init__(self, items=None, item_filter=None):
        self.alas_name = "alas"
        self._meow_item_filter = item_filter
        self.drop = {"items": _items() if items is None else items}
        self.render_calls = []

    def _render_meowofficer_farming(self, **kwargs):
        self.render_calls.append(kwargs)


class OpsiDropFilterTest(unittest.TestCase):
    def setUp(self):
        self.harness = _OpsiDropHarness()

    def _filtered_names(self, key):
        self.harness._meow_item_filter = key
        return [
            item["name"]
            for item in self.harness._opsi_drop_filtered_items(self.harness.drop)
        ]

    def test_filter_map_covers_strip_items(self):
        """筛选映射表必须覆盖合计条六项 + 「其他」，防止键名手误。"""
        self.assertEqual(
            set(MEOW_STRIP_FILTERS),
            {key for key, *_ in MEOW_LOOT_ITEMS} | {"Other"},
        )

    def test_other_filter_is_complement_of_six(self):
        """「其他」= 六项贵重物口径之外的掉落：货币/材料/其他/猫箱T2/数字未知物品。

        贵重六项已覆盖的（含类别口径下的全部图纸/机密、两个坐标、金猫箱）不出现。
        """
        names = self._filtered_names("Other")
        self.assertEqual(
            names,
            [
                "CatT2",
                "OperationCoin",
                "SpecialGearPrototype",
                "44",
            ],
        )

    def test_no_filter_returns_all(self):
        self.assertEqual(len(self.harness._opsi_drop_filtered_items(
            self.harness.drop)), len(_items()))

    def test_plate_filter_uses_category(self):
        names = self._filtered_names("Plate")
        self.assertEqual(names, ["PlateGeneralT4", "PlatePlaneT4"])

    def test_design_filter_covers_all_tiers(self):
        """彩图纸联动的是图纸类别：SSR T4 与 UR T5 都要显示。"""
        names = self._filtered_names("GearDesignPlanT5")
        self.assertEqual(names, ["GearDesignPlanGunT4", "GearDesignPlanGunT5"])

    def test_report_filter_covers_all_tiers(self):
        """金机密联动的是机密类别：T1~T4 全部军械测试报告都要显示。"""
        names = self._filtered_names("OrdnanceTestingReportT4")
        self.assertEqual(
            names, ["OrdnanceTestingReportT2", "OrdnanceTestingReportT4"]
        )

    def test_coordinate_filter_by_name(self):
        """隐蔽/深渊坐标是两个合计项，不能按「坐标」类别混在一起。"""
        self.assertEqual(self._filtered_names("CoordinateObscure"),
                         ["CoordinateObscure"])
        self.assertEqual(self._filtered_names("CoordinateAbyssal"),
                         ["CoordinateAbyssal"])

    def test_cat_filter_by_name(self):
        """金猫箱只指猫箱T3，不能把猫箱T2 也带出来。"""
        self.assertEqual(self._filtered_names("CatT3"), ["CatT3"])

    def test_table_html_only_shows_filtered_rows(self):
        # 明细表显示的是名称表里的中文物品名，不是模板名
        self.harness._meow_item_filter = "Plate"
        html = self.harness._build_opsi_drop_table_html(self.harness.drop)
        self.assertIn("通用部件T4", html)
        self.assertIn("舰载机部件T4", html)
        self.assertNotIn("作战补给凭证", html)

    def test_table_empty_notice_when_filter_matches_nothing(self):
        self.harness._meow_item_filter = "CatT3"
        self.harness.drop["items"] = [{"name": "OperationCoin", "amount": 1,
                                       "count": 1, "avg": 1.0}]
        with patch("module.webui.app_stat_opsi_export.t",
                   side_effect=lambda key, **kwargs: key):
            html = self.harness._build_opsi_drop_table_html(self.harness.drop)
        self.assertIn("Gui.Stat.OpsiDropFilterEmptyNotice", html)
        self.assertNotIn("Gui.Stat.OpsiDropEmptyNotice", html)

    def test_hidden_rows_count_filtered_list(self):
        """「其余 N 项」按筛选后的行数算：筛剩 2 行时不再出现折叠。"""
        self.harness._meow_item_filter = "Plate"
        self.assertEqual(self.harness._opsi_drop_hidden_rows(self.harness.drop), 0)
        self.harness._meow_item_filter = None
        self.assertEqual(
            self.harness._opsi_drop_hidden_rows(self.harness.drop),
            len(_items()) - 5,
        )

    def test_numeric_item_shows_as_unknown(self):
        """数字编号的脏数据物品显示成「未知物品 #编号」，归其他类别。"""
        info = item_info("44")
        self.assertEqual(info["zh"], "未知物品 #44")
        self.assertEqual(info["category"], "other")
        # 已翻译的正常物品不受影响
        self.assertEqual(item_info("SpecialGearPrototype")["zh"], "特殊装备原型")

    def test_strip_click_toggles_filter(self):
        """点击合计项设置筛选，再点同一个取消；期间整块重绘一次。"""
        self.harness._on_meow_strip_click("Plate")
        self.assertEqual(self.harness._meow_item_filter, "Plate")
        self.assertEqual(len(self.harness.render_calls), 1)
        self.harness._on_meow_strip_click("Plate")
        self.assertIsNone(self.harness._meow_item_filter)
        self.harness._on_meow_strip_click("CatT3")
        self.assertEqual(self.harness._meow_item_filter, "CatT3")

    def test_strip_chips_clickable_and_active_marked(self):
        """合计条渲染出七个带点击回调的合计项，选中项有主题色描边标记。"""
        harness = _OpsiDropHarness(item_filter="Plate")
        with patch("module.webui.app_stat_opsi_export.put_html",
                   side_effect=lambda html: _FakeChip(html)), \
             patch("module.webui.app_stat_opsi_export.put_widget") as put_widget, \
             patch("module.webui.app_stat_opsi_export.t",
                   side_effect=lambda key, **kwargs: key):
            harness._render_meow_loot_strip(
                {"year": 2026, "month": 10, "is_current": True}, harness.drop
            )
        template, data = put_widget.call_args[0]
        chips = data["chips"]
        self.assertEqual(len(chips), 7)
        for chip in chips:
            self.assertIsNotNone(chip.click_callback)
        # 选中项（金菜）内联主题色描边 + 提示换成「再点一次取消」；其余项是
        # 「点击筛选」提示且无内联标记
        plate_chip = next(
            c for c in chips if "Gui.Stat.MeowLootItemPlate" in c.html
        )
        self.assertIn("box-shadow", plate_chip.html)
        self.assertIn("Gui.Stat.MeowLootStripActiveHint", plate_chip.html)
        other_chip = next(c for c in chips
                          if "Gui.Stat.MeowLootItemCat" in c.html)
        self.assertNotIn("box-shadow", other_chip.html)
        self.assertIn("Gui.Stat.MeowLootStripClickHint", other_chip.html)
        # chip 上的回调就是切换筛选本身：点「金猫箱」应切到该筛选
        cat_chip = next(c for c in chips
                        if "Gui.Stat.MeowLootItemCat" in c.html)
        cat_chip.click_callback()
        self.assertEqual(harness._meow_item_filter, "CatT3")
        # 合计数字按全量算：金菜 20+40，不因筛选只剩自己而变化
        plate_chip_html = next(
            c.html for c in chips if "Gui.Stat.MeowLootItemPlate" in c.html
        )
        self.assertIn("<b>60</b>", plate_chip_html)
        # 「其他」chip 显示类别名而不是合计数字
        other_chip = next(
            c for c in chips if "Gui.Stat.OpsiDropCategoryOther" in c.html
        )
        self.assertIn("<b>Gui.Stat.OpsiDropCategoryOther</b>", other_chip.html)
        # 点「其他」chip 切到补集筛选
        other_chip.click_callback()
        self.assertEqual(harness._meow_item_filter, "Other")

    def test_strip_renders_via_widget_template(self):
        """合计条走 put_widget 模板渲染（chips 必须是独立 Output 才能挂回调）。"""
        with patch("module.webui.app_stat_opsi_export.put_html",
                   side_effect=lambda html: _FakeChip(html)), \
             patch("module.webui.app_stat_opsi_export.put_widget") as put_widget, \
             patch("module.webui.app_stat_opsi_export.t",
                   side_effect=lambda key, **kwargs: key):
            self.harness._render_meow_loot_strip(
                {"year": 2026, "month": 9, "is_current": False}, self.harness.drop
            )
        template, data = put_widget.call_args[0]
        self.assertIn("meow-loot-strip", template)
        self.assertIn("pywebio_output_parse", template)
        self.assertEqual(data["title"], "Gui.Stat.MeowLootTitleHistory")
        self.assertEqual(len(data["chips"]), 7)


if __name__ == "__main__":
    unittest.main()
