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

    # ---------- 打包范围与体积统计 ----------

    def test_normalize_scope_accepts_known_and_falls_back(self):
        self.assertEqual(log_export.normalize_scope("full"), "full")
        self.assertEqual(log_export.normalize_scope("TEXT"), "text")
        self.assertEqual(log_export.normalize_scope(" text "), "text")
        for bad in (None, "", "bogus", "../full", "text;rm -rf"):
            with self.subTest(bad=bad):
                self.assertEqual(log_export.normalize_scope(bad), "full")

    def test_describe_error_log_dir_full_counts_everything(self):
        self._make_error_tree()
        with self._patch_root():
            data = log_export.describe_error_log_dir()

        self.assertEqual(data["scope"], "full")
        # alas/.../log.txt、alas/.../shot.png、小号/log.txt、stray.jpg
        self.assertEqual(data["files"], 4)
        self.assertGreater(data["bytes"], 0)
        # 压缩估计不会超过原始体积（图片是 STORED，文本才压）
        self.assertLess(data["estimate_bytes"], data["bytes"])
        self.assertTrue(data["human_bytes"])
        self.assertTrue(data["human_estimate"])

    def test_describe_error_log_dir_text_scope_skips_images(self):
        self._make_error_tree()
        with self._patch_root():
            full = log_export.describe_error_log_dir("full")
            text = log_export.describe_error_log_dir("text")

        self.assertEqual(text["scope"], "text")
        self.assertEqual(text["files"], 2)  # 只算两个 log.txt
        self.assertLess(text["bytes"], full["bytes"])

    def test_describe_error_log_dir_missing_dir_raises(self):
        (self.root / "log").mkdir(parents=True)
        with self._patch_root():
            with self.assertRaises(FileNotFoundError):
                log_export.describe_error_log_dir()

    def test_describe_error_log_dir_real_data_is_usable(self):
        """真实 log/error 上跑一遍，确认统计口径与打包口径一致（无数据则跳过）。"""
        real_dir = log_export.get_project_root() / "log" / "error"
        has_data = real_dir.is_dir() and any(p.is_file() for p in real_dir.rglob("*"))
        if not has_data:
            self.skipTest("本机 log/error 无数据，跳过真实数据检查")

        data = log_export.describe_error_log_dir()
        self.assertGreater(data["files"], 0)
        self.assertGreater(data["bytes"], 0)
        text = log_export.describe_error_log_dir("text")
        # 截图占大头，仅文本应该小得多
        self.assertLessEqual(text["bytes"], data["bytes"])

    def test_build_error_log_zip_text_scope_excludes_images(self):
        self._make_error_tree()
        with self._patch_root():
            zip_path = log_export.build_error_log_zip("text")
        self.addCleanup(zip_path.unlink, True)

        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
        self.assertIn("alas/1788282433215/log.txt", names)
        self.assertIn("小号/log.txt", names)
        self.assertNotIn("alas/1788282433215/shot.png", names)
        self.assertNotIn("stray.jpg", names)


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

    # ---------- 导出前体积统计 ----------

    def _info_payload(self):
        return {
            "scope": "full",
            "files": 67,
            "bytes": 24 * 1024 * 1024,
            "estimate_bytes": 20 * 1024 * 1024,
            "human_bytes": "24.0 MB",
            "human_estimate": "20.0 MB",
        }

    def test_error_info_route_is_registered(self):
        paths = {route.path for route in build_log_app().routes}
        self.assertIn("/api/log/error/info", paths)

    def test_error_info_returns_size_data(self):
        with patch.object(
            webui_api, "describe_error_log_dir", return_value=self._info_payload()
        ) as probe:
            response = self.client.get("/api/log/error/info?scope=full")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["files"], 67)
        self.assertEqual(data["human_estimate"], "20.0 MB")
        probe.assert_called_once_with("full")

    def test_error_info_normalizes_unknown_scope(self):
        with patch.object(
            webui_api, "describe_error_log_dir", return_value=self._info_payload()
        ) as probe:
            self.client.get("/api/log/error/info?scope=../etc")

        probe.assert_called_once_with("full")

    def test_error_info_missing_dir_returns_404(self):
        with patch.object(
            webui_api,
            "describe_error_log_dir",
            side_effect=FileNotFoundError("no dir"),
        ):
            response = self.client.get("/api/log/error/info")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    # ---------- 仅日志文本的轻量导出 ----------

    def test_error_archive_text_scope_uses_scope_and_names_file(self):
        zip_path = self.root / "text.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("alas/log.txt", "boom")

        with patch.object(
            webui_api, "build_error_log_zip", return_value=zip_path
        ) as builder:
            response = self.client.get("/api/log/error?scope=text")

        builder.assert_called_once_with("text")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AzurPilot-error-logs-text-", response.headers["content-disposition"])
        self.assertFalse(zip_path.exists())

    def test_error_archive_defaults_to_full_scope(self):
        zip_path = self.root / "full.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("alas/log.txt", "boom")

        with patch.object(
            webui_api, "build_error_log_zip", return_value=zip_path
        ) as builder:
            self.client.get("/api/log/error")

        builder.assert_called_once_with("full")


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
            "log-export-error-text",
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

    def test_panel_queries_real_size_before_exporting(self):
        """点导出前必须先查真实体积，并把文件数/大小填进确认框。"""
        _, js = self._render_panel("alas")
        self.assertIn("/api/log/error/info?scope=", js)
        self.assertIn("formatConfirm", js)
        self.assertIn("human_bytes", js)
        self.assertIn("human_estimate", js)
        self.assertIn("scope === 'text'", js)
        # 统计失败也要能继续导出，不能把功能卡死
        self.assertIn("text.confirm", js)

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


