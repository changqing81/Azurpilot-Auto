"""新 UI 页面壳（Shell）实例名传递的回归测试。

覆盖 2026-11-25 事故：多实例用户在小号的配置页点任何菜单项，
都会被带回 alas 的配置页（长卿实测：「点小号的联合作战能给我调到 alas 的实例配置页里」）。

根因有两条，必须同时锁住，缺一条就会复发：

1. **实例名来源错**。`<Shell active={task}>` 里的 active 是「高亮用的 key」——
   配置页传的是任务名（Alas/Commission…）、开发者工具传 'Utils'、设置页传
   'Setting'、集合页传 'CollectionEntry'，全都不是实例名。Shell 里
   `instNames.includes(active) ? active : instNames[0] ?? 'alas'` 于是永远走
   回退分支，而 alas_instance() 的顺序来自 os.listdir，第一个通常是 alas。
   修法：Shell 增加 instance 参数，凡是有实例名的页面必须显式传。

2. **onclick 被「只绑一次」的守卫挡住**。menuRef 里原来先 `if (__alasToggleBound) return`
   再赋值 onclick，React 复用同一个 #pywebio-scope-menu 节点跨路由时，
   callback ref 再被调用一次就 return 了，onclick 永远停在首次渲染那份闭包上
   （instanceName 过期）。因为问题 1 的兜底值等于常量才一直没暴露；修好问题 1
   之后立刻表现为「在 alas 配置页点委托跳到小号」。
   修法：守卫只包 addEventListener；onclick 是赋值、天然幂等，放在守卫外面。

本机没有前端测试框架（tsc 只管类型），所以这里用源码断言锁住这两条不变量。
⚠️ 断言一律在**去掉注释后的代码**上做：修法说明里就写着错误写法，全文匹配会误判。
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend" / "src"
SHELL = FRONTEND / "components" / "Shell.tsx"


def strip_comments(source: str) -> str:
    """去掉 TS 的 // 行注释与 /* */ 块注释，只留可执行代码。

    ⚠️ 必须**逐字符扫描**，不能用 `re.sub(r"/\\*.*?\\*/", ...)`：
    这些 tsx 的**行注释里就写着 `/newapi/scheduler/*` 这种带 `/*` 的路径**
    （pages/tool/task.tsx 顶部注释就有一处），正则会把那一行后面的整段
    import 和组件开头当成块注释一起删掉，断言于是对着残缺源码匹配 ——
    本文件第一次跑就是被这个坑到，报「pages/tool/task.tsx 拿不到 name」，
    实际源码完全正确。

    同时也不要加「删 JSX 注释 {/\* … *\/}」的正则：它会从 interface 的
    `{` 一路吞到末个注释后的 `}`，同样吞掉后面的函数头。块注释删干净后
    残留的 `{ }` 对本来就只看关键片段的断言无影响。
    """
    out = []
    i, n = 0, len(source)
    in_block = False
    while i < n:
        if in_block:
            if source.startswith("*/", i):
                in_block = False
                i += 2
            else:
                i += 1
            continue
        if source.startswith("/*", i):
            in_block = True
            i += 2
            continue
        if source.startswith("//", i):
            # 行注释：跳到换行，保留换行符维持行结构
            j = source.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(source[i])
        i += 1
    return "".join(out)


class TestShellInstanceProp(unittest.TestCase):
    """Shell 必须支持 instance 参数，且以它为实例名的最高优先级来源。"""

    def setUp(self):
        self.assertTrue(SHELL.is_file(), f"缺少 {SHELL}")
        self.code = strip_comments(SHELL.read_text(encoding="utf-8"))

    def test_prop_declared_in_signature(self):
        """解构参数里要有 instance，类型声明里要有 instance?: string。"""
        sig = self.code[self.code.index("export function Shell"):]
        sig = sig[: sig.index("}) {")]
        self.assertIn("instance,", sig, "Shell 参数解构里缺少 instance")
        self.assertRegex(sig, r"instance\?:\s*string", "Shell 缺少 instance?: string 类型声明")

    def test_instance_takes_priority_over_fallback(self):
        """instanceName 必须优先取 instance，回退链只能作降级。"""
        m = re.search(r"const instanceName = (.+)", self.code)
        self.assertIsNotNone(m, "找不到 instanceName 的赋值")
        expr = m.group(1)
        self.assertTrue(
            expr.strip().startswith("instance ||"),
            f"instanceName 必须以 instance 为第一优先级，实际是：{expr.strip()}",
        )
        # 降级链要保留，保证全局页（更新器/公告/管理）仍能工作
        self.assertIn("instNames[0]", expr, "instanceName 丢了实例列表兜底")


class TestShellMenuRefBinding(unittest.TestCase):
    """menuRef 里 addEventListener 才需要守卫，onclick 赋值必须每次刷新。"""

    def setUp(self):
        self.code = strip_comments(SHELL.read_text(encoding="utf-8"))
        start = self.code.index("const menuRef = (el: HTMLDivElement | null) => {")
        # menuRef 之后的下一个顶层声明处收尾
        rest = self.code[start + 10:]
        # 终点必须是**代码**行：注释会被 strip_comments 删掉，拿注释当锚点会 ValueError
        end = rest.index("const instanceIcon = (state: number) =>")
        self.menu_ref = self.code[start: start + 10 + end]

    def test_onclick_assignment_not_behind_guard(self):
        """onclick 赋值不得出现在 __alasToggleBound 的 if 块内。"""
        body = self.menu_ref
        guard = "if (!(el as any).__alasToggleBound) {"
        self.assertIn(guard, body)
        lo = body.index(guard)
        open_at = body.index("{", lo)
        # 大括号配对，取出守卫 if 块的真实范围
        depth, hi = 0, None
        for i in range(open_at, len(body)):
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
                if depth == 0:
                    hi = i
                    break
        self.assertIsNotNone(hi, "守卫 if 块的大括号没有闭合")
        onclick_at = body.index("el.onclick = (ev) => {")
        self.assertFalse(
            open_at < onclick_at < hi,
            "el.onclick 落进了「只绑一次」的守卫块里：React 复用同一节点时"
            "callback ref 会被 return 掉，onclick 停在过期闭包上（2026-11-25 事故）",
        )

    def test_toggle_listener_still_guarded(self):
        """addEventListener('toggle') 仍必须防重复注册，否则会叠加执行。"""
        self.assertIn("if (!(el as any).__alasToggleBound) {", self.menu_ref)
        guard_at = self.menu_ref.index("if (!(el as any).__alasToggleBound) {")
        toggle_at = self.menu_ref.index("el.addEventListener('toggle'")
        self.assertLess(guard_at, toggle_at, "toggle 监听必须在守卫块内部")


class TestPagesPassInstance(unittest.TestCase):
    """凡是有实例名的页面，都必须把实例名传给 Shell。"""

    #: 文件 → 该文件里必须出现的 Shell 写法（去掉空白后比对）
    PAGES = {
        "pages/config/index.tsx": "<Shell active={task} instance={name}",
        "pages/tool/task.tsx": "<Shell active={task} instance={name}",
        "pages/tool/index.tsx": '<Shell active="Utils" instance={name}',
        "pages/settings/index.tsx": '<Shell active="Setting" instance={name}',
        "pages/eventcalc/index.tsx": '<Shell active="EventCalcEntry" instance={name}',
        "pages/otherconfig/TaskCollectionPage.tsx":
            '<Shell active="CollectionEntry" instance={name}',
    }

    def test_each_page_passes_instance(self):
        for rel, expected in self.PAGES.items():
            with self.subTest(page=rel):
                path = FRONTEND / rel
                self.assertTrue(path.is_file(), f"缺少 {path}")
                code = strip_comments(path.read_text(encoding="utf-8"))
                flat = re.sub(r"\s+", " ", code)
                self.assertIn(expected, flat, f"{rel} 的 Shell 没传 instance={{name}}")

    def test_pages_have_name_in_scope(self):
        """传了 name 就得能从路由参数拿到，否则 tsc 过不了、运行时变成 undefined。"""
        for rel in self.PAGES:
            with self.subTest(page=rel):
                code = strip_comments((FRONTEND / rel).read_text(encoding="utf-8"))
                has_params = re.search(r"\bname\s*=\s*'alas'", code) or "useParamsSafe()" in code
                self.assertTrue(has_params, f"{rel} 里拿不到 name（缺 useParams）")


if __name__ == "__main__":
    unittest.main()
