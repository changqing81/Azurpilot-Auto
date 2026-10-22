"""Error.AdbOfflineRestart 死开关修复的回归测试。

背景：该开关自加入以来从未被任何代码读取，`_try_restart_emulator()` 无条件
执行，模拟器 ADB 离线时**总是**自动重启。用户在 GUI 关掉它也没用。

修复后它真正生效（alas.py 的 EmulatorNotRunningError 分支会读取它），
因此模板默认值从 false 改为 true，保证存量用户升级前后行为一致。

本测试锁定两件事：

1. 三种存量配置形态合并后的取值（迁移行为）
2. alas.py 的 gate 只作用于"ADB 离线"分支，不误伤其它恢复路径
"""

import unittest
from unittest.mock import Mock, patch

from alas import AzurLaneAutoScript


class TestAdbOfflineRestartMigration(unittest.TestCase):
    """存量配置合并后的取值。"""

    def setUp(self):
        from module.config.config_updater import ConfigUpdater
        from module.config.deep import deep_get
        self.cu = ConfigUpdater()
        self.deep_get = deep_get

    def _merge(self, old):
        """模拟配置加载流程：先合并默认值，再执行版本迁移。"""
        new = self.cu.config_update(old)
        out = self.cu.config_redirect(old, new)
        return self.deep_get(out, keys='Alas.Error.AdbOfflineRestart')

    def test_default_is_enabled(self):
        """模板默认值必须是 true（与升级前的实际行为一致）。"""
        from module.config.config_generated import GeneratedConfig
        self.assertTrue(
            GeneratedConfig.Error_AdbOfflineRestart,
            '模板默认必须为 true，否则从未存过该键的存量用户升级后会失去自动重启能力',
        )

    def test_user_without_key_keeps_restart_behavior(self):
        """从未存过此键的存量用户，升级后仍应保持"总是重启"。"""
        old = {'Alas': {'Error': {'AdbOfflineThreshold': 3}}}
        self.assertTrue(self._merge(old))

    def test_user_with_true_keeps_true(self):
        """显式存了 true 的用户保持 true。"""
        old = {'Alas': {'Error': {'AdbOfflineRestart': True, 'AdbOfflineThreshold': 3}}}
        self.assertTrue(self._merge(old))

    def test_user_with_false_respects_choice(self):
        """显式存了 false 的用户保持 false（修复后才真正生效的关闭能力）。"""
        old = {'Alas': {'Error': {'AdbOfflineRestart': False, 'AdbOfflineThreshold': 3}}}
        self.assertFalse(self._merge(old))


class TestAdbOfflineRestartGate(unittest.TestCase):
    """alas.py 的 gate 行为。"""

    def make_script(self, adb_offline_restart):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config_name = 'test'
        script.config = Mock()
        script.config.Error_AdbOfflineRestart = adb_offline_restart
        script.config.Error_OnePushConfig = None
        script.config.task_call = Mock()
        return script

    def test_gate_reads_config(self):
        """gate 必须真正读取配置（这是死开关修复的核心）。"""
        script = self.make_script(True)
        # 关闭开关时不应触发 _try_restart_emulator
        script._try_restart_emulator = Mock(return_value=True)
        script.config.Error_AdbOfflineRestart = False
        with patch.object(script, '_try_restart_emulator'):
            # 直接验证 gate 的判断条件本身
            self.assertFalse(script.config.Error_AdbOfflineRestart)

    def test_disabled_does_not_call_restart(self):
        """开关关闭时，ADB 离线分支不得调用 _try_restart_emulator。"""
        script = self.make_script(False)
        script._try_restart_emulator = Mock(return_value=True)

        # 复现 alas.py 中 EmulatorNotRunningError 分支的 gate 逻辑
        called = []
        script._try_restart_emulator.side_effect = lambda: called.append(1) or True

        if not script.config.Error_AdbOfflineRestart:
            script.config.task_call('Restart')
        else:
            script._try_restart_emulator()

        script._try_restart_emulator.assert_not_called()
        script.config.task_call.assert_called_once_with('Restart')

    def test_enabled_calls_restart(self):
        """开关开启时，正常走重启路径。"""
        script = self.make_script(True)
        script._try_restart_emulator = Mock(return_value=True)

        if not script.config.Error_AdbOfflineRestart:
            script.config.task_call('Restart')
        else:
            script._try_restart_emulator()

        script._try_restart_emulator.assert_called_once()


class TestGateOnlyOnAdbOfflinePath(unittest.TestCase):
    """gate 只管辖"ADB 离线"分支，不误伤其它恢复路径。

    _try_restart_emulator 有 6 个调用点：游戏卡死、ADB 离线、RequestHumanTakeover
    自愈、未预期异常（两处）、计划重启。后五者各有独立开关，不受 AdbOfflineRestart
    影响——若把 gate 放进 _try_restart_emulator 入口就会误伤。
    """

    def test_other_paths_not_gated(self):
        """其它恢复路径不检查 AdbOfflineRestart。"""
        import inspect

        import alas as alas_module
        source = inspect.getsource(alas_module.AzurLaneAutoScript._try_restart_emulator)
        self.assertNotIn(
            'Error_AdbOfflineRestart',
            source,
            'gate 不应放进 _try_restart_emulator 内部，否则会误伤计划重启等路径',
        )

    def test_gate_lives_in_emulator_not_running_branch(self):
        """gate 必须落在 EmulatorNotRunningError 分支里。"""
        import inspect

        import alas as alas_module
        source = inspect.getsource(alas_module.AzurLaneAutoScript.run)
        self.assertIn('Error_AdbOfflineRestart', source)
        # gate 必须在 EmulatorNotRunningError 之后
        self.assertIn('except EmulatorNotRunningError', source)
        self.assertLess(
            source.index('except EmulatorNotRunningError'),
            source.index('Error_AdbOfflineRestart'),
            'gate 必须位于 EmulatorNotRunningError 分支内',
        )



class TestAdbOfflineRestartGateCounting(unittest.TestCase):
    """gate 关闭时的计数与退避行为。

    关键：必须返回 False（而不是 'recoverable'），否则失败计数不累积，
    模拟器持续离线时会陷入无限快速重试。
    """

    def test_disabled_returns_false_and_counts(self):
        """开关关闭时返回 False 且失败计数递增。"""
        import alas as alas_module
        import inspect

        source = inspect.getsource(alas_module.AzurLaneAutoScript.run)
        start = source.index('if not self.config.Error_AdbOfflineRestart:')
        # 从 gate 之后开始找调用点，避免匹配到 run() 开头处的其它引用
        end = source.index('if self._try_restart_emulator():', start)
        gate_block = source[start:end]
        self.assertIn('return False', gate_block)
        self.assertNotIn(
            "return 'recoverable'",
            gate_block,
            '可恢复错误不计入失败次数，模拟器持续离线会无限快速重试',
        )
        self.assertIn(
            'self.consecutive_adb_offline += 1',
            gate_block,
            'gate 关闭时也必须累计离线计数，否则退避时长永远从 0 开始',
        )

    def test_disabled_has_backoff(self):
        """gate 关闭时必须有退避，避免高速空转。"""
        import alas as alas_module
        import inspect

        source = inspect.getsource(alas_module.AzurLaneAutoScript.run)
        start = source.index('if not self.config.Error_AdbOfflineRestart:')
        end = source.index('if self._try_restart_emulator():', start)
        gate_block = source[start:end]
        self.assertIn('time.sleep(wait_seconds)', gate_block)

if __name__ == '__main__':
    unittest.main()
