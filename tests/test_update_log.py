"""更新日志（渲染 + 发布工具）的回归测试。

覆盖 2026-09-20 新增的「更新器 · 更新日志」：
- 正文解析：纯文本 / 整行 `![说明](图片)` / 整行裸图片直链 / 危险协议一律不放行
- HTML 转义：服务端渲染必须 escape 文本，图片地址只允许 http(s) 与 data:image
- 条目归一化：坏数据跳过、超限截断、缺字段不影响其余条目
- 折叠渲染：最新一条默认展开，其余收起
- 发布辅助：条目 id 按当天递增、合并时远端独有条目不能丢
"""

import json
import tempfile
import unittest
from pathlib import Path

from dev_tools import changelog_publish
from module.webui import update_log


class TestParseImageLine(unittest.TestCase):
    def test_markdown_image(self):
        self.assertEqual(
            update_log.parse_image_line("![说明](https://cdn.example.com/a.png)"),
            ("https://cdn.example.com/a.png", "说明"),
        )

    def test_bare_image_link(self):
        self.assertEqual(
            update_log.parse_image_line("https://cdn.example.com/b.jpg"),
            ("https://cdn.example.com/b.jpg", ""),
        )

    def test_angle_bracketed_link(self):
        self.assertEqual(
            update_log.parse_image_line("<https://cdn.example.com/c.webp>"),
            ("https://cdn.example.com/c.webp", ""),
        )

    def test_non_image_lines(self):
        for line in ("普通文字", "https://example.com/page.html", "看图 https://a.png 谢谢"):
            self.assertIsNone(update_log.parse_image_line(line))

    def test_unsafe_protocol_rejected(self):
        self.assertIsNone(update_log.parse_image_line("![x](javascript:alert(1))"))
        self.assertIsNone(update_log.parse_image_line("![x](vbscript:msgbox)"))
        self.assertFalse(update_log.is_safe_image_url("javascript:alert(1)"))
        self.assertTrue(update_log.is_safe_image_url("data:image/png;base64,AAAA"))


class TestRenderContentHtml(unittest.TestCase):
    def test_text_is_escaped(self):
        html = update_log.render_content_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_text_lines_merged_into_one_block(self):
        html = update_log.render_content_html("第一行\n第二行")
        self.assertEqual(html.count(update_log.TEXT_CLASS), 1)
        self.assertIn("第一行\n第二行", html)

    def test_image_line_becomes_img(self):
        html = update_log.render_content_html("![配图](https://cdn.example.com/a.png)")
        self.assertIn(f'class="{update_log.IMAGE_CLASS}"', html)
        self.assertIn('src="https://cdn.example.com/a.png"', html)
        self.assertIn('alt="配图"', html)
        self.assertIn('loading="lazy"', html)

    def test_no_empty_blocks_for_blank_lines(self):
        html = update_log.render_content_html("文字\n\n   \n更多")
        self.assertEqual(html.count(update_log.TEXT_CLASS), 1)


class TestNormalizeEntries(unittest.TestCase):
    def test_skips_broken_items_and_keeps_order(self):
        data = {
            "entries": [
                "not-a-dict",
                {"title": "", "content": "   "},
                {"title": "第一条", "content": "正文", "date": "2026-09-20", "id": "20260920-1", "sha": "abc"},
                {"title": "第二条", "content": ""},
            ]
        }
        entries = update_log.normalize_entries(data)
        self.assertEqual([item["title"] for item in entries], ["第一条", "第二条"])

    def test_limit_and_bad_payload(self):
        data = {"entries": [{"title": f"t{i}"} for i in range(10)]}
        self.assertEqual(len(update_log.normalize_entries(data, limit=3)), 3)
        self.assertEqual(update_log.normalize_entries(None), [])
        self.assertEqual(update_log.normalize_entries({"entries": "oops"}), [])

    def test_accepts_bare_list(self):
        entries = update_log.normalize_entries([{"title": "只有列表"}])
        self.assertEqual(len(entries), 1)


class TestRenderEntriesHtml(unittest.TestCase):
    def test_first_entry_open_rest_collapsed(self):
        html = update_log.render_entries_html(
            [
                {"title": "新", "date": "2026-09-20", "sha": "abcdef1234567", "content": "a"},
                {"title": "旧", "date": "2026-09-19", "sha": "", "content": "b"},
            ]
        )
        # 整块折叠层（update-log-root）不是条目，按 class 数而不是数 <details>，
        # 否则加一层折叠就会把这里算成 3。
        self.assertEqual(html.count('class="update-log-root"'), 1)
        self.assertEqual(html.count('class="update-log-entry"'), 2)
        self.assertEqual(html.count('class="update-log-entry" open'), 1)
        self.assertIn("abcdef1234", html)  # sha 截断到 10 位
        self.assertIn("2026-09-19", html)

    def test_title_is_escaped(self):
        html = update_log.render_entries_html([{"title": "<b>x</b>", "content": "c"}])
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)


class TestChangelogFileHelpers(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "changelog.json"

    def test_save_and_load_roundtrip(self):
        data = {"updatedAt": "2026-09-20", "entries": [{"id": "20260920-1", "title": "t"}]}
        changelog_publish.save_changelog(data, self.path)
        self.assertEqual(changelog_publish.load_changelog(self.path), data)

    def test_load_missing_file(self):
        self.assertEqual(changelog_publish.load_changelog(self.path)["entries"], [])

    def test_load_rejects_bad_schema(self):
        self.path.write_text('{"entries": {}}', encoding="utf-8")
        with self.assertRaises(RuntimeError):
            changelog_publish.load_changelog(self.path)

    def test_next_entry_id_increments_within_day(self):
        existing = [{"id": "20260920-1"}, {"id": "20260920-3"}, {"id": "20260919-9"}, {"id": "bad"}]
        self.assertEqual(changelog_publish.next_entry_id(existing, "20260920"), "20260920-4")
        self.assertEqual(changelog_publish.next_entry_id(existing, "20260921"), "20260921-1")

    def test_merge_keeps_remote_only_entries(self):
        local = [{"id": "20260920-2", "title": "本地新增"}]
        remote = [{"id": "20260920-1", "title": "远端已有"}, {"id": "20260920-2", "title": "旧版本"}]
        merged = changelog_publish.merge_entries(local, remote)
        self.assertEqual([item["id"] for item in merged], ["20260920-2", "20260920-1"])
        self.assertEqual(merged[0]["title"], "本地新增")

    def test_merge_skips_non_dict(self):
        merged = changelog_publish.merge_entries([{"id": "a"}], ["junk", None])
        self.assertEqual(len(merged), 1)


class TestPreviewHtml(unittest.TestCase):
    def test_preview_contains_styles_and_entries(self):
        html = changelog_publish.render_preview_html(
            [{"id": "20260920-1", "date": "2026-09-20", "title": "标题", "content": "正文"}],
            {"updatedAt": "2026-09-20"},
        )
        self.assertIn("update-log-entry", html)
        self.assertIn("亮色主题", html)
        self.assertIn("暗色主题", html)
        self.assertIn("标题", html)
        # 预览底部的 JSON 会做 HTML 转义，这里只断言 id 出现
        self.assertIn("20260920-1", html)


if __name__ == "__main__":
    unittest.main()
