"""智能调度的进程内状态查询，不导入游戏模块，也不持久化运行标记。"""

from contextlib import contextmanager
from datetime import datetime

from module.config.time_source import now as current_time


# 调度器每轮可能重建配置对象，按配置名称共享状态；不同实例之间互不干扰。
# 进程退出后状态自然消失，历史 GuardTempClosed 不参与任何运行状态判断。
_RUNTIME_STATES = {}


def _runtime(config):
    """取得当前配置实例的运行记录，匿名测试配置仅使用自身内存。"""
    name = getattr(config, 'config_name', None)
    if isinstance(name, str) and name:
        return _RUNTIME_STATES.setdefault(name, {})
    return config.__dict__.setdefault('_opsi_scheduling_runtime', {})


def get_smart_scheduling_state(config, now=None):
    """查询关闭、等待、待执行、运行、中断、完成或失败状态。

    开关仅表达用户意愿；结束和异常记录优先于过期的 NextRun，避免把
    已退出的智能调度误判为一直运行。等待到期后允许开始下一轮。
    """
    enabled = config.cross_get('OpsiScheduling.Scheduler.Enable', default=False)
    if isinstance(enabled, list):
        enabled = any(enabled)
    if not enabled:
        return 'disabled'
    next_run = config.cross_get('OpsiScheduling.Scheduler.NextRun', default=None)
    if not isinstance(next_run, datetime):
        return 'idle'
    if next_run >= (current_time() if now is None else now):
        return 'waiting'
    phase = _runtime(config).get('phase', 'ready')
    return 'ready' if phase == 'waiting' else phase


def smart_scheduling_blocks_overflow(config, now=None):
    """只在智能调度实际执行或具有有效待执行轮次时挂起防溢出。"""
    return get_smart_scheduling_state(config, now=now) in ('ready', 'running', 'interrupted')


@contextmanager
def smart_scheduling_session(config):
    """跟踪真实调用生命周期，包括初始化失败与正常任务切换。

    TaskEnd 是框架原生的延迟/切换信号，必须与异常崩溃区分；嵌套入口
    共用外层生命周期，防止内部返回提前解除互斥。所有状态仅在内存中。
    """
    from module.config.config import TaskEnd

    runtime = _runtime(config)
    if runtime.get('phase') == 'running':
        yield
        return
    runtime['phase'] = 'running'
    try:
        yield
    except TaskEnd:
        state = get_smart_scheduling_state(config)
        runtime['phase'] = 'interrupted' if state == 'running' else 'waiting'
        raise
    except BaseException:
        # 初始化、业务异常和进程正常退出信号均释放互斥，异常仍交由顶层处理。
        runtime['phase'] = 'failed'
        raise
    else:
        runtime['phase'] = 'finished'
