"""智能调度在信标未收满时放宽行动力保留值的回归测试。

覆盖：
- _get_coin_task_action_point_preserve / _get_effective_cl1_ap_preserve
  在 ash_ignore_action_point_preserve() 为真时返回 0（暂时忽略保留值），
  否则保持原有回退逻辑。
"""

import unittest

from module.os.tasks.scheduling import CoinTaskMixin, OpsiScheduling


class _StubConfig:
    def __init__(self, values):
        self.values = values

    def cross_get(self, keys, default=None):
        return self.values.get(keys, default)


class _PreserveStub(CoinTaskMixin):
    """只接两个保留值 getter 依赖的最小接口。"""

    _get_coin_task_action_point_preserve = (
        OpsiScheduling._get_coin_task_action_point_preserve)
    _get_effective_cl1_ap_preserve = OpsiScheduling._get_effective_cl1_ap_preserve

    def __init__(self, ignoring, values):
        self._ignoring = ignoring
        self.config = _StubConfig(values)

    def ash_ignore_action_point_preserve(self):
        return self._ignoring

    def _get_smart_scheduling_action_point_preserve(self):
        return self.config.cross_get(
            'OpsiScheduling.OpsiScheduling.ActionPointPreserve', 0) or 0


class TestCoinTaskPreserve(unittest.TestCase):
    """补黄币任务的行动力保留值。"""

    def test_zero_when_beacon_not_full(self):
        stub = _PreserveStub(
            True, {'OpsiScheduling.OpsiScheduling.ActionPointPreserve': 200})
        self.assertEqual(stub._get_coin_task_action_point_preserve(), 0)

    def test_smart_value_when_beacon_full(self):
        stub = _PreserveStub(
            False, {'OpsiScheduling.OpsiScheduling.ActionPointPreserve': 200})
        self.assertEqual(stub._get_coin_task_action_point_preserve(), 200)

    def test_falls_back_to_task_page_when_no_override(self):
        stub = _PreserveStub(False, {
            'OpsiScheduling.OpsiScheduling.ActionPointPreserve': 0,
            'OpsiMeowfficerFarming.OpsiMeowfficerFarming.ActionPointPreserve': 800,
        })
        self.assertEqual(stub._get_coin_task_action_point_preserve(), 800)


class TestCl1Preserve(unittest.TestCase):
    """侵蚀1练级的行动力保留值。"""

    def test_zero_when_beacon_not_full(self):
        stub = _PreserveStub(
            True, {'OpsiScheduling.OpsiScheduling.Hazard1ActionPointPreserve': 300})
        self.assertEqual(stub._get_effective_cl1_ap_preserve(), 0)

    def test_override_when_beacon_full(self):
        stub = _PreserveStub(
            False, {'OpsiScheduling.OpsiScheduling.Hazard1ActionPointPreserve': 300})
        self.assertEqual(stub._get_effective_cl1_ap_preserve(), 300)

    def test_task_page_value_when_no_override(self):
        stub = _PreserveStub(False, {
            'OpsiScheduling.OpsiScheduling.Hazard1ActionPointPreserve': 0,
            'OpsiHazard1Leveling.OpsiHazard1Leveling.MinimumActionPointReserve': 150,
        })
        self.assertEqual(stub._get_effective_cl1_ap_preserve(), 150)


if __name__ == '__main__':
    unittest.main()
