import unittest
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from module.campaign.os_run import OSCampaignRun
from module.config.config import TaskEnd
from module.os.tasks.prevent_action_point_overflow import OpsiPreventActionPointOverflow
from module.os.tasks.scheduling import CoinTaskMixin, OpsiScheduling
from module.os_handler.action_point import ActionPointLimit


class SmartSchedulingConfig:
    """仅提供智能调度与防溢出测试所需的配置接口。"""

    def __init__(self, task_command='OpsiScheduling'):
        self.task = SimpleNamespace(command=task_command)
        self.task_delay_calls = []

    def cross_get(self, keys, default=None):
        if keys == 'OpsiScheduling.Scheduler.ServerUpdate':
            return '00:00'
        return default

    def task_delay(self, *args, **kwargs):
        self.task_delay_calls.append((args, kwargs))

    @staticmethod
    def temporary(**kwargs):
        return nullcontext()

    @staticmethod
    def task_stop():
        raise TaskEnd


class MeowPreserveConfig:
    """提供智能调度代跑短猫时的共享行动力保留状态。"""

    def __init__(self):
        self.OS_ACTION_POINT_PRESERVE = 180

    @contextmanager
    def temporary(self, **kwargs):
        backup = {key: getattr(self, key) for key in kwargs}
        for key, value in kwargs.items():
            setattr(self, key, value)
        try:
            yield
        finally:
            for key, value in backup.items():
                setattr(self, key, value)

    @staticmethod
    def task_stop():
        raise AssertionError('达到短猫保留值不应停止智能调度')


class SchedulingMeowHarness:
    """复现短猫达到自身阈值后异常冒泡的最小调度环境。"""

    TASK_NAME_MEOWFFICER_FARMING = OpsiScheduling.TASK_NAME_MEOWFFICER_FARMING

    def __init__(self):
        self.config = MeowPreserveConfig()
        self.executed_task_name = None

    def run_meowfficer_farming_once(self, ap_preserve, ap_checked=False):
        self.config.OS_ACTION_POINT_PRESERVE = ap_preserve
        raise ActionPointLimit(total=5985, preserve=ap_preserve)

    def _run_with_opsi_task_context(self, task_name, func, **kwargs):
        self.executed_task_name = task_name
        return func(**kwargs)

    def run_scheduled_meowfficer_farming(self, ap_preserve):
        return OpsiScheduling._run_scheduled_meowfficer_farming(self, ap_preserve)


class SchedulingMeowCostLimitHarness(SchedulingMeowHarness):
    def run_meowfficer_farming_once(self, ap_preserve, ap_checked=False):
        self.config.OS_ACTION_POINT_PRESERVE = ap_preserve
        raise ActionPointLimit(current=15, total=15, cost=120)


class TestSmartSchedulingMeowPreserve(unittest.TestCase):
    def test_returns_to_scheduling_and_restores_global_preserve_at_meow_limit(self):
        scheduling = SchedulingMeowHarness()

        scheduling.run_scheduled_meowfficer_farming(ap_preserve=6000)

        self.assertEqual(
            scheduling.executed_task_name,
            OpsiScheduling.TASK_NAME_MEOWFFICER_FARMING,
        )
        self.assertEqual(scheduling.config.OS_ACTION_POINT_PRESERVE, 180)

    def test_propagates_real_ap_shortage_and_still_restores_global_preserve(self):
        scheduling = SchedulingMeowCostLimitHarness()

        with self.assertRaises(ActionPointLimit):
            scheduling.run_scheduled_meowfficer_farming(ap_preserve=6000)

        self.assertEqual(scheduling.config.OS_ACTION_POINT_PRESERVE, 180)


