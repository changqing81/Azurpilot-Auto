"""数据导出（统计库 + 掉落记录 zip，全部/单实例）的回归测试。

覆盖三层：
1. data_export 纯逻辑（文件收集范围、sqlite 按实例过滤、zip 打包与目录结构）
2. api 路由（注册、无本机限制、范围参数校验、404/500、临时文件清理）
3. 界面结构（管理页「导出数据」菜单、范围下拉与下载面板的关键钩子）
"""

import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from module.webui.fake_pil_module import remove_fake_pil_module

remove_fake_pil_module()

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from module.webui import api as webui_api
from module.webui import data_export


def build_data_app():
    """只挂数据导出相关的路由，避免引入 pywebio 全套页面依赖。"""
    routes = [
        route
        for route in webui_api.api_routes
        if isinstance(route, Route) and route.path.startswith("/api/data/")
    ]
    assert routes, "未找到 /api/data/* 路由"
    return Starlette(routes=routes)


class TestDataExportLogic(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _patch_root(self):
        return patch.object(data_export, "get_project_root", return_value=self.root)

    def _make_db(self, name="cl1_data.db", content=b"sqlite"):
        path = self.root / "config" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _make_cl1(self, instance="alas", name="ship_exp_data.json", content=b"{}"):
        path = self.root / "log" / "cl1" / instance / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    # ---------- 文件收集范围 ----------

    def test_iter_collects_dbs_cl1_and_meow_csv(self):
        db = self._make_db()
        cl1 = self._make_cl1()
        cl2 = self._make_cl1(instance="alas3", name="drop.json", content=b"[1]")
        csv = self.root / "log" / "azurstat_meowofficer_farming.csv"
        csv.write_text("a,b\n", encoding="utf-8")

        with self._patch_root():
            files = list(data_export.iter_data_files())

        self.assertIn(db, files)
        self.assertIn(cl1, files)
        self.assertIn(cl2, files)
        self.assertIn(csv, files)

    def test_iter_skips_missing_files_and_non_db_config(self):
        # config 下的 json 是配置文件，不属于数据导出；缺的 db/csv 直接跳过
        self._make_db()
        (self.root / "config" / "alas.json").write_text("{}", encoding="utf-8")

        with self._patch_root():
            files = list(data_export.iter_data_files())

        self.assertEqual([f.name for f in files], ["cl1_data.db"])

    def test_iter_instance_scope_limits_cl1_but_keeps_shared(self):
        self._make_db()
        self._make_cl1(instance="alas")
        self._make_cl1(instance="alas3", name="drop.json")

        with self._patch_root():
            all_files = list(data_export.iter_data_files())
            alas_files = list(data_export.iter_data_files("alas"))

        self.assertEqual(len(all_files) - len(alas_files), 1)
        self.assertTrue(all("alas3" not in f.parts for f in alas_files))

    def test_iter_is_empty_on_fresh_install(self):
        (self.root / "config").mkdir()
        (self.root / "log").mkdir()
        with self._patch_root():
            self.assertEqual(list(data_export.iter_data_files()), [])

    def test_describe_counts_files_and_bytes(self):
        self._make_db(content=b"12345")
        self._make_cl1(content=b"67")
        with self._patch_root():
            info = data_export.describe_data_files()
        self.assertEqual(info["files"], 2)
        self.assertEqual(info["bytes"], 7)
        self.assertEqual(info["instance"], "all")

    # ---------- sqlite 按实例过滤 ----------

    def _make_instance_db(self, name="cl1_data.db"):
        """建一个与真实结构同形的库：一张表带 instance 列，一张不带。"""
        # content=b"" 只保证目录存在；空文件对 sqlite 就是合法的新库
        path = self._make_db(name, content=b"")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE cl1_data (instance TEXT, month TEXT, data_json TEXT)")
        conn.execute("CREATE INDEX idx_cl1 ON cl1_data (instance)")
        conn.execute("CREATE TABLE global_notes (note TEXT)")
        conn.executemany(
            "INSERT INTO cl1_data VALUES (?, ?, ?)",
            [("alas", "2026-09", "a1"), ("alas3", "2026-09", "b1"), ("alas", "2026-08", "a2")],
        )
        conn.execute("INSERT INTO global_notes VALUES ('shared')")
        conn.commit()
        conn.close()
        return path

    def test_filter_db_keeps_only_target_instance_rows(self):
        src = self._make_instance_db()
        copy = data_export.filter_db_by_instance(src, "alas")
        try:
            conn = sqlite3.connect(copy)
            rows = sorted(conn.execute("SELECT instance, month FROM cl1_data"))
            self.assertEqual(rows, [("alas", "2026-08"), ("alas", "2026-09")])
            # 无 instance 列的全局表整表保留
            self.assertEqual(
                [r[0] for r in conn.execute("SELECT note FROM global_notes")], ["shared"]
            )
            # 索引随 backup 原样保留
            indexes = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
            self.assertTrue(any("idx_cl1" in name for (name,) in indexes))
            conn.close()
        finally:
            copy.unlink(missing_ok=True)

    def test_filter_db_unknown_instance_leaves_only_global_rows(self):
        src = self._make_instance_db()
        copy = data_export.filter_db_by_instance(src, "nobody")
        try:
            conn = sqlite3.connect(copy)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cl1_data").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM global_notes").fetchone()[0], 1)
            conn.close()
        finally:
            copy.unlink(missing_ok=True)

    def test_filter_db_unregistered_table_is_kept_whole(self):
        """未登记过滤的表（含 instance 列）整表保留，不丢数据。"""
        path = self._make_db(name="new_stats.db", content=b"")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE future_table (instance TEXT, value TEXT)")
        conn.executemany(
            "INSERT INTO future_table VALUES (?, ?)", [("alas", "1"), ("alas3", "2")]
        )
        conn.commit()
        conn.close()

        copy = data_export.filter_db_by_instance(path, "alas")
        try:
            conn = sqlite3.connect(copy)
            # future_table 未在 _INSTANCE_FILTER_TABLES 登记 → 整表保留
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM future_table").fetchone()[0], 2)
            conn.close()
        finally:
            copy.unlink(missing_ok=True)

    # ---------- zip 打包 ----------

    def test_build_data_zip_keeps_import_layout(self):
        """zip 内保留 config/、log/ 相对结构，解压后可直接被「导入旧数据」还原。"""
        self._make_db()
        self._make_cl1()
        with self._patch_root():
            zip_path = data_export.build_data_zip()
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names = sorted(archive.namelist())
            self.assertEqual(
                names,
                [
                    "config/cl1_data.db",
                    "log/cl1/alas/ship_exp_data.json",
                ],
            )
        finally:
            zip_path.unlink(missing_ok=True)

    def test_build_data_zip_instance_filters_db_and_cl1(self):
        """单实例包：db 只剩该实例的行，cl1 只取该实例目录，文件名结构不变。"""
        self._make_instance_db()
        self._make_cl1(instance="alas")
        self._make_cl1(instance="alas3", name="drop.json")
        with self._patch_root():
            zip_path = data_export.build_data_zip("alas")
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names = sorted(archive.namelist())
                self.assertEqual(
                    names,
                    [
                        "config/cl1_data.db",
                        "log/cl1/alas/ship_exp_data.json",
                    ],
                )
                # 解压验证 zip 里的 db 已经过滤
                with tempfile.TemporaryDirectory() as extract_dir:
                    archive.extract("config/cl1_data.db", extract_dir)
                    conn = sqlite3.connect(Path(extract_dir) / "config" / "cl1_data.db")
                    instances = {
                        r[0] for r in conn.execute("SELECT DISTINCT instance FROM cl1_data")
                    }
                    conn.close()
            self.assertEqual(instances, {"alas"})
        finally:
            zip_path.unlink(missing_ok=True)

    def test_build_data_zip_roundtrip_content(self):
        db = self._make_db(content=b"sqlite-content")
        self._make_cl1(content=b"{}")
        with self._patch_root():
            zip_path = data_export.build_data_zip()
        try:
            with tempfile.TemporaryDirectory() as extract_dir:
                with zipfile.ZipFile(zip_path) as archive:
                    archive.extractall(extract_dir)
                restored = Path(extract_dir) / "config" / "cl1_data.db"
                self.assertEqual(restored.read_bytes(), db.read_bytes())
        finally:
            zip_path.unlink(missing_ok=True)

    def test_build_data_zip_no_files_raises(self):
        (self.root / "config").mkdir()
        (self.root / "log").mkdir()
        with self._patch_root():
            with self.assertRaises(FileNotFoundError):
                data_export.build_data_zip()

    def test_build_data_zip_instance_no_files_raises(self):
        (self.root / "config").mkdir()
        (self.root / "log").mkdir()
        with self._patch_root():
            with self.assertRaises(FileNotFoundError):
                data_export.build_data_zip("alas")


