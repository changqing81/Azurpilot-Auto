"""模拟器恢复 cancel 毒标记事故的回归测试（2026-10-25 鹿管机）。

事故链（日志 03:08-03:13）：
1. MapDetectionError → 触发模拟器恢复，MuMuManager 启动实例
2. 探测 300s 超时 → 失败分支 cancel.set()（原 emulator_recovery.py:221）
3. 7 秒后调度器重试恢复 → allowed() 先查 cancel.is_set() → 拒绝
4. 报「模拟器离线且未允许自动恢复」→ RecoveryStopped → 调度器退出
5. 停机 6 小时（03:13→09:34 用户手动重启脚本）

根因：cancel 的语义是「用户停止信号 / 杀超时 runaway 执行者」，
但失败分支把它永久置位且无人清除，一轮失败 = 永久封死后续恢复。
报错文字「未允许自动恢复」严重误导（开关实际是开着的）。

修复三层：
1. 失败分支不再 cancel.set()（主修复）
2. restart() 在确认上一执行者已死后回收过期置位（清历史污染）
3. allowed() 只在「执行者存活」时把 cancel 视为中止信号（纵深防御）
"""

import ast
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from module.device.session import DeviceSession
from module.exception import RecoveryFailed
from module.handler.emulator_health import AdbProbe
from module.handler.emulator_recovery import EmulatorRecovery
from module.handler.emulator_recovery_state import InstanceLease

SOURCE_PATH = Path(__file__).resolve().parent.parent / 'module' / 'handler' / 'emulator_recovery.py'


class CancelPoisonFixture:
    """与 test_emulator_recovery.TestRecoveryTransaction 相同的桩环境。"""

    def __init__(self, ready=True):
        self.temp = tempfile.TemporaryDirectory()
        self.config = SimpleNamespace(
            EmulatorManagement_CrashProtection=True,
            EmulatorManagement_CrashDryRun=False,
            EmulatorManagement_CrashMaxRestarts=99,
            EmulatorManagement_CrashRestartWindowHours=1,
            # 冷却配置会被生产代码钳位到 ≥60s；测试里通过回拨租约时间戳跨过冷却期
            Error_HandleError=True,
            Error_AdbOfflineRestart=True,
            Emulator_PackageName='com.bilibili.azurlane',
        )
        self.alas = SimpleNamespace(
            config=self.config, config_name=None, stop_event=None,
            device=DeviceSession(), _emulator_lock=threading.Lock(),
        )
        self.recovery = EmulatorRecovery(self.alas)
        self._patches = [
            patch('module.device.platform.Platform', autospec=True),
            patch('module.handler.emulator_target.RecoveryTarget', autospec=True),
            patch('module.handler.emulator_recovery.InstanceLease',
                  side_effect=lambda identity: InstanceLease(identity, self.temp.name)),
            patch.object(AdbProbe, 'ready', autospec=True, return_value=ready),
        ]
        self.mocks = [p.start() for p in self._patches]
        self.addCleanup = None
        platform = self.mocks[0].return_value
        platform.serial = '127.0.0.1:16384'
        platform.adb_binary = 'adb'
        target = self.mocks[1].return_value
        target.identity = ('MuMuPlayer12', '/mumu', '0')
        target.wait_stopped.return_value = True

    def stop(self):
        for p in reversed(self._patches):
            p.stop()
        self.temp.cleanup()