class TestSmartSchedulingExploreDelay(unittest.TestCase):
    def test_skips_campaign_initialization_when_opsi_explore_is_in_progress(self):
        runner = OSCampaignRun.__new__(OSCampaignRun)
        runner.config = SmartSchedulingConfig()

        with (
            patch.object(runner, 'is_in_opsi_explore', return_value=True),
            patch.object(runner, '_run_opsi_task_with_ap_overflow_guard') as run_task,
        ):
            with self.assertRaises(TaskEnd):
                runner.opsi_scheduling()

        self.assertEqual(
            runner.config.task_delay_calls,
            [
                (
                    (),
                    {
                        'server_update': '00:00',
                        'task': 'OpsiScheduling',
                    },
                )
            ],
        )
        run_task.assert_not_called()

    def test_initializes_campaign_when_opsi_explore_is_complete(self):
        runner = OSCampaignRun.__new__(OSCampaignRun)
        runner.config = SmartSchedulingConfig()

        with (
            patch.object(runner, 'is_in_opsi_explore', return_value=False),
            patch.object(runner, '_run_opsi_task_with_ap_overflow_guard') as run_task,
        ):
            runner.opsi_scheduling()

        self.assertEqual(runner.config.task_delay_calls, [])
        run_task.assert_called_once()

    def test_delays_scheduling_when_opsi_explore_is_in_progress(self):
        scheduling = OpsiScheduling.__new__(OpsiScheduling)
        scheduling.config = SmartSchedulingConfig()

        with (
            patch.object(scheduling, 'is_in_opsi_explore', return_value=True),
            patch.object(scheduling, 'is_smart_scheduling_enabled') as enabled,
        ):
            with self.assertRaises(TaskEnd):
                scheduling.run_smart_scheduling()

        self.assertEqual(
            scheduling.config.task_delay_calls,
            [
                (
                    (),
                    {
                        'server_update': '00:00',
                        'task': 'OpsiScheduling',
                    },
                )
            ],
        )
        enabled.assert_not_called()

    def test_does_not_delay_when_smart_scheduling_is_normally_disabled(self):
        scheduling = OpsiScheduling.__new__(OpsiScheduling)
        scheduling.config = SmartSchedulingConfig()

        with (
            patch.object(scheduling, 'is_in_opsi_explore', return_value=False),
            patch.object(scheduling, 'is_smart_scheduling_enabled', return_value=False),
        ):
            scheduling.run_smart_scheduling()

        self.assertEqual(scheduling.config.task_delay_calls, [])

    def test_prevent_overflow_delays_itself_during_opsi_explore(self):
        prevent = OpsiPreventActionPointOverflow.__new__(OpsiPreventActionPointOverflow)
        prevent.config = SmartSchedulingConfig(
            task_command='OpsiPreventActionPointOverflow'
        )

        with (
            patch.object(
                prevent,
                '_get_prevent_action_point_overflow_thresholds',
                return_value=(200, 0),
            ),
            patch.object(
                prevent,
                '_get_prevent_action_point_overflow_task',
                return_value='OpsiScheduling',
            ),
            patch.object(
                prevent,
                '_get_current_action_point_for_overflow',
                return_value=200,
            ),
            patch.object(prevent, 'is_in_opsi_explore', return_value=True),
            patch.object(
                prevent,
                '_run_with_opsi_task_context',
                side_effect=lambda task, func, *args, **kwargs: func(*args, **kwargs),
            ),
            patch.object(
                prevent,
                'get_yellow_coins',
                side_effect=AssertionError('开荒期间不应进入智能调度决策'),
            ),
        ):
            with self.assertRaises(TaskEnd):
                prevent.run_prevent_action_point_overflow()

        self.assertEqual(
            prevent.config.task_delay_calls,
            [
                (
                    (),
                    {
                        'server_update': True,
                        'task': 'OpsiPreventActionPointOverflow',
                    },
                )
            ],
        )


