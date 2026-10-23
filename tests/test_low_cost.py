"""低耗检测（LowCostChecker）回归测试。

覆盖：基线建立、下降累计、阈值边界、自然恢复忽略、滑动窗口淘汰、
跨任务抢占重新基线、无效读数与超过上限读数的过滤，以及触发时写进主日志的判据。
"""

import unittest
from datetime import datetime, timedelta
from unittest import mock

from module.campaign.low_cost import LowCostChecker
from module.campaign.campaign_status import CampaignStatus
from module.campaign.run import CampaignRun


class LowCostCheckerTest(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 10, 10, 9, 0, 0)

    def test_first_read_only_baselines(self):
        """首次读数只建立基线，不判定、不累计。"""
        checker = LowCostChecker(3600, 800)
        self.assertFalse(checker.update(12000, self.t0))
        self.assertEqual(checker.consumed, 0)
        self.assertEqual(checker.last_oil, 12000)

    def test_accumulate_below_limit(self):
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(11500, self.t0 + timedelta(minutes=5)))
        self.assertFalse(checker.update(11400, self.t0 + timedelta(minutes=10)))
        self.assertEqual(checker.consumed, 600)

    def test_trigger_over_limit(self):
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(11600, self.t0 + timedelta(minutes=5)))
        self.assertTrue(checker.update(11100, self.t0 + timedelta(minutes=10)))
        self.assertEqual(checker.consumed, 900)

    def test_exactly_limit_not_triggered(self):
        """阈值判定为严格大于。"""
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(11200, self.t0 + timedelta(minutes=5)))

    def test_recovery_ignored(self):
        """石油自然恢复（上升）不计入，且基线随之抬高。"""
        checker = LowCostChecker(3600, 800)
        checker.update(10000, self.t0)
        self.assertFalse(checker.update(10050, self.t0 + timedelta(minutes=5)))
        self.assertEqual(checker.consumed, 0)
        self.assertFalse(checker.update(9700, self.t0 + timedelta(minutes=10)))
        self.assertEqual(checker.consumed, 350)

    def test_sliding_window_evicts_old_events(self):
        """滑出窗口的旧消耗事件被剔除。"""
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        checker.update(11500, self.t0 + timedelta(minutes=5))  # -500
        # 65 分钟后，-500 已滑出 1 小时窗口
        self.assertFalse(checker.update(11400, self.t0 + timedelta(minutes=70)))
        self.assertEqual(checker.consumed, 100)

    def test_cross_task_preemption_rebaselines(self):
        """任务被抢占后实例销毁，恢复时重新基线，不把缺口算作自己的消耗。"""
        first_session = LowCostChecker(3600, 800)
        first_session.update(12000, self.t0)

        # 抢占期间其它任务消耗了 3000 油；恢复时新建 checker
        resumed_session = LowCostChecker(3600, 800)
        self.assertFalse(resumed_session.update(9000, self.t0 + timedelta(minutes=20)))
        self.assertEqual(resumed_session.consumed, 0)

    def test_invalid_readings_ignored(self):
        """无效读数不污染基线，也不产生事件。"""
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        for bad in (None, 0, -5, 3.5, True, 'x'):
            self.assertFalse(checker.update(bad, self.t0 + timedelta(minutes=1)))
        self.assertEqual(checker.consumed, 0)
        self.assertEqual(checker.last_oil, 12000)

    def test_reading_above_cap_skipped(self):
        """超过石油上限的读数视为 OCR 误读，跳过且不更新基线。"""
        checker = LowCostChecker(3600, 800, oil_cap=25000)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(99000, self.t0 + timedelta(minutes=1)))
        self.assertEqual(checker.last_oil, 12000)
        self.assertFalse(checker.update(11500, self.t0 + timedelta(minutes=2)))
        self.assertEqual(checker.consumed, 500)

    def test_zero_cap_disables_check(self):
        """上限缺失/为 0 时不做上限校验。"""
        checker = LowCostChecker(3600, 800, oil_cap=0)
        self.assertIsNone(checker.oil_cap)
        checker.update(12000, self.t0)
        self.assertFalse(checker.update(11500, self.t0 + timedelta(minutes=1)))
        self.assertEqual(checker.consumed, 500)


class FakeConfig:
    """触发日志用到的配置项的最小桩。"""

    config_name = 'alas'
    LowCostCheck_Window = 1
    LowCostCheck_OilLimit = 800


class FakeRun:
    """只挂低耗日志方法的桩。"""

    _log_low_cost_trigger = CampaignRun._log_low_cost_trigger
    _log_low_cost_events = CampaignRun._log_low_cost_events
    _low_cost_consumed = CampaignRun._low_cost_consumed

    def __init__(self, checker):
        self.config = FakeConfig()
        self._low_cost_checker = checker