class TestFailureDoesNotPoisonCancel(unittest.TestCase):
    """主修复：恢复失败不得置位 cancel，下一轮必须能真正重试。"""

    def _make(self, ready=False):
        fx = CancelPoisonFixture(ready=ready)
        self.addCleanup(fx.stop)
        return fx

    def test_failure_leaves_cancel_cleared(self):
        """失败分支不得 cancel.set()——这是鹿管机停机 6 小时的直接根因。"""
        fx = self._make(ready=False)
        with self.assertRaises(RecoveryFailed):
            fx.recovery.restart('offline')
        self.assertFalse(fx.recovery.cancel.is_set(),
                         '失败后 cancel 被置位：下一轮恢复会被 allowed() 拦截')

    def test_retry_after_failure_actually_restarts(self):
        """鹿管机 03:13:26 路径：失败 → 重试必须重新执行恢复而非被拒。

        旧代码第二轮 restart() 直接返回 False（allowed 拒绝），修复后
        第二轮应真正再跑一轮 stop/start（ready 桩改为成功则应成功）。
        """
        import time as _time
        fx = self._make(ready=False)
        with self.assertRaises(RecoveryFailed):
            fx.recovery.restart('offline')  # 第一轮：探测超时失败
        # 回拨租约时间戳：模拟调度器退避（冷却期默认钳位 60s，最小不可配置）
        # 后的下一次重试——即鹿管机 Restart.NextRun=03:14:30 那一轮
        lease_file = next(Path(fx.temp.name).glob('*.json'))
        lease_file.write_text(json.dumps([_time.time() - 7200]), encoding='utf-8')
        # 第二轮：设备这次就绪（模拟退避后模拟器终于起来）
        fx.mocks[3].return_value = True
        self.assertTrue(fx.recovery.restart('offline'),
                        '第二轮恢复被拒：cancel 毒标记仍拦截重试（鹿管机事故复现）')

    def test_stale_cancel_cleared_before_new_round(self):
        """纵深防御：restart() 必须在启动新执行者前回收过期置位。"""
        fx = self._make(ready=True)
        fx.recovery.cancel.set()  # 模拟历史污染（如升级前版本遗留）
        self.assertTrue(fx.recovery.restart('offline'))
        self.assertFalse(fx.recovery.cancel.is_set())

    def test_alive_worker_cancel_still_blocks(self):
        """cancel 语义保留：执行者存活期间的置位仍须拦截新恢复。"""
        fx = self._make(ready=True)
        fx.recovery.cancel.set()
        alive = threading.Event()
        alive.set()
        fake_worker = SimpleNamespace(is_alive=lambda: True)
        fx.recovery.worker = fake_worker
        self.assertFalse(fx.recovery.allowed('offline'),
                         '执行者存活 + cancel 置位 = 有效中止信号，应拒绝')


class TestSourceLocks(unittest.TestCase):
    """AST 源码锁：防止未来改动悄悄恢复毒标记。"""

    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)

    def _func_source(self, name):
        func = next(n for n in ast.walk(self.tree)
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        lines = self.source.splitlines()
        return chr(10).join(lines[func.lineno - 1: func.end_lineno])

    def test_failure_branch_has_no_cancel_set(self):
        """失败分支（if error or not result:）不得再执行 cancel.set()。"""
        body = self._func_source('restart')
        start = body.index('if error or not result:')
        end = body.index('raise RecoveryFailed(detail)')
        branch = body[start:end]
        # 只匹配真实代码行：剥掉注释（防止注释里的说明文字误伤断言）
        code_lines = [ln.split('#', 1)[0] for ln in branch.split(chr(10))]
        self.assertNotIn('cancel.set()', chr(10).join(code_lines),
                         '失败分支重新引入 cancel.set() 会再次毒死重试链路')

    def test_allowed_gates_cancel_on_worker_alive(self):
        """allowed() 的 cancel 检查必须以执行者存活为前提。"""
        body = self._func_source('allowed')
        self.assertIn('worker_alive', body,
                      'allowed() 须先判 worker 存活再解释 cancel，否则历史置位又变毒标记')

    def test_timeout_branch_keeps_cancel_set(self):
        """超时分支（worker 仍活）保留 cancel.set()——杀 runaway 的语义不变。"""
        body = self._func_source('restart')
        start = body.index('if self.worker.is_alive():')
        end = body.index('if error or not result:')
        branch = body[start:end]
        self.assertIn('self.cancel.set()', branch,
                      '超时分支的 cancel.set() 是杀 runaway 执行者的手段，不得移除')


if __name__ == '__main__':
    unittest.main()
