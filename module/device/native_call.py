"""原生调用的有界等待：超时不注入线程异常，也不重用不确定的句柄。

设计边界：
- 单次原生调用的等待预算是一分钟。MuMu 的 IPC 在战斗切场景、结算弹窗这类
  负载尖峰上偶有几百毫秒延迟，原先 0.5 秒的预算会把正常抖动误判成永久卡死。
  一分钟没有任何响应，才认为设备已经失去控制。
- 超时后不向工作线程注入异步异常（Python 无法安全中断 DLL），也不并发调用
  其它 DLL 函数去释放该句柄；但不终止脚本进程：异常交给调度器的模拟器恢复
  路径，重启整台实例后继续挂机。设备挂死是设备的问题，把还能管理实例的
  自动化进程杀掉是本末倒置。
"""

import threading

from module.exception import NativeIpcTimeout
from module.logger import logger

# 单次原生调用的等待预算（秒）。一分钟无任何响应，视为设备已失控。
NATIVE_CALL_TIMEOUT = 60.0


class NativeCall:
    """每个 IPC 实例最多一条在途调用；只有预算耗尽才封禁该实例。

    Python 无法可靠中断 DLL；等待超时后不再复用该实例的句柄，也不并发
    调用其它 DLL 函数。原调用即使迟到完成，也不能触发重连或再次输入。
    """

    def __init__(self, timeout=NATIVE_CALL_TIMEOUT):
        self.thread = None
        self.poisoned = False
        self.lock = threading.Lock()
        self.timeout = timeout

    def ensure_idle(self):
        """确认没有在途调用；仍被占用时拒绝释放句柄。

        与超时同语义：抛 NativeIpcTimeout（可恢复的"设备失控"），
        不使用 RecoveryStopped，避免把脚本进程一起结束。
        """
        if self.poisoned or (self.thread is not None and self.thread.is_alive()):
            raise NativeIpcTimeout('原生 IPC 调用未安全结束，禁止释放句柄或重启模拟器')

    def run(self, func, *args, timeout=None):
        """执行一次有界原生调用。

        Args:
            func: DLL 函数或截图组合函数。
            *args: 原生调用参数。
            timeout: 本次等待预算；默认使用模块常量 NATIVE_CALL_TIMEOUT。

        Raises:
            NativeIpcTimeout: 预算内没有返回。调用线程不能被安全终止，
                因此该实例被标记为失效，由调度器重启模拟器实例。
        """
        budget = self.timeout if timeout is None else timeout
        name = getattr(func, '__name__', repr(func))

        # 截图实现内部的分辨率查询属于同一原生事务，不创建嵌套线程。
        if self.thread is threading.current_thread():
            if self.poisoned:
                raise NativeIpcTimeout('原生 IPC 会话已超时失效')
            result = func(*args)
            if self.poisoned:
                raise NativeIpcTimeout('原生 IPC 嵌套调用迟到返回，禁止继续操作')
            return result

        if not self.lock.acquire(blocking=False):
            # 同一实例并发原生调用是异常信号，但先给 5 秒容忍，
            # 避免把偶发的线程竞争误判成设备挂死而重启模拟器。
            if not self.lock.acquire(timeout=min(5.0, budget)):
                raise NativeIpcTimeout('同一 IPC 实例存在并发原生调用，拒绝继续操作')

        try:
            if self.poisoned or (self.thread is not None and self.thread.is_alive()):
                raise NativeIpcTimeout('原生 IPC 实例已失效，禁止继续调用')
            result = []
            error = []

            def invoke():
                try:
                    result.append(func(*args))
                except BaseException as exc:
                    error.append(exc)

            self.thread = threading.Thread(target=invoke, name='alas-native-call', daemon=True)
            self.thread.start()
            self.thread.join(timeout=budget)
            if self.thread.is_alive():
                self.poisoned = True
                logger.critical(
                    f'[设备-IPC] 原生调用 {name} 超过 {budget:g} 秒无响应，判定设备已失控；'
                    f'停止复用该句柄，交由调度器重启模拟器，不终止脚本'
                )
                raise NativeIpcTimeout(
                    f'原生调用 {name} 超过 {budget:g} 秒无响应，需要重启模拟器')
            if error:
                raise error[0]
            return result[0]
        finally:
            self.lock.release()