class LowCostTriggerLogTest(unittest.TestCase):
    """触发低耗异常时，判据必须落到主日志上。"""

    def setUp(self):
        self.t0 = datetime(2026, 10, 23, 12, 6, 0)

    def _logged(self, checker, oil):
        """跑一次触发日志，返回写出去的日志行。"""
        lines = []
        with mock.patch('module.campaign.run.logger') as fake_logger:
            fake_logger.info.side_effect = lines.append
            fake_logger.attr.side_effect = lambda name, text: lines.append(f'[{name}] {text}')
            FakeRun(checker)._log_low_cost_trigger(oil)
        return lines

    def test_consumed_and_readings_logged(self):
        """累计消耗、阈值、本次读数都要能直接从日志读到。"""
        checker = LowCostChecker(3600, 800)
        checker.update(18706, self.t0)
        checker.update(16070, self.t0 + timedelta(minutes=1))
        lines = self._logged(checker, 16070)
        text = '\n'.join(lines)
        self.assertIn('窗口 1 小时累计 2636 油 / 阈值 800', text)
        self.assertIn('本次 16070', text)
        self.assertIn('窗口内下降明细', text)
        self.assertIn('-2636', text)

    def test_detail_lists_only_recent_events(self):
        """明细最多列最近 10 笔，并带上时间与降幅。"""
        checker = LowCostChecker(3600, 800)
        oil = 20000
        checker.update(oil, self.t0)
        for i in range(15):
            oil -= 100
            checker.update(oil, self.t0 + timedelta(minutes=i))
        lines = self._logged(checker, oil)
        detail = [line for line in lines if '窗口内下降明细' in line]
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0].count('-100'), 10)
        self.assertIn('12:14:00', detail[0])

    def test_no_events_skips_detail(self):
        """窗口内没有下降事件时不打明细行。"""
        checker = LowCostChecker(3600, 800)
        checker.update(12000, self.t0)
        lines = self._logged(checker, 12000)
        self.assertFalse([line for line in lines if '窗口内下降明细' in line])



class FakeLogRes:
    """LogRes 桩：group() 读 Dashboard.Oil。"""

    group_value = None

    def __init__(self, config):
        pass

    def group(self, name):
        return FakeLogRes.group_value if name == 'Oil' else None

    @property
    def Oil(self):
        return self.group_value

    @Oil.setter
    def Oil(self, value):
        FakeLogRes.group_value = value


class FakeStatusConfig:
    """get_oil 用到的配置项的最小桩。"""

    def __init__(self):
        self.modified = {}

    def update(self):
        pass


class FakeStatus:
    """只挂石油读取相关方法的桩，用脚本化读数模拟 OCR 结果。"""

    OIL_DROP_CONFIRM_RATIO = CampaignStatus.OIL_DROP_CONFIRM_RATIO
    OIL_DROP_CONFIRM_AMOUNT = CampaignStatus.OIL_DROP_CONFIRM_AMOUNT
    _ocr_oil_once = CampaignStatus._ocr_oil_once
    _oil_dropped_suspiciously = CampaignStatus._oil_dropped_suspiciously
    _get_num = CampaignStatus._get_num
    _last_valid_oil = CampaignStatus._last_valid_oil

    def __init__(self, readings, previous=None):
        self.readings = list(readings)
        self.index = 0
        self.config = FakeStatusConfig()
        self.config.modified = {}
        if previous is not None:
            self.config.modified['Dashboard.Oil.Value'] = previous
        self.config.Oil = previous
        self.screenshots = 0
        self.stored = None

    # ── 设备与 UI 桩 ──
    def appear(self, button, offset=0):
        return True

    @property
    def device(self):
        return self

    def screenshot(self):
        self.screenshots += 1

    def sleep(self, seconds):
        pass

    # ── 读数桩 ──
    def _next_reading(self):
        value = self.readings[min(self.index, len(self.readings) - 1)]
        self.index += 1
        return value

    def _get_num(self, button, name, letter=(247, 247, 247), refresh_offset=True):
        if name != 'OCR_OIL':
            return 20200
        return self._next_reading()

    def _ocr_oil_once(self):
        return self._next_reading()

    def _store(self, oil):
        self.stored = oil


class FakeTimer:
    """get_oil 的重试计时器桩：第 3 次 reached() 判超时，避免测试里真等 1 秒。"""

    def __init__(self, limit, count=0):
        self.calls = 0

    def start(self):
        return self

    def reached(self):
        self.calls += 1
        return self.calls > 2


