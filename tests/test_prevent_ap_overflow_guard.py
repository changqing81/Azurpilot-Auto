"""防止行动力溢出任务「临时关闭」残留自愈的回归护栏。

线上背景（2026-10-12）：`OSCampaignRun._run_opsi_task_with_ap_overflow_guard`
在运行普通大世界任务前，临时把防溢出任务 `Scheduler.Enable` 置 False，并在
`finally` 里恢复。若进程在运行期间被终止（WebUI 手动停止、崩溃），`finally`
不执行，任务会残留 `Enable=False`，被调度器 `get_next_task()` 永久跳过。

修复：guard 关闭时写持久化标记，恢复时清除；下次运行大世界任务时先自愈残留。
标记用于区分「guard 临时关闭」与「用户主动关闭」——后者不带标记，绝不被自愈
逻辑改动。
"""
import unittest
from types import MethodType, SimpleNamespace

from module.campaign.os_run import OSCampaignRun

ENABLE_PATH = 'OpsiPreventActionPointOverflow.Scheduler.Enable'


class FakeConfig:
    """最小化配置替身，记录 Enable 与残留标记的状态和写入。"""

    def __init__(self, enable=True, flag=False):
        self.enable = enable
        self.flag = flag
        self.enable_writes = []
        self.flag_writes = []

    def cross_get(self, keys, default=None):
        if 'GuardTempClosed' in keys:
            return self.flag
        return default

    def is_task_enabled(self, task):
        return self.enable

    def cross_set(self, keys, value):
        if 'GuardTempClosed' in keys:
            self.flag = bool(value)
            self.flag_writes.append(bool(value))
        elif keys.endswith('Scheduler.Enable'):
            self.enable = bool(value)
            self.enable_writes.append(bool(value))


def make_campaign(config):
    """构造会真实回写 Enable 的 campaign 替身。"""

    def update_prevent_action_point_overflow_schedule(enable=True):
        config.cross_set(keys=ENABLE_PATH, value=enable)

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


class GuardResidualHealTests(unittest.TestCase):
    """自愈：仅处理带标记的残留，不碰用户主动关闭。"""

    def test_heal_recovers_residual_disabled_task(self):
        cfg = FakeConfig(enable=False, flag=True)
        guard = make_guard(cfg)
        self.assertTrue(guard.heal_prevent_ap_overflow_guard_residual())
        self.assertTrue(cfg.enable)
        self.assertFalse(cfg.flag)

    def test_heal_does_nothing_without_flag(self):
        """无标记 = 用户主动关闭 → 不恢复、不写入。"""
        cfg = FakeConfig(enable=False, flag=False)
        guard = make_guard(cfg)
        self.assertFalse(guard.heal_prevent_ap_overflow_guard_residual())
        self.assertFalse(cfg.enable)
        self.assertEqual(cfg.enable_writes, [])
        self.assertEqual(cfg.flag_writes, [])

    def test_heal_clears_stale_flag_when_task_already_enabled(self):
        """标记残留但任务已启用 → 只清标记，不重复写 Enable。"""
        cfg = FakeConfig(enable=True, flag=True)
        guard = make_guard(cfg)
        self.assertTrue(guard.heal_prevent_ap_overflow_guard_residual())
        self.assertTrue(cfg.enable)
        self.assertFalse(cfg.flag)
        self.assertEqual(cfg.enable_writes, [])


class GuardRunTests(unittest.TestCase):
    """guard 正常路径与异常路径都必须留下 Enable=True 且清除标记。"""

    def test_guard_sets_and_clears_flag_on_normal_run(self):
        cfg = FakeConfig(enable=True, flag=False)
        guard = make_guard(cfg, campaign=make_campaign(cfg))
        guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertTrue(cfg.enable)
        self.assertFalse(cfg.flag)
        self.assertEqual(cfg.flag_writes, [True, False])

    def test_guard_heals_before_running(self):
        """进入 guard 时已是残留状态 → 先自愈，再正常关闭/恢复。"""
        cfg = FakeConfig(enable=False, flag=True)
        guard = make_guard(cfg, campaign=make_campaign(cfg))
        guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertTrue(cfg.enable)
        self.assertFalse(cfg.flag)

    def test_guard_skips_when_task_user_disabled(self):
        """用户主动关闭（无标记）→ guard 既不关闭也不恢复。"""
        cfg = FakeConfig(enable=False, flag=False)
        guard = make_guard(cfg, campaign=make_campaign(cfg))
        guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertFalse(cfg.enable)
        self.assertEqual(cfg.flag_writes, [])
        self.assertEqual(cfg.enable_writes, [])

    def test_guard_recovers_when_runner_raises(self):
        cfg = FakeConfig(enable=True, flag=False)
        guard = make_guard(cfg, campaign=make_campaign(cfg))

        def boom(_campaign):
            raise ValueError('task failed')

        with self.assertRaises(ValueError):
            guard._run_opsi_task_with_ap_overflow_guard(boom)
        self.assertTrue(cfg.enable)
        self.assertFalse(cfg.flag)

    def test_guard_recovers_when_load_campaign_fails(self):
        cfg = FakeConfig(enable=True, flag=False)
        guard = make_guard(cfg, campaign=None, load_raises=True)
        with self.assertRaises(RuntimeError):
            guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertTrue(cfg.enable)
        self.assertFalse(cfg.flag)

    def test_guard_falls_back_when_schedule_update_fails(self):
        """恢复调度失败 → 直接重新启用任务，标记仍被清除。"""
        cfg = FakeConfig(enable=True, flag=False)

        def boom(enable=True):
            raise RuntimeError('ocr failed')

        campaign = SimpleNamespace(update_prevent_action_point_overflow_schedule=boom)
        guard = make_guard(cfg, campaign=campaign)
        guard._run_opsi_task_with_ap_overflow_guard(lambda campaign: 'ok')
        self.assertTrue(cfg.enable)
        self.assertFalse(cfg.flag)


if __name__ == '__main__':
    unittest.main()
