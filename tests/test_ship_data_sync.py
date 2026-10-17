"""舰船数据自动同步（module/ship_data_sync）与 file_cached 缓存机制测试。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from module import ship_data_sync
from module.base import decorator as decorator_module
from module.base.decorator import file_cached
from module.ship_data_sync import DATA_FILES, ROOT, sync_ship_data


class Completed:
    """subprocess.CompletedProcess 的最小替身。"""

    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fetch_ok(handler):
    """fetch 成功；其余 git 命令交给 handler 处理。"""

    def dispatch(*args, **kwargs):
        if args[:1] == ('fetch',):
            return Completed(0)
        return handler(*args, **kwargs)

    return dispatch


class SyncShipDataTests(unittest.TestCase):
    def test_git_exec_failure_is_error_status(self):
        """git 缺失或超时：只记日志，返回 error 状态，不向上抛出。"""
        with patch.object(ship_data_sync, '_git', side_effect=FileNotFoundError('git')):
            summary = sync_ship_data()
        self.assertEqual(summary['status'], 'error')

    def test_missing_branch_is_skipped(self):
        """data/ship 尚未由 CI 创建：静默跳过，不算错误。"""
        with patch.object(
            ship_data_sync, '_git',
            side_effect=lambda *a, **k: Completed(1, stderr="fatal: couldn't find remote ref data/ship"),
        ):
            summary = sync_ship_data()
        self.assertEqual(summary['status'], 'skipped')
        self.assertEqual(summary['files'], {})

    def test_matching_blobs_are_uptodate(self):
        """远端与本地 blob 一致：不读取内容、不写文件。"""

        def handler(*args, **kwargs):
            if args[0] == 'rev-parse':
                return Completed(0, stdout='sha-1\n')
            if args[0] == 'hash-object':
                return Completed(0, stdout='sha-1\n')
            raise AssertionError(f'未预期命令: {args}')

        with patch.object(ship_data_sync, '_git', side_effect=_fetch_ok(handler)):
            summary = sync_ship_data()
        self.assertEqual(summary['status'], 'uptodate')
        self.assertEqual(len(summary['files']), len(DATA_FILES))

    def test_remote_change_writes_files(self):
        """远端 blob 变化：用 git show 的内容覆盖工作区文件。"""

        def handler(*args, **kwargs):
            if args[0] == 'rev-parse':
                return Completed(0, stdout='sha-new\n')
            if args[0] == 'hash-object':
                return Completed(0, stdout='sha-old\n')
            if args[0] == 'show':
                return Completed(0, stdout=b'{"cn": ["a"]}')
            raise AssertionError(f'未预期命令: {args}')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'assets' / 'ship').mkdir(parents=True)
            with patch.object(ship_data_sync, 'ROOT', root), \
                    patch.object(ship_data_sync, '_git', side_effect=_fetch_ok(handler)):
                summary = sync_ship_data()
            self.assertEqual(summary['status'], 'updated')
            for path in DATA_FILES:
                self.assertEqual((root / path).read_bytes(), b'{"cn": ["a"]}')

    def test_apply_failure_reports_error(self):
        """检测到更新但写入失败：状态 error，不抛异常。"""

        def handler(*args, **kwargs):
            if args[0] == 'rev-parse':
                return Completed(0, stdout='sha-new\n')
            if args[0] == 'hash-object':
                return Completed(0, stdout='sha-old\n')
            if args[0] == 'show':
                return Completed(0, stdout=b'{"cn": []}')
            raise AssertionError(f'未预期命令: {args}')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'assets' / 'ship').mkdir(parents=True)
            with patch.object(ship_data_sync, 'ROOT', root), \
                    patch.object(Path, 'write_bytes', side_effect=OSError('disk full')), \
                    patch.object(ship_data_sync, '_git', side_effect=_fetch_ok(handler)):
                summary = sync_ship_data()
        self.assertEqual(summary['status'], 'error')

    def test_data_files_exist_in_repository(self):
        """DATA_FILES 指向仓库内真实存在的资产（防路径笔误）。"""
        for path in DATA_FILES:
            self.assertTrue((ROOT / path).is_file(), path)


class FileCachedTests(unittest.TestCase):
    def test_cache_hits_and_invalidates_on_mtime(self):
        """同一 mtime 命中缓存；mtime 变化后自动重新计算。"""
        calls = []

        @file_cached('dep.json')
        def load():
            calls.append(1)
            return len(calls)

        stamp = {'value': 1}

        def fake_stat(path, *args, **kwargs):
            return type('Stat', (), {'st_mtime_ns': stamp['value']})()

        with patch.object(decorator_module.os, 'stat', fake_stat):
            first = load()
            second = load()
            self.assertIs(first, second)
            self.assertEqual(len(calls), 1)
            stamp['value'] = 2
            third = load()
        self.assertIsNot(third, second)
        self.assertEqual(len(calls), 2)

    def test_callable_path_and_missing_file(self):
        """路径可延迟求值；文件缺失（stat 抛 OSError）也按路径戳缓存。"""
        state = {'path': 'a.json'}
        calls = []

        @file_cached(lambda: state['path'])
        def load():
            calls.append(1)
            return len(calls)

        def fake_stat(path, *args, **kwargs):
            raise FileNotFoundError(path)

        with patch.object(decorator_module.os, 'stat', fake_stat):
            load()
            load()
            self.assertEqual(len(calls), 1)
            state['path'] = 'b.json'
            load()
        self.assertEqual(len(calls), 2)

    def test_cache_clear_resets_module_entries(self):
        """cache_clear 提供给测试与手工失效使用。"""
        calls = []

        @file_cached('dep.json')
        def load():
            calls.append(1)
            return object()

        def fake_stat(path, *args, **kwargs):
            return type('Stat', (), {'st_mtime_ns': 7})()

        with patch.object(decorator_module.os, 'stat', fake_stat):
            first = load()
            load.cache_clear()
            second = load()
        self.assertIsNot(first, second)
        self.assertEqual(len(calls), 2)


class WorkflowFileTests(unittest.TestCase):
    def test_ship_data_workflow_is_parsable(self):
        """工作流文件可解析且包含发布任务（防 YAML 笔误）。"""
        path = Path(__file__).parents[1] / '.github' / 'workflows' / 'ship-data-sync.yml'
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        self.assertIn('sync', data['jobs'])
        steps = data['jobs']['sync']['steps']
        self.assertTrue(any('ship_data_extractor' in step.get('run', '') for step in steps))


if __name__ == '__main__':
    unittest.main()
