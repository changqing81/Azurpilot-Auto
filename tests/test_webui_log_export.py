"""日志导出（当天运行日志 / log/error 压缩包）与设置页保存按钮置顶的回归测试。

覆盖三层：
1. log_export 纯逻辑（实例校验、当天日志定位、错误日志打包）
2. api 路由（参数校验、404、Content-Disposition、临时文件清理）
3. 界面结构（工具页日志导出面板、设置页保存按钮位于顶部）
"""

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from module.webui.fake_pil_module import remove_fake_pil_module

remove_fake_pil_module()

from starlette.routing import Route
from starlette.applications import Starlette
from starlette.testclient import TestClient

from module.webui import log_export
from module.webui import api as webui_api


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_log_app():
    """只挂日志导出相关的路由，避免引入 pywebio 全套页面依赖。"""
    routes = [
        route
        for route in webui_api.api_routes
        if isinstance(route, Route) and route.path.startswith("/api/log/")
    ]
    assert routes, "未找到 /api/log/* 路由"
    return Starlette(routes=routes)


class TestLogExportLogic(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _patch_root(self):
        return patch.object(log_export, "get_project_root", return_value=self.root)

    # ---------- 项目根与日期 ----------

    def test_real_project_root_contains_module_and_log(self):
        root = log_export.get_project_root()
        self.assertTrue((root / "module").is_dir())
        self.assertTrue((root / "log").is_dir())

    def test_today_str_matches_log_rotation_prefix(self):
        value = log_export.today_str()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}$")

    # ---------- 实例名校验 ----------

    def test_validate_instance_accepts_existing_instance(self):
        from module.config.utils import alas_instance

        instances = alas_instance()
        self.assertTrue(instances)
        self.assertEqual(log_export.validate_instance(instances[0]), instances[0])
        # 两侧空白应被裁掉
        self.assertEqual(log_export.validate_instance(f"  {instances[0]}  "), instances[0])

    def test_validate_instance_rejects_empty_and_path_traversal(self):
        for bad in ("", None, "   ", "../etc/passwd", "..", "a/b", "template"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    log_export.validate_instance(bad)

    # ---------- 当天运行日志定位 ----------

    def test_find_today_runtime_log_prefers_today(self):
        log_dir = self.root / "log"
        log_dir.mkdir(parents=True)
        today = log_dir / f"{log_export.today_str()}_alas.txt"
        today.write_text("today", encoding="utf-8")
        (log_dir / "2000-01-01_alas.txt").write_text("old", encoding="utf-8")

        with self._patch_root():
            self.assertEqual(log_export.find_today_runtime_log("alas"), today)

    def test_find_today_runtime_log_falls_back_to_latest_history(self):
        log_dir = self.root / "log"
        log_dir.mkdir(parents=True)
        old = log_dir / "2000-01-01_alas.txt"
        old.write_text("old", encoding="utf-8")
        newer = log_dir / "2000-01-02_alas.txt"
        newer.write_text("newer", encoding="utf-8")
        # 其他实例的文件不应被选中
        (log_dir / "2000-06-06_小号.txt").write_text("other", encoding="utf-8")

        with self._patch_root():
            self.assertEqual(log_export.find_today_runtime_log("alas"), newer)

    def test_find_today_runtime_log_falls_back_to_base_file(self):
        log_dir = self.root / "log"
        log_dir.mkdir(parents=True)
        base = log_dir / "alas.txt"
        base.write_text("base", encoding="utf-8")

        with self._patch_root():
            self.assertEqual(log_export.find_today_runtime_log("alas"), base)

    def test_find_today_runtime_log_returns_none_when_absent(self):
        (self.root / "log").mkdir(parents=True)
        with self._patch_root():
            self.assertIsNone(log_export.find_today_runtime_log("alas"))

    # ---------- 错误日志打包 ----------

    def _make_error_tree(self):
        error_dir = self.root / "log" / "error"
        (error_dir / "alas" / "1788282433215").mkdir(parents=True)
        (error_dir / "alas" / "1788282433215" / "log.txt").write_text(
            "boom", encoding="utf-8"
        )
        (error_dir / "alas" / "1788282433215" / "shot.png").write_bytes(b"\x89PNG\r\n")
        (error_dir / "小号").mkdir(parents=True)
        (error_dir / "小号" / "log.txt").write_text("boom2", encoding="utf-8")
        # 顶层散落文件
        (error_dir / "stray.jpg").write_bytes(b"\xff\xd8\xff")
        return error_dir

    def test_build_error_log_zip_preserves_structure_and_stores_images(self):
        self._make_error_tree()

        with self._patch_root():
            zip_path = log_export.build_error_log_zip()
        self.addCleanup(zip_path.unlink, True)

        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            self.assertIn("alas/1788282433215/log.txt", names)
            self.assertIn("alas/1788282433215/shot.png", names)
            self.assertIn("小号/log.txt", names)
            self.assertIn("stray.jpg", names)
            self.assertEqual(
                archive.getinfo("alas/1788282433215/shot.png").compress_type,
                zipfile.ZIP_STORED,
            )
            self.assertEqual(
                archive.getinfo("stray.jpg").compress_type, zipfile.ZIP_STORED
            )
            self.assertEqual(
                archive.getinfo("alas/1788282433215/log.txt").compress_type,
                zipfile.ZIP_DEFLATED,
            )
            self.assertEqual(
                archive.read("alas/1788282433215/log.txt").decode("utf-8"), "boom"
            )

    def test_build_error_log_zip_missing_dir_raises(self):
        (self.root / "log").mkdir(parents=True)
        with self._patch_root():
            with self.assertRaises(FileNotFoundError):
                log_export.build_error_log_zip()

    def test_build_error_log_zip_empty_dir_creates_valid_zip(self):
        (self.root / "log" / "error").mkdir(parents=True)

        with self._patch_root():
            zip_path = log_export.build_error_log_zip()
        self.addCleanup(zip_path.unlink, True)

        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path) as archive:
            self.assertEqual(archive.namelist(), [])
            self.assertIsNone(archive.testzip())

    def test_format_bytes(self):
        self.assertEqual(log_export.format_bytes(0), "0 B")
        self.assertEqual(log_export.format_bytes(512), "512 B")
        self.assertEqual(log_export.format_bytes(2048), "2.0 KB")
        self.assertEqual(log_export.format_bytes(5 * 1024 * 1024), "5.0 MB")


