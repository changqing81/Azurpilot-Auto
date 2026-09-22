"""公告配图本地化：白名单、缓存复用、失败降级、防路径穿越。"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from module.webui import announcement_images as ai


class TestAnnouncementImages(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="wb_annimg_"))
        self.addCleanup(shutil.rmtree, self.tmp_dir, True)
        patcher = patch.object(ai, "CACHE_DIR", self.tmp_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = (
            "https://cdn.jsdelivr.net/gh/changqing81/announcement-changelog"
            "@eb1b3cadf3cd71e4854376dbe1fae85929063e40/announcement/20260920-1/f6669cba.png"
        )

    def test_is_allowed_url(self):
        self.assertTrue(ai.is_allowed_url(self.url))
        self.assertTrue(
            ai.is_allowed_url(
                "https://raw.githubusercontent.com/changqing81/"
                "announcement-changelog/main/announcement/1/a.jpg"
            )
        )
        # 非白名单来源一律拒绝，避免变成任意 URL 代理
        for url in (
            "https://evil.example.com/x.png",
            "https://cdn.jsdelivr.net/gh/other/repo/x.png",
            "http://internal.example.com/secret.png",
        ):
            self.assertFalse(ai.is_allowed_url(url), url)

    def test_cache_name_is_content_addressed(self):
        name = ai.cache_name(self.url)
        self.assertTrue(ai.is_valid_name(name))
        self.assertTrue(name.endswith(".png"))
        self.assertEqual(name, ai.cache_name(self.url))
        self.assertNotEqual(name, ai.cache_name(self.url + "?v=2"))

    def test_is_valid_name_rejects_traversal(self):
        for bad in ("", "../x.png", "a/b.png", "x.png", "0" * 19 + ".png", "zz" * 10 + ".png", None):
            self.assertFalse(ai.is_valid_name(bad), repr(bad))

    def test_localize_replaces_url_and_reuses_cache(self):
        with patch.object(ai.requests, "get", autospec=True) as get:
            get.return_value = MagicMock(status_code=200, content=b"PNGDATA")
            content = f"公告正文\n\n![测试配图]({self.url})\n\n裸链接：\n{self.url}\n"
            out = ai.localize_images(content)

        self.assertIn("![测试配图](/api/announcement/image/", out)
        self.assertNotIn("jsdelivr", out)
        # 同一个 URL 出现两次，只下载一次
        self.assertEqual(get.call_count, 1)
        # 缓存文件已落地
        cached = list(self.tmp_dir.iterdir())
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0].read_bytes(), b"PNGDATA")

        # 再跑一次不应再发起请求（内容寻址，天然复用）
        with patch.object(ai.requests, "get", autospec=True) as get2:
            again = ai.localize_images(content)
            self.assertEqual(get2.call_count, 0)
        self.assertEqual(again, out)

    def test_download_failure_keeps_original_url(self):
        with patch.object(ai.requests, "get", autospec=True) as get:
            get.return_value = MagicMock(status_code=404, content=b"")
            out = ai.localize_images(f"![x]({self.url})")
        self.assertIn(self.url, out)
        self.assertEqual(list(self.tmp_dir.iterdir()), [])

    def test_exception_keeps_original_url(self):
        with patch.object(ai.requests, "get", autospec=True) as get:
            get.side_effect = OSError("boom")
            out = ai.localize_images(f"![x]({self.url})")
        self.assertIn(self.url, out)

    def test_oversized_image_skipped(self):
        with patch.object(ai, "MAX_BYTES", 4), patch.object(
            ai.requests, "get", autospec=True
        ) as get:
            get.return_value = MagicMock(status_code=200, content=b"12345")
            out = ai.localize_images(f"![x]({self.url})")
        self.assertIn(self.url, out)
        self.assertEqual(list(self.tmp_dir.iterdir()), [])

    def test_non_whitelisted_url_untouched(self):
        evil = "https://evil.example.com/a.png"
        with patch.object(ai.requests, "get", autospec=True) as get:
            out = ai.localize_images(f"![x]({evil})")
        self.assertIn(evil, out)
        self.assertEqual(get.call_count, 0)

    def test_cached_file_returns_none_for_bad_name(self):
        self.assertIsNone(ai.cached_file("../secret.png"))
        self.assertIsNone(ai.cached_file("deadbeef.png"))

    def test_empty_content(self):
        self.assertEqual(ai.localize_images(""), "")
        self.assertEqual(ai.localize_images(None), None)


if __name__ == "__main__":
    unittest.main()
