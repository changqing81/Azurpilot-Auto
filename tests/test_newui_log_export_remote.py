"""新 UI 日志导出（远控）回归测试。

覆盖 2026-11-20 事故：远控地址下点「导出今日日志」弹 `导出失败：Error: HTTP 400`。

实测（remurl 中继 /89982203008c27b2）：
    200  GET /api/log/runtime/alas/today
    400  GET /api/log/runtime?instance=alas&scope=today   → {"error":"缺少实例名"}

中继会把 query string 整段剥掉，后端拿不到 instance 就直接 400。
同一个坑 2026-09-15 在老 UI 踩过（修法见 app_overview.py:104、
app_developer_tools.py:566-602：path 形式排第一 + 多候选逐个试），
新 UI 这三处又退回了只传 query，于是复发。

这里用源码断言锁住「path 优先 + 不用 window.open + 不调不存在的路由」，
因为本机没有前端测试框架，改动很容易在下次重构时被无声改回去。

⚠️ 断言一律在**去掉注释后的代码**上做：这几处代码的注释里就写着反例
（"以前是 /api/log/runtime?instance=… 会 400"），直接对全文 indexOf 会误判。
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend" / "src"

LOG_EXPORT = FRONTEND / "api" / "logExport.ts"
HOME = FRONTEND / "pages" / "home" / "index.tsx"
TOOL_LOG = FRONTEND / "pages" / "tool" / "log.tsx"
TOOL_INDEX = FRONTEND / "pages" / "tool" / "index.tsx"


def strip_comments(source: str) -> str:
    """去掉 TS 的 // 行注释与 /* */ 块注释，只留可执行代码。

    注释里会引用错误写法作为说明（例如老 bug 的 URL），
    必须排除掉，否则"不许出现 query 形式"这类断言会误报。
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    lines = []
    for line in source.splitlines():
        # 只处理行首/代码后面紧跟的 //；字符串里的 http:// 不算
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        lines.append(line)
    return "\n".join(lines)


class TestLogExportModule(unittest.TestCase):
    """api/logExport.ts：候选 URL 必须 path 在前、query 在后。"""

    def setUp(self):
        self.assertTrue(LOG_EXPORT.is_file(), "缺少 frontend/src/api/logExport.ts")
        self.code = strip_comments(LOG_EXPORT.read_text(encoding="utf-8"))

    def test_runtime_path_form_comes_first(self):
        """path 形式必须排在 query 形式之前。

        顺序反了等于没修：query 形式在直连时也会 200，fetchFirst 拿到第一个
        200 就收工；远控下则会 400 把正确的候选挡住。
        """
        path_frag = "/api/log/runtime/${inst}/${sc}"
        query_frag = "/api/log/runtime?instance="
        self.assertIn(path_frag, self.code)
        self.assertIn(query_frag, self.code)
        self.assertLess(
            self.code.index(path_frag),
            self.code.index(query_frag),
            "path 形式必须排在 query 形式之前",
        )

    def test_error_log_path_form_comes_first(self):
        path_frag = "/api/log/error/${sc}"
        query_frag = "/api/log/error?scope="
        self.assertIn(path_frag, self.code)
        self.assertLess(self.code.index(path_frag), self.code.index(query_frag))

    def test_has_three_runtime_candidates(self):
        """三个候选缺一不可：老 UI 就是这三条。"""
        for frag in (
            "/api/log/runtime/${inst}/${sc}",
            "/api/log/runtime?instance=${inst}&scope=${sc}",
            "/api/log/runtime/${inst}?scope=${sc}",
        ):
            self.assertIn(frag, self.code, f"缺候选：{frag}")

    def test_error_detail_is_surfaced(self):
        """失败时必须带后端的中文原因，不能只显示裸 HTTP 400。"""
        self.assertIn("responseErrorDetail", self.code)
        self.assertIn(".error", self.code, "需要读取后端 JSON 的 error 字段")

    def test_uses_prefix_wrapper(self):
        """所有请求都要过 withPrefix，否则远控中继不认路径。"""
        self.assertIn("withPrefix", self.code)


class TestHomeExportToday(unittest.TestCase):
    """总览页「导出今日日志」。"""

    def setUp(self):
        self.code = strip_comments(HOME.read_text(encoding="utf-8"))

    def test_no_bare_query_call(self):
        self.assertNotIn(
            "/api/log/runtime?instance=",
            self.code,
            "总览页不许直接拼 query 形式（中继会剥掉，实测 400）",
        )

    def test_scope_is_today_literal(self):
        """scope 交给服务端解析，不能用客户端 new Date() 算。

        远控时手机时区/时钟可能和服务器不一致，会算成别的天（导错或 404）。
        """
        self.assertIn("downloadRuntimeLog(instance, 'today')", self.code)

    def test_uses_shared_download_channel(self):
        self.assertIn("from '../../api/logExport'", self.code)


class TestToolLogExport(unittest.TestCase):
    """工具页「导出今日日志」。"""

    def setUp(self):
        self.code = strip_comments(TOOL_LOG.read_text(encoding="utf-8"))

    def test_no_window_open(self):
        """window.open 在远控下打开的是裸 URL，不带 /{peer} 前缀 → 中继 502。"""
        self.assertNotIn("window.open(", self.code)

    def test_no_bare_query_call(self):
        self.assertNotIn("/api/log/runtime?instance=", self.code)

    def test_uses_shared_download_channel(self):
        self.assertIn("downloadRuntimeLog", self.code)


class TestToolPageDownloads(unittest.TestCase):
    """工具页三个下载按钮。"""

    def setUp(self):
        self.code = strip_comments(TOOL_INDEX.read_text(encoding="utf-8"))

    def test_no_nonexistent_text_route(self):
        """`/api/log/text` 这个路由根本不存在（后端只有 /api/log/runtime 和
        /api/log/error，text 是 error 的 scope），以前点了必 404。"""
        self.assertNotIn("/api/log/text", self.code)

    def test_error_text_uses_scope_route(self):
        self.assertIn("/api/log/error/text", self.code)
        self.assertIn("/api/log/error/full", self.code)

    def test_no_url_search_params_for_log(self):
        """new URLSearchParams 拼出来的全是 query，远控会被剥掉。"""
        self.assertNotIn("new URLSearchParams({ instance", self.code)


class TestNoRegressionInOldUi(unittest.TestCase):
    """老 UI 的正确写法不能被顺手改掉。"""

    def test_old_overview_still_path_first(self):
        text = (REPO_ROOT / "module" / "webui" / "app_overview.py").read_text(
            encoding="utf-8"
        )
        path_frag = "'/api/log/runtime/' + enc + '/today'"
        query_frag = "'/api/log/runtime?instance=' + enc"
        self.assertIn(path_frag, text)
        self.assertIn(query_frag, text)
        self.assertLess(
            text.index(path_frag),
            text.index(query_frag),
            "老 UI 的导出候选顺序被改坏了会让远控静默导错内容",
        )


if __name__ == "__main__":
    unittest.main()
