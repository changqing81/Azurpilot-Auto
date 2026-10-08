"""科研批量模式回归：配置开关、显示时长解析与调度判定纯函数。"""

import inspect
import unittest
from datetime import timedelta
from unittest.mock import patch

from module.config.config import AzurLaneConfig, name_to_function
from module.config.utils import read_file
from module.research.research import RewardResearch, batch_all_completed, batch_total_remaining
from module.research.rqueue import parse_display_time


def make_config(**groups):
    """使用真实更新和绑定流程，全程在内存中操作配置。"""
    config = AzurLaneConfig('template')
    config.auto_update = False
    config.data = config.config_update({'Research': groups})
    config.bind('Research')
    config.task = name_to_function('Research')
    return config


class TestResearchBatchConfig(unittest.TestCase):
    def test_batch_mode_defaults_to_off(self):
        """新配置默认关闭，旧配置经 config_update 自动补齐。

        Research 任务的参数组名恰好也是 Research，故更新键为组名一层。
        """
        self.assertIs(make_config().Research_BatchMode, False)
        self.assertIs(make_config(Research={'BatchMode': True}).Research_BatchMode, True)

    def test_batch_mode_visible_in_webui(self):
        """开关必须对 GUI 可编辑：checkbox 类型且未隐藏。"""
        definition = read_file('module/config/argument/args.json')['Research']['Research']['BatchMode']
        self.assertEqual(definition.get('type'), 'checkbox')
        self.assertIs(definition.get('value'), False)
        self.assertNotIn(definition.get('display'), ('hide', 'disabled'))

    def test_fill_queue_keeps_sixth_by_default(self):
        """既有调用方不传参时行为不变（第 6 个项目照常处理）。"""
        signature = inspect.signature(RewardResearch.research_fill_queue)
        self.assertIs(signature.parameters['include_sixth'].default, True)


class TestDisplayTimeParsing(unittest.TestCase):
    def test_parse_display_time(self):
        """合法时长、误读纠正与噪声输入。"""
        cases = {
            '02:25:57': timedelta(hours=2, minutes=25, seconds=57),
            '00:00:00': timedelta(0),
            ' 1:00:00 ': timedelta(hours=1),
            'I2:25:57': timedelta(hours=12, minutes=25, seconds=57),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_display_time(text), expected)
        for text in ('', 'EMPTY', '等待进行', '12:3X:45', '01:70:00', 'ABCD'):
            with self.subTest(text=text):
                self.assertIsNone(parse_display_time(text))


class TestBatchScheduling(unittest.TestCase):
    """批量模式调度判定纯函数（times: 'empty'=空槽，其余为秒数）。

    判定只看队列 5 槽：队列外正在进行的第 6 个项目不识别、不参与。
    """

    @staticmethod
    def times(*items):
        return [None if v == 'empty' else timedelta(seconds=v) for v in items]

    def test_sixth_not_involved(self):
        """判定函数只接受队列 5 槽，第 6 个已从签名中移除。"""
        for func in (batch_all_completed, batch_total_remaining):
            with self.subTest(func=func.__name__):
                self.assertEqual(list(inspect.signature(func).parameters), ['times'])

    def test_snapshot_returns_queue_only(self):
        """快照只返回队列 5 槽，不再返回第 6 个的时长。"""
        runner = RewardResearch.__new__(RewardResearch)
        with patch.object(RewardResearch, 'queue_enter', autospec=True), \
                patch.object(RewardResearch, 'queue_quit', autospec=True), \
                patch.object(RewardResearch, 'get_queue_display_times', autospec=True,
                             return_value=self.times(0, 3600, 0, 0, 0)):
            result = runner._batch_snapshot()
        self.assertEqual(result, self.times(0, 3600, 0, 0, 0))

    def test_all_completed(self):
        """全空、全 00:00:00 及手动收取后的空/完成混合都算收获时机。"""
        self.assertTrue(batch_all_completed(self.times('empty', 'empty', 'empty', 'empty', 'empty')))
        self.assertTrue(batch_all_completed(self.times(0, 0, 0, 0, 0)))
        self.assertTrue(batch_all_completed(self.times(0, 'empty', 0, 'empty', 0)))

    def test_not_all_completed(self):
        """任一正时长（进行中/等待中）都不是收获时机。"""
        self.assertFalse(batch_all_completed(self.times(0, 3600, 0, 0, 0)))

    def test_total_remaining(self):
        """进行中 + 等待按队列 5 槽时长求和（FIFO 管线剩余总时长）。"""
        times = self.times(8757, 3600, 1800, 9000, 3600)
        self.assertEqual(batch_total_remaining(times),
                         timedelta(hours=7, minutes=25, seconds=57))
        self.assertEqual(batch_total_remaining(self.times('empty', 'empty', 0, 'empty', 0)), timedelta(0))
        self.assertEqual(batch_total_remaining(self.times('empty', 'empty', 'empty', 'empty', 'empty')),
                         timedelta(0))


if __name__ == '__main__':
    unittest.main()
