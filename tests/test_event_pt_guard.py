"""活动 PT 读数门禁与骤降防抖测试，不连接游戏。

覆盖两类异常来源：

- 主线任务（Main2 等）与切到主线的低耗/打捞任务跑在普通战役页
  （page_campaign），该页右上角没有 PT 追踪条，按 OCR_EVENT_PT 区域读数
  只会读到页面上的其它数字（实测被写成 0/2/7/10）→
  ``event_pt_limit_triggered`` 必须跳过读取与上限判断；
- 活动页上的单帧骤降（脏数据）→ ``get_event_pt`` 重截一帧复核：两次都骤降
  （真实消耗）才更新仪表盘，两次不一致则保留上一有效值并告警。
"""

import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class FakePtOcr:
    """按脚本顺序返回识别结果的 PT OCR 替身，行为对齐 PtOcr.last_valid。"""

    def __init__(self, readings):
        self._readings = iter(readings)
        self.last_valid = False

    def ocr(self, image):
        text = next(self._readings, '')
        self.last_valid = bool(re.search(r'\d', text))
        return text


class FakeLogRes:
    """记录 Pt 写入的 LogRes 替身，避免测试触碰真实统计数据库。

    基线与 ``_last_valid_pt`` 一致：优先读 config.modified，否则读 config.data。
    """

    writes = []
    config = None

    def __init__(self, config):
        self.__dict__['config'] = config

    def __setattr__(self, key, value):
        FakeLogRes.writes.append((key, value))

    def group(self, name):
        return self.__dict__['config'].data.get('Dashboard', {}).get(name)


class TestEventPtLimitSkip(unittest.TestCase):
    """只有真正显示活动 PT 的页面/任务才读取 PT。"""

    def make_event(self, command, name, limit):
        from module.campaign.campaign_event import CampaignEvent

        event = CampaignEvent.__new__(CampaignEvent)
        event.config = SimpleNamespace(
            EventGeneral_PtLimit=limit,
            Scheduler_Command=command,
            Campaign_Name=name,
        )
        event.reads = []
        event.disabled = []
        event.get_event_pt = lambda: event.reads.append('read') or event.pt
        event._disable_tasks = lambda tasks: event.disabled.append(tasks)
        event.pt = 0
        return event

    def test_main_task_skips_pt_read(self):
        # 主线任务跑在 page_campaign，右上角没有 PT 追踪条
        event = self.make_event('Main2', '16-4', 120000)
        self.assertFalse(event.event_pt_limit_triggered())
        self.assertEqual(event.reads, [], '主线任务不得读取活动 PT')

    def test_main_stage_three_oil_skips_pt_read(self):
        # 复现场景：ThreeOilLowCost 打主线 16-4、上限 120000——
        # 页面上读到的个位数就是这样写进仪表盘的
        event = self.make_event('ThreeOilLowCost', '16-4', 120000)
        self.assertFalse(event.event_pt_limit_triggered())
        self.assertEqual(event.reads, [], '主线关卡的 PT 读取必须跳过')

    def test_main_stage_gems_farming_skips_pt_read(self):
        event = self.make_event('GemsFarming', 'campaign_16_4', 120000)
        self.assertFalse(event.event_pt_limit_triggered())
        self.assertEqual(event.reads, [])

    def test_main_stage_three_oil_no_limit_skips_pt_read(self):
        # 未设上限时旧实现也会读一次刷新仪表盘：主线页面同样必须跳过
        event = self.make_event('ThreeOilLowCost', '16-4', 0)
        self.assertFalse(event.event_pt_limit_triggered())
        self.assertEqual(event.reads, [])

    def test_event_task_reads_and_triggers(self):
        event = self.make_event('Event', 'D3', 120000)
        event.pt = 150000
        self.assertTrue(event.event_pt_limit_triggered())
        self.assertEqual(event.reads, ['read'])
        self.assertEqual(len(event.disabled), 1, '达到上限应停用活动任务')

    def test_event_task_below_limit_not_triggered(self):
        event = self.make_event('Event', 'D3', 120000)
        event.pt = 101930
        self.assertFalse(event.event_pt_limit_triggered())
        self.assertEqual(event.reads, ['read'])

    def test_event_stage_low_cost_still_checked(self):
        # 低耗/打捞跑在活动关卡时页面照常显示 PT，上限判断不能丢
        event = self.make_event('ThreeOilLowCost', 'D3', 100000)
        event.pt = 143790
        self.assertTrue(event.event_pt_limit_triggered())
        self.assertEqual(len(event.disabled), 1)

    def test_no_limit_event_task_refreshes_dashboard(self):
        event = self.make_event('Event', 'D3', 0)
        self.assertFalse(event.event_pt_limit_triggered())
        self.assertEqual(event.reads, ['read'], '活动任务未设上限时仍应刷新仪表盘 PT')


