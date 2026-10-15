"""短猫相接行动力快照：读到 0/未知时不得写入统计（2026-10-15 事故同类修复）。

线上背景：防溢出收尾读数在游戏已退出大世界时会 OCR 出整屏垃圾并落盘，
污染统计图。`action_point_safe_get` 加门后失败不再更新属性，但
`_action_point_current/_action_point_total` 的类默认值是 0——短猫计时的
结束快照原样写入，会把 0 带进统计图（插到底的坑）。

本护栏要求：行动力读数全为 0/未知时跳过快照；读到有效值时照常记录。
"""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.statistics.opsi_runtime import finish_meow_search_timer


def make_main(**attrs):
    values = {
        'config': SimpleNamespace(config_name='test'),
        '_action_point_current': 0,
        '_action_point_total': 0,
    }
    values.update(attrs)
    return SimpleNamespace(**values)


class MeowApSnapshotGuardTests(unittest.TestCase):
    """结束快照必须有非零守卫，0/未知不落库。"""

    def _run(self, main):
        with patch('module.statistics.opsi_runtime.refresh_action_point'), \
                patch('module.statistics.opsi_runtime.meow_hazard_level_from_runtime', return_value=3), \
                patch('module.statistics.opsi_runtime.record_ap_snapshot') as record:
            # search_started_at 取当前时间 → 时长 ≈0 → 提前返回，不触达数据库
            finish_meow_search_timer(main, time.time(), 0)
        return record

    def test_zero_values_skip_snapshot(self):
        record = self._run(make_main())
        record.assert_not_called()

    def test_valid_values_record_snapshot(self):
        record = self._run(make_main(
            _action_point_current=96,
            _action_point_total_with_box=5408,
        ))
        record.assert_called_once()
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs['ap_current'], 96)
        self.assertEqual(kwargs['ap_total'], 5408)
        self.assertEqual(kwargs['source'], 'meow')

    def test_total_only_still_records(self):
        """当前行动力耗尽（0）但含箱总量有效时仍应记录。"""
        record = self._run(make_main(
            _action_point_current=0,
            _action_point_total_with_box=5408,
        ))
        record.assert_called_once()


if __name__ == '__main__':
    unittest.main()