def run_get_oil(readings, previous=None):
    """用给定读数序列跑一遍 get_oil，返回 (返回值, 桩, 日志行)。

    Args:
        readings (list[int]): 依次返回的 OCR 读数序列。
        previous (int | None): 仪表盘里的上一有效石油余量，None 表示没有基线。
    """
    fake = FakeStatus(readings, previous=previous)
    lines = []
    written = []
    FakeLogRes.group_value = previous

    class RecordingLogRes(FakeLogRes):
        """记下每次写入仪表盘的石油读数。"""

        @property
        def Oil(self):
            return FakeLogRes.group_value

        @Oil.setter
        def Oil(self, value):
            FakeLogRes.group_value = value
            written.append(value)

    with mock.patch('module.campaign.campaign_status.LogRes', RecordingLogRes), \
            mock.patch('module.campaign.campaign_status.logger') as fake_logger, \
            mock.patch('module.campaign.campaign_status.Timer', FakeTimer):
        fake_logger.info.side_effect = lines.append
        fake_logger.warning.side_effect = lines.append
        result = CampaignStatus.get_oil(fake, skip_first_screenshot=True)
    return result, fake, lines, written


class GetOilDebounceTest(unittest.TestCase):
    """get_oil 必须挡住「偏小」的 OCR 误读，否则低耗检测会累计出假消耗。"""

    def test_stable_reading_kept(self):
        """读数正常下降（小于验证门槛）时不复核，直接采信。"""
        result, fake, lines, written = run_get_oil([18690], previous=18706)
        self.assertEqual(result, 18690)
        self.assertEqual(fake.screenshots, 0)
        self.assertFalse([line for line in lines if '二次校验' in line])
        self.assertEqual(written[-1]['Value'], 18690)

    def test_truncated_reading_rescued(self):
        """14654 被读成 1404：复核读到 14654 就采信复核帧。"""
        result, fake, lines, written = run_get_oil([1404, 14654], previous=14685)
        self.assertEqual(result, 14654)
        self.assertTrue([line for line in lines if '二次校验' in line])
        self.assertEqual(written[-1]['Value'], 14654)

    def test_partial_drop_rescued(self):
        """18706 被读成 16070：只掉一位也要复核。"""
        result, fake, lines, written = run_get_oil([16070, 18697], previous=18706)
        self.assertEqual(result, 18697)
        self.assertTrue([line for line in lines if '二次校验' in line])
        self.assertEqual(written[-1]['Value'], 18697)

    def test_rescue_failed_keeps_previous(self):
        """复核也读不到有效值时保留上一有效值，不写仪表盘。"""
        result, fake, lines, written = run_get_oil([1404, 0], previous=14685)
        self.assertEqual(result, 14685)
        self.assertEqual(written, [])
        self.assertTrue([line for line in lines if '复核失败' in line])

    def test_real_large_drop_takes_recheck(self):
        """真实大额消耗（如大世界买行动力）复核读到新值就采信。"""
        result, fake, lines, written = run_get_oil([13180, 13180], previous=14685)
        self.assertEqual(result, 13180)
        self.assertEqual(written[-1]['Value'], 13180)

    def test_no_baseline_skips_debounce(self):
        """仪表盘没有基线时不复核，避免把首次记录拦下。"""
        result, fake, lines, written = run_get_oil([14654], previous=None)
        self.assertEqual(result, 14654)
        self.assertEqual(fake.screenshots, 0)
        self.assertEqual(written[-1]['Value'], 14654)

    def test_never_read_degrades(self):
        """整轮读数都无效且无基线时保留旧行为。"""
        result, fake, lines, written = run_get_oil([3], previous=None)
        self.assertEqual(result, 3)
        self.assertEqual(written[-1]['Value'], 3)

    def test_never_read_keeps_previous_with_baseline(self):
        """有基线时读不到有效值就沿用上一值，不把 0 当「石油不足」。"""
        result, fake, lines, written = run_get_oil([0], previous=14685)
        self.assertEqual(result, 14685)
        self.assertEqual(written, [])


class OilDropSuspiciousTest(unittest.TestCase):
    """可疑下降的判据本身。"""

    def _suspicious(self, previous, value):
        fake = FakeStatus([value], previous=previous)
        return CampaignStatus._oil_dropped_suspiciously(fake, previous, value)

    def test_small_drop_ok(self):
        self.assertFalse(self._suspicious(18706, 18690))

    def test_partial_drop_flagged(self):
        self.assertTrue(self._suspicious(18706, 16070))

    def test_truncated_flagged(self):
        self.assertTrue(self._suspicious(14685, 1404))

    def test_rise_not_flagged(self):
        self.assertFalse(self._suspicious(14000, 14500))

    def test_invalid_value_not_flagged(self):
        self.assertFalse(self._suspicious(14000, 0))


if __name__ == '__main__':
    unittest.main()
