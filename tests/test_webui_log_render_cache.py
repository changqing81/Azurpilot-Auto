"""RichLog 日志渲染缓存的行为测试。

HTML 片段缓存按（宽度、对象 id）键控、以对象同一性命中，
切换页面后的全量首显只渲染未缓存的新增日志。
"""

import unittest
from unittest.mock import patch

from rich.text import Text

from module.webui.widgets import RichLog


class TestRichLogRenderCache(unittest.TestCase):
    def setUp(self):
        RichLog._html_cache.clear()

    def tearDown(self):
        RichLog._html_cache.clear()

    def test_cache_hit_skips_rerender(self):
        """同一 renderable 再次渲染命中缓存，不再调用 rich 转换。"""
        log = RichLog("log")
        renderable = Text("hello cache\n")
        first = log.render_cached([renderable])

        with patch.object(log, "render_many") as mock_render:
            second = log.render_cached([renderable])
        mock_render.assert_not_called()
        self.assertEqual(first, second)

    def test_cache_miss_renders_and_stores(self):
        log = RichLog("log")
        renderable = Text("world cache\n")
        html = log.render_cached([renderable])
        self.assertIn("world cache", html)
        key = (log.console.width, id(renderable))
        self.assertIn(key, RichLog._html_cache)
        self.assertIs(RichLog._html_cache[key][0], renderable)

    def test_reused_id_does_not_hit_stale_entry(self):
        """缓存条目必须校验对象同一性：键相同但对象不同时不得误命中。

        模拟 id 复用场景：条目键为 (width, id)，条目内的对象引用被换成
        另一个对象（同键不同对象），渲染必须走 miss 重新转换。
        """
        log = RichLog("log")
        renderable = Text("first\n")
        html_first = log.render_cached([renderable])
        key = (log.console.width, id(renderable))

        with patch.object(log, "render_many", return_value="RENDERED") as mock_render:
            # 同键但对象引用不同（id 被复用的假象）
            RichLog._html_cache[key] = (object(), "STALE")
            html_second = log.render_cached([renderable])
        mock_render.assert_called_once()
        self.assertEqual(html_second, "RENDERED")
        self.assertNotEqual(html_first, html_second)

    def test_cache_is_bounded(self):
        """缓存超出上限时淘汰最旧条目，不会随日志量无限增长。"""
        log = RichLog("log")
        limit = RichLog._html_cache_limit
        for i in range(limit + 10):
            log.render_cached([Text(f"line {i}\n")])
        self.assertLessEqual(len(RichLog._html_cache), limit)


if __name__ == "__main__":
    unittest.main()
