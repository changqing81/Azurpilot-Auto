"""守护模式基类。

继承 ModuleBase 并禁用卡死检测，为所有守护任务提供基础功能。
守护模式用于后台持续运行，不会因超时自动停止。
"""

from module.base.base import ModuleBase


class DaemonBase(ModuleBase):
    # 覆盖 Combat 的结算阶段超时：守护模式供用户手动游玩时辅助，
    # 用户中途暂停操作不应触发 GameStuckError 重启。
    _combat_status_timeout = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.device.disable_stuck_detection()
