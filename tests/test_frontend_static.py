"""验证生产服务公开完整构建资源，且不会把缺失图片当作页面。"""
import tempfile
import unittest

from starlette.testclient import TestClient

from module.api.app import create_app
from tests.test_api import fixture


class FrontendStaticTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = fixture(temporary.name)
        dist = root / 'frontend/dist'
        (dist / 'assets').mkdir(parents=True)
        (dist / 'icons').mkdir()
        (dist / 'index.html').write_text('<html>前端页面</html>', encoding='utf-8')
        (dist / '.source-fingerprint').write_text('internal')
        (dist / 'oil.webp').write_bytes(b'RIFF-test-image')
        (dist / 'icons/nested.svg').write_text('<svg/>')
        (dist / 'assets/app.css').write_text('body {color: red}')
        self.client = TestClient(create_app(root=root, password='', manage_runtime=False, mount_mcp=False))

    def test_public_resources_are_files(self):
        response = self.client.get('/oil.webp')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'RIFF-test-image')
        self.assertEqual(response.headers['content-type'], 'image/webp')
        self.assertEqual(response.headers['cache-control'], 'no-cache')
        self.assertEqual(self.client.head('/oil.webp').content, b'')
        self.assertEqual(self.client.get('/icons/nested.svg').text, '<svg/>')
        self.assertIn('text/css', self.client.get('/assets/app.css').headers['content-type'])

    def test_navigation_and_api_keep_their_routes(self):
        for path in ['/', '/some/page']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('前端页面', response.text)
            self.assertEqual(response.headers['cache-control'], 'no-cache')
        self.assertEqual(self.client.get('/healthz').json()['status'], 'ok')

    def test_missing_and_private_resources_are_not_pages(self):
        for path in ['/missing.webp', '/assets/missing.js', '/.source-fingerprint',
                     '/%2e%2e/%2e%2e/config/testpilot.json']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
