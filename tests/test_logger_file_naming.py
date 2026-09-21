"""文件日志命名规则的回归测试。

历史问题：`log/` 里会不断出现 `-c` / `python` / `meow` / `config` 这类没人看的
日志族。原因是 `set_file_logger()` 直接拿 `sys.argv[0]` 派生日志名——只要换一种
启动方式（`python -c`、临时脚本、`-m module.xxx`、被包装器当成脚本名的解释器名）
就会多一族文件；而且多进程 spawn 子进程会继承父进程的 `sys.argv`，子进程一 import
就建了文件，随后显式 `set_file_logger(config_name)` 又被“每个进程只挂一次”挡掉，
实例日志被写进了那一族垃圾文件里。

这里覆盖 `resolve_log_name()` 的判定规则（纯函数，不碰真实 log 目录），
以及 `set_file_logger()` 在非标准启动下不再挂文件处理器的行为。
"""

import os
import sys
import unittest
from unittest.mock import patch

from module import logger as logger_module
from module.logger import FILE_LOG_ENTRIES, resolve_log_name


class TestResolveLogName(unittest.TestCase):
    def test_known_entries_are_logged(self):
        """入口脚本照常写文件日志。"""
        self.assertEqual(resolve_log_name(pname='probe', argv0='gui.py'), 'gui')
        self.assertEqual(
            resolve_log_name(pname='probe', argv0=os.path.join('dir', 'alas.py')), 'alas'
        )
        self.assertEqual(
            resolve_log_name(pname='probe', argv0='mcp_server_sse.py'), 'mcp_server_sse'
        )
        self.assertEqual(
            resolve_log_name(pname='probe', argv0='/root/AzurPilot/alas.py'), 'alas'
        )

    @unittest.skipUnless(os.name == 'nt', 'Windows 反斜杠路径')
    def test_windows_backslash_argv0(self):
        """Windows 下 argv[0] 带盘符反斜杠，仍要认出入口名。"""
        self.assertEqual(resolve_log_name(pname='probe', argv0=r'C:\a\alas.py'), 'alas')

    def test_non_entry_launch_is_not_logged(self):
        """非标准启动不再产生孤儿日志族。"""
        for argv0 in (
            '-c',                      # python -c
            'python',                  # 解释器名被当成 argv[0]
            'python.exe',
            '-',                       # python - （stdin）
            '__main__.py',             # python -m unittest
            'config_updater.py',       # python -m module.config.config_updater
            'meow_probe.py',           # 临时脚本
        ):
            with self.subTest(argv0=argv0):
                self.assertIsNone(resolve_log_name(pname='probe', argv0=argv0))

    def test_explicit_instance_name_wins(self):
        """显式传入的实例名（含非入口启动）总是生效。"""
        self.assertEqual(
            resolve_log_name(name='小号', pname='Process-3', argv0='-c'), '小号'
        )
        self.assertEqual(
            resolve_log_name(name='alas', pname='MainProcess', argv0='python'), 'alas'
        )

    def test_explicit_name_keeps_legacy_underscore_split(self):
        """保持既有行为：显式名字在第一个下划线处截断。"""
        self.assertEqual(resolve_log_name(name='alas_v2'), 'alas')
        self.assertEqual(resolve_log_name(name='no_underscore'), 'no')

    def test_gui_process_is_logged_regardless_of_argv0(self):
        """WebUI 服务跑在 Process(name="gui") 里，不依赖 argv[0]。"""
        self.assertEqual(resolve_log_name(pname='gui', argv0='-c'), 'gui')
        self.assertEqual(resolve_log_name(pname='gui', argv0='whatever_else.py'), 'gui')

    @unittest.skipUnless(os.name == 'nt', 'Windows 专属的多进程进程名规则')
    def test_internal_mp_processes_are_skipped(self):
        """多进程内部进程（含 spawn 的 worker 子进程）不写文件日志。"""
        for pname in ('Process-1', 'SpawnProcess-2', 'SyncManager-1_2'):
            with self.subTest(pname=pname):
                self.assertIsNone(resolve_log_name(pname=pname, argv0='gui.py'))
                self.assertIsNone(resolve_log_name(pname=pname, argv0='alas.py'))

    @unittest.skipUnless(os.name == 'nt', 'Windows 专属的多进程进程名规则')
    def test_main_process_keeps_own_entry_log_but_not_gui(self):
        """gui.py 的主进程只是监督进程（写日志的是 gui 子进程）；alas.py 主进程照常写。"""
        self.assertIsNone(resolve_log_name(pname='MainProcess', argv0='gui.py'))
        self.assertIsNone(resolve_log_name(pname='MainProcess', argv0='-c'))
        self.assertEqual(resolve_log_name(pname='MainProcess', argv0='alas.py'), 'alas')
        self.assertEqual(
            resolve_log_name(pname='MainProcess', argv0=os.path.join('some', 'dir', 'alas.py')),
            'alas',
        )

    def test_entries_cover_documented_entry_points(self):
        """AGENTS.md 里的入口脚本必须仍在允许列表内。"""
        for entry in ('gui', 'alas'):
            self.assertIn(entry, FILE_LOG_ENTRIES)


class TestSetFileLoggerNoJunkFiles(unittest.TestCase):
    def test_non_entry_launch_adds_no_handler(self):
        """从 `python -c` 之类启动时，import 阶段不会挂上文件日志处理器。"""
        before = list(logger_module.logger.handlers)
        with patch.object(sys, 'argv', ['-c']):
            logger_module.set_file_logger()
        self.assertEqual(list(logger_module.logger.handlers), before)


if __name__ == '__main__':
    unittest.main()
