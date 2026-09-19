"""worker 登记文件进程内读缓存的行为测试。

读路径（get_workers/get_owner/is_current_owner）命中快照缓存时
跳过读盘；写路径写穿缓存；外部修改（mtime 变化）自动失效。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from module.webui import worker_registry


class TestWorkerRegistryReadCache(unittest.TestCase):
    def setUp(self):
        worker_registry._registry_cache.clear()

    def tearDown(self):
        worker_registry._registry_cache.clear()

    def test_write_updates_cache_immediately(self):
        """写穿：注册后立即读取能看到最新数据。"""
        with tempfile.TemporaryDirectory() as directory:
            registry_file = Path(directory) / "workers.json"
            with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file), \
                 patch.object(worker_registry, "_process_created_at", return_value=10.5):
                worker_registry.claim_owner(100)
                worker_registry.register_worker(100, "alas", 200)
                self.assertEqual(
                    {"alas": {"created_at": 10.5, "pid": 200}},
                    worker_registry.get_workers(100),
                )

    def test_cached_read_skips_disk(self):
        """缓存命中时不读盘：读取期间文件被移走仍返回缓存内容。"""
        with tempfile.TemporaryDirectory() as directory:
            registry_file = Path(directory) / "workers.json"
            with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file), \
                 patch.object(worker_registry, "_process_created_at", return_value=10.5):
                worker_registry.claim_owner(100)
                worker_registry.register_worker(100, "alas", 200)
                # 预热缓存
                self.assertEqual(100, worker_registry.get_owner())

                registry_file.unlink()
                # mtime/size 校验：文件消失 → 缓存失效 → 空登记
                self.assertIsNone(worker_registry.get_owner())

    def test_external_modification_invalidates_cache(self):
        """外部进程修改文件后（mtime 变化），读取拿到新内容。"""
        with tempfile.TemporaryDirectory() as directory:
            registry_file = Path(directory) / "workers.json"
            with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file), \
                 patch.object(worker_registry, "_process_created_at", return_value=10.5):
                worker_registry.claim_owner(100)
                self.assertEqual(100, worker_registry.get_owner())

                # 模拟外部写入（不经写穿路径）：修改内容并显式推进 mtime
                registry_file.write_text(
                    '{"owner_created_at": 10.5, "owner_pid": 300, "workers": {}}',
                    encoding="utf-8",
                )
                stat = registry_file.stat()
                os.utime(registry_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

                self.assertEqual(300, worker_registry.get_owner())

    def test_cached_snapshot_is_shared_reference_for_readonly_paths(self):
        """get_workers 返回 deepcopy：调用方修改不影响后续读取。"""
        with tempfile.TemporaryDirectory() as directory:
            registry_file = Path(directory) / "workers.json"
            with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file), \
                 patch.object(worker_registry, "_process_created_at", return_value=10.5):
                worker_registry.claim_owner(100)
                worker_registry.register_worker(100, "alas", 200)

                workers = worker_registry.get_workers(100)
                workers["alas"]["pid"] = 999999
                # 再次读取不受调用方修改影响
                self.assertEqual(200, worker_registry.get_workers(100)["alas"]["pid"])


if __name__ == "__main__":
    unittest.main()
