"""舰队等级记忆（FleetMemory）读写、决策与混入行为测试。

覆盖：
- decide_flagship_check 三分支决策（跳过 / 立即换船 / 实地核查）
- lv_flagship_vanguard 的 OCR 等级提取
- FleetMemory 的读写回环、按任务隔离、TTL 过期、舰队顺序校验、
  部分写入合并、损坏文件降级、路径穿越校验
- FleetMemoryMixin 与宿主类的接线：开局决策与退出写回
  （模拟"被打断后恢复不再重复换船检查"的核心场景）
"""

import json
import os
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from module.campaign.fleet_memory import (
    FleetMemory,
    FleetMemoryMixin,
    decide_flagship_check,
    lv_flagship_vanguard,
)


class DecideFlagshipCheckTest(unittest.TestCase):
    """开局决策函数的三分支。"""

    def test_no_memory_checks(self):
        """无记忆 / 缺字段 / 非法等级时都回退实地核查。"""
        for memory in (None, {}, {'vanguard_lv': 5}, {'flagship_lv': 0},
                       {'flagship_lv': -1}, {'flagship_lv': '12'}, {'flagship_lv': True}):
            with self.subTest(memory=memory):
                self.assertEqual(decide_flagship_check(memory), 'check')

    def test_valid_level_skips(self):
        """旗舰等级 1~31 级：跳过初始检查直接出击。"""
        for lv in (1, 5, 31):
            with self.subTest(lv=lv):
                self.assertEqual(decide_flagship_check({'flagship_lv': lv}), 'skip')

    def test_over_limit_changes(self):
        """旗舰等级 ≥32：出击前先换船。"""
        for lv in (32, 40, 125):
            with self.subTest(lv=lv):
                self.assertEqual(decide_flagship_check({'flagship_lv': lv}), 'change')


class LvFlagshipVanguardTest(unittest.TestCase):
    """战斗 OCR 等级的旗舰/先锋提取。"""

    def test_invalid_input(self):
        for lv in (None, [], [1, 2], [-1] * 7):
            with self.subTest(lv=lv):
                self.assertEqual(lv_flagship_vanguard(lv), (None, None))

    def test_unknown_positions_ignored(self):
        self.assertEqual(lv_flagship_vanguard([-1] * 6), (None, None))

    def test_extract_flagship_and_vanguard_max(self):
        self.assertEqual(lv_flagship_vanguard([5, -1, -1, 12, 3, -1]), (5, 12))
        self.assertEqual(lv_flagship_vanguard([32, 1, 1, 1, 1, 1]), (32, 1))
        self.assertEqual(lv_flagship_vanguard([7, 8, 9, -1, -1, -1]), (7, None))


