"""中断自律寻敌路径必须处理「知道了」说明弹窗的源码锁测试。

背景（2026-11-11 用户日志 log/error/alas/1794330671637）：
大世界自律寻敌进行中，Tactical 任务到期触发任务切换，脚本走
`OSMap.interrupt_auto_search()` 中断流程：点暂停 → 点退出战斗。
游戏在退出战斗时弹出「信息 INFORMATION / 注意，自律战斗时可能出现以下情况 /
知道了」说明弹窗，而这条循环里没有任何分支认识它：

    01:07:40.843  [设备-控制] 点击 (435, 512) @ QUIT_Iridescent_Fantasy
    ...          （195.2 秒内零点击，784 条「处理经验信息」空转日志）
    01:10:56.063  GameStuckError: [设备-卡死] 等待时间过长

195.2s 精确命中 device.py 的 `stuck_timer_long = Timer(195, count=195)`。
之所以不走 60 秒的短阈值，是因为 `stuck_long_wait_list` 含 'PAUSE'，而
`is_combat_executing()` 在模板匹配之前就 `stuck_record_add(PAUSE)`，
PAUSE 一直躺在 detect_record 里，60 秒阈值被永久豁免。

弹窗本身早就有素材和处理方法（`AUTOMATION_CONFIRM` 的模板图就是「知道了」
三个字，实测事故截图相似度 0.9685，其余截图仅 0.07~0.14；配套的颜色门禁
`AUTOMATION_CONFIRM_CHECK` 在 13 张报错截图中只在事故那张命中）。
14 个调用点里唯独 `module/os/map.py` 一次都没有——补上即可。
"""

import ast
import unittest
from pathlib import Path

OS_MAP = Path(__file__).resolve().parent.parent / 'module' / 'os' / 'map.py'

# 中断循环里必须出现的弹窗处理器
REQUIRED_HANDLERS = (
    'handle_combat_automation_confirm',
    'handle_popup_single_white',
    'handle_game_tips',
)


def _find_function(tree: ast.Module, name: str):
    """在模块顶层/类体内查找指定名字的函数定义。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _called_names(func_node: ast.AST):
    """收集函数体内所有被调用的方法名（形如 self.xxx()）。"""
    names = set()
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


class TestInterruptAutoSearchPopupHandling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 个别模块带 BOM，utf-8-sig 兜住
        source = OS_MAP.read_text(encoding='utf-8-sig')
        cls.tree = ast.parse(source)
        cls.func = _find_function(cls.tree, 'interrupt_auto_search')

    def test_function_exists(self):
        self.assertIsNotNone(
            self.func,
            'module/os/map.py 中未找到 interrupt_auto_search，源码锁失效',
        )

    def test_required_handlers_are_called(self):
        """中断循环必须能处理「知道了」说明弹窗及同类单按钮提示。"""
        called = _called_names(self.func)
        missing = [h for h in REQUIRED_HANDLERS if h not in called]
        self.assertEqual(
            missing, [],
            'interrupt_auto_search 缺少弹窗处理器，游戏中途退出战斗时弹出的'
            '「信息 INFORMATION / 知道了」弹窗会让循环空转到 195s 抛 GameStuckError：'
            + ', '.join(missing),
        )

    def test_no_unguarded_every_frame_log(self):
        """「处理经验信息」日志不能每帧都打。

        2026-11-11 事故里 195 秒刷了 784 条同质日志，把真正的现场信息淹没。
        修复方式是用 info_logged 标志位只记录一次，这里锁住该标志被读取。
        """
        names = {
            node.id
            for node in ast.walk(self.func)
            if isinstance(node, ast.Name)
        }
        self.assertIn(
            'info_logged', names,
            '「处理经验信息」日志缺少 info_logged 节流标志，会在卡死期间刷屏',
        )


# 2026-11-12 补漏：同类「点退出战斗后弹说明弹窗」的循环不止 interrupt_auto_search 一处。
# 这些都是「独立循环 + 内部会点退出/暂停」的组合，没有外层战斗循环兜底，
# 同一个弹窗出现时同样会空转到 GameStuckError。以下逐处上锁。
LOOP_TARGETS = (
    # (文件, 函数, 额外必须调用的处理器)
    ('module/os/map.py', 'interrupt_auto_search',
     ('handle_combat_automation_confirm', 'handle_popup_single_white', 'handle_game_tips')),
    ('module/os/fleet.py', 'boss_leave',
     ('handle_combat_automation_confirm', 'handle_popup_single_white')),
    ('module/combat/auto_search_combat.py', 'auto_search_combat_status',
     ('handle_combat_automation_confirm',)),
    ('module/combat/combat.py', 'combat_status',
     ('handle_combat_automation_confirm',)),
    ('module/coalition/coalition_scuttle.py', 'auto_search_combat_execute',
     ('handle_combat_automation_confirm',)),
    ('module/coalition/combat.py', 'coalition_combat_re_enter',
     ('handle_combat_automation_confirm',)),
)

ROOT = Path(__file__).resolve().parent.parent


class TestExitCombatLoopsHandlePopup(unittest.TestCase):
    """所有「会点退出战斗的独立循环」都必须处理「知道了」说明弹窗。"""

    def test_each_loop_calls_required_handlers(self):
        missing_report = []
        for relpath, funcname, handlers in LOOP_TARGETS:
            path = ROOT / relpath
            self.assertTrue(path.exists(), f'找不到 {relpath}')
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            func = _find_function(tree, funcname)
            self.assertIsNotNone(
                func, f'{relpath} 中未找到 {funcname}，源码锁失效（函数可能被重命名）'
            )
            called = _called_names(func)
            missing = [h for h in handlers if h not in called]
            if missing:
                missing_report.append(
                    f'{relpath}::{funcname} 缺少 {", ".join(missing)}'
                )
        self.assertEqual(
            missing_report, [],
            '这些循环会点「退出战斗」，游戏随后弹出「信息 INFORMATION / 知道了」'
            '说明弹窗；缺处理器会空转到 195s 抛 GameStuckError：\n'
            + '\n'.join(missing_report),
        )

    def test_scan_targets_exist(self):
        """防止 LOOP_TARGETS 被悄悄清空导致测试形同虚设。"""
        self.assertGreaterEqual(len(LOOP_TARGETS), 6)


class TestAutomationConfirmAssetStillCovered(unittest.TestCase):
    """确认「知道了」弹窗的识别素材与识别方法仍然存在。"""

    def test_handle_method_exists(self):
        source = (
            Path(__file__).resolve().parent.parent
            / 'module' / 'combat' / 'combat.py'
        ).read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        self.assertIsNotNone(
            _find_function(tree, 'handle_combat_automation_confirm'),
            'module/combat/combat.py 中的 handle_combat_automation_confirm 被删了，'
            '「知道了」弹窗将无人识别',
        )

    def test_assets_exist(self):
        source = (
            Path(__file__).resolve().parent.parent
            / 'module' / 'combat' / 'assets.py'
        ).read_text(encoding='utf-8-sig')
        for name in ('AUTOMATION_CONFIRM', 'AUTOMATION_CONFIRM_CHECK'):
            self.assertIn(
                name + ' = ',
                source,
                f'module/combat/assets.py 中缺少 {name} 素材定义',
            )


if __name__ == '__main__':
    unittest.main()