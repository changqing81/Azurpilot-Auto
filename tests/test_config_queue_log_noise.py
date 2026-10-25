"""任务队列快照日志降噪：重复的「待处理任务 / 任务」不得刷 INFO。

真机现场（2026-10-25 三油低耗 16-4）：结算菜单里 task_switched() 每轮截图都走
get_next()，于是

    [配置] 待处理任务: ['ThreeOilLowCost']
    [任务] ThreeOilLowCost (Enable, 2020-01-01 00:00:00)
    [配置] 继续任务 `ThreeOilLowCost (Enable, 2020-01-01 00:00:00)`

三行连刷成片——单日 513 行「继续任务」里 115 次间隔 <0.2 秒，最长连发 9 串。

队列快照对 INFO 是噪音：真要看的「继续/切换任务」由 task_switched() 记 INFO，
任务名的节标题由 alas.py 的 logger.hr 输出。本测试锁定快照只走 debug。
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from module.config.config import AzurLaneConfig, Function
from module.logger import logger

TASK = 'ThreeOilLowCost'
NOW = datetime(2026, 10, 25, 17, 17, 49)


class MemoryConfig(AzurLaneConfig):
    """保留真实选任务实现，仅把磁盘读写换成内存数据。"""

    SCHEDULER_PRIORITY = f'{TASK} > Restart'

    def __init__(self, next_run):
        self.bound = {}
        self.modified = {}
        self.auto_update = False
        self.config_name = 'log_noise_test'
        self.data = {
            TASK: {'Scheduler': {'Enable': True, 'Command': TASK,
                                 'NextRun': next_run}},
            'Restart': {'Scheduler': {'Enable': True, 'Command': 'Restart',
                                      'NextRun': NOW + timedelta(hours=1)}},
        }
        self.task = Function(self.data[TASK])
        self.pending_task = []
        self.waiting_task = []

    def load(self):
        """沿用内存配置，模拟热重载。"""


class QueueSnapshotLogLevelTest(unittest.TestCase):
    """锁定 get_next() 的队列快照日志级别。"""

    def setUp(self):
        patcher = patch('module.config.config.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        hoist = patch.object(AzurLaneConfig, 'is_hoarding_task', False)
        hoist.start()
        self.addCleanup(hoist.stop)

    def assert_no_info_snapshot(self, config):
        """get_next() 期间不得有 INFO 级的队列快照行。"""
        with self.assertNoLogs(logger, level='INFO'):
            config.get_next()

    def test_pending_task_snapshot_is_debug(self):
        """有到期任务时，「待处理任务 / 任务」不进 INFO。"""
        config = MemoryConfig(NOW - timedelta(minutes=1))
        self.assert_no_info_snapshot(config)
        self.assertEqual(config.pending_task[0].command, TASK)

    def test_waiting_task_snapshot_is_debug(self):
        """无到期任务时，「没有待处理任务 / 任务」同样不进 INFO。"""
        config = MemoryConfig(NOW + timedelta(minutes=30))
        self.assert_no_info_snapshot(config)
        self.assertFalse(config.pending_task)
        self.assertTrue(config.waiting_task)

    def test_snapshot_still_available_in_debug(self):
        """降噪不能丢信息：debug 级别仍能看到队列快照。"""
        config = MemoryConfig(NOW - timedelta(minutes=1))
        old_level = logger.level
        logger.setLevel('DEBUG')
        self.addCleanup(logger.setLevel, old_level)
        with self.assertLogs(logger, level='DEBUG') as captured:
            config.get_next()
        text = '\n'.join(captured.output)
        self.assertIn('[配置] 待处理任务', text)
        self.assertIn('[任务]', text)


class TaskSwitchLogStillInfoTest(unittest.TestCase):
    """真正要看的「继续任务」必须保留 INFO，且每次检查只有一行。"""

    def setUp(self):
        patcher = patch('module.config.config.current_time', return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        hoist = patch.object(AzurLaneConfig, 'is_hoarding_task', False)
        hoist.start()
        self.addCleanup(hoist.stop)

    def test_task_switched_logs_single_continue_line(self):
        """未切任务时只留一行「继续任务」，不再跟随队列快照。"""
        config = MemoryConfig(NOW - timedelta(minutes=1))
        config.task = Function(config.data[TASK])

        with self.assertLogs(logger, level='INFO') as captured:
            switched = config.task_switched()

        self.assertFalse(switched)
        output = [record.getMessage() for record in captured.records]
        self.assertEqual(len(output), 1, output)
        self.assertIn('[配置] 继续任务', output[0])
        self.assertNotIn('待处理任务', output[0])


if __name__ == '__main__':
    unittest.main()
