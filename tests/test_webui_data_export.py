"""数据导出（统计库 + 掉落记录 zip）的回归测试。

覆盖三层：
1. data_export 纯逻辑（文件收集范围、体积统计、zip 打包与目录结构）
2. api 路由（注册、无本机限制、404/500、Content-Disposition、临时文件清理）
3. 界面结构（管理页「导出数据」菜单与下载面板的关键钩子）
"""

import json
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
        self.assertIn("/api/data/export", paths)
        self.assertIn("/api/data/export/info", paths)
        # 静态段排在动态段无所谓（这里没有动态段），但 info 与 export 都必须存在
        self.assertEqual(len(paths), 2)

    def test_data_handlers_are_not_local_only(self):
        """远控经 P2P 代理进来也必须可用，因此不得使用 is_local_request 门禁。"""
        for handler in (
            webui_api.api_data_export,
            webui_api.api_data_export_info,
        ):
            self.assertNotIn("is_local_request", handler.__code__.co_names)

    def test_info_returns_counts(self):
        with patch.object(
            webui_api,
            "describe_data_files",
            return_value={"files": 3, "bytes": 42, "human_bytes": "42.0 B"},
        ):
            response = self.client.get("/api/data/export/info")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["files"], 3)

    def test_export_ok_sets_attachment_filename_and_cleans_temp(self):
        temp = self.root / "bundle.zip"
        with zipfile.ZipFile(temp, "w") as archive:
            archive.writestr("config/cl1_data.db", b"sqlite")
        with patch.object(webui_api, "build_data_zip", return_value=temp):
            response = self.client.get("/api/data/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        disposition = response.headers["content-disposition"]
        self.assertIn("attachment", disposition)
        self.assertIn(".zip", disposition)
        # BackgroundTask 在响应发送完成后删除临时文件
        self.assertFalse(temp.exists())

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
    """管理页「导出数据」入口的关键钩子：菜单按钮、面板与下载脚本。"""

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
        # 面板 DOM 与 JS 下载逻辑
        for hook in (
            "data-export-size",
            "data-export-start",
            "data-export-status",
            "/api/data/export/info",
            "'/api/data/export'",
        ):
            self.assertIn(hook, self.source)

    def test_panel_uses_fetch_download_not_pywebio_download(self):
        """大文件必须走 HTTP fetch → Blob，不能走 PyWebIO 的 WebSocket download()。"""
        self.assertIn("createObjectURL", self.source)

    def test_export_data_keys_exist_in_all_languages(self):
        # 不走 module.config.utils.read_file：logger 导入时会 os.chdir，
        # filepath_i18n 的相对路径会读错目录，这里直接用绝对路径读 json
        i18n_dir = Path(__file__).resolve().parents[1] / "module" / "config" / "i18n"
        for lang in ("zh-CN", "zh-MIAO", "en-US", "ja-JP", "zh-TW"):
            data = json.loads((i18n_dir / f"{lang}.json").read_text(encoding="utf-8"))
            section = data["Gui"]["AppManage"]
            for key in (
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
            ):
                self.assertIn(key, section, f"{lang} 缺少 Gui.AppManage.{key}")
                self.assertNotEqual(
                    section[key], f"Gui.AppManage.{key}", f"{lang} 的 {key} 未翻译"
                )


if __name__ == "__main__":
    unittest.main()
