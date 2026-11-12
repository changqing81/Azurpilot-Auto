"""维护等待横幅的单元测试。

被测对象是零依赖的 `module.webui.maintenance_banner`——刻意不导入
app_dashboard / app_overview：它们的 import 链带 WebUI 全家桶副作用，
会污染同进程的 pywebio 会话注册表（见 test_webui_overview_actions.py 的说明）。

背景（2026-11-12）：服务器维护期间每 30 秒一条 Watchdog 误报，用户看不清
「到底在等什么」。修复分两层：实例侧写状态文件（alas.py），WebUI 侧读文件
点亮横幅。这里锁住读取、归一化与 DOM 脚本三条纯逻辑。
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from module.webui import maintenance_banner as mb

LABELS = {
    "state": "服务器维护中",
    "desc": "模拟器已关闭，等待开服",
    "elapsed": "已等待",
    "next": "下次查询",
}


class TestReadAndNormalize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self._cwd)
        os.makedirs("log", exist_ok=True)

    def _write(self, name, payload):
        with open(mb.state_path(name), "w", encoding="utf-8") as f:
            f.write(payload if isinstance(payload, str) else json.dumps(payload))

    def test_missing_file_returns_none(self):
        self.assertIsNone(mb.read_state("alas"))

    def test_broken_json_returns_none(self):
        self._write("alas", "{not json")
        self.assertIsNone(mb.read_state("alas"))

    def test_read_valid_state(self):
        self._write("alas", {"waiting": True, "since": 1, "last_poll": 2})
        self.assertEqual(mb.read_state("alas")["since"], 1)

    def test_normalize_drops_not_waiting(self):
        self.assertIsNone(mb.normalize({"waiting": False}, alive=True))

    def test_normalize_drops_dead_instance(self):
        """进程被杀不会清状态文件，实例已停止时必须隐藏横幅。"""
        self.assertIsNone(mb.normalize({"waiting": True}, alive=False))

    def test_normalize_keeps_alive_waiting(self):
        state = {"waiting": True, "since": 1}
        self.assertIs(mb.normalize(state, alive=True), state)


class TestDomScript(unittest.TestCase):
    def test_html_hidden_by_default(self):
        html = mb.build_html(LABELS)
        self.assertIn(mb.BANNER_ID, html)
        self.assertIn("display:none", html)
        self.assertIn(LABELS["elapsed"], html)
        self.assertIn(LABELS["next"], html)

    def test_hide_js_hides_and_clears_timer(self):
        js = mb.build_js(None, LABELS)
        self.assertIn("display='none'", js)
        self.assertIn("clearInterval", js)

    def test_show_js_sets_text_and_ticks_every_second(self):
        js = mb.build_js(
            {"waiting": True, "since": 1000, "last_poll": 1030, "poll_seconds": 60},
            LABELS,
        )
        self.assertIn(LABELS["state"], js)
        self.assertIn(LABELS["desc"], js)
        self.assertIn("setInterval(tick,1000)", js)
        # 倒计时目标 = last_poll + poll_seconds
        self.assertIn(str((1030 + 60) * 1000), js)
        self.assertIn(str(1000 * 1000), js)


if __name__ == '__main__':
    unittest.main()
