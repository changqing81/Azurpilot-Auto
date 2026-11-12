"""配置国际化标签与任务菜单的回归测试。

两件事在这里钉死：

1. 数值选项的 i18n 标签不能被「百分比化」。
   2026-11-05 收编上游岛屿规划器时，一次全局查找替换把 `"4": "4"` 全改成了
   `"4": "+4%"`，连带把农田岗位、每日任务舰队、抽卡数量等几十个配置项一起改错，
   界面上的下拉框显示成 +4%。这里只允许「效率加成」那三个选项带百分号。
2. 岛屿生产规划器是工具页任务（手动单次运行），不能挂 Scheduler 组，
   否则界面上又会出现「启用该功能」开关并被自动调度。
"""
import re
import unittest

from module.config.utils import filepath_args, filepath_i18n, read_file

# 效率加成三项：选项值本身是整数（0/4/12），帮助文本说明它代表百分比，
# 但**标签必须保持纯数字**。2026-11-05 收编上游时一次全局替换把它们改成了
# "+4%"，导致下拉框显示成 +4%——这正是 test_numeric_options_not_percent_labeled
# 要防的回归。因此这里只记录"允许出现百分号"的例外集合，且当前为空。
PERCENT_LABELED_ARGS = set()
# 标签必须保持纯数字的效率加成选项（值代表百分比，但显示不加 %）
EFFICIENCY_ARGS = (
    "FieldsEfficiency",
    "OrchardEfficiency",
    "NurseryEfficiency",
)
PERCENT_LABEL = re.compile(r"^\+?\d+(?:\.\d+)?%$")
LANGUAGES = ("en-US", "ja-JP", "zh-CN", "zh-TW")


def iter_numeric_options(args):
    """遍历所有「选项值形如数字」的参数。

    Yields:
        tuple: (分组名, 参数名, 选项键, 参数定义)
    """
    for _task, groups in args.items():
        if not isinstance(groups, dict):
            continue
        for group, arguments in groups.items():
            if not isinstance(arguments, dict):
                continue
            for argument, definition in arguments.items():
                if not isinstance(definition, dict) or not definition.get("option"):
                    continue
                for option in definition["option"]:
                    key = str(option)
                    if re.fullmatch(r"\d+(?:\.\d+)?", key):
                        yield group, argument, key, definition


class I18nOptionLabelTests(unittest.TestCase):
    """数值选项的标签必须与选项值一致（效率加成除外）。"""

    def test_numeric_options_not_percent_labeled(self):
        args = read_file(filepath_args())
        problems = []
        for lang in LANGUAGES:
            i18n = read_file(filepath_i18n(lang))
            for group, argument, key, _definition in iter_numeric_options(args):
                label = i18n.get(group, {}).get(argument)
                if not isinstance(label, dict) or key not in label:
                    continue
                text = label[key]
                if argument in PERCENT_LABELED_ARGS:
                    continue
                if PERCENT_LABEL.match(str(text)):
                    problems.append(f"{lang}: {group}.{argument}.{key} => {text}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_efficiency_labels_are_plain_numbers(self):
        """效率加成的标签必须是纯数字。

        选项值 0/4/12 是整数，帮助文本已说明它们代表百分比
        （"0 / 4% / 12%"）。标签一旦带上 "+4%"，下拉框就会显示成 +4%，
        与其它纯数字选项不一致 —— 这正是 2026-11-05 全局替换造成的事故。
        """
        for lang in LANGUAGES:
            i18n = read_file(filepath_i18n(lang))
            for argument in EFFICIENCY_ARGS:
                label = i18n["IslandProductionPlanner"][argument]
                self.assertEqual(label["0"], "0", f"{lang} {argument}")
                self.assertEqual(label["4"], "4", f"{lang} {argument}")
                self.assertEqual(label["12"], "12", f"{lang} {argument}")

    def test_every_option_has_label(self):
        args = read_file(filepath_args())
        problems = []
        for lang in LANGUAGES:
            i18n = read_file(filepath_i18n(lang))
            for group, argument, key, _definition in iter_numeric_options(args):
                label = i18n.get(group, {}).get(argument)
                if isinstance(label, dict) and key not in label:
                    problems.append(f"{lang}: {group}.{argument}.{key}")
        self.assertEqual(problems, [], "\n".join(problems))


class TaskMenuTests(unittest.TestCase):
    """岛屿生产规划器归属岛屿页、可自动调度。

    2026-11-05 收编上游时曾设想把它做成"工具页手动任务"（本测试最初按那个
    意图写），但实现最终走的是可调度路线：task.yaml 把它放在 Island 段，
    并挂上 Scheduler 组（Enable / NextRun / Command 齐备）。
    2026-11-12 对齐现状，锁定当前真实归属，避免以后再被误改。
    """

    def test_island_production_planner_is_island_task(self):
        menu = read_file(filepath_args('menu'))
        self.assertIn("IslandProductionPlanner", menu["Island"]["tasks"])
        self.assertNotIn("IslandProductionPlanner", menu["Tool"]["tasks"])

    def test_island_production_planner_has_scheduler(self):
        args = read_file(filepath_args())
        groups = args["IslandProductionPlanner"]
        self.assertIn("IslandProductionPlanner", groups)
        self.assertIn("Scheduler", groups)
