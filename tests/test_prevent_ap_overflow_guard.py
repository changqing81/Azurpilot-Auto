"""防止行动力溢出任务「guard 临时挂起」的回归护栏。

线上背景（2026-10-14）：用户已在配置里关闭 `OpsiPreventActionPointOverflow`
（`Scheduler.Enable=false`），但任务仍持续运行，日志里一天出现 4 次
`[战役] 检测到防止行动力溢出任务残留的临时关闭，自动恢复调度`：

1. `heal_prevent_ap_overflow_guard_residual` 把「有残留标记 + Enable=false」
   一律当成 guard 残留，自动把 Enable 写回 True（无法区分用户主动关闭）；
2. `finally` 里 `update_prevent_action_point_overflow_schedule(enable=True)`
   无条件重新启用任务。

两者叠加形成自激循环：跑过一次就自己续命，用户的关闭被反复覆盖。

修复：拆键 —— `Scheduler.Enable` 只表达用户意愿，guard 临时挂起改用独立的
`GuardTempClosed` 标记，调度判据见 `module.config.config.GUARD_SUSPEND_FLAG_PATHS`。
本模块的所有断言都要求 guard 与自愈逻辑**永不写 `Scheduler.Enable`**。
"""
import unittest
from types import MethodType, SimpleNamespace

from module.campaign.os_run import OSCampaignRun
from module.os.tasks.prevent_action_point_overflow import OpsiPreventActionPointOverflow

TASK = 'OpsiPreventActionPointOverflow'
ENABLE_PATH = 'OpsiPreventActionPointOverflow.Scheduler.Enable'
GUARD_FLAG_PATH = 'OpsiPreventActionPointOverflow.Storage.Storage.GuardTempClosed'


class FakeConfig:
    """最小化配置替身，分别记录 Enable 与挂起标记的写入。"""

    def __init__(self, enable=True, flag=False):
        self.enable = enable
        self.flag = flag
        self.enable_writes = []
        self.flag_writes = []
        self.schedule_delays = []
        self.reschedule_calls = []

    def cross_get(self, keys, default=None):
        if isinstance(keys, str):
            if keys == GUARD_FLAG_PATH:
                return self.flag
            return default
        if list(keys) == [TASK, 'Scheduler', 'Enable']:
            return self.enable
        if list(keys) == [TASK, 'Storage', 'Storage', 'GuardTempClosed']:
            return self.flag
        return default

    def is_task_enabled(self, task):
        """与真实实现一致：用户启用 且 未被 guard 挂起。"""
        return bool(self.enable and not self.flag)

    def cross_set(self, keys, value):
        if isinstance(keys, str) and keys == GUARD_FLAG_PATH:
            self.flag = bool(value)
            self.flag_writes.append(bool(value))
        elif keys == ENABLE_PATH:
            self.enable = bool(value)
            self.enable_writes.append(bool(value))

    def task_delay(self, minute=None, task=None, **kwargs):
        self.schedule_delays.append((minute, task))


def make_campaign(config):
    """构造调度更新替身：只记录被调用，不写任何配置。"""

    def update_prevent_action_point_overflow_schedule():
        config.reschedule_calls.append(True)

    return SimpleNamespace(
        update_prevent_action_point_overflow_schedule=update_prevent_action_point_overflow_schedule,
    )


def make_guard(config, campaign=None, load_raises=False):
    """构造只绑定 guard 相关方法的 OSCampaignRun 替身。"""
    guard = SimpleNamespace()
    guard.config = config
    guard.PREVENT_AP_OVERFLOW_TASK = OSCampaignRun.PREVENT_AP_OVERFLOW_TASK
    guard.PREVENT_AP_OVERFLOW_GUARD_FLAG = OSCampaignRun.PREVENT_AP_OVERFLOW_GUARD_FLAG
    for name in (
        '_is_prevent_ap_overflow_guard_flag_set',
        '_set_prevent_ap_overflow_guard_flag',
        'heal_prevent_ap_overflow_guard_residual',
        '_run_opsi_task_with_ap_overflow_guard',
    ):
        setattr(guard, name, MethodType(getattr(OSCampaignRun, name), guard))

    def load_campaign():
        if load_raises:
            raise RuntimeError('load campaign failed')
        return campaign

    guard.load_campaign = load_campaign
    return guard


