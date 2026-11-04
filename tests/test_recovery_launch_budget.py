"""纯粹拉起（实例本来就停着）不占用卡死重启额度——2026-11-04 事故回归。

事故链（用户日志 09:11-09:20）：
1. 09:12:01 模拟器没运行 → 恢复事务在 start() 之前记账（crash 桶）
2. 09:12:03 下发 launch_player，用户此时手动叉掉模拟器并停止脚本
3. 09:16:27 重启脚本，设备仍离线 → 再次进入恢复 → 「实例仍在恢复冷却期」
4. 直到 09:22:01 冷却结束，**一次都没有真正调用 target.start()**
   日志表现为「明明能启动却拉不起来」，用户完全看不出在等什么

修复两层：
1. 实例本来就停止时走独立的 launch 记账桶：不消耗 CrashMaxRestarts，
   冷却从 600 秒降到 30 秒，且不限次数（拉起实例是开机正常路径，不是故障重试）
2. 冷却中的异常升级为 RecoveryCoolingDown：携带剩余秒数与记录文件路径，
   调度器直接睡到冷却结束，不再包装成「恢复失败」逐轮推送

注意 stop() 仍然保留（幂等）：即使判定为已停止，也不跳过停止确认流程，
探测误判的代价只是多走一次无害的 shutdown_player。
"""

import ast
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from module.device.session import DeviceSession
from module.exception import RecoveryCoolingDown, RecoveryFailed
from module.handler.emulator_health import AdbProbe
from module.handler.emulator_recovery import EmulatorRecovery
from module.handler.emulator_recovery_state import CoolingDown, InstanceLease, RecoveryBlocked

ALAS_SOURCE = Path(__file__).resolve().parent.parent / 'alas.py'
STATE_SOURCE = Path(__file__).resolve().parent.parent / 'module' / 'handler' / 'emulator_recovery_state.py'
IDENTITY = ('MuMuPlayer12', '/mumu', '0')


class Fixture:
    """与 test_emulator_recovery.TestRecoveryTransaction 同构的桩环境。"""

    def __init__(self, running=False, ready=True):
        self.temp = tempfile.TemporaryDirectory()
        self.config = SimpleNamespace(
            EmulatorManagement_CrashProtection=True,
            EmulatorManagement_CrashDryRun=False,
            EmulatorManagement_CrashMaxRestarts=3,
            EmulatorManagement_CrashRestartWindowHours=6,
            EmulatorManagement_CrashCooldownSeconds=600,
            Error_HandleError=True,
            Error_AdbOfflineRestart=True,
            Emulator_PackageName='com.bilibili.azurlane',
        )
        self.alas = SimpleNamespace(config=self.config, config_name=None, stop_event=None,
                                    device=DeviceSession(), _emulator_lock=threading.Lock())
        self.recovery = EmulatorRecovery(self.alas)
        self._patches = [
            patch('module.device.platform.Platform', autospec=True),
            patch('module.handler.emulator_target.RecoveryTarget', autospec=True),
            patch('module.handler.emulator_recovery.InstanceLease',
                  side_effect=lambda identity: InstanceLease(identity, self.temp.name)),
            patch.object(AdbProbe, 'ready', autospec=True, return_value=ready),
        ]
        self.mocks = [p.start() for p in self._patches]
        platform = self.mocks[0].return_value
        platform.serial = '127.0.0.1:16384'
        platform.adb_binary = 'adb'
        self.target = self.mocks[1].return_value
        self.target.identity = IDENTITY
        self.target.wait_stopped.return_value = True
        self.target.running.return_value = running

    def stop(self):
        for p in reversed(self._patches):
            p.stop()
        self.temp.cleanup()

    @property
    def root(self):
        return Path(self.temp.name)

    def launch_files(self):
        return sorted(self.root.glob('*.launch.json'))

    def crash_files(self):
        return sorted(p for p in self.root.glob('*.json') if not p.name.endswith('.launch.json'))

    def seed_crash_record(self, timestamp):
        """按当前身份算出 crash 桶文件名并写入历史（模拟上次真的执行过重启）。"""
        path = InstanceLease(IDENTITY, self.root).path
        path.write_text(json.dumps([timestamp]), encoding='utf-8')
        return path


