"""守护：通配导入掩盖的未定义资产常量。

背景（2026-11-09 FLEET_UNAVAILABLE 事故）：
提交 40e6b2553f 移植上游「冬季赛季」代码时，info_handler.py 的
handle_urgent_commission 引用了 FLEET_UNAVAILABLE，但
module/handler/assets.py 没定义、assets/cn/handler 下也没有 PNG。
该函数位在几乎所有任务的公共路径（ui_additional / handle_app_login 都会调），
一跑到就 NameError，任务连环崩溃，并被「未处理异常 3 次」判定拉去重启模拟器。

为什么现有门禁抓不到：
- info_handler.py 等大量使用 from module.handler.assets import *，
  ruff / pyflakes 遇到通配导入会放弃判断名字是否存在；
- CI 的 lint 又显式 --ignore F821（未定义名称检查被关闭）。

本测试在字节码层兜底：对每个含通配导入的模块，收集其自身函数与类方法的
LOAD_GLOBAL 常量名，要求都能在模块命名空间（含通配导入带来的名字）或
builtins 中找到。做法要点（每一条都踩过坑）：
- 函数取代码用 __code__，co_code 是代码对象自己的属性，写错会导致
  一个函数都扫不到、测试恒真通过（vacuous pass）；
- 只扫 __module__ 属于本模块的对象，否则 vars(mod) 里导入进来的函数
  也会被扫，却拿本模块命名空间判定，产出大量误报；
- 只扫 co_filename 指向本文件的代码对象，排除 dataclass / exec 生成的
  函数（它们会报 _FIELDS / MISSING 之类的假阳性）；
- 同时断言扫描规模，避免上述任一过滤把样本清空后「静默通过」。

已知代价：需 import 全部含通配导入的模块，约 40 秒。
"""
import builtins
import dis
import importlib
import os
import pathlib
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _module_name(py_file: pathlib.Path) -> str:
    name = '.'.join(py_file.relative_to(ROOT).with_suffix('').parts)
    if name.endswith('.__init__'):
        name = name[: -len('.__init__')]
    return name


def _wildcard_modules():
    """收集 module/ 下所有含通配导入的模块。"""
    found = []
    for py in (ROOT / 'module').rglob('*.py'):
        try:
            text = py.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if 'import *' in text:
            found.append((_module_name(py), py))
    return found


def _iter_codes(code):
    """递归展开嵌套代码对象。"""
    yield code
    for const in code.co_consts:
        if hasattr(const, 'co_code'):
            yield from _iter_codes(const)


def _iter_module_functions(module_name, namespace):
    """产出本模块自己定义的函数与类方法（不含导入进来的对象）。"""
    for obj in list(namespace.values()):
        if isinstance(obj, types.FunctionType):
            if getattr(obj, '__module__', None) == module_name:
                yield obj
        elif isinstance(obj, type) and getattr(obj, '__module__', None) == module_name:
            for value in list(vars(obj).values()):
                if isinstance(value, types.FunctionType):
                    yield value
                elif isinstance(value, (staticmethod, classmethod)):
                    yield value.__func__


class TestNoGhostAssetConstants(unittest.TestCase):
    def test_wildcard_modules_have_no_undefined_constants(self):
        modules = _wildcard_modules()
        missing = set()
        scanned_files = 0
        scanned_codes = 0
        import_failures = []

        for module_name, py_path in modules:
            try:
                module = importlib.import_module(module_name)
            except BaseException as exc:  # 导入副作用不阻断守卫，但要记录
                import_failures.append(f'{py_path.relative_to(ROOT)}: {type(exc).__name__}')
                continue

            namespace = vars(module)
            expected_file = os.path.normcase(os.path.abspath(str(py_path)))
            file_scanned = False

            for fn in _iter_module_functions(module_name, namespace):
                for code in _iter_codes(fn.__code__):
                    # 排除 dataclass / exec 生成的函数，避免假阳性
                    if os.path.normcase(os.path.abspath(code.co_filename)) != expected_file:
                        continue
                    scanned_codes += 1
                    file_scanned = True
                    for ins in dis.get_instructions(code):
                        if ins.opname not in ('LOAD_GLOBAL', 'LOAD_NAME'):
                            continue
                        name = ins.argval
                        if not isinstance(name, str) or not name.isupper():
                            continue
                        if name in namespace or hasattr(builtins, name):
                            continue
                        lineno = getattr(getattr(ins, 'positions', None), 'lineno', None) \
                            or code.co_firstlineno
                        missing.add(f'{py_path.relative_to(ROOT)}:{lineno} -> {name}')

            if file_scanned:
                scanned_files += 1

        # 防止过滤条件把样本清空后「静默通过」
        self.assertGreater(len(modules), 100, f'通配导入模块数量异常：{len(modules)}')
        self.assertGreater(scanned_files, 50, f'实际扫描到的文件过少：{scanned_files}')
        self.assertGreater(scanned_codes, 500, f'实际扫描到的代码对象过少：{scanned_codes}')
        self.assertEqual(import_failures, [], '存在导入失败的模块，守卫覆盖不完整：\n'
                         + '\n'.join(import_failures[:20]))
        self.assertEqual(
            missing, set(),
            '以下大写常量被引用但未定义（通配导入掩盖，运行时会 NameError）：\n'
            + '\n'.join(sorted(missing))
        )


if __name__ == '__main__':
    unittest.main()
