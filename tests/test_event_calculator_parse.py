"""Wiki 活动计算器解析测试。

锁定两种版式都能解析：2026-09-18 Wiki 把商品数据从 `ECALCPt` 表格挪进了
`{{#vardefine:_shop_items|...}}`，按表格解析会得到 0 项并报「table is incomplete」。
"""

import unittest

from module.webui.event_calculator import _clean_name, parse_event_calculator

NEW_FORMAT = """\
当前活动：[[碧蓝海事局测试活动专题|测试活动]]
{{#vardefine:_shop_items|
{{道具2|辰辉之饰}},150,500
{{小图标2|测试装备T0}},10000,1
{{道具2|外观装备箱（测试）|ext=png}},2000,10
}}
{|id="ECALCTime" class="wikitable"
!结束日期||剩余天数
|-
||2026/09/30||
|}
{|id="ECALCDaily" class="wikitable"
!日常任务||点数||不做这个||今天已做
|-
|建造3次||300||||
|}
{|id="ECALC" class="wikitable"
!如果只打Ｘ||每次拿Ｙ点||还要打Ｚ次
|-
|A1||30||
|-
|A2||40||
|}
"""

LEGACY_FORMAT = """\
当前活动：[[碧蓝海事局旧版活动专题|旧版活动]]
{|id="ECALCTime" class="wikitable"
!结束日期||剩余天数
|-
||2026/01/15||
|}
{|id="ECALCPt" class="wikitable"
!项目||单价||个数
|-
|{{道具2|旧版道具}}||300||5
|-
|{{小图标|旧版舰船}}||8000||1
|}
{|id="ECALC" class="wikitable"
!如果只打Ｘ||每次拿Ｙ点||还要打Ｚ次
|-
|A1||30||
|}
"""


class TestEventCalculatorParse(unittest.TestCase):
    def test_new_vardefine_format(self):
        data = parse_event_calculator(NEW_FORMAT)

        self.assertEqual(data["event_name"], "测试活动")
        self.assertEqual(data["end_date"], "2026-09-30")
        self.assertEqual(len(data["shop_items"]), 3)
        self.assertEqual(len(data["stages"]), 2)
        self.assertEqual(len(data["daily"]), 1)
        # 150*500 + 10000*1 + 2000*10
        self.assertEqual(data["shop_total"], 105000)

    def test_shop_item_names_have_no_template_params(self):
        names = [i["name"] for i in parse_event_calculator(NEW_FORMAT)["shop_items"]]

        # `|ext=png` 之类后续参数不能被吞进名称里
        self.assertEqual(names, ["辰辉之饰", "测试装备T0", "外观装备箱（测试）"])
        for name in names:
            self.assertNotIn("|", name)
            self.assertNotIn("{", name)

    def test_legacy_table_format_still_parses(self):
        data = parse_event_calculator(LEGACY_FORMAT)

        self.assertEqual(data["event_name"], "旧版活动")
        self.assertEqual(data["end_date"], "2026-01-15")
        self.assertEqual([i["name"] for i in data["shop_items"]], ["旧版道具", "旧版舰船"])
        self.assertEqual(data["stages"][0]["points"], 30)

    def test_clean_name_keeps_quantity_suffix(self):
        # `{{道具2|心智单元}}*100` 里的 *100 在模板之外，属于名称的一部分
        self.assertEqual(_clean_name("{{道具2|心智单元}}*100"), "心智单元*100")
        self.assertEqual(_clean_name("{{道具2|外观装备箱（测试）|ext=png}}"), "外观装备箱（测试）")


if __name__ == "__main__":
    unittest.main()
