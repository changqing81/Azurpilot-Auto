"""`is_task_enabled` / `Function.enable` 对 guard 临时挂起的判据。

`Scheduler.Enable` 只表达用户意愿；guard 运行其他大世界任务期间用独立的
`GuardTempClosed` 标记让防溢出任务暂时不可调度，不改动用户配置。
"""
import unittest
from types import MethodType

from module.config.config import AzurLaneConfig, Function, GUARD_SUSPEND_FLAG_PATHS

TASK = 'OpsiPreventActionPointOverflow'


class FakeConfig:
    """只提供 `cross_get`（按 key 路径下钻）的最小替身。"""

    def __init__(self, data):
        self.data = data

    def cross_get(self, keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def make_config(task_enable, flag):
    data = {
        TASK: {
            'Scheduler': {'Enable': task_enable},
            'Storage': {'Storage': {'GuardTempClosed': flag}},
        },
        'Research': {'Scheduler': {'Enable': True}},
    }
    cfg = FakeConfig(data)
    cfg.is_task_enabled = MethodType(AzurLaneConfig.is_task_enabled, cfg)
    return cfg


class TaskEnabledGuardSuspendTests(unittest.TestCase):
    def test_enabled_when_no_suspend_flag(self):
        cfg = make_config(task_enable=True, flag=False)
        self.assertTrue(cfg.is_task_enabled(TASK))

    def test_disabled_when_user_turned_off(self):
        cfg = make_config(task_enable=False, flag=False)
        self.assertFalse(cfg.is_task_enabled(TASK))

    def test_suspended_while_guard_runs(self):
        cfg = make_config(task_enable=True, flag=True)
        self.assertFalse(cfg.is_task_enabled(TASK))

    def test_other_tasks_unaffected_by_flag(self):
        cfg = make_config(task_enable=True, flag=True)
        self.assertTrue(cfg.is_task_enabled('Research'))


class FunctionEnableTests(unittest.TestCase):
    """调度队列（`get_next_task`）用的是 `Function.enable`，必须同样受挂起约束。"""

    def _data(self, enable, flag):
        return {
            'Scheduler': {'Enable': enable, 'Command': TASK, 'NextRun': None},
            'Storage': {'Storage': {'GuardTempClosed': flag}},
        }

    def test_function_enable_false_when_suspended(self):
        self.assertFalse(Function(self._data(True, True)).enable)

    def test_function_enable_true_without_flag(self):
        self.assertTrue(Function(self._data(True, False)).enable)

    def test_function_enable_false_when_user_disabled(self):
        self.assertFalse(Function(self._data(False, False)).enable)

    def test_guard_suspend_paths_registered(self):
        self.assertIn(TASK, GUARD_SUSPEND_FLAG_PATHS)


if __name__ == '__main__':
    unittest.main()
