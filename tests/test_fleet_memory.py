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

    def test_pending_change_forces_change(self):
        """换装事务未收尾时，无论等级高低都必须先换船换装。"""
        for memory in ({'change_pending': True},
                       {'change_pending': True, 'flagship_lv': 1},
                       {'change_pending': True, 'flagship_lv': 40},
                       {'change_pending': True, 'flagship_lv': 'bad'}):
            with self.subTest(memory=memory):
                self.assertEqual(decide_flagship_check(memory), 'change')

    def test_pending_false_falls_back_to_level(self):
        """change_pending 为 False / 非 True 时不影响原有等级判定。"""
        self.assertEqual(decide_flagship_check({'change_pending': False, 'flagship_lv': 5}), 'skip')
        self.assertEqual(decide_flagship_check({'change_pending': 1, 'flagship_lv': 5}), 'skip')


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

    def test_change_pending_roundtrip_without_levels(self):
        """换装标记在没有等级数据时也能独立落盘与清除。"""
        self.memory.set_change_pending('ThreeOilLowCost', True, fleet_order='fleet1_all')
        self.assertTrue(self.memory.is_change_pending('ThreeOilLowCost', 'fleet1_all'))
        self.memory.set_change_pending('ThreeOilLowCost', False, fleet_order='fleet1_all')
        self.assertFalse(self.memory.is_change_pending('ThreeOilLowCost', 'fleet1_all'))

    def test_change_pending_survives_level_write(self):
        """等级写回（任务退出 finally）不得冲掉未完成标记。"""
        self.memory.set_change_pending('ThreeOilLowCost', True, fleet_order='fleet1_all')
        self.memory.write('ThreeOilLowCost', flagship_lv=3, fleet_order='fleet1_all')
        entry = self.memory.read('ThreeOilLowCost', 'fleet1_all')
        self.assertTrue(entry['change_pending'])
        self.assertEqual(entry['flagship_lv'], 3)
        self.assertTrue(self.memory.is_change_pending('ThreeOilLowCost', 'fleet1_all'))

    def test_change_pending_scoped_by_fleet_order(self):
        self.memory.set_change_pending('ThreeOilLowCost', True, fleet_order='fleet1_all')
        self.assertFalse(self.memory.is_change_pending(
            'ThreeOilLowCost', 'fleet1_standby_fleet2_all'))

    def test_change_pending_expires_with_record(self):
        """标记随记录一起过期，避免长期停用后反复重做换装。"""
        self.memory.set_change_pending('ThreeOilLowCost', True, fleet_order='fleet1_all')
        data = self._read_memory_file()
        data['ThreeOilLowCost']['record'] = (datetime.now() - timedelta(hours=25)).isoformat()
        self._write_memory_file(data)
        self.assertFalse(self.memory.is_change_pending('ThreeOilLowCost', 'fleet1_all'))

    def test_change_pending_corrupted_file_degrades(self):
        self.memory.set_change_pending('ThreeOilLowCost', True, fleet_order='fleet1_all')
        self._write_raw_text('{not a json')
        self.assertFalse(self.memory.is_change_pending('ThreeOilLowCost', 'fleet1_all'))

    def test_change_code_kept_until_cleared(self):
        """装备码随标记持久化；只刷新标记不得冲掉它，收尾才清除。"""
        self.memory.set_change_pending(
            'ThreeOilLowCost', True, fleet_order='fleet1_all', code='AAAA')
        self.assertEqual(self.memory.get_change_code('ThreeOilLowCost', 'fleet1_all'), 'AAAA')
        # _begin_change_transaction() 在导出之前先落标记，此时不得清掉已有码
        self.memory.set_change_pending('ThreeOilLowCost', True, fleet_order='fleet1_all')
        self.assertEqual(self.memory.get_change_code('ThreeOilLowCost', 'fleet1_all'), 'AAAA')
        self.memory.set_change_pending('ThreeOilLowCost', False, fleet_order='fleet1_all')
        self.assertIsNone(self.memory.get_change_code('ThreeOilLowCost', 'fleet1_all'))

    def test_change_code_invalid_values_ignored(self):
        for value in (None, '', '   ', 123, [], {}):
            with self.subTest(value=value):
                self.memory.set_change_pending(
                    'ThreeOilLowCost', True, fleet_order='fleet1_all', code=value)
                self.assertIsNone(self.memory.get_change_code('ThreeOilLowCost', 'fleet1_all'))


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

    # 与 EquipmentCodeHandler 同名的跨进程恢复槽位
    resume_code = None

    def __init__(self, config, change_flagship=True, campaign=None):
        self.config = config
        self._change_flagship = change_flagship
        self.campaign = campaign
        self.resume_code = None

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


