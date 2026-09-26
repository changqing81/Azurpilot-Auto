"""统计页 v2 组件构造器的单元测试。

覆盖 `module/webui/app_helpers.py` 里三个从 statistics-v2 原型移植的构造器：
指标组（metric）、折叠块（fold）、以及在用的标题块 / 简洁表格。
这些函数的 HTML 结构被 statistics-alas.css 按类名选中，结构改了样式就失效，
所以这里对类名与转义行为做断言。
"""

import unittest

from module.webui.app_helpers import (
    build_fold_block,
    build_metric_grid,
    build_muted_notice,
    build_simple_table,
    build_title_block,
)


class TestBuildMetricGrid(unittest.TestCase):
    def test_renders_one_cell_per_metric(self):
        html = build_metric_grid(["月份", "作战次数"], ["2026-09", "1,234"])
        self.assertEqual(html.count('class="metric"'), 2)
        self.assertIn('class="metric-label"', html)
        self.assertIn('class="metric-value"', html)
        self.assertIn("2026-09", html)
        self.assertIn("1,234", html)

    def test_keeps_column_order(self):
        html = build_metric_grid(["A", "B", "C"], ["1", "2", "3"])
        self.assertLess(html.index("A"), html.index("B"))
        self.assertLess(html.index("B"), html.index("C"))

    def test_escapes_label_but_keeps_value_html(self):
        html = build_metric_grid(["<b>裸标签</b>"], ["<span>值</span>"])
        # 标签必须转义（防注入），数值允许带 HTML（调用方负责）
        self.assertNotIn("<b>裸标签</b>", html)
        self.assertIn("&lt;b&gt;裸标签&lt;/b&gt;", html)
        self.assertIn("<span>值</span>", html)

    def test_mismatched_lengths_pair_by_position(self):
        html = build_metric_grid(["A", "B"], ["1"])
        self.assertEqual(html.count('class="metric"'), 1)

    def test_empty_input_renders_empty_grid(self):
        html = build_metric_grid([], [])
        self.assertIn('class="metric-grid"', html)
        self.assertNotIn('class="metric"', html)


class TestBuildFoldBlock(unittest.TestCase):
    def test_renders_details_with_title_and_digest(self):
        html = build_fold_block("每日经验检测", "<p>body</p>", digest="共 3 艘")
        self.assertIn("<details", html)
        self.assertIn('class="fold"', html)
        self.assertIn('class="fold-title"', html)
        self.assertIn('class="fold-digest"', html)
        self.assertIn("每日经验检测", html)
        self.assertIn("共 3 艘", html)
        self.assertIn("<p>body</p>", html)

    def test_closed_by_default(self):
        html = build_fold_block("t", "b")
        self.assertNotIn(" open>", html)

    def test_can_open_by_default(self):
        html = build_fold_block("t", "b", open_by_default=True)
        self.assertIn("<details class=\"fold\" open>", html)

    def test_escapes_title_and_digest_but_keeps_body_html(self):
        html = build_fold_block("<script>x</script>", "<b>bold</b>", digest="<i>d</i>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<i>d</i>", html)
        self.assertIn("<b>bold</b>", html)


class TestExistingBuilders(unittest.TestCase):
    """标题块与简洁表格的结构是样式表的挂载点，一并锁定。"""

    def test_title_block_carries_class(self):
        html = build_title_block("标题")
        self.assertIn('class="st-title-block"', html)

    def test_simple_table_headers_and_data_th(self):
        html = build_simple_table(["列A", "列B"], [["1", "2"]])
        self.assertIn("列A", html)
        self.assertIn('data-th="列A"', html)

    def test_muted_notice_keeps_text_verbatim(self):
        self.assertIn("没有数据", build_muted_notice("没有数据"))


if __name__ == "__main__":
    unittest.main()