class GuardSuspendTests(unittest.TestCase):
    """guard 只写挂起标记，绝不改写用户控制的 Scheduler.Enable。"""

    def test_guard_suspends_and_clears_flag_only(self):
        cfg = FakeConfig(enable=True, flag=False)
        guard = make_guard(cfg, campaign=make_campaign(cfg))
        guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertEqual(cfg.flag_writes, [True, False])
        self.assertEqual(cfg.enable_writes, [])
        self.assertEqual(cfg.reschedule_calls, [True])

    def test_guard_skips_when_task_user_disabled(self):
        cfg = FakeConfig(enable=False, flag=False)
        guard = make_guard(cfg, campaign=make_campaign(cfg))
        guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertEqual(cfg.flag_writes, [])
        self.assertEqual(cfg.enable_writes, [])
        self.assertEqual(cfg.reschedule_calls, [])

    def test_guard_clears_flag_when_runner_raises(self):
        cfg = FakeConfig(enable=True, flag=False)
        guard = make_guard(cfg, campaign=make_campaign(cfg))

        def boom(_campaign):
            raise ValueError('task failed')

        with self.assertRaises(ValueError):
            guard._run_opsi_task_with_ap_overflow_guard(boom)
        self.assertEqual(cfg.flag_writes, [True, False])
        self.assertEqual(cfg.enable_writes, [])

    def test_guard_clears_flag_when_load_campaign_fails(self):
        cfg = FakeConfig(enable=True, flag=False)
        guard = make_guard(cfg, campaign=None, load_raises=True)
        with self.assertRaises(RuntimeError):
            guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertEqual(cfg.flag_writes, [True, False])
        self.assertEqual(cfg.enable_writes, [])

    def test_guard_clears_flag_when_schedule_update_fails(self):
        cfg = FakeConfig(enable=True, flag=False)

        def boom():
            raise RuntimeError('ocr failed')

        campaign = SimpleNamespace(update_prevent_action_point_overflow_schedule=boom)
        guard = make_guard(cfg, campaign=campaign)
        guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertEqual(cfg.flag_writes, [True, False])
        self.assertEqual(cfg.enable_writes, [])


class GuardResidualHealTests(unittest.TestCase):
    """自愈只清残留标记，绝不启用任务。"""

    def test_heal_only_clears_flag(self):
        cfg = FakeConfig(enable=False, flag=True)
        guard = make_guard(cfg)
        self.assertTrue(guard.heal_prevent_ap_overflow_guard_residual())
        self.assertFalse(cfg.flag)
        self.assertFalse(cfg.enable)
        self.assertEqual(cfg.enable_writes, [])
        self.assertEqual(cfg.flag_writes, [False])

    def test_heal_does_nothing_without_flag(self):
        cfg = FakeConfig(enable=False, flag=False)
        guard = make_guard(cfg)
        self.assertFalse(guard.heal_prevent_ap_overflow_guard_residual())
        self.assertEqual(cfg.enable_writes, [])
        self.assertEqual(cfg.flag_writes, [])


class UserDisableRegressionTests(unittest.TestCase):
    """⭐ 回归：用户关闭后，多轮大世界任务都不得把任务重新启用。"""

    def test_user_disable_survives_repeated_guard_cycles(self):
        cfg = FakeConfig(enable=False, flag=True)
        guard = make_guard(cfg, campaign=make_campaign(cfg))
        for _ in range(3):
            guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertFalse(cfg.enable)
        self.assertEqual(cfg.enable_writes, [])
        self.assertEqual(cfg.reschedule_calls, [])
        self.assertFalse(cfg.flag)


class ScheduleUpdateTests(unittest.TestCase):
    """调度更新只写 NextRun，绝不写 Scheduler.Enable。"""

    def _make_runner(self, config):
        runner = SimpleNamespace()
        runner.config = config
        runner.TASK_NAME_PREVENT_AP_OVERFLOW = TASK
        runner._get_prevent_action_point_overflow_thresholds = lambda: (200, 50)
        runner.update_prevent_action_point_overflow_schedule = MethodType(
            OpsiPreventActionPointOverflow.update_prevent_action_point_overflow_schedule,
            runner,
        )
        return runner

    def test_update_never_writes_enable(self):
        cfg = FakeConfig(enable=False, flag=False)
        runner = self._make_runner(cfg)
        runner.update_prevent_action_point_overflow_schedule(current_ap=100)
        self.assertEqual(cfg.enable_writes, [])
        self.assertEqual(len(cfg.schedule_delays), 1)
        self.assertEqual(cfg.schedule_delays[0][1], TASK)

    def test_update_writes_next_run_when_task_enabled(self):
        cfg = FakeConfig(enable=True, flag=False)
        runner = self._make_runner(cfg)
        runner.update_prevent_action_point_overflow_schedule(current_ap=200)
        self.assertEqual(cfg.enable_writes, [])
        self.assertEqual(cfg.schedule_delays, [(1, TASK)])


if __name__ == '__main__':
    unittest.main()
