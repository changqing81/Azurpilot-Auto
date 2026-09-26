"""主页启动/停止按钮的后台执行与乐观状态（零重依赖，可安全单测）。

stop_by_user 会在生命周期锁内终止 worker、等待退出并执行关游戏等收尾，
全程可达数十秒。若在 WebUI 会话线程里同步调用，按钮刷新、日志泵、状态
轮询全部排队冻结（用户实测「点了关闭要再跑一会儿、日志区落后两分钟」）。

这里提供两个纯函数：
- ``spawn_instance_action``：把操作挪进后台线程并打上进行中标记，立即返回；
  进行中重复点击直接忽略，操作失败/完成后标记自动清理。
- ``alas_ui_state``：供 1s 轮询的按钮状态生成器使用，操作进行中做乐观翻转，
  失败时随 alive 自动翻回。
"""

import threading


def spawn_instance_action(process_manager, action, pending: str) -> bool:
    """把启动/停止操作挪到后台线程执行。

    Args:
        process_manager: 实例的 ProcessManager（乐观标记挂在其上）。
        action: 无参可调用，实际执行的启动/停止流程。
        pending: "start" / "stop"，用于 ``alas_ui_state`` 的乐观翻转。

    Returns:
        bool: True = 已由本调用接管；False = 已有操作进行中，本次忽略。
    """
    if getattr(process_manager, "_ui_action_running", False):
        return False
    process_manager._ui_action_running = True
    process_manager._ui_pending = pending

    def runner():
        try:
            action()
        finally:
            process_manager._ui_action_running = False
            process_manager._ui_pending = None

    threading.Thread(target=runner, name="ui-instance-action", daemon=True).start()
    return True


def alas_ui_state(process_manager) -> bool:
    """启动/停止进行中的乐观按钮状态。

    - 停止中（pending=stop）：立即显示「启动」，失败则随 alive 翻回；
    - 启动中（pending=start）：立即显示「停止」，失败则随 alive 翻回；
    - 无进行中操作：回落到真实的 alive 状态。
    """
    pending = getattr(process_manager, "_ui_pending", None)
    if pending == "stop":
        return False
    if pending == "start":
        return True
    return process_manager.alive