class TestEventPtReadGuard(unittest.TestCase):
    """骤降防抖：单帧低位值不直接写仪表盘。"""

    def make_status(self, readings, recorded=None, modified=None):
        from module.campaign import campaign_status as campaign_status_module
        from module.campaign.campaign_status import CampaignStatus

        status = CampaignStatus.__new__(CampaignStatus)
        status.config = SimpleNamespace(
            data={'Dashboard': {'Pt': {'Value': recorded if recorded is not None else 0}}},
            modified=modified if modified is not None else {},
            update=lambda: None,
        )
        sleeps = []
        screenshots = []
        status.device = SimpleNamespace(
            image='img',
            sleep=lambda seconds: sleeps.append(seconds),
            screenshot=lambda: screenshots.append('shot'),
        )
        FakeLogRes.writes = []
        fake_ocr = FakePtOcr(readings)
        patcher = patch.object(campaign_status_module, 'OCR_PT', fake_ocr)
        patcher.start()
        self.addCleanup(patcher.stop)
        logres_patcher = patch.object(campaign_status_module, 'LogRes', FakeLogRes)
        logres_patcher.start()
        self.addCleanup(logres_patcher.stop)
        return status, sleeps, screenshots

    def test_normal_read_writes_dashboard(self):
        status, sleeps, screenshots = self.make_status(['X101930'])
        self.assertEqual(status.get_event_pt(), 101930)
        self.assertEqual(FakeLogRes.writes, [('Pt', 101930)])
        self.assertEqual(sleeps, [], '正常读数不做二次校验')

    def test_first_frames_without_baseline_are_accepted(self):
        # 活动刚开、仪表盘没有基线：0/1 这类小数值是合法读数，不得拦下
        for raw, expected in (('X0', 0), ('X1', 1)):
            with self.subTest(raw=raw):
                status, sleeps, screenshots = self.make_status([raw])
                self.assertEqual(status.get_event_pt(), expected)
                self.assertEqual(FakeLogRes.writes, [('Pt', expected)])
                self.assertEqual(sleeps, [])

    def test_single_frame_drop_confirmed_by_second_frame(self):
        # 真实消耗（活动商店兑换）会连续两帧读到低位，必须放行
        status, sleeps, screenshots = self.make_status(['2', '2'], recorded=101930)
        self.assertEqual(status.get_event_pt(), 2)
        self.assertEqual(FakeLogRes.writes, [('Pt', 2)])
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(screenshots, ['shot'], '二次校验必须重截一帧')

    def test_small_baseline_drop_is_not_blocked_forever(self):
        # 150 → 2 的真实归零：相对阈值同样触发复核，复核一致即接受
        status, sleeps, screenshots = self.make_status(['X2', 'X2'], recorded=150)
        self.assertEqual(status.get_event_pt(), 2)
        self.assertEqual(FakeLogRes.writes, [('Pt', 2)])

    def test_drop_rejected_when_second_frame_differs(self):
        status, sleeps, screenshots = self.make_status(['2', 'X101930'], recorded=101930)
        self.assertEqual(status.get_event_pt(), 101930, '复核不一致应保留上一有效值')
        self.assertEqual(FakeLogRes.writes, [], '异常读数不得写进仪表盘')
        self.assertEqual(sleeps, [0.5])

    def test_drop_rejected_when_second_frame_empty(self):
        # 界面切换/加载动画：复核帧什么都没读到时同样保留旧值
        status, sleeps, screenshots = self.make_status(['2', ''], recorded=101930)
        self.assertEqual(status.get_event_pt(), 101930)
        self.assertEqual(FakeLogRes.writes, [])

    def test_empty_first_frame_returns_zero_without_write(self):
        status, sleeps, screenshots = self.make_status([''], recorded=101930)
        self.assertEqual(status.get_event_pt(), 0)
        self.assertEqual(FakeLogRes.writes, [], '空读数不写仪表盘')
        self.assertEqual(sleeps, [], '空读数不是骤降，无需二次校验')

    def test_zero_with_baseline_confirmed_by_second_frame(self):
        # 玩家把 PT 花光的真实归零：连续两帧一致，按新值更新
        status, sleeps, screenshots = self.make_status(['X0', 'X0'], recorded=101930)
        self.assertEqual(status.get_event_pt(), 0)
        self.assertEqual(FakeLogRes.writes, [('Pt', 0)])

    def test_mild_drop_needs_no_confirm(self):
        # 1000 → 900 是正常波动，不该多花一帧
        status, sleeps, screenshots = self.make_status(['X900'], recorded=1000)
        self.assertEqual(status.get_event_pt(), 900)
        self.assertEqual(FakeLogRes.writes, [('Pt', 900)])
        self.assertEqual(sleeps, [])

    def test_threshold_is_relative_not_absolute(self):
        # 小基线（20）读 10 不触发复核：阈值随基线缩放，不含写死的绝对门槛
        status, sleeps, screenshots = self.make_status(['X10'], recorded=20)
        self.assertEqual(status.get_event_pt(), 10)
        self.assertEqual(sleeps, [])

    def test_pending_modified_value_used_as_baseline(self):
        # 上一轮读数还停在 config.modified（未落盘）时也要作为基线
        status, sleeps, screenshots = self.make_status(
            ['2', ''], recorded=0, modified={'Dashboard.Pt.Value': 101930})
        self.assertEqual(status.get_event_pt(), 101930)
        self.assertEqual(FakeLogRes.writes, [])
        self.assertEqual(sleeps, [0.5])

    def test_update_flag_persists_config(self):
        updates = []
        status, sleeps, screenshots = self.make_status(['X101930'])
        status.config.update = lambda: updates.append('update')
        self.assertEqual(status.get_event_pt(update=True), 101930)
        self.assertEqual(updates, ['update'])


class TestPtOcrFormatGate(unittest.TestCase):
    """识别区域里是否含数字由 OCR 类统一判定，与所用引擎无关。"""

    def test_digits_mark_valid(self):
        from module.campaign.campaign_status import PtOcr

        ocr = PtOcr.__new__(PtOcr)
        self.assertEqual(ocr.after_process('X150000'), 'X150000')
        self.assertTrue(ocr.last_valid)
        ocr.after_process('150000')
        self.assertTrue(ocr.last_valid)

    def test_empty_result_invalid(self):
        from module.campaign.campaign_status import PtOcr

        ocr = PtOcr.__new__(PtOcr)
        ocr.after_process('X')
        self.assertFalse(ocr.last_valid, '只有 X 没有数字时不得视为有效读数')
        ocr.after_process('')
        self.assertFalse(ocr.last_valid)


if __name__ == '__main__':
    unittest.main()
