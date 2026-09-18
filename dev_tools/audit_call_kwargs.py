"""全仓调用的「签名自检」：静态找出传了目标方法不接受的关键字 / 位置参数的位置。

起因：岛屿计划统一调度器上线后第一次真机运行就 CRITICAL ——
`TypeError: UI.ui_ensure() got an unexpected keyword argument 'get_ship'`
（`get_ship` 是 `ui_goto()` / `ui_additional()` 的参数，`ui_ensure()` 签名里没有）。
单测与 CI 全绿都没拦住，因为测试里用的是裸 `Mock()`：接受任何参数；
`importlib.import_module` 也被打桩，模块路径/类名写错同样溜过去。
**Mock 边界 = 单测盲区**，这类错只能在真机第一次执行时暴露，所以补一层静态检查。

纯 `ast`，不导入任何业务模块（秒级、无副作用）：

    .venv/Scripts/python.exe dev_tools/audit_call_kwargs.py
    .venv/Scripts/python.exe dev_tools/audit_call_kwargs.py <其他仓库根目录>

覆盖四类调用，检查「关键字名」与「位置参数个数」：

    self.method(...)          按 MRO 解析，含仓库内基类
    super().method(...)       从本类基类开始解析
    self.attr.method(...)     按 self.x = Xxx(...) / 类型注解 / @property 返回注解推断类型
    Name(...)                 仓库内的类 __init__ 与模块级函数（含同文件 import ... as 别名）

判定规则保守：**只有所有同名候选都不接受时才报**（宁可漏报不误报）。
已知盲区：推断不出类型的 `self.attr.method()`、实现在仓库外的方法、未知符号的 `Name(...)`，
它们会被计为「跳过」而非「通过」。

发现问题时以退出码 1 结束（方便挂 CI）；没发现问题退出码 0。
"""
import ast
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else r'D:\alas\AzurPilot')
SKIP_DIRS = {'.venv', '__pycache__', '.git', 'node_modules', 'log', 'webapp'}


def iter_py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
        for name in filenames:
            if name.endswith('.py'):
                yield Path(dirpath) / name


def func_params(node):
    a = node.args
    names = {x.arg for x in list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs)}
    pos = [x.arg for x in list(a.posonlyargs) + list(a.args)]
    # self/cls 由调用方隐式传入，容量要减掉
    cap = len(pos) - (1 if pos and pos[0] in ('self', 'cls') else 0)
    # (可接受的关键字, 有 **kwargs, 位置参数容量, 有 *args)
    return names, a.kwarg is not None, cap, a.vararg is not None


class Collector(ast.NodeVisitor):
    def __init__(self, rel):
        self.rel = rel
        self.classes = defaultdict(list)   # name -> [{'bases', 'methods', 'init'}]
        self.functions = []                # 模块级函数 (params, var_kw)
        self.methods = []                  # 所有类方法 (params, var_kw)，供兜底查找
        self.calls = []                    # (class|None, kind, target, kwargs, line, receiver)
        self.attr_types = defaultdict(set)  # (class, attr) -> {类型名}
        self.aliases = {}                  # 本文件内的 import 别名: 别名 -> 原名

    def visit_ImportFrom(self, node):
        for a in node.names:
            self.aliases[a.asname or a.name] = a.name

    def visit_Import(self, node):
        for a in node.names:
            if a.asname:
                self.aliases[a.asname] = a.name.split('.')[-1]

    def _record_class(self, node):
        info = {'bases': [], 'methods': {}, 'init': None}
        for b in node.bases:
            if isinstance(b, ast.Name):
                info['bases'].append(b.id)
            elif isinstance(b, ast.Attribute):
                info['bases'].append(b.attr)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                p = func_params(item)
                info['methods'][item.name] = p
                self.methods.append(p)
                if item.name == '__init__':
                    info['init'] = p
        self.classes[node.name].append(info)
        self._collect_attr_types(node)

    def _collect_attr_types(self, node):
        """推断 self.<attr> 的类型：类级注解、self.x = Xxx(...)、@property 返回注解、__init__ 参数注解。"""
        def add(attr, type_name):
            if type_name and type_name[0].isupper():
                self.attr_types[(node.name, attr)].add(type_name)

        for item in ast.walk(node):
            # self.x = Xxx(...) / self.x = foo.Xxx(...)
            if isinstance(item, ast.Assign):
                for t in item.targets:
                    if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                            and t.value.id == 'self' and isinstance(item.value, ast.Call)):
                        callee = item.value.func
                        if isinstance(callee, ast.Name):
                            add(t.attr, callee.id)
                        elif isinstance(callee, ast.Attribute):
                            add(t.attr, callee.attr)
            # self.x: Xxx
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Attribute):
                t = item.target
                if isinstance(t.value, ast.Name) and t.value.id == 'self':
                    ann = item.annotation
                    if isinstance(ann, ast.Name):
                        add(t.attr, ann.id)
                    elif isinstance(ann, ast.Attribute):
                        add(t.attr, ann.attr)
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            ret = item.returns
            ret_name = None
            if isinstance(ret, ast.Name):
                ret_name = ret.id
            elif isinstance(ret, ast.Attribute):
                ret_name = ret.attr
            is_prop = any((isinstance(d, ast.Name) and d.id == 'property')
                          or (isinstance(d, ast.Attribute) and d.attr == 'property')
                          or (isinstance(d, ast.Call) and getattr(d.func, 'attr', '') in ('setter', 'getter'))
                          for d in item.decorator_list)
            if is_prop and ret_name:
                add(item.name, ret_name)
            # __init__(self, config: AzurLaneConfig) + self.config = config
            if item.name == '__init__':
                ann_by_param = {}
                for a in list(item.args.args) + list(item.args.kwonlyargs):
                    ann = a.annotation
                    if isinstance(ann, ast.Name):
                        ann_by_param[a.arg] = ann.id
                    elif isinstance(ann, ast.Attribute):
                        ann_by_param[a.arg] = ann.attr
                for sub in ast.walk(item):
                    if (isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Name)
                            and sub.value.id in ann_by_param):
                        for t in sub.targets:
                            if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                                    and t.value.id == 'self'):
                                add(t.attr, ann_by_param[sub.value.id])

    def visit_Module(self, node):
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions.append(func_params(item))
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self._record_class(node)
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            kwargs = [k.arg for k in sub.keywords if k.arg]
            n_pos = len([a for a in sub.args if not isinstance(a, ast.Starred)])
            has_star = any(isinstance(a, ast.Starred) for a in sub.args)
            if not kwargs and n_pos <= 1:
                continue
            f = sub.func
            if isinstance(f, ast.Attribute):
                chain = []
                cur = f
                while isinstance(cur, ast.Attribute):
                    chain.append(cur.attr)
                    cur = cur.value
                chain.reverse()
                if isinstance(cur, ast.Name):
                    if cur.id == 'self' and len(chain) == 1:
                        self.calls.append((node.name, 'self', chain[0], kwargs, sub.lineno, 'self.' + chain[0], n_pos, has_star))
                    elif cur.id == 'self':
                        self.calls.append((node.name, 'chain', chain[-1], kwargs, sub.lineno, 'self.' + '.'.join(chain), n_pos, has_star))
                    elif cur.id == 'super':
                        self.calls.append((node.name, 'super', chain[-1], kwargs, sub.lineno, 'super().' + chain[-1], n_pos, has_star))
            elif isinstance(f, ast.Name):
                self.calls.append((node.name, 'name', f.id, kwargs, sub.lineno, f.id, n_pos, has_star))
        # 类定义本身不要再被父级 walk 重复处理
        for sub in node.body:
            if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.visit(sub)


