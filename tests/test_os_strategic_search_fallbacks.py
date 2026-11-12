"""策略搜索入口循环必须能兜底三种「没有搜索开关」的页面。

背景：`StrategicSearchHandler.strategy_search_enter()` 是个状态死循环，
只认策略搜索弹窗、地图事件、作战情报页退出按钮、计划作战开关这四样。
一旦落在下面三种页面上，四者全不命中，循环只截图不操作，
最终被 device.stuck_timer（60s）判为 GameStuckError 并重启游戏：

1. 地球仪页      —— 靠 `GLOBE_GOTO_MAP`（左上角地球图标）
2. 作战总览页    —— 靠 `ORDER_CHECK`（左下角指令按钮）
3. 结算奖励弹窗  —— 靠 `AUTO_SEARCH_REWARD`（中下「合计获得奖励」）

其中第 3 条原来写成 `if self.appear(AUTO_SEARCH_REWARD): continue`，
只检测不点击，等于在弹窗上原地空转（上游 5955125be3 修的正是这条）。

本测试是源码锁：用 AST 检查这三个判定分支还在，且弹窗是「检测并点击」
而不是「只检测」。
"""

import ast
import unittest
from pathlib import Path

STRATEGIC = (
    Path(__file__).resolve().parent.parent
    / 'module' / 'os_handler' / 'strategic.py'
)

# 循环里必须出现检测的素材
REQUIRED_BUTTONS = (
    'GLOBE_GOTO_MAP',
    'ORDER_CHECK',
    'AUTO_SEARCH_REWARD',
)


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _if_button_names(func_node):
    """收集所有 if 判定里出现过的素材名（appear/appear_then_click 的第一个参数）。"""
    found = set()
    for node in ast.walk(func_node):
        if not isinstance(node, ast.If):
            continue
        for sub in ast.walk(node.test):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if sub.args and isinstance(sub.args[0], ast.Name):
                    found.add(sub.args[0].id)
    return found


def _clicked_button_names(func_node):
    """收集用 appear_then_click 处理过的素材名。"""
    clicked = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == 'appear_then_click' and node.args and isinstance(node.args[0], ast.Name):
                clicked.add(node.args[0].id)
    return clicked


class TestStrategySearchEnterFallbacks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = STRATEGIC.read_text(encoding='utf-8-sig')
        cls.tree = ast.parse(cls.source)
        cls.func = _find_function(cls.tree, 'strategy_search_enter')

    def test_function_exists(self):
        self.assertIsNotNone(
            self.func,
            'module/os_handler/strategic.py 中未找到 strategy_search_enter，源码锁失效',
        )

    def test_all_fallback_buttons_are_checked(self):
        """三种「没有搜索开关」的页面都要有对应判定。"""
        checked = _if_button_names(self.func)
        missing = [b for b in REQUIRED_BUTTONS if b not in checked]
        self.assertEqual(
            missing, [],
            'strategy_search_enter 缺少这些页面的兜底判定：'
            + ', '.join(missing)
            + '；落在对应页面上会空转到 60s 抛 GameStuckError',
        )

    def test_reward_popup_is_clicked_not_just_detected(self):
        """结算弹窗必须点掉，不能只检测后 continue。

        2026-11-08 上游修的坑：`if self.appear(AUTO_SEARCH_REWARD): continue`
        看着像处理了，实际每帧都「看见但不点」，60 秒后照样卡死。
        """
        for node in ast.walk(self.func):
            if not isinstance(node, ast.If):
                continue
            hit = False
            for sub in ast.walk(node.test):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == 'appear'
                    and sub.args
                    and isinstance(sub.args[0], ast.Name)
                    and sub.args[0].id == 'AUTO_SEARCH_REWARD'
                ):
                    hit = True
            if hit:
                self.fail('strategy_search_enter 里 AUTO_SEARCH_REWARD 仍用 appear() 只检测不点击，'
                          '弹窗会一直挡着策略搜索入口，应改为 appear_then_click(..., interval=3)')

    def test_globe_and_order_fallback_are_clicked(self):
        """地球仪与作战总览兜底必须是点击动作，不能只是检测。"""
        clicked = _clicked_button_names(self.func)
        for name in ('GLOBE_GOTO_MAP', 'ORDER_CHECK'):
            self.assertIn(
                name, clicked,
                f'{name} 兜底必须是 appear_then_click，否则误入该页面时只会检测不动作',
            )

    def test_import_present(self):
        """GLOBE_GOTO_MAP 来自 module.os.assets，必须显式导入。"""
        self.assertIn(
            'from module.os.assets import GLOBE_GOTO_MAP',
            self.source,
            'strategy_search_enter 用到了 GLOBE_GOTO_MAP，但缺少 from module.os.assets import GLOBE_GOTO_MAP',
        )


if __name__ == '__main__':
    unittest.main()
