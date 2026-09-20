"""公告发布工具（dev_tools/announcement_publish.py）的行为回归测试。

覆盖 2026-09-20 新增的公告发布链路里与"内容解析/渲染"相关的纯逻辑：
- 草稿解析（标题、正文、异常输入）
- 公告 ID 按当天序号递增（客户端同一 ID 只弹一次，ID 用错=公告发不出去）
- 正文里"本地图片"的识别与改写（预览内嵌 / 发布换成远端 URL）
- 预览 HTML 的图片渲染与文本转义（正文走 textContent 语义，防 XSS）

不触网：只测纯函数，任何网络分支都不进入。
"""

import base64
import json
import tempfile
import unittest
from pathlib import Path

from dev_tools import announcement_publish
from dev_tools.announcement_publish import (
    body_to_html,
    is_local_image,
    next_announcement_id,
    read_draft,
    rewrite_body,
    write_preview,
)


class _DraftTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = Path(tmp.name)
        self.image = self.tmp_dir / "shot.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\nfake-body")

    def write_draft(self, text: str) -> Path:
        path = self.tmp_dir / "draft.md"
        path.write_text(text, encoding="utf-8")
        return path


class TestReadDraft(_DraftTestCase):
    def test_parses_title_and_trims_body(self):
        draft = self.write_draft("# 标题在这\n\n正文第一行\n正文第二行\n\n\n")
        title, body = read_draft(draft)
        self.assertEqual(title, "标题在这")
        self.assertEqual(body, ["正文第一行", "正文第二行"])

    def test_requires_hash_title(self):
        draft = self.write_draft("没有标题\n正文")
        with self.assertRaisesRegex(RuntimeError, "没有找到标题"):
            read_draft(draft)

    def test_rejects_empty_body(self):
        draft = self.write_draft("# 只有标题\n\n   \n")
        with self.assertRaisesRegex(RuntimeError, "正文为空"):
            read_draft(draft)

    def test_missing_file(self):
        with self.assertRaisesRegex(RuntimeError, "草稿文件不存在"):
            read_draft(self.tmp_dir / "nope.md")


class TestAnnouncementId(unittest.TestCase):
    def test_increments_within_same_day(self):
        self.assertEqual(next_announcement_id("20260920-1", "20260920"), "20260920-2")
        self.assertEqual(next_announcement_id("20260920-9", "20260920"), "20260920-10")

    def test_resets_on_new_day(self):
        self.assertEqual(next_announcement_id("20260919-3", "20260920"), "20260920-1")

    def test_without_or_broken_current(self):
        self.assertEqual(next_announcement_id(None, "20260920"), "20260920-1")
        self.assertEqual(next_announcement_id("legacy-id", "20260920"), "20260920-1")


class TestIsLocalImage(_DraftTestCase):
    def test_markdown_local_file(self):
        line = f"![说明](file:{self.image})"
        self.assertEqual(is_local_image(line), self.image)

    def test_bare_local_path(self):
        self.assertEqual(is_local_image(str(self.image)), self.image)

    def test_remote_link_is_not_local(self):
        self.assertIsNone(is_local_image("![说明](https://cdn.example.com/a.png)"))
        self.assertIsNone(is_local_image("https://cdn.example.com/a.png"))

    def test_missing_file_is_not_local(self):
        self.assertIsNone(is_local_image(str(self.tmp_dir / "ghost.png")))

    def test_trailing_text_is_not_image(self):
        self.assertIsNone(is_local_image(f"看图：{self.image}"))


class TestRewriteBody(_DraftTestCase):
    def test_replaces_local_image_with_remote_url_and_keeps_alt(self):
        body = ["正文", f"![截图](file:{self.image})"]
        mapping = {str(self.image.resolve()): "https://cdn.example.com/a.png"}
        self.assertEqual(
            rewrite_body(body, mapping, embed_local=False),
            ["正文", "![截图](https://cdn.example.com/a.png)"],
        )

    def test_embeds_local_image_as_data_uri_for_preview(self):
        result = rewrite_body([str(self.image)], None, embed_local=True)
        payload = base64.b64encode(self.image.read_bytes()).decode("ascii")
        self.assertEqual(result, [f"![](data:image/png;base64,{payload})"])

    def test_remote_lines_untouched(self):
        body = ["https://cdn.example.com/a.png", "普通文字"]
        self.assertEqual(rewrite_body(body, {}, embed_local=False), body)


class TestBodyToHtml(_DraftTestCase):
    def test_image_line_becomes_img_tag(self):
        html = body_to_html([f"![截图](file:{self.image})"])
        self.assertIn('<img class="alas-announcement-image"', html)
        self.assertIn('alt="截图"', html)

    def test_text_is_escaped_and_grouped(self):
        html = body_to_html(["<script>alert(1)</script>", "第二行"])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(html.count('class="alas-announcement-text"'), 1)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;\n第二行", html)

    def test_blank_lines_do_not_create_empty_blocks(self):
        html = body_to_html(["文字", "", "  ", "更多"])
        self.assertEqual(html.count('class="alas-announcement-text"'), 1)


class TestWritePreview(_DraftTestCase):
    def test_preview_contains_payload_and_both_themes(self):
        out = self.tmp_dir / "preview.html"
        payload = {
            "announcementId": "20260920-1",
            "title": "标题",
            "content": "正文",
            "url": "",
        }
        write_preview("标题", "20260920-1", ["正文"], payload, out)
        html = out.read_text(encoding="utf-8")
        self.assertIn("亮色主题", html)
        self.assertIn("暗色主题", html)
        self.assertIn(json.dumps("20260920-1", ensure_ascii=False), html)
        self.assertIn("正文", html)


class TestNoHardcodedEnvironment(unittest.TestCase):
    """守护用例：本模块会进公共仓库，不得写死本机用户目录或绝对路径。

    （2026-09-20 事故：初版把 `C:\\Users\\<用户名>\\...\\git-credential-manager.exe`
     直接写进常量，提交后才发现泄漏了本机环境信息、且换台机器就不可用。）
    """

    def test_source_has_no_home_path(self):
        source = Path(announcement_publish.__file__).read_text(encoding="utf-8")
        home = str(Path.home())
        self.assertNotIn(home, source)
        # 转义形式（"C:\\Users\\..."）同样不允许
        self.assertNotIn(home.replace("\\", "\\\\"), source)

    def test_resolvers_return_usable_values(self):
        self.assertTrue(announcement_publish.find_git())
        self.assertTrue(announcement_publish.find_credential_helper())

    def test_resolved_paths_use_forward_slashes(self):
        """回归：git 的 -c 配置值会吃掉反斜杠，探测结果必须是正斜杠路径。

        （2026-09-20 实测：探测返回 `C:\\Users\\...\\git-credential-manager.EXE` 时，
         git 把路径解析成 `C:Users...` 并报 command not found，导致取不到凭据。）
        """
        for value in (
            announcement_publish.find_git(),
            announcement_publish.find_credential_helper(),
            announcement_publish._to_posix_path(r"C:\Users\a\b.exe"),
        ):
            self.assertNotIn("\\", value)

    def test_to_posix_path(self):
        self.assertEqual(
            announcement_publish._to_posix_path(r"C:\Users\x\gcm.exe"),
            "C:/Users/x/gcm.exe",
        )


if __name__ == "__main__":
    unittest.main()
