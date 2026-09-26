"""WebUIASGI生命周期管理"""

import threading

from module.webui.app_dependencies import (
    ProcessManager,
    RemoteAccess,
    State,
    close_discord_rpc,
    init_discord_rpc,
    lang,
    logger,
    os,
    start_ocr_server_process,
    stop_ocr_server_process,
    task_handler,
    updater,
)

from module.webui.app_helpers import (
    is_demo_mode,
)


def _clearup_step(name, handler) -> bool:
    """执行单项清理；一项失败不应阻断其余资源回收。"""
    try:
        return handler() is not False
    except Exception as exc:
        logger.exception_context(
            title=f'WebUI 清理失败: {name}',
            exc=exc,
            impact='其余 WebUI 资源仍会继续清理。',
            action='检查对应资源的退出日志，确认是否遗留子进程。',
            level=40,
        )
        return False


def _warm_statistics_modules() -> None:
    """后台预热统计页的一次性成本。

    实测第一次打开统计页要等约 930ms，其中：
        import 统计模块（azurstats / cl1_database / opsi_month / ship_exp_stats）0.72s
        import numpy                                                            0.19s
        import sqlite3                                                          0.02s
        设备指纹 get_device_id()（WMIC 子进程）                                  ~0.9s
    全是一次性的，跟数据量无关；预热后「耄耋相接」表格的构造
    从冷启 728ms 降到 16ms。放到 WebUI 起来之后的空闲时段后台消化，
    用户点开统计页就不用等这几百毫秒。

    只做导入和一次只读调用，不碰任何状态 —— 预热失败也不该影响 WebUI 启动，
    因此静默兜底。
    """
    try:
        import sqlite3  # noqa: F401

        import numpy  # noqa: F401

        from module.base.device_id import get_device_id
        from module.statistics.azurstats import AzurStats
        from module.statistics.cl1_database import db as _cl1_db  # noqa: F401
        from module.statistics.opsi_month import get_opsi_stats  # noqa: F401
        from module.statistics.ship_exp_stats import get_ship_exp_stats  # noqa: F401

        # 设备指纹走 WMIC 子进程，首次调用约 0.9s —— 拖进后台预热
        get_device_id()

        # 顺带把 numpy 的首次读表路径走一遍（只读本地累积 CSV）
        AzurStats.load_meowofficer_farming()
    except Exception:
        pass


def startup() -> None:
    """初始化 WebUI 进程级后台服务。"""
    State.init()
    lang.reload()
    updater.event = State.manager.Event()
    if updater.delay > 0:
        task_handler.add(updater.check_update, updater.delay, group="slow")
    task_handler.add(updater.schedule_update(), 86400, group="slow")
    task_handler.start()
    if State.deploy_config.DiscordRichPresence:
        init_discord_rpc()
    if State.deploy_config.StartOcrServer and not is_demo_mode():
        start_ocr_server_process(State.deploy_config.OcrServerPort)
    if State.deploy_config.EnableRemoteAccess and (
        State.deploy_config.Password is not None or os.environ.get("DEMO") == "1"
    ):
        # 10 秒轮询：远程访问线程一旦退出，最多 10 秒内自动拉起（原 60 秒会造成远控盲区）
        task_handler.add(RemoteAccess.keep_ssh_alive(), 10, group="slow")
    threading.Thread(
        target=_warm_statistics_modules, daemon=True, name="warm-statistics"
    ).start()


def clearup() -> bool:
    """停止 WebUI 进程级资源，避免热重载遗留子进程。"""
    with State.cleanup_lock:
        if State._clearup:
            return True

        logger.info("[WebUI-生命周期] 开始清理")
        success = _clearup_step("任务处理器", task_handler.stop)

        for name, handler in (
            ("远程访问", RemoteAccess.kill_ssh_process),
            ("Discord RPC", close_discord_rpc),
            ("OCR 服务", stop_ocr_server_process),
        ):
            success = _clearup_step(name, handler) and success

        try:
            instances = ProcessManager.running_instances()
        except Exception as exc:
            logger.exception_context(
                title='WebUI 清理失败: 枚举运行实例',
                exc=exc,
                impact='无法确认所有 AzurPilot 工作进程是否已停止。',
                action='检查 WebUI 进程注册表和 Manager 服务状态。',
                level=40,
            )
            instances = []
            success = False

        for alas in instances:
            success = _clearup_step(f"AzurPilot 实例 {alas.config_name}", alas.stop) and success

        if success:
            try:
                State.clearup()
            except Exception as exc:
                logger.exception_context(
                    title='WebUI 清理失败: 共享状态',
                    exc=exc,
                    impact='Manager 未能完全关闭，父进程将通过进程树终止兜底。',
                    action='检查 Manager 服务和系统进程权限。',
                    level=40,
                )
                success = False
        else:
            logger.error("WebUI 清理未完成，保留 Manager 直到父进程终止进程树")
        logger.info("[WebUI-生命周期] Alas 已关闭")
        return success
