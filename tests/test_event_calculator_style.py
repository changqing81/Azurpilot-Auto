"""活动计算器表格样式回归测试。

计算器整块 HTML 挂在 PyWebIO 的 #markdown-body 容器下面，会吃到
pywebio/html/css/markdown.min.css（GitHub 风格）的这几条规则：

    .markdown-body table                  { display: block; width: 100%; overflow: auto }
    .markdown-body table tr               { background-color: #fff; border-top: 1px solid #c6cbd1 }
    .markdown-body table tr:nth-child(2n) { background-color: #f6f8fa }
    .markdown-body table td, table th     { border: 1px solid #dfe2e5 }

后果（2026-10-31 实测，高级材质暗色下最刺眼）：
1. table 变块级滚动容器后，thead / tbody 各自独立排版，列宽不再对齐 ——
   表头「项目/单价/个数」挤在左侧，滑杆列被压扁；
2. 行被强制 #fff 白底 + #f6f8fa 斑马纹，而主题文字是 #f4f8ff 白色，
   整张表变成深色面板里的一块白表，白字压白底基本读不出来。

这两条都属于「不报错、单测全绿、只有肉眼看才发现」的回归，所以用源码锁
住：只要 #scope table 丢了 display: table，或行底色没被清成透明，就红。
"""

import re
import unittest

from module.webui.event_calculator import build_event_calculator_html

SCOPE_ID = "event_calculator_alas"


def _css_blocks(html: str):
    """把 <style> 里的规则切成 (选择器, 声明块) 列表，注释已剥离。"""
    style = re.search(r"<style>(.*?)</style>", html, re.S)
    assert style is not None, "计算器 HTML 里没有内联 <style>"
    css = re.sub(r"/\*.*?\*/", "", style.group(1), flags=re.S)
    blocks = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        blocks.append((selector.strip(), body))
    return blocks


def _declarations(blocks, selector: str):
    """取某个选择器最后一次出现的声明（后面覆盖前面）。"""
    body = ""
    for sel, decl in blocks:
        if sel == selector:
            body = decl
    return {
        key.strip().lower(): value.strip().lower()
        for key, value in (item.split(":", 1) for item in body.split(";") if ":" in item)
    }


class TestEventCalculatorTableStyle(unittest.TestCase):
    def setUp(self):
        self.blocks = _css_blocks(build_event_calculator_html(SCOPE_ID))

    def test_table_keeps_table_layout(self):
        """table 必须显式写回 display: table。

        markdown.min.css 给了 .markdown-body table { display: block }，
        计算器自己的 #scope table 只要不写 display，就永远输给它 ——
        因为主题/base 样式都在计算器之前或之后，谁也救不回来。
        """
        decl = _declarations(self.blocks, f"#{SCOPE_ID} table")
        self.assertEqual(decl.get("display"), "table")

    def test_row_background_is_transparent(self):
        """普通行与偶数行都要清成透明，否则白底会盖住深色主题底色。"""
        for selector in (f"#{SCOPE_ID} tr", f"#{SCOPE_ID} tr:nth-child(2n)"):
            decl = _declarations(self.blocks, selector)
            self.assertEqual(decl.get("background-color"), "transparent", selector)
        # 斑马纹的分隔线也要清掉
        self.assertEqual(
            _declarations(self.blocks, f"#{SCOPE_ID} tr").get("border-top"), "0"
        )

    def test_cell_border_not_left_to_markdown(self):
        """格子线用自带半透明灰，不留给 markdown 的 #dfe2e5。"""
        decl = _declarations(self.blocks, f"#{SCOPE_ID} th,\n#{SCOPE_ID} td")
        self.assertIn("border", decl)
        self.assertNotIn("#dfe2e5", decl["border"])


if __name__ == "__main__":
    unittest.main()
