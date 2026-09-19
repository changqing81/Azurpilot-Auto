"""日志导出链路回归测试：纯逻辑打包 + WS 申请令牌 + HTTP 一次性下载。

覆盖从旧 WebUI 迁移过来的导出能力在新 API 架构下的等价行为：
1. log_export 纯逻辑（运行日志定位、多文件合并、错误日志打包、字节数格式化）
2. logs.requestExport 签发的令牌只能用一次，过期/伪造令牌返回 404
3. 下载响应带 Content-Disposition，临时文件在响应后清理
"""

import io
import json
import secrets
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from module.api import log_export
from module.api.app import create_app
from module.api.config_service import ConfigService
from tests.test_api import fixture

# 测试夹具口令运行时随机生成，避免在源码中出现任何字面量凭据
TEST_PASSWORD = secrets.token_urlsafe(16)


def make_starlette(app):
    """TestClient 接受 ASGI 应用本身；这里显式包一层以便复用 create_app 工厂。"""
    return app


class LogExportLogicTests(unittest.TestCase):
    def test_normalize_runtime_scope_accepts_only_known_shapes(self):
        self.assertEqual('all', log_export.normalize_runtime_scope(''))
        self.assertEqual('all', log_export.normalize_runtime_scope('../etc'))
        self.assertRegex(log_export.normalize_runtime_scope('TODAY'), r'^\d{4}-\d{2}-\d{2}$')
        self.assertRegex(log_export.normalize_runtime_scope('today'), r'^\d{4}-\d{2}-\d{2}$')
        self.assertEqual('2026-09-19', log_export.normalize_runtime_scope('2026-09-19'))

    def test_runtime_bundle_merges_history_with_banners(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_dir = root / 'log'
            log_dir.mkdir()
            (log_dir / '2026-09-17_testpilot.txt').write_text('day-17', encoding='utf-8')
            (log_dir / '2026-09-18_testpilot.txt').write_text('day-18', encoding='utf-8')

            with patch.object(log_export, 'get_project_root', return_value=root):
                path, filename, temp = log_export.build_runtime_log_bundle('testpilot')

            self.assertTrue(temp)
            self.assertEqual('2026-09-17~2026-09-18_testpilot.txt', filename)
            content = Path(path).read_text(encoding='utf-8')
            self.assertIn('2026-09-17_testpilot.txt', content)
            self.assertIn('day-17', content)
            self.assertIn('day-18', content)
            self.assertLess(content.index('day-17'), content.index('day-18'))
            Path(path).unlink()

    def test_runtime_bundle_single_file_returns_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log_dir = root / 'log'
            log_dir.mkdir()
            (log_dir / '2026-09-19_testpilot.txt').write_text('only', encoding='utf-8')

            with patch.object(log_export, 'get_project_root', return_value=root):
                path, filename, temp = log_export.build_runtime_log_bundle('testpilot')

            self.assertFalse(temp)
            self.assertEqual('2026-09-19_testpilot.txt', filename)

    def test_runtime_bundle_without_logs_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'log').mkdir()
            with patch.object(log_export, 'get_project_root', return_value=root):
                with self.assertRaises(FileNotFoundError):
                    log_export.build_runtime_log_bundle('testpilot')

    def test_error_log_zip_keeps_layout_and_stores_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            error_dir = root / 'log' / 'error'
            (error_dir / 'sub').mkdir(parents=True)
            (error_dir / 'traceback.txt').write_text('boom', encoding='utf-8')
            (error_dir / 'sub' / 'screenshot.png').write_bytes(b'\x89PNG fake')

            with patch.object(log_export, 'get_project_root', return_value=root):
                archive_path = log_export.build_error_log_zip(log_export.SCOPE_FULL)

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                self.assertIn('traceback.txt', names)
                self.assertIn('sub/screenshot.png', names)
                stored = archive.getinfo('sub/screenshot.png').compress_type
                self.assertEqual(zipfile.ZIP_STORED, stored)
            archive_path.unlink()

    def test_format_bytes(self):
        self.assertEqual('0 B', log_export.format_bytes(0))
        self.assertEqual('512 B', log_export.format_bytes(512))
        self.assertEqual('1.0 KB', log_export.format_bytes(1024))
        self.assertEqual('1.0 MB', log_export.format_bytes(1024 * 1024))


class LogExportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = fixture(self.temp.name)
        # 项目的 log 目录不可预知，全部指到临时目录
        self.log_dir = self.root / 'log'
        self.log_dir.mkdir()

    def _client(self):
        app = create_app(root=self.root, password=TEST_PASSWORD, manage_runtime=False, mount_mcp=False)
        return TestClient(app)

    def _login(self, ws):
        """连接初始推送之后登录，返回鉴权结果。"""
        ws.receive_json()
        login = self._call(ws, 'auth.login', {'password': TEST_PASSWORD})
        self.assertTrue(login['ok'], login)

    def _call(self, ws, method, params):
        """发送请求并按 id 过滤出对应响应（跳过事件推送）。"""
        ws.send_json({'v': 1, 'type': 'request', 'id': method, 'method': method, 'params': params})
        while True:
            result = ws.receive_json()
            if result.get('id') == method:
                return result

    def test_request_export_then_one_time_download(self):
        (self.log_dir / '2026-09-19_testpilot.txt').write_text('hello log', encoding='utf-8')
        with patch.object(log_export, 'get_project_root', return_value=self.root):
            client = self._client()
            with client.websocket_connect('/api/v1/ws') as ws:
                self._login(ws)
                response = self._call(ws, 'logs.requestExport',
                                      {'instance': 'testpilot', 'kind': 'runtime', 'scope': 'today'})
            self.assertTrue(response['ok'], response)
            self.assertEqual('2026-09-19_testpilot.txt', response['result']['filename'])

            downloaded = client.get(response['result']['url'])
            self.assertEqual(200, downloaded.status_code)
            self.assertIn('attachment', downloaded.headers.get('content-disposition', ''))
            self.assertEqual('hello log', downloaded.text)

            # 一次性令牌：第二次下载必须 404
            self.assertEqual(404, client.get(response['result']['url']).status_code)

    def test_invalid_token_is_rejected(self):
        client = self._client()
        self.assertEqual(404, client.get('/api/v1/export/not-a-real-token').status_code)

    def test_unknown_instance_is_rejected(self):
        client = self._client()
        with client.websocket_connect('/api/v1/ws') as ws:
            self._login(ws)
            response = self._call(ws, 'logs.requestExport', {'instance': 'ghost', 'kind': 'runtime'})
        self.assertFalse(response['ok'])

    def test_error_zip_flow_cleans_temp_file(self):
        error_dir = self.log_dir / 'error'
        error_dir.mkdir()
        (error_dir / 'traceback.txt').write_text('boom', encoding='utf-8')
        with patch.object(log_export, 'get_project_root', return_value=self.root):
            client = self._client()
            with client.websocket_connect('/api/v1/ws') as ws:
                self._login(ws)
                response = self._call(ws, 'logs.requestExport',
                                      {'instance': 'testpilot', 'kind': 'error', 'scope': 'text'})
            self.assertTrue(response['ok'], response)
            downloaded = client.get(response['result']['url'])
            self.assertEqual(200, downloaded.status_code)
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                self.assertIn('traceback.txt', archive.namelist())
            # 临时 zip 已在响应后清理：下载目录里不应残留 alas_error_log_ 前缀文件
            leftovers = list(Path(self.temp.name).glob('alas_error_log_*'))
            self.assertEqual([], leftovers)


if __name__ == '__main__':
    unittest.main()