class TestLogExportApi(unittest.TestCase):
    """远控可达性相关的关键行为：两个接口都不得被本机限制拦下。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.client = TestClient(build_log_app())

    def tearDown(self):
        self._tmp.cleanup()

    def test_log_routes_are_registered(self):
        paths = {route.path for route in build_log_app().routes}
        self.assertIn("/api/log/runtime", paths)
        self.assertIn("/api/log/runtime/{instance}", paths)
        self.assertIn("/api/log/error", paths)

    def test_log_handlers_are_not_local_only(self):
        """远控经 P2P 代理进来也必须可用，因此不得使用 is_local_request 门禁。"""
        for handler in (webui_api.api_log_runtime, webui_api.api_log_error_archive):
            source = handler.__code__.co_names
            self.assertNotIn("is_local_request", source)

    def test_runtime_log_rejects_invalid_instance(self):
        response = self.client.get("/api/log/runtime?instance=..%2Fetc")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_runtime_log_missing_file_returns_404(self):
        with patch.object(webui_api, "find_today_runtime_log", return_value=None):
            response = self.client.get("/api/log/runtime?instance=alas")
        self.assertEqual(response.status_code, 404)
        self.assertIn("alas", response.json()["error"])

    def _runtime_log_file(self, name=None):
        path = self.root / (name or f"{log_export.today_str()}_alas.txt")
        path.write_text("line1\nline2\n", encoding="utf-8")
        return path

    def test_runtime_log_ok_sets_attachment_filename(self):
        log_file = self._runtime_log_file(f"{log_export.today_str()}_小号.txt")
        with (
            patch.object(webui_api, "validate_instance", return_value="小号"),
            patch.object(webui_api, "find_today_runtime_log", return_value=log_file),
        ):
            response = self.client.get("/api/log/runtime?instance=小号")

        self.assertEqual(response.status_code, 200)
        disposition = response.headers["content-disposition"]
        self.assertIn("attachment", disposition)
        # 非 ASCII 实例名走 RFC 5987，前端据此还原文件名
        self.assertIn("filename*=utf-8''", disposition)
        self.assertIn(quote(log_file.name), disposition)
        # 原样透传文件字节（Windows 上文本模式写文件会带 CRLF，按字节比对）
        self.assertEqual(response.content, log_file.read_bytes())

    def test_runtime_log_keeps_actual_date_when_falling_back(self):
        """回退到历史日志时，文件名必须沿用磁盘上的真实日期，不能冒充今天。"""
        old = self._runtime_log_file("2026-09-11_alas.txt")
        with patch.object(webui_api, "find_today_runtime_log", return_value=old):
            response = self.client.get("/api/log/runtime?instance=alas")

        self.assertEqual(response.status_code, 200)
        disposition = response.headers["content-disposition"]
        self.assertIn("2026-09-11_alas.txt", disposition)
        self.assertNotIn(log_export.today_str(), disposition)

    def test_runtime_log_accepts_instance_in_path(self):
        log_file = self._runtime_log_file()
        with (
            patch.object(webui_api, "validate_instance", return_value="小号") as guard,
            patch.object(webui_api, "find_today_runtime_log", return_value=log_file),
        ):
            response = self.client.get(f"/api/log/runtime/{quote('小号')}")

        self.assertEqual(response.status_code, 200)
        # query 被远控链路剥掉时，实例名仍能从 path 里取到
        guard.assert_called_once_with("小号")

    def test_error_archive_missing_dir_returns_404(self):
        with patch.object(
            webui_api, "build_error_log_zip", side_effect=FileNotFoundError("no dir")
        ):
            response = self.client.get("/api/log/error")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    def test_error_archive_ok_sets_zip_attachment_and_cleans_temp(self):
        zip_path = self.root / "archive.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("alas/log.txt", "boom")

        with patch.object(webui_api, "build_error_log_zip", return_value=zip_path):
            response = self.client.get("/api/log/error")

        self.assertEqual(response.status_code, 200)
        disposition = response.headers["content-disposition"]
        self.assertIn("attachment", disposition)
        self.assertIn(
            f"AzurPilot-error-logs-{log_export.today_str()}.zip", disposition
        )
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.read("alas/log.txt").decode("utf-8"), "boom")
        # 响应发完后由 BackgroundTask 删除临时压缩包，不留垃圾
        self.assertFalse(zip_path.exists())

    def test_error_archive_oserror_returns_500(self):
        with patch.object(
            webui_api, "build_error_log_zip", side_effect=OSError("disk full")
        ), patch.object(webui_api.logger, "error"):
            response = self.client.get("/api/log/error")
        self.assertEqual(response.status_code, 500)


class TestLogExportPanel(unittest.TestCase):
    """工具页面板与设置页按钮位置：锁住用户明确要求的界面形态。"""

    def _render_panel(self, alas_name):
        import module.webui.app_developer_tools as tools

        captured = {"html": [], "js": []}
        with (
            patch.object(tools, "put_html", lambda html, *a, **k: captured["html"].append(html)),
            patch.object(tools, "run_js", lambda js, *a, **k: captured["js"].append(js)),
            patch.object(tools, "t", lambda s, *a, **k: s),
        ):
            stub = type("Stub", (), {"alas_name": alas_name})()
            tools.DeveloperToolsMixin._render_log_export_panel(stub)
        return "\n".join(captured["html"]), "\n".join(captured["js"])

    def test_panel_contains_instance_select_and_both_buttons(self):
        html, js = self._render_panel("alas")
        for element_id in (
            "log-export-instance",
            "log-export-runtime",
            "log-export-error",
            "log-export-status",
        ):
            self.assertIn(element_id, html)
        # 下拉来自真实实例列表，当前实例默认选中
        from module.config.utils import alas_instance

        instances = alas_instance()
        for name in instances:
            self.assertIn(f'value="{name}"', html)
        self.assertIn('value="alas" selected', html)

        self.assertIn("/api/log/runtime", js)
        self.assertIn("/api/log/error", js)
        # 远控：query 与 path 两种取法都要在
        self.assertIn("/api/log/runtime/' + enc", js)

    def test_panel_js_is_valid_javascript(self):
        _, js = self._render_panel("alas")
        # f-string 里 JS 花括号必须双写，渲染后不应残留 {{ 或 }}
        self.assertNotIn("{{", js)
        self.assertNotIn("}}", js)
        # 正则中的转义要正确到达前端
        self.assertIn("filename\\*=", js)
        self.assertIn("{8,}", js)

    def test_panel_falls_back_to_first_instance_without_current(self):
        html, _ = self._render_panel("")
        from module.config.utils import alas_instance

        first = alas_instance()[0]
        self.assertIn(f'value="{first}" selected', html)


class TestDeploySettingLayout(unittest.TestCase):
    """设置页「保存设置」按钮必须在字段之前（顶部），不能退回底部。"""

    def setUp(self):
        self.source = (
            PROJECT_ROOT / "module" / "webui" / "app_developer_settings.py"
        ).read_text(encoding="utf-8")

    def test_save_button_is_above_fields(self):
        save_at = self.source.index('id="deploy-setting-save"')
        fields_at = self.source.index('id="deploy-setting-fields"')
        refresh_at = self.source.index('id="deploy-setting-refresh"')
        self.assertLess(save_at, fields_at)
        # 与「重新读取」同排在工具栏里
        self.assertLess(refresh_at, fields_at)
        self.assertLess(abs(save_at - refresh_at), 400)

    def test_bottom_actions_row_is_removed(self):
        self.assertNotIn("deploy-setting-actions", self.source)

    def test_js_hook_ids_are_untouched(self):
        import module.webui.app_developer_settings  # noqa: F401

        for element_id in (
            "deploy-setting-save",
            "deploy-setting-refresh",
            "deploy-setting-notice",
            "deploy-setting-fields",
            "deploy-setting-status",
        ):
            self.assertIn(f"getElementById('{element_id}')", self.source)


if __name__ == "__main__":
    unittest.main()