class TestLogExportI18n(unittest.TestCase):
    """锁住 Gui.LogExport 的 5 语言文案。

    两条容易踩的坑：
    1. 文案只在 i18n JSON 里改是不够的 —— 键必须声明在
       module/config/argument/gui.yaml，否则下一次 config_updater 会把它整体抹掉。
    2. t() 会对取到的字符串执行 .format()，运行时占位符必须写成 {{files}} 双花括号，
       写成单花括号会在渲染界面时抛 KeyError。
    """

    LANGUAGES = ("zh-CN", "en-US", "ja-JP", "zh-TW", "zh-MIAO")
    KEYS = (
        "Title",
        "InstanceLabel",
        "RuntimeLog",
        "ErrorLogs",
        "ErrorLogsText",
        "Pending",
        "Packing",
        "Started",
        "Failed",
        "NoInstance",
        "Confirm",
        "ConfirmSize",
        "InfoLoading",
        "NoFiles",
    )
    I18N_DIR = PROJECT_ROOT / "module" / "config" / "i18n"
    GUI_YAML = PROJECT_ROOT / "module" / "config" / "argument" / "gui.yaml"

    def _group(self, lang):
        data = json.loads((self.I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        return data["Gui"]["LogExport"]

    def test_all_languages_have_full_key_set(self):
        for lang in self.LANGUAGES:
            with self.subTest(lang=lang):
                group = self._group(lang)
                missing = [key for key in self.KEYS if not group.get(key)]
                self.assertEqual(missing, [], f"{lang} 缺少或为空的键")

    def test_keys_are_declared_in_gui_yaml(self):
        """gui.yaml 是 i18n 的唯一真源，缺声明就会被 config_updater 抹掉。"""
        declared = set()
        in_block = False
        for line in self.GUI_YAML.read_text(encoding="utf-8").splitlines():
            if line.startswith("LogExport:"):
                in_block = True
                continue
            if in_block:
                if line and not line.startswith("  "):
                    break
                stripped = line.strip().rstrip(":")
                if stripped:
                    declared.add(stripped)
        missing = [key for key in self.KEYS if key not in declared]
        self.assertEqual(missing, [], "gui.yaml 未声明这些键")

    def test_confirm_size_uses_doubled_braces(self):
        """单花括号会让 t() 的 .format() 抛 KeyError。"""
        for lang in self.LANGUAGES:
            with self.subTest(lang=lang):
                raw = self._group(lang)["ConfirmSize"]
                try:
                    formatted = raw.format()
                except (KeyError, IndexError) as e:
                    self.fail(f"{lang} 的 ConfirmSize 含单花括号占位符，t() 会炸: {e}")
                # 双花括号被 .format() 还原成单花括号，交给前端替换
                for token in ("{files}", "{size}", "{zip}"):
                    self.assertIn(token, formatted)
                # 证明原文确实是双花括号（否则 formatted 会与 raw 相同）
                self.assertNotEqual(raw, formatted)


if __name__ == "__main__":
    unittest.main()
