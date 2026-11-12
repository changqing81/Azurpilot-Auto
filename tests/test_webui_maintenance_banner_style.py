"""维护等待横幅的静态资源与样式守卫。

对应 2026-11-12 的维护横幅功能。实测踩到的坑写在这里防止回退：
横幅最初用 `color: var(--alas-content-text)`，但该变量只在浅色主题的
`alas.css` 里以 `var(..., #202124)` 兜底形式出现，深色主题并没有重新定义它，
于是深底上渲染出深灰字，完全不可读（截图确认）。现在两套主题都显式配色。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALAS_CSS = ROOT / "assets" / "gui" / "css" / "alas.css"
DARK_CSS = ROOT / "assets" / "gui" / "css" / "dark-alas.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestBannerCss(unittest.TestCase):
    def test_light_theme_defines_banner(self):
        css = _read(ALAS_CSS)
        for cls in (
            ".alas-maint-banner",
            ".alas-maint-banner__dot",
            ".alas-maint-banner__body",
            ".alas-maint-banner__state",
            ".alas-maint-banner__desc",
            ".alas-maint-banner__meta",
        ):
            self.assertIn(cls, css, f"alas.css 缺少 {cls}")

    def test_light_theme_does_not_rely_on_content_text(self):
        """横幅不能依赖 --alas-content-text：深色主题没定义它。"""
        css = _read(ALAS_CSS)
        start = css.index("/* --- 维护等待横幅")
        block = css[start:]
        self.assertNotIn("var(--alas-content-text)", block)

    def test_dark_theme_overrides_all_text_colors(self):
        """深色主题必须同时覆盖容器色与标题色，否则正文继承深灰看不清。"""
        css = _read(DARK_CSS)
        self.assertIn("html:root .alas-maint-banner {", css)
        self.assertIn("html:root .alas-maint-banner__state", css)
        # 容器上必须给出亮色文字
        block = css[css.index("html:root .alas-maint-banner {"):]
        self.assertRegex(block, r"color:\s*#")


class TestBannerMarkup(unittest.TestCase):
    def test_banner_id_unique_in_pure_module(self):
        from module.webui import maintenance_banner as mb

        self.assertEqual(mb.BANNER_ID, "alas-maint-banner")
        html = mb.build_html({"elapsed": "已等待", "next": "下次查询"})
        self.assertEqual(html.count(f'id="{mb.BANNER_ID}"'), 1)


if __name__ == "__main__":
    unittest.main()
