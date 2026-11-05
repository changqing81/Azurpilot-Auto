"""长等待关闭模拟器：开关迁移 + 可配置阈值的回归测试。

背景：该功能原来写死在 alas.py 里（`wait_duration > timedelta(hours=3)`），
GUI 上只有一个开关，用户既改不了 3 小时，也找不到参数在哪。

改造后：

1. 开关从 `Alas.Optimization` 迁到 `EmulatorSettings.EmulatorManagement`
   （GUI 上归入"模拟器设置"页），存量配置由 config_updater 的 redirection 搬运。
2. 阈值改为可配置的分钟数 `CloseEmulatorLongWaitMinutes`（默认 180 分钟）。

本测试锁定三件事：

1. 存量配置迁移后用户的选择不丢（false 仍是 false）
2. 阈值读取的兜底：脏值一律回落到 180 分钟，避免关得太勤或永不关
3. alas.py 不再出现硬编码的 3 小时（源码锁，防止改回去）
"""

import inspect
import unittest
from unittest.mock import Mock

from alas import AzurLaneAutoScript

SWITCH = 'EmulatorSettings.EmulatorOptimization.CloseEmulatorDuringLongWait'
MINUTES = 'EmulatorSettings.EmulatorOptimization.CloseEmulatorLongWaitMinutes'
WARMUP_SWITCH = 'EmulatorSettings.EmulatorOptimization.WarmupEnable'
WARMUP_MINUTES = 'EmulatorSettings.EmulatorOptimization.WarmupMinutes'


class TestSwitchMigration(unittest.TestCase):
    """开关搬迁后的存量配置取值。"""

    def setUp(self):
        from module.config.config_updater import ConfigUpdater
        from module.config.deep import deep_get

        self.cu = ConfigUpdater()
        self.deep_get = deep_get

    def _merge(self, old):
        """模拟配置加载流程：先合并默认值，再执行版本迁移。"""
        new = self.cu.config_update(old)
        out = self.cu.config_redirect(old, new)
        return self.deep_get(out, keys=SWITCH)

    def test_default_is_enabled(self):
        """模板默认值保持开启，升级前后行为一致。"""
        from module.config.config_generated import GeneratedConfig

        self.assertTrue(GeneratedConfig.EmulatorOptimization_CloseEmulatorDuringLongWait)
        self.assertEqual(GeneratedConfig.EmulatorOptimization_CloseEmulatorLongWaitMinutes, 180)
        # 预热两项也在同一分组，默认值保持不变（升级后行为不漂移）
        self.assertTrue(GeneratedConfig.EmulatorOptimization_WarmupEnable)
        self.assertEqual(GeneratedConfig.EmulatorOptimization_WarmupMinutes, 15)

    def test_user_without_key_uses_default(self):
        """从未存过此键的存量用户，升级后取模板默认值。"""
        self.assertTrue(self._merge({'Alas': {'Optimization': {'WhenTaskQueueEmpty': 'goto_main'}}}))

    def test_user_with_true_keeps_true(self):
        """显式存了 true 的用户保持 true。"""
        self.assertTrue(self._merge({
            'Alas': {'Optimization': {'CloseEmulatorDuringLongWait': True}}
        }))

    def test_user_with_false_respects_choice(self):
        """显式关掉的用户，迁移后仍然是关闭——否则等于替他重新打开。"""
        self.assertFalse(self._merge({
            'Alas': {'Optimization': {'CloseEmulatorDuringLongWait': False}}
        }))

    def test_warmup_migrated(self):
        """任务预热的开关和提前量也要跟着搬，否则用户调过的 3 分钟会丢。"""
        from module.config.deep import deep_get

        old = {'Alas': {'Optimization': {'WarmupEnable': False, 'WarmupMinutes': 3}}}
        new = self.cu.config_redirect(old, self.cu.config_update(old))
        self.assertFalse(deep_get(new, keys=WARMUP_SWITCH))
        self.assertEqual(deep_get(new, keys=WARMUP_MINUTES), 3)
        old_still_there = deep_get(
            new, keys='Alas.Optimization.WarmupMinutes', default=None
        )
        self.assertIsNone(old_still_there, '旧路径应被清掉，避免两份配置各说各话')

    def test_emulator_management_interim_path_migrated(self):
        """同一天里长等待关闭曾短暂落在 EmulatorManagement，那条路径也要能搬过来。"""
        from module.config.deep import deep_get

        old = {'EmulatorSettings': {'EmulatorManagement': {
            'CloseEmulatorDuringLongWait': True,
            'CloseEmulatorLongWaitMinutes': 15,
        }}}
        new = self.cu.config_redirect(old, self.cu.config_update(old))
        self.assertTrue(deep_get(new, keys=SWITCH))
        self.assertEqual(deep_get(new, keys=MINUTES), 15)

    def test_old_path_not_left_behind(self):
        """旧路径不应残留在迁移结果里，避免两份配置各说各话。"""
        from module.config.deep import deep_get

        new = self.cu.config_update({'Alas': {'Optimization': {'CloseEmulatorDuringLongWait': False}}})
        old_value = deep_get(
            new, keys='Alas.Optimization.CloseEmulatorDuringLongWait', default=None
        )
        self.assertIsNone(old_value)