class FleetMemoryTest(unittest.TestCase):
    """FleetMemory 文件读写与有效性校验。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log_dir = Path(directory.name)
        patcher = patch('module.campaign.fleet_memory.LOG_DIR', self.log_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.memory = FleetMemory('alas')

    def _guarded(self):
        """测试内访问记忆文件前做路径包含校验，与生产 _guarded_path 同款。"""
        path = self.memory.filepath.resolve()
        path.relative_to(self.log_dir.resolve())
        return path

    def _read_memory_file(self):
        path = self._guarded()
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _write_memory_file(self, data):
        path = self._guarded()
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def _write_raw_text(self, text):
        path = self._guarded()
        path.write_text(text, encoding='utf-8')

    def test_write_read_roundtrip(self):
        self.memory.write('ThreeOilLowCost', flagship_lv=5, vanguard_lv=3, fleet_order='fleet1_all')
        entry = self.memory.read('ThreeOilLowCost', 'fleet1_all')
        self.assertEqual(entry['flagship_lv'], 5)
        self.assertEqual(entry['vanguard_lv'], 3)
        self.assertIn('record', entry)
        # 原子写：不残留临时文件
        self.assertEqual([p.name for p in self.memory.filepath.parent.iterdir()],
                         [self.memory.filepath.name])

    def test_tasks_are_isolated(self):
        self.memory.write('GemsFarming', flagship_lv=40, fleet_order='fleet1_all')
        self.memory.write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        self.assertEqual(self.memory.read('GemsFarming', 'fleet1_all')['flagship_lv'], 40)
        self.assertEqual(self.memory.read('ThreeOilLowCost', 'fleet1_all')['flagship_lv'], 5)

    def test_partial_write_merges(self):
        self.memory.write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        self.memory.write('ThreeOilLowCost', vanguard_lv=7, fleet_order='fleet1_all')
        entry = self.memory.read('ThreeOilLowCost', 'fleet1_all')
        self.assertEqual(entry['flagship_lv'], 5)
        self.assertEqual(entry['vanguard_lv'], 7)

    def test_write_without_levels_keeps_record(self):
        self.memory.write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        before = self.memory.read('ThreeOilLowCost', 'fleet1_all')['record']
        self.memory.write('ThreeOilLowCost', flagship_lv=None, vanguard_lv=None)
        entry = self.memory.read('ThreeOilLowCost', 'fleet1_all')
        self.assertEqual(entry['record'], before)

    def test_read_expired_returns_none(self):
        self.memory.write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        data = self._read_memory_file()
        data['ThreeOilLowCost']['record'] = (datetime.now() - timedelta(hours=25)).isoformat()
        self._write_memory_file(data)
        self.assertIsNone(self.memory.read('ThreeOilLowCost', 'fleet1_all'))

    def test_read_fleet_order_mismatch_returns_none(self):
        self.memory.write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        self.assertIsNone(self.memory.read('ThreeOilLowCost', 'fleet1_standby_fleet2_all'))

    def test_read_missing_command_returns_none(self):
        self.assertIsNone(self.memory.read('Ambush11', 'fleet1_all'))

    def test_corrupted_file_degrades_to_no_memory(self):
        self.memory.write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        self._write_raw_text('{not a json')
        self.assertIsNone(self.memory.read('ThreeOilLowCost', 'fleet1_all'))

    def test_invalid_config_name_rejected(self):
        """含上级目录、路径分隔符、盘符的实例名一律拒绝。"""
        # os.path.pardir 即上级目录符，与分隔符拼接构造穿越样例
        traversal = os.path.pardir + '/evil'
        for name in (traversal, os.path.pardir, 'a/b', 'a\\b', 'C:evil', ''):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    FleetMemory(name)

    def test_unicode_config_name_accepted(self):
        memory = FleetMemory('小号')
        memory.write('ThreeOilLowCost', flagship_lv=9, fleet_order='fleet1_all')
        self.assertEqual(memory.read('ThreeOilLowCost', 'fleet1_all')['flagship_lv'], 9)


class _FakeCampaign:
    """实现混入回退所需的 lv 属性。"""

    def __init__(self, lv=None):
        self.lv = lv


class _FakeTaskEnd(Exception):
    """模拟调度器的 TaskEnd 中断异常。"""


def _make_config(command='ThreeOilLowCost', fleet_order='fleet1_all', allow_high=False):
    return types.SimpleNamespace(
        config_name='alas',
        task=types.SimpleNamespace(command=command),
        Fleet_FleetOrder=fleet_order,
        GemsFarming_AllowHighFlagshipLevel=allow_high,
    )


class _FakeTask(FleetMemoryMixin):
    """实现混入所依赖的最小宿主接口：config / change_flagship / campaign。"""

    def __init__(self, config, change_flagship=True, campaign=None):
        self.config = config
        self._change_flagship = change_flagship
        self.campaign = campaign

    @property
    def change_flagship(self):
        return self._change_flagship


class FleetMemoryMixinTest(unittest.TestCase):
    """混入与宿主类的接线行为。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch('module.campaign.fleet_memory.LOG_DIR', Path(directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_init_flags(self):
        """关闭换船 / 允许高等级旗舰时，混入不介入初始检查。"""
        task = _FakeTask(_make_config(allow_high=True))
        self.assertFalse(task._init_fleet_memory())
        task = _FakeTask(_make_config(), change_flagship=False)
        self.assertFalse(task._init_fleet_memory())

    def test_init_without_memory_needs_check(self):
        task = _FakeTask(_make_config())
        self.assertTrue(task._init_fleet_memory())

    def test_init_skips_with_valid_memory(self):
        FleetMemory('alas').write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        task = _FakeTask(_make_config())
        self.assertFalse(task._init_fleet_memory())

    def test_task_commands_are_isolated(self):
        """紧急委托与三油低耗的记忆互不影响。"""
        FleetMemory('alas').write('GemsFarming', flagship_lv=40, fleet_order='fleet1_all')
        # GemsFarming 读到 40 级记忆（需换船），ThreeOilLowCost 无自己的记忆（需核查）
        self.assertTrue(_FakeTask(_make_config(command='GemsFarming'))._init_fleet_memory())
        self.assertTrue(_FakeTask(_make_config(command='ThreeOilLowCost'))._init_fleet_memory())
        # 给 ThreeOilLowCost 写入合格记忆后即跳过，证明没有被 GemsFarming 的 40 级串扰
        FleetMemory('alas').write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        self.assertFalse(_FakeTask(_make_config(command='ThreeOilLowCost'))._init_fleet_memory())

    def test_save_from_battle_ocr(self):
        """被打断退出时从最后 OCR 写回等级（核心场景）。"""
        task = _FakeTask(_make_config(), campaign=_FakeCampaign([12, -1, -1, 8, 3, -1]))
        task._init_fleet_memory()
        task._save_fleet_memory()
        entry = FleetMemory('alas').read('ThreeOilLowCost', 'fleet1_all')
        self.assertEqual(entry['flagship_lv'], 12)
        self.assertEqual(entry['vanguard_lv'], 8)

    def test_save_exact_hooks_override_ocr(self):
        """换船流程记下的精确等级优先于换船前的旧 OCR。"""
        task = _FakeTask(_make_config(), campaign=_FakeCampaign([50, 1, 1, 40, 40, 40]))
        task._init_fleet_memory()
        task._memory_flagship_lv = 1
        task._memory_vanguard_lv = 2
        task._save_fleet_memory()
        entry = FleetMemory('alas').read('ThreeOilLowCost', 'fleet1_all')
        self.assertEqual(entry['flagship_lv'], 1)
        self.assertEqual(entry['vanguard_lv'], 2)

    def test_save_partial_hook_falls_back_to_ocr(self):
        """旗舰换成功、先锋未换：旗舰用精确值，先锋回退 OCR（船未变，旧值即现值）。"""
        task = _FakeTask(_make_config(), campaign=_FakeCampaign([32, -1, -1, 30, 30, 30]))
        task._init_fleet_memory()
        task._memory_flagship_lv = 1
        task._save_fleet_memory()
        entry = FleetMemory('alas').read('ThreeOilLowCost', 'fleet1_all')
        self.assertEqual(entry['flagship_lv'], 1)
        self.assertEqual(entry['vanguard_lv'], 30)

    def test_save_without_any_data_keeps_old_record(self):
        """没有任何可信数据（如还没进图就崩）不落盘，旧记录原样保留。"""
        FleetMemory('alas').write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        before = FleetMemory('alas').read('ThreeOilLowCost', 'fleet1_all')['record']
        task = _FakeTask(_make_config(), campaign=_FakeCampaign([-1] * 6))
        task._init_fleet_memory()
        task._save_fleet_memory()
        entry = FleetMemory('alas').read('ThreeOilLowCost', 'fleet1_all')
        self.assertEqual(entry['flagship_lv'], 5)
        self.assertEqual(entry['record'], before)

    def test_interrupt_resume_skips_check(self):
        """端到端：运行中被打断 → 写回等级 → 恢复后跳过初始换船检查。"""
        # 第一次运行：无记忆，需要初始检查；跑了若干仗后被 TaskEnd 打断
        first = _FakeTask(_make_config(), campaign=_FakeCampaign([9, -1, -1, 4, -1, -1]))
        self.assertTrue(first._init_fleet_memory())
        # 模拟 run() 的 finally：TaskEnd 穿过时先写记忆再向调度器抛出
        with self.assertRaises(_FakeTaskEnd):
            try:
                raise _FakeTaskEnd
            finally:
                first._save_fleet_memory()
        # 第二次运行（新实例，模拟进程重启后恢复）：读记忆直接出击
        second = _FakeTask(_make_config(), campaign=_FakeCampaign([9, -1, -1, 5, -1, -1]))
        self.assertFalse(second._init_fleet_memory())


if __name__ == '__main__':
    unittest.main()