class ChangeTransactionTest(unittest.TestCase):
    """换船/换装事务：未完成标记 + 任务切换保护。

    核心场景：换装过程中被用户手动打断 → 标记落盘 → 重启后强制重做，
    不再以空装备状态直接出击。
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch('module.campaign.fleet_memory.LOG_DIR', Path(directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _new_task(self, **kwargs):
        task = _FakeTask(_make_config(**kwargs))
        task._init_fleet_memory()
        return task

    def test_init_pending_forces_change(self):
        """上次换装未完成：即便等级合格也强制重做换船换装。"""
        FleetMemory('alas').write('ThreeOilLowCost', flagship_lv=5, fleet_order='fleet1_all')
        FleetMemory('alas').set_change_pending('ThreeOilLowCost', True, fleet_order='fleet1_all')
        task = _FakeTask(_make_config())
        self.assertTrue(task._init_fleet_memory())
        self.assertTrue(task._change_pending)

    def test_init_pending_overrides_allow_high(self):
        """允许高等级旗舰时也不能跳过未完成的换装事务。"""
        FleetMemory('alas').set_change_pending('ThreeOilLowCost', True, fleet_order='fleet1_all')
        self.assertTrue(_FakeTask(_make_config(allow_high=True))._init_fleet_memory())

    def test_transaction_success_clears_pending(self):
        task = self._new_task()
        self.assertTrue(task._change_transaction(lambda: True))
        self.assertFalse(task._change_pending)
        self.assertFalse(FleetMemory('alas').is_change_pending('ThreeOilLowCost', 'fleet1_all'))
        self.assertFalse(task.config._disable_task_switch)

    def test_transaction_disables_task_switch_while_running(self):
        """事务期间禁止任务切换，结束后还原为原值。"""
        task = self._new_task()
        seen = {}

        def _probe():
            seen['during'] = task.config._disable_task_switch
            return True

        task._change_transaction(_probe)
        self.assertTrue(seen['during'])
        self.assertFalse(task.config._disable_task_switch)

    def test_transaction_keeps_pending_on_interrupt(self):
        """用户手动打断（TaskEnd）判为换装失败：保留标记，重启后重做。"""
        task = self._new_task()

        def _interrupted():
            raise _FakeTaskEnd

        with self.assertRaises(_FakeTaskEnd):
            task._change_transaction(_interrupted)
        self.assertTrue(FleetMemory('alas').is_change_pending('ThreeOilLowCost', 'fleet1_all'))
        # 任务切换开关同样必须还原，避免异常逃逸后永久锁死
        self.assertFalse(task.config._disable_task_switch)
        # 下一次启动（新实例）：强制重做换船换装
        resumed = _FakeTask(_make_config())
        self.assertTrue(resumed._init_fleet_memory())

    def test_transaction_restores_preexisting_switch_flag(self):
        """事务前已是 True（嵌套在其他上下文中）时不能被还原成 False。"""
        config = _make_config()
        config._disable_task_switch = True
        task = _FakeTask(config)
        task._init_fleet_memory()
        task._change_transaction(lambda: True)
        self.assertTrue(config._disable_task_switch)

    def test_transaction_graceful_failure_clears_pending(self):
        """无可用舰船属正常收尾（装备已在流程末尾恢复），不算换装失败。"""
        task = self._new_task()
        self.assertFalse(task._change_transaction(lambda: False))
        self.assertFalse(FleetMemory('alas').is_change_pending('ThreeOilLowCost', 'fleet1_all'))

    def test_save_memory_keeps_pending_after_interrupt(self):
        """run() 的 finally 写回等级时不得冲掉未完成标记。"""
        task = _FakeTask(_make_config(), campaign=_FakeCampaign([1, -1, -1, 1, -1, -1]))
        task._init_fleet_memory()
        with self.assertRaises(_FakeTaskEnd):
            task._change_transaction(lambda: (_ for _ in ()).throw(_FakeTaskEnd))
        task._save_fleet_memory()
        entry = FleetMemory('alas').read('ThreeOilLowCost', 'fleet1_all')
        self.assertTrue(entry['change_pending'])
        self.assertEqual(entry['flagship_lv'], 1)

    def test_exported_code_persisted_and_reused_after_restart(self):
        """换船后才被打断：重启时目标船型无配置码，仍能复用中断前导出的方案。"""
        code = 'MkpKQS9JUkUvRzNELzQ4TC80OElcMA=='
        task = self._new_task()
        task._on_equip_code_exported(code)
        self.assertEqual(
            FleetMemory('alas').get_change_code('ThreeOilLowCost', 'fleet1_all'), code)
        resumed = _FakeTask(_make_config())
        self.assertTrue(resumed._init_fleet_memory())
        self.assertEqual(resumed.resume_code, code)

    def test_clear_change_pending_drops_persisted_code(self):
        code = 'MkpKQS9JUkUvRzNELzQ4TC80OElcMA=='
        task = self._new_task()
        task._on_equip_code_exported(code)
        task.resume_code = code
        task._clear_change_pending()
        self.assertIsNone(task.resume_code)
        self.assertIsNone(
            FleetMemory('alas').get_change_code('ThreeOilLowCost', 'fleet1_all'))
        self.assertFalse(
            FleetMemory('alas').is_change_pending('ThreeOilLowCost', 'fleet1_all'))


if __name__ == '__main__':
    unittest.main()