class TestThresholdMinutes(unittest.TestCase):
    """阈值读取的兜底行为。"""

    def make_script(self, minutes):
        script = AzurLaneAutoScript.__new__(AzurLaneAutoScript)
        script.config = Mock()
        script.config.EmulatorOptimization_CloseEmulatorLongWaitMinutes = minutes
        return script

    def test_reads_config_value(self):
        """正常数值直接用。"""
        self.assertEqual(self.make_script(45)._long_wait_close_emulator_minutes(), 45)
        self.assertEqual(self.make_script(180)._long_wait_close_emulator_minutes(), 180)
        self.assertEqual(self.make_script(10080)._long_wait_close_emulator_minutes(), 10080)

    def test_non_positive_falls_back(self):
        """0 和负数会变成"立刻关闭"，必须回落到默认 180。"""
        for bad in (0, -1):
            self.assertEqual(self.make_script(bad)._long_wait_close_emulator_minutes(), 180)

    def test_dirty_value_falls_back(self):
        """非数字（手改 JSON 写坏）回落到默认 180，不抛异常。"""
        for bad in ('abc', None, '', []):
            self.assertEqual(self.make_script(bad)._long_wait_close_emulator_minutes(), 180)

    def test_numeric_string_accepted(self):
        """JSON 里写成字符串的数字仍能识别，避免误回落到 180。"""
        self.assertEqual(self.make_script('45')._long_wait_close_emulator_minutes(), 45)


class TestNoHardcodedHours(unittest.TestCase):
    """源码锁：不允许退回硬编码 3 小时。"""

    def setUp(self):
        import alas as alas_module

        self.source = inspect.getsource(alas_module.AzurLaneAutoScript.get_next_task)

    def test_uses_configurable_minutes(self):
        self.assertIn('_long_wait_close_emulator_minutes()', self.source)

    def test_no_hardcoded_three_hours(self):
        self.assertNotIn(
            'timedelta(hours=3)',
            self.source,
            '阈值已改为可配置分钟数，不得再硬编码 3 小时',
        )

    def test_switch_read_from_new_group(self):
        self.assertIn('EmulatorOptimization_CloseEmulatorDuringLongWait', self.source)
        # 注意 'EmulatorOptimization_X' 本身包含子串 'Optimization_X'，
        # 判旧属性前必须先把新前缀抹掉，否则永远误报。
        stripped = self.source.replace('EmulatorOptimization_', '')
        for stale in (
            'Optimization_CloseEmulatorDuringLongWait',
            'EmulatorManagement_CloseEmulatorDuringLongWait',
        ):
            self.assertNotIn(stale, stripped, f'旧属性 {stale} 不应再被读取')

    def test_warmup_read_from_new_group(self):
        """预热两项跟着一起搬，代码不能再读 Alas 的 Optimization_* 属性。"""
        import alas as alas_module

        full = inspect.getsource(alas_module)
        self.assertIn('EmulatorOptimization_WarmupEnable', full)
        self.assertIn('EmulatorOptimization_WarmupMinutes', full)
        self.assertNotIn('Optimization_WarmupEnable', full.replace('EmulatorOptimization_WarmupEnable', ''))
        self.assertNotIn('Optimization_WarmupMinutes', full.replace('EmulatorOptimization_WarmupMinutes', ''))


if __name__ == '__main__':
    unittest.main()
