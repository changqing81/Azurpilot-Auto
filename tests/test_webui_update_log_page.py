"""更新器页面「更新日志」区块的装配与文案回归测试。

覆盖 2026-09-20 的更新器改造：
- `_render_update_log` 往 `updater_changelog` 作用域输出标题与占位，并注册异步任务
- 新增文案键在五种语言里都有真实翻译（防止界面直接显示 `Gui.Update.Changelog` 这种 key 路径）
"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from module.webui.app_developer_update import DeveloperUpdateMixin
from module.webui.update_log import render_entries_html

REPO_ROOT = Path(__file__).resolve().parent.parent
LANGUAGES = ("zh-CN", "zh-TW", "zh-MIAO", "en-US", "ja-JP")
NEW_KEYS = ("Changelog", "ChangelogLoading", "ChangelogEmpty")


class _FakeThread:
    """同步执行 target，避免测试里真的起线程。"""

    def __init__(self, target=None, daemon=None):
        self._target = target
        self.daemon = daemon

    def start(self):
        if self._target:
            self._target()


class _Harness(DeveloperUpdateMixin):
    def __init__(self):
        self.added = []
        self.task_handler = SimpleNamespace(add=self._add)

    def _add(self, generator, **kwargs):
        self.added.append((generator, kwargs))
        next(generator)  # 启动到第一个 yield 即停，不进入轮询循环


class TestRenderUpdateLog(unittest.TestCase):
    def setUp(self):
        self.text_calls = []
        self.html_calls = []
        self.patches = (
            patch(
                "module.webui.app_developer_update.put_text",
                side_effect=lambda *args, **kwargs: self.text_calls.append((args, kwargs)),
            ),
            patch(
                "module.webui.app_developer_update.put_html",
                side_effect=lambda *args, **kwargs: self.html_calls.append((args, kwargs)),
            ),
            patch(
                "module.webui.app_developer_update.t",
                side_effect=lambda key, **kwargs: key,
            ),
            patch("module.webui.app_developer_update.threading", SimpleNamespace(Thread=_FakeThread)),
            patch(
                "module.base.api_client.ApiClient.get_changelog",
                return_value={"entries": [{"title": "标题"}]},
            ),
        )
        for active in self.patches:
            active.start()
            self.addCleanup(active.stop)

    def test_writes_into_changelog_scope_and_registers_task(self):
        harness = _Harness()
        harness._render_update_log()

        # 区块标题已并入折叠行（由 render_entries_html 输出），不再单独 put_text，
        # 否则「更新日志」标题会和可点开的折叠行上下重复。
        self.assertFalse(self.text_calls, "不应再单独输出区块标题")

        self.assertTrue(self.html_calls, "应先输出加载中占位")
        args, html_kwargs = self.html_calls[0]
        self.assertEqual(html_kwargs.get("scope"), "updater_changelog")
        self.assertIn("Gui.Update.ChangelogLoading", args[0])

        self.assertEqual(len(harness.added), 1, "应把拉取结果的轮询任务挂到 task_handler")
        self.assertEqual(harness.added[0][1].get("delay"), 0.5)


class TestRenderEntriesHtml(unittest.TestCase):
    """折叠结构回归：整块一层折叠，避免更新日志把更新器页面越撑越长。"""

    ENTRIES = [
        {
            "id": "20260921-1",
            "date": "2026-09-21",
            "title": "新版本",
            "content": "正文",
            "sha": "3238fcbe6",
        },
        {
            "id": "20260920-2",
            "date": "2026-09-20",
            "title": "旧版本",
            "content": "旧正文",
            "sha": "a4fa3e33b",
        },
    ]

    def test_wrapped_in_collapsed_details(self):
        html_text = render_entries_html(self.ENTRIES, title="更新日志")

        # 整块默认收起：根 details 不带 open，常驻的只有一行 summary
        self.assertIn('<details class="update-log-root">', html_text)
        self.assertNotIn('<details class="update-log-root" open>', html_text)
        # 收起状态下也能看到标题与最新一条的日期/提交
        self.assertIn("更新日志", html_text)
        self.assertIn("2026-09-21", html_text)
        self.assertIn("3238fcbe6", html_text)
        # 条目各自仍可折叠，且只有第一条默认展开
        self.assertEqual(html_text.count('class="update-log-entry"'), 2)
        self.assertEqual(html_text.count('<details class="update-log-entry" open>'), 1)

    def test_empty_entries_returns_empty_string(self):
        self.assertEqual("", render_entries_html([]))


class TestChangelogI18n(unittest.TestCase):
    def test_new_keys_translated_in_all_languages(self):
        for language in LANGUAGES:
            path = REPO_ROOT / "module" / "config" / "i18n" / f"{language}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            update_group = data.get("Gui", {}).get("Update", {})
            for key in NEW_KEYS:
                self.assertIn(key, update_group, f"{language} 缺少 Gui.Update.{key}")
                value = str(update_group[key]).strip()
                self.assertTrue(value, f"{language} 的 Gui.Update.{key} 为空")
                self.assertFalse(
                    value.startswith("Gui."),
                    f"{language} 的 Gui.Update.{key} 还是 key 路径，未翻译",
                )


if __name__ == "__main__":
    unittest.main()