class TestLaunchBudget(unittest.TestCase):
    """实例本来就停着：只记账 launch 桶，不动卡死重启额度。"""

    def make(self, **kwargs):
        fx = Fixture(**kwargs)
        self.addCleanup(fx.stop)
        return fx

    def test_stopped_instance_does_not_consume_crash_budget(self):
        fx = self.make(running=False)
        self.assertTrue(fx.recovery.restart('offline'))
        self.assertEqual(fx.crash_files(), [], '纯启动不该写入卡死重启额度')
        self.assertEqual(len(fx.launch_files()), 1, '纯启动应写入独立 launch 记录')
        self.assertEqual(len(json.loads(fx.launch_files()[0].read_text())), 1)
        # 停止确认流程仍要走一遍：判定的只是额度口径，不是安全网
        fx.target.stop.assert_called_once()
        fx.target.wait_stopped.assert_called_once()

    def test_running_instance_still_consumes_crash_budget(self):
        fx = self.make(running=True)
        self.assertTrue(fx.recovery.restart('crash'))
        self.assertEqual(fx.launch_files(), [], '实例在运行时必须记在卡死重启桶')
        self.assertEqual(len(fx.crash_files()), 1)

    def test_probe_failure_is_conservative(self):
        """状态探测失败按「在运行」处理：不能靠探测故障绕开配额。"""
        fx = self.make(running=False)
        fx.target.running.side_effect = OSError('实例状态不可读')
        self.assertTrue(fx.recovery.restart('crash'))
        self.assertEqual(len(fx.crash_files()), 1)
        self.assertEqual(fx.launch_files(), [])

    def test_existing_crash_cooldown_no_longer_blocks_pure_launch(self):
        """事故复现：crash 桶里刚记过账，实例已停止时必须能马上拉起。"""
        fx = self.make(running=False)
        fx.seed_crash_record(time.time())  # 上一轮 09:12:01 记的账
        self.assertTrue(fx.recovery.restart('offline'),
                        '实例已停止时被 crash 冷却拦住 = 第二次启动不了（事故复现）')
        fx.target.start.assert_called_once()


class TestLaunchCooldown(unittest.TestCase):
    """launch 桶的冷却只有 30 秒，且不限次数。"""

    def make(self, **kwargs):
        fx = Fixture(**kwargs)
        self.addCleanup(fx.stop)
        return fx

    def test_second_launch_within_30s_reports_cooling(self):
        fx = self.make(running=False)
        self.assertTrue(fx.recovery.restart('offline'))
        with self.assertRaises(RecoveryCoolingDown) as ctx:
            fx.recovery.restart('offline')
        self.assertTrue(0 < ctx.exception.remain <= 30,
                        f'纯启动冷却应远小于 CrashCooldownSeconds，实际 {ctx.exception.remain}')
        self.assertEqual(fx.target.start.call_count, 1, '冷却期内不得再下发启动命令')
        self.assertTrue(ctx.exception.record and ctx.exception.record.endswith('.launch.json'))
        self.assertIn('剩余', str(ctx.exception))

    def test_cooling_is_not_reported_as_failure(self):
        """冷却中不推送告警：否则 600 秒冷却会被报成连续 3 轮恢复失败。"""
        fx = self.make(running=False)
        self.assertTrue(fx.recovery.restart('offline'))
        with patch('module.handler.emulator_recovery.handle_notify', autospec=True) as notify:
            with self.assertRaises(RecoveryCoolingDown):
                fx.recovery.restart('offline')
        notify.assert_not_called()

    def test_cooling_keeps_device_cache_clean(self):
        """冷却中不产生失效会话残留（否则下一轮 session_guard 会无声杀进程）。"""
        fx = self.make(running=False)
        self.assertTrue(fx.recovery.restart('offline'))
        with self.assertRaises(RecoveryCoolingDown):
            fx.recovery.restart('offline')
        self.assertNotIn('device', fx.alas.__dict__)


class TestCoolingMessage(unittest.TestCase):
    """冷却记录必须自带剩余时间与文件路径，别再只给一句话。"""

    def test_reserve_reports_remain_and_path(self):
        with tempfile.TemporaryDirectory() as root:
            with InstanceLease(IDENTITY, root) as lease:
                lease.reserve(now=1000, cooldown=600)
                with self.assertRaises(CoolingDown) as ctx:
                    lease.reserve(now=1300, cooldown=600)
            self.assertAlmostEqual(ctx.exception.remain, 300, delta=1)
            self.assertEqual(ctx.exception.record, str(lease.path))
            self.assertIn('剩余', str(ctx.exception))

    def test_quota_exhausted_reports_record_path(self):
        with tempfile.TemporaryDirectory() as root:
            with InstanceLease(IDENTITY, root) as lease:
                for now in (1000, 2000, 3000):
                    lease.reserve(now=now, maximum=3, cooldown=0)
                with self.assertRaises(RecoveryBlocked) as ctx:
                    lease.reserve(now=4000, maximum=3, cooldown=0)
            self.assertIn(str(lease.path), str(ctx.exception))
            self.assertNotIsInstance(ctx.exception, CoolingDown)

    def test_launch_bucket_never_exhausts_quota(self):
        """拉起实例是开机正常路径：再慢也不该熔断成「请人工检查」。"""
        with tempfile.TemporaryDirectory() as root:
            with InstanceLease(IDENTITY, root) as lease:
                for index in range(50):
                    lease.reserve(now=1000 + index * 60, maximum=None,
                                  window=1800, cooldown=30, bucket='launch')
            self.assertNotEqual(lease.bucket_path('launch'), lease.path)