def main():
    files = sorted(iter_py_files(ROOT))
    classes = defaultdict(list)
    functions = []
    functions_by_name = defaultdict(list)
    methods = []
    calls = []
    attr_types = defaultdict(set)
    aliases_by_file = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError:
            continue
        rel = str(path.relative_to(ROOT))
        c = Collector(rel)
        c.visit(tree)
        for k, v in c.classes.items():
            classes[k].extend(v)
        for k, v in c.attr_types.items():
            attr_types[k] |= v
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions_by_name[node.name].append(func_params(node))
        aliases_by_file[rel] = c.aliases
        functions.extend(c.functions)
        methods.extend(c.methods)
        calls.extend((rel, *x) for x in c.calls)

    def resolve_mro(cls_name, method, _seen=None):
        _seen = _seen or set()
        if cls_name in _seen:
            return []
        _seen.add(cls_name)
        out = [info['methods'][method] for info in classes.get(cls_name, []) if method in info['methods']]
        if out:
            return out
        for info in classes.get(cls_name, []):
            for base in info['bases']:
                out.extend(resolve_mro(base, method, _seen))
        return out

    problems = []
    unresolved = defaultdict(int)
    checked = 0
    for rel, cls, kind, target, kwargs, line, receiver, n_pos, has_star in calls:
        if kind == 'name':
            # 同文件内的 import 别名：`OSShopItemGrid as ItemGrid` → 按 OSShopItemGrid 解析
            name = aliases_by_file.get(rel, {}).get(target, target)
            cands = list(functions_by_name.get(name, []))
            cands += [i['init'] for i in classes.get(name, []) if i['init']]
        elif kind == 'super':
            cands = []
            for info in classes.get(cls, []):
                for base in info['bases']:
                    cands.extend(resolve_mro(base, target))
        elif kind == 'self':
            cands = resolve_mro(cls, target) if cls else []
        else:  # chain：先按 self.<attr> 的类型注解/赋值推断，推不出来就跳过
            parts = receiver[len('self.'):].split('.')
            cands = []
            if len(parts) == 2:
                for t in attr_types.get((cls, parts[0]), set()):
                    cands.extend(resolve_mro(t, parts[1]))
        if not cands:
            unresolved[kind] += 1
            continue
        checked += 1
        bad_kw = not any(var_kw or all(k in params for k in kwargs) for params, var_kw, _, _ in cands)
        bad_pos = False if has_star else all(
            (not var_arg) and n_pos > cap for _, _, cap, var_arg in cands)
        if not bad_kw and not bad_pos:
            continue
        sample = sorted({(tuple(sorted(p)), cap) for p, _, cap, _ in cands})[:3]
        problems.append((rel, line, receiver, kwargs, n_pos, sample))

    print(f'扫描 {len(files)} 个文件 / {len(classes)} 个类 / {len(methods)} 个方法定义')
    print(f'带关键字的调用点 {len(calls)}，其中能定位到定义的 {checked} 个（其余按类别跳过：{dict(unresolved)}）')
    print()
    if not problems:
        print('未发现「所有同名候选都不接受这些关键字」的调用点')
        return 0
    print(f'发现 {len(problems)} 处可疑：')
    for rel, line, receiver, kwargs, n_pos, sample in problems:
        print(f'  {rel}:{line}  {receiver}(...)  传入位置参数 {n_pos} 个, 关键字 {kwargs}')
        for p, cap in sample:
            print(f'      候选: 关键字 {list(p)} / 位置容量 {cap}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
