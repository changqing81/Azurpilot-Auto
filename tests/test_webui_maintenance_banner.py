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
    "state_open": "服务器已开服",
    "desc": "模拟器已关闭，等待开服",
    "desc_running": "模拟器保持运行，等待开服",
    "desc_open": "正在启动模拟器并登录",
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

    def test_normalize_keeps_opened_within_ttl(self):
        """开服后要在 TTL 内保留绿色横幅。"""
        state = {"opened": True, "finished_at": 1000}
        self.assertIs(mb.normalize(state, alive=True, now=1000 + mb.OPENED_TTL - 1), state)

    def test_normalize_drops_opened_after_ttl(self):
        state = {"opened": True, "finished_at": 1000}
        self.assertIsNone(mb.normalize(state, alive=True, now=1000 + mb.OPENED_TTL + 1))

    def test_normalize_drops_opened_when_dead(self):
        self.assertIsNone(
            mb.normalize({"opened": True, "finished_at": 1000}, alive=False, now=1000)
        )

    def test_normalize_drops_dead_instance(self):
        """进程被杀不会清状态文件，实例已停止时必须隐藏横幅。"""
        self.assertIsNone(mb.normalize({"waiting": True}, alive=False))

    def test_normalize_keeps_alive_waiting(self):
        state = {"waiting": True, "since": 1}
        self.assertIs(mb.normalize(state, alive=True), state)


class TestServerText(unittest.TestCase):
    """服务器名在横幅上的显示规则（含空值降级）。"""

    def test_empty_forms_become_blank(self):
        """未填 / disabled / auto / 空白都不该显示成服务器名。"""
        for value in (None, "", "   ", "disabled", "DISABLED", "auto", "none"):
            self.assertEqual(mb._server_text(value), "", f"{value!r} 应为空")

    def test_real_name_kept(self):
        self.assertEqual(mb._server_text("奥林匹克行动"), "奥林匹克行动")
        self.assertEqual(mb._server_text("  Olympic  "), "Olympic")

    def test_js_hides_server_span_when_blank(self):
        js = mb.build_js(
            {"waiting": True, "emulator_closed": True, "server": "", "since": 1, "last_poll": 2},
            LABELS,
        )
        self.assertIn("srv.style.display='none'", js)

    def test_js_shows_server_when_present(self):
        js = mb.build_js(
            {
                "waiting": True,
                "emulator_closed": True,
                "server": "奥林匹克行动",
                "since": 1,
                "last_poll": 2,
            },
            LABELS,
        )
        self.assertIn("奥林匹克行动", js)


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

    def test_desc_switches_by_emulator_closed(self):
        """B 部分：模拟器关闭中 / 保持运行要显示不同描述。"""
        closed = mb.build_js(
            {"waiting": True, "emulator_closed": True, "since": 1, "last_poll": 2}, LABELS
        )
        self.assertIn(LABELS["desc"], closed)
        self.assertNotIn(LABELS["desc_running"], closed)

        running = mb.build_js(
            {"waiting": True, "emulator_closed": False, "since": 1, "last_poll": 2}, LABELS
        )
        self.assertIn(LABELS["desc_running"], running)
        self.assertNotIn(LABELS["desc"], running)

    def test_opened_js_uses_green_state_and_auto_hides(self):
        """C 部分：开服态用绿色 class、显示「已开服」，并按 TTL 自动收起。"""
        js = mb.build_js({"opened": True, "since": 1000, "finished_at": 1001}, LABELS)
        self.assertIn(LABELS["state_open"], js)
        self.assertIn(LABELS["desc_open"], js)
        self.assertIn("is-opened", js)
        self.assertIn("open_ttl", js)

    def test_show_js_sets_text_and_ticks_every_second(self):
        js = mb.build_js(
            {
                "waiting": True,
                "emulator_closed": True,
                "since": 1000,
                "last_poll": 1030,
                "poll_seconds": 60,
            },
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
