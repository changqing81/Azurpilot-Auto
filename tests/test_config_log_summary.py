"""配置保存日志的取值摘要：高频保存日志不得整坨 dump 嵌套状态。

真机现场（2026-10-15 侵蚀1/智能调度）：一轮里 3~4 条
`[配置] 保存配置 …` 把 `OpsiScheduling.Storage.Storage.ApLedgerState`
整坨 repr 刷成长行，日志被重复状态淹没。摘要只保留键名与规模，
WebUI 手动保存（app_task_config）沿用完整值，行为不变。
"""
import unittest

from module.config.utils import dict_to_kv, summarize_value


class DictToKvSummaryTest(unittest.TestCase):
    def test_default_keeps_full_repr_for_manual_paths(self):
        """默认行为不变：WebUI 手动保存依赖完整值（GUI 实测证据格式）。"""
        modified = {'OpsiScheduling.Storage.Storage.ApLedgerState':
                    {'v': 1, 'current': 21, 'total': 4941}}
        text = dict_to_kv(modified)
        self.assertIn("'current': 21", text)
        self.assertIn("'total': 4941", text)

    def test_summarize_compresses_containers_and_long_text(self):
        modified = {
            'Dashboard.ActionPoint.Value': 21,
            'OpsiScheduling.Storage.Storage.ApLedgerState': {'v': 1, 'current': 21},
            'Some.List': [1, 2, 3],
            'Memo': 'x' * 100,
        }
        text = dict_to_kv(modified, summarize=True)
        self.assertIn('Dashboard.ActionPoint.Value=21', text)
        self.assertIn('OpsiScheduling.Storage.Storage.ApLedgerState=<dict 2 键>', text)
        self.assertIn('Some.List=<list 3 项>', text)
        self.assertNotIn('current', text)
        self.assertIn('（共 100 字符）', text)

    def test_summarize_value_keeps_scalars_and_bounds_text(self):
        self.assertEqual(summarize_value(True), 'True')
        self.assertEqual(summarize_value(None), 'None')
        self.assertEqual(summarize_value('短文本'), "'短文本'")
        self.assertNotIn('x' * 61, summarize_value('x' * 100))


if __name__ == '__main__':
    unittest.main()