class StrongholdNotFoundConfig:
    """塞壬要塞跳过测试用的最小配置桩。"""

    def __init__(self):
        self.task = SimpleNamespace(command='OpsiStronghold')
        self.task_delay_calls = []
        self._state = {}
        self.OpsiStronghold_HasStronghold = True

    def cross_get(self, keys, default=None):
        if keys == 'OpsiScheduling.Storage.Storage':
            return self._state
        if keys == 'OpsiScheduling.Scheduler.ServerUpdate':
            return '00:00'
        return default

    @property
    def modified(self):
        return self._state

    def save(self):
        pass

    @staticmethod
    def temporary(**kwargs):
        return nullcontext()

    @staticmethod
    def task_stop():
        raise TaskEnd


class StrongholdNotFoundHarness:
    """最小调度桩，仅暴露塞壬要塞跳过逻辑所需的方法。"""

    STATE_KEY_STRONGHOLD_NOT_FOUND_DATE = (
        CoinTaskMixin.STATE_KEY_STRONGHOLD_NOT_FOUND_DATE
    )

    def __init__(self):
        self.config = StrongholdNotFoundConfig()
        self._smart_scheduling_context = True

    # --- CoinTaskMixin 状态读写 ---

    def _get_smart_scheduling_state(self):
        state = self.config.cross_get(
            keys='OpsiScheduling.Storage.Storage', default={})
        if not isinstance(state, dict):
            return {}
        return dict(state)

    def _get_smart_scheduling_state_value(self, key, default=None):
        return self._get_smart_scheduling_state().get(key, default)

    def _set_smart_scheduling_state_value(self, key, value):
        state = self._get_smart_scheduling_state()
        if state.get(key) == value:
            return
        state[key] = value
        self.config._state = state

    def _clear_smart_scheduling_state_value(self, key):
        state = self._get_smart_scheduling_state()
        if key not in state:
            return
        state.pop(key, None)
        self.config._state = state

    # --- 被测方法直接引用 CoinTaskMixin ---

    _get_stronghold_not_found_date = (
        CoinTaskMixin._get_stronghold_not_found_date
    )
    _set_stronghold_not_found_today = (
        CoinTaskMixin._set_stronghold_not_found_today
    )
    _is_stronghold_not_found_today = (
        CoinTaskMixin._is_stronghold_not_found_today
    )
    _clear_stronghold_not_found_date = (
        CoinTaskMixin._clear_stronghold_not_found_date
    )

    # --- _handle_coin_task_no_content 桩 ---

    def _handle_coin_task_no_content(self, task_display_name, log_message):
        return True  # 模拟智能调度上下文中返回 True


class TestStrongholdNotFoundSkip(unittest.TestCase):
    """测试塞壬要塞当日扫描未找到标记的读写和过期逻辑。"""

    def test_set_and_check_not_found_today(self):
        """标记今日未找到后，_is_stronghold_not_found_today 返回 True。"""
        harness = StrongholdNotFoundHarness()
        self.assertFalse(harness._is_stronghold_not_found_today())

        harness._set_stronghold_not_found_today()
        self.assertTrue(harness._is_stronghold_not_found_today())

    def test_clear_not_found_date(self):
        """清除标记后 _is_stronghold_not_found_today 返回 False。"""
        harness = StrongholdNotFoundHarness()
        harness._set_stronghold_not_found_today()
        self.assertTrue(harness._is_stronghold_not_found_today())

        harness._clear_stronghold_not_found_date()
        self.assertFalse(harness._is_stronghold_not_found_today())

    def test_expired_date_auto_clears(self):
        """过期的日期标记在检查时自动清除。"""
        harness = StrongholdNotFoundHarness()
        # 写入昨天的日期
        from module.config.utils import server_time_offset
        from module.config.time_source import now as current_time
        server_now = current_time() - server_time_offset()
        yesterday = (server_now - timedelta(days=1)).strftime('%Y-%m-%d')
        harness._set_smart_scheduling_state_value(
            harness.STATE_KEY_STRONGHOLD_NOT_FOUND_DATE, yesterday
        )

        # 检查时应返回 False 并自动清除
        self.assertFalse(harness._is_stronghold_not_found_today())
        self.assertIsNone(harness._get_stronghold_not_found_date())