class TestDataExportApi(unittest.TestCase):
    """远控可达性相关的关键行为：接口不得被本机限制拦下。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.client = TestClient(build_data_app())

    def tearDown(self):
        self._tmp.cleanup()

    def test_data_routes_are_registered(self):
        paths = [route.path for route in build_data_app().routes]
        for expected in (
            "/api/data/export",
            "/api/data/export/{instance}",
            "/api/data/export/info",
            "/api/data/export/info/{instance}",
        ):
            self.assertIn(expected, paths)
        # 静态段必须排在带参段之前，否则 all/info 会被当成实例名吃掉
        self.assertLess(
            paths.index("/api/data/export/info"),
            paths.index("/api/data/export/info/{instance}"),
        )
        self.assertLess(
            paths.index("/api/data/export"),
            paths.index("/api/data/export/{instance}"),
        )

    def test_data_handlers_are_not_local_only(self):
        """远控经 P2P 代理进来也必须可用，因此不得使用 is_local_request 门禁。"""
        for handler in (
            webui_api.api_data_export,
            webui_api.api_data_export_info,
        ):
            self.assertNotIn("is_local_request", handler.__code__.co_names)

    def test_info_returns_counts_for_all_and_instance(self):
        with patch.object(
            webui_api,
            "describe_data_files",
            return_value={"instance": "all", "files": 3, "bytes": 42, "human_bytes": "42.0 B"},
        ) as describe:
            response = self.client.get("/api/data/export/info")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["success"])
            # 无参 = 全部：describe_data_files 收到 None
            describe.assert_called_with(None)

            response = self.client.get("/api/data/export/info/alas")
            self.assertEqual(response.status_code, 200)
            # path 里的实例名转成单实例范围
            describe.assert_called_with("alas")

    def test_info_rejects_unknown_instance(self):
        # validate_instance 白名单同时挡住路径穿越；httpx 会把 ..%2F 规范化
        # 掉，所以这里用普通未知名验证 400 分支（穿越用例在 log_export 测试覆盖）
        response = self.client.get("/api/data/export/info/nonexistent")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_export_ok_sets_attachment_filename_and_cleans_temp(self):
        temp = self.root / "bundle.zip"
        with zipfile.ZipFile(temp, "w") as archive:
            archive.writestr("config/cl1_data.db", b"sqlite")
        with patch.object(webui_api, "build_data_zip", return_value=temp) as build:
            response = self.client.get("/api/data/export/alas")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        disposition = response.headers["content-disposition"]
        self.assertIn("attachment", disposition)
        self.assertIn("alas", disposition)
        build.assert_called_with("alas")
        # BackgroundTask 在响应发送完成后删除临时文件
        self.assertFalse(temp.exists())

    def test_export_all_uses_none_scope(self):
        temp = self.root / "bundle.zip"
        with zipfile.ZipFile(temp, "w") as archive:
            archive.writestr("config/cl1_data.db", b"sqlite")
        with patch.object(webui_api, "build_data_zip", return_value=temp) as build:
            response = self.client.get("/api/data/export/all")
        self.assertEqual(response.status_code, 200)
        build.assert_called_with(None)

    def test_export_missing_data_returns_404(self):
        with patch.object(
            webui_api, "build_data_zip", side_effect=FileNotFoundError("没有可导出的数据文件")
        ):
            response = self.client.get("/api/data/export")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    def test_export_os_error_returns_500(self):
        with patch.object(
            webui_api, "build_data_zip", side_effect=OSError("disk full")
        ):
            response = self.client.get("/api/data/export")
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.json()["success"])


class TestManageExportUi(unittest.TestCase):
    """管理页「导出数据」入口的关键钩子：菜单按钮、范围下拉与下载脚本。"""

    def setUp(self):
        self.source = (
            Path(__file__).resolve().parents[1]
            / "module"
            / "webui"
            / "app_manage.py"
        ).read_text(encoding="utf-8")

    def test_menu_and_panel_hooks_exist(self):
        # 菜单按钮 + 高亮钩子（init_menu 靠 style 变量找按钮）
        self.assertIn('t("Gui.AppManage.ExportData")', self.source)
        self.assertIn("--menu-ManageExportData--", self.source)
        # 面板 DOM（含导出范围下拉）与 JS 下载逻辑
        for hook in (
            "data-export-instance",
            "data-export-size",
            "data-export-start",
            "data-export-status",
            "/api/data/export/' + encodeURIComponent(sel.value)",
        ):
            self.assertIn(hook, self.source)

    def test_panel_uses_fetch_download_not_pywebio_download(self):
        """大文件必须走 HTTP fetch → Blob，不能走 PyWebIO 的 WebSocket download()。"""
        self.assertIn("createObjectURL", self.source)

    def test_export_data_keys_exist_in_all_languages(self):
        # 不走 module.config.utils.read_file：logger 导入时会 os.chdir，
        # filepath_i18n 的相对路径会读错目录，这里直接用绝对路径读 json
        i18n_dir = Path(__file__).resolve().parents[1] / "module" / "config" / "i18n"
        keys = (
            "ExportData",
            "ExportDataTitle",
            "ExportDataHint",
            "ExportDataContent",
            "ExportDataRemote",
            "ExportDataChecking",
            "ExportDataSize",
            "ExportDataStart",
            "ExportDataPacking",
            "ExportDataStarted",
            "ExportDataNoFiles",
            "ExportDataInfoFailed",
            "ExportDataFailed",
            "ExportDataScopeLabel",
            "ExportDataScopeAll",
            "ExportDataFilteredHint",
        )
        for lang in ("zh-CN", "zh-MIAO", "en-US", "ja-JP", "zh-TW"):
            data = json.loads((i18n_dir / f"{lang}.json").read_text(encoding="utf-8"))
            section = data["Gui"]["AppManage"]
            for key in keys:
                self.assertIn(key, section, f"{lang} 缺少 Gui.AppManage.{key}")
                self.assertNotEqual(
                    section[key], f"Gui.AppManage.{key}", f"{lang} 的 {key} 未翻译"
                )

    def test_export_size_text_uses_escaped_placeholders(self):
        """t() 会对文案执行 .format()：体积文案必须用 {{files}} 双花括号，
        否则无参调用直接 KeyError（管理页面板打不开的回归）。"""
        i18n_dir = Path(__file__).resolve().parents[1] / "module" / "config" / "i18n"
        for lang in ("zh-CN", "zh-MIAO", "en-US", "ja-JP", "zh-TW"):
            data = json.loads((i18n_dir / f"{lang}.json").read_text(encoding="utf-8"))
            size_text = data["Gui"]["AppManage"]["ExportDataSize"]
            self.assertIn("{{files}}", size_text, f"{lang} 的 ExportDataSize 缺少转义占位符")
            self.assertNotIn("{files}", size_text.replace("{{files}}", ""))


if __name__ == "__main__":
    unittest.main()