class TestDefaultBucketCompatibility(unittest.TestCase):
    """升级兼容：默认桶必须还是原文件名，旧记录（冷却时间戳）继续生效。"""

    def test_restart_bucket_keeps_legacy_filename(self):
        with tempfile.TemporaryDirectory() as root:
            with InstanceLease(IDENTITY, root) as lease:
                self.assertEqual(lease.bucket_path(), lease.path)
                lease.reserve(now=1000)
            legacy = list(Path(root).glob('*.json'))
            self.assertEqual(len(legacy), 1)
            self.assertFalse(legacy[0].name.endswith('.launch.json'))


class TestHandlerOrder(unittest.TestCase):
    """AST 源码锁：冷却分支必须排在 RecoveryFailed 之前才会被触发。"""

    @classmethod
    def setUpClass(cls):
        cls.source = ALAS_SOURCE.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)

    def _handler_names(self):
        out = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Try):
                names = [h.type.id for h in node.handlers if isinstance(h.type, ast.Name)]
                if 'RecoveryCoolingDown' in names:
                    out.append(names)
        return out

    def test_cooling_handler_precedes_recovery_failed(self):
        groups = self._handler_names()
        self.assertTrue(groups, 'alas.py 缺少 except RecoveryCoolingDown 分支')
        for names in groups:
            if 'RecoveryFailed' in names:
                self.assertLess(
                    names.index('RecoveryCoolingDown'), names.index('RecoveryFailed'),
                    'RecoveryCoolingDown 是 RecoveryFailed 的子类，必须写在前面，'
                    '否则永远进不到冷却等待分支')

    def test_cooling_handler_waits_without_counting_failure(self):
        """冷却分支必须是「等到点后 continue」，不能累计连续失败或 exit。"""
        lines = self.source.splitlines()
        checked = 0
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if not (isinstance(handler.type, ast.Name)
                        and handler.type.id == 'RecoveryCoolingDown'):
                    continue
                body = chr(10).join(ln.split('#', 1)[0]
                                    for ln in lines[handler.lineno - 1:handler.end_lineno])
                if '_wait_recovery_cooldown' not in body:
                    # 转发分支：_try_restart_emulator 保留异常类型后重新抛出，
                    # 由上层 device 属性/run()/loop() 决定等待时长，不在这里睡。
                    self.assertIn('raise', body,
                                  '冷却分支既没等待也没重新抛出，异常会被吞掉')
                    continue
                self.assertIn('_wait_recovery_cooldown', body,
                              '冷却分支必须调用 _wait_recovery_cooldown 睡到冷却结束')
                self.assertNotIn('consecutive_global_failures += 1', body,
                                 '冷却不是失败，不能累计连续失败轮次')
                self.assertNotIn('exit(1)', body, '冷却期间绝不退出进程')
                checked += 1
        self.assertGreaterEqual(checked, 3, '冷却分支数量异常，检查 run()/loop() 的覆盖')


class TestCooldownWait(unittest.TestCase):
    """冷却等待必须能被停止信号打断，不能把配置停不下来。"""

    def test_wait_returns_immediately_when_stop_event_set(self):
        from alas import AzurLaneAutoScript
        wait = AzurLaneAutoScript.__dict__['_wait_recovery_cooldown']
        event = threading.Event()
        event.set()
        fake = SimpleNamespace(stop_event=event)
        started = time.monotonic()
        wait(fake, RecoveryCoolingDown('模拟器恢复冷却中', remain=600))
        self.assertLess(time.monotonic() - started, 5,
                        '冷却等待没有响应停止信号：调度器会卡住无法停止')

    def test_wait_reads_class_level_stop_event_default(self):
        """stop_event 是类属性默认 None，getattr 才能取到（get __dict__ 会漏）。"""
        from alas import AzurLaneAutoScript
        source = ALAS_SOURCE.read_text(encoding='utf-8')
        start = source.index('def _wait_recovery_cooldown')
        body = source[start:source.index('def _wait_adb_ready_after_restart')]
        code = chr(10).join(ln.split('#', 1)[0] for ln in body.split(chr(10)))
        self.assertIn("getattr(self, 'stop_event'", code,
                      '用 self.__dict__.get 会漏掉类属性默认值，冷却等待变成不可中断')


class TestColdStartStateMessage(unittest.TestCase):
    def test_state_module_exposes_cooling_down(self):
        body = STATE_SOURCE.read_text(encoding='utf-8')
        self.assertIn('class CoolingDown(RecoveryBlocked)', body)
        self.assertIn('bucket_path', body)


if __name__ == '__main__':
    unittest.main()
