"""桥接 config 属性异常不得 exit(1) 的回归测试（2026-10-26 同类缺陷排查）。

背景：alas.py 三个 @cached_property（config / device / checker）曾共用
「except Exception → exit(1)」模板。device 那个已造成 2026-10-26 停机
3.5 小时，checker 已修。本轮排查把同样的模板在桥接里也找了出来：

- submodule/AlasFpyBridge/fpy.py  config 属性
- submodule/AlasMaaBridge/maa.py  config 属性

两者的 except Exception 分支都走 exit(1)。桥接跑在 WebUI 的独立子进程
（module/webui/process_manager.py:900/902 经 load_mod 动态导入），
所以 exit(1) 不会杀主调度，但会静默结束该桥接子进程，且
process_manager 只记一行日志、不会自动拉起——表现为「Fpy/Maa 无缘无故
停了，得手动点启动」。

修复：RequestHumanTakeover（真正需要人工介入）保留 exit(1)；
其余 Exception 改为 raise，交给上层重试。

说明：cached_property 在异常路径不写缓存（实测确认），因此 raise 之后
下一次访问会真正重新构造，符合重试语义。
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    'AlasFpyBridge': ROOT / 'submodule' / 'AlasFpyBridge' / 'fpy.py',
    'AlasMaaBridge': ROOT / 'submodule' / 'AlasMaaBridge' / 'maa.py',
}


class BridgeConfigSourceLock(unittest.TestCase):
    """AST 源码锁：桥接 config 的通用异常分支不得 exit。"""

    def _find_config_func(self, path):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'config':
                for deco in node.decorator_list:
                    if 'cached_property' in ast.unparse(deco):
                        return node
        return None

    def _handler_for(self, func, name):
        """按异常类型名找 handler，返回其函数体节点列表。"""
        for node in ast.walk(func):
            if not isinstance(node, ast.Try):
                continue
            for h in node.handlers:
                if h.type is None:
                    continue
                type_name = ast.unparse(h.type)
                if type_name == name:
                    return h.body
        return None

    def _has_exit(self, body_nodes):
        for n in body_nodes:
            for c in ast.walk(n):
                if isinstance(c, ast.Call):
                    src = ast.unparse(c)
                    if src.startswith('exit(') or src.startswith('sys.exit(') \
                            or src.startswith('os._exit('):
                        return c.lineno
                if isinstance(c, ast.Raise) and c.exc is not None \
                        and 'SystemExit' in ast.unparse(c.exc):
                    return c.lineno
        return None

    def _has_bare_raise(self, body_nodes):
        for n in body_nodes:
            for c in ast.walk(n):
                if isinstance(c, ast.Raise) and c.exc is None:
                    return c.lineno
        return None

    def test_generic_exception_does_not_exit(self):
        """except Exception 分支不得 exit(1)，必须是裸 raise。"""
        for name, path in TARGETS.items():
            with self.subTest(bridge=name):
                self.assertTrue(path.is_file(), f'{path} 不存在')
                func = self._find_config_func(path)
                self.assertIsNotNone(func, f'{name} 中找不到 @cached_property config')

                generic = self._handler_for(func, 'Exception')
                self.assertIsNotNone(generic,
                                     f'{name}.config 缺少 except Exception 分支')
                exit_line = self._has_exit(generic)
                self.assertIsNone(
                    exit_line,
                    f'{name}.config 的 except Exception 在行 {exit_line} 调用了 exit：'
                    f'配置读取的瞬时失败会静默结束桥接子进程',
                )
                self.assertIsNotNone(
                    self._has_bare_raise(generic),
                    f'{name}.config 的 except Exception 应裸 raise 交给上层重试',
                )

    def test_human_takeover_still_terminates(self):
        """RequestHumanTakeover 语义保留：真正需要人工介入时仍退出。"""
        for name, path in TARGETS.items():
            with self.subTest(bridge=name):
                func = self._find_config_func(path)
                handler = self._handler_for(func, 'RequestHumanTakeover')
                self.assertIsNotNone(
                    handler,
                    f'{name}.config 缺少 except RequestHumanTakeover 分支：'
                    f'不可恢复语义被误删',
                )
                self.assertIsNotNone(
                    self._has_exit(handler),
                    f'{name}.config 的 RequestHumanTakeover 分支应当退出（人工介入语义）',
                )


class BridgeConfigBehaviour(unittest.TestCase):
    """行为验证：构造失败时抛异常而不是 SystemExit。"""

    def test_fgo_config_failure_raises_not_exits(self):
        """FgoConfig 构造抛 Exception 时，config 属性应抛异常而非 SystemExit。"""
        from unittest.mock import patch
        from submodule.AlasFpyBridge import fpy

        script = fpy.FgoAutoScript.__new__(fpy.FgoAutoScript)
        script.__dict__['config_name'] = 'alas'

        with patch.object(fpy, 'FgoConfig', side_effect=RuntimeError('config io error')):
            with self.assertRaises(RuntimeError):
                _ = script.config

    def test_arknights_config_failure_raises_not_exits(self):
        """ArknightsConfig 构造抛 Exception 时，config 属性应抛异常而非 SystemExit。"""
        from unittest.mock import patch
        from submodule.AlasMaaBridge import maa

        script = maa.ArknightsAutoScript.__new__(maa.ArknightsAutoScript)
        script.__dict__['config_name'] = 'alas'

        with patch.object(maa, 'ArknightsConfig', side_effect=RuntimeError('config io error')):
            with self.assertRaises(RuntimeError):
                _ = script.config


if __name__ == '__main__':
    unittest.main()
