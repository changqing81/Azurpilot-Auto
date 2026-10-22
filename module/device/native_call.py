"""原生调用的有界等待：超时不注入线程异常，也不重用不确定的句柄。"""

import threading

from module.device.session import RecoveryStopped
from module.logger import logger


class NativeCall:
    """每个 IPC 实例最多一条在途调用；超时永久封禁该实例。

    Python 无法可靠中断 DLL；等待超时后停止配置，让用户处理宿主机故障。
    原调用即使迟到完成，也不能触发重连、释放正使用的句柄或再次输入。
    """

    def __init__(self):
        self.thread = None
        self.poisoned = False
        self.lock = threading.Lock()

    def ensure_idle(self):
        if self.poisoned or (self.thread is not None and self.thread.is_alive()):
            raise RecoveryStopped('原生 IPC 调用未安全结束，禁止释放句柄或重启模拟器')

    def run(self, func, *args, timeout=0.5):
        # 截图实现内部的分辨率查询属于同一原生事务，不创建嵌套线程。
        if self.thread is threading.current_thread():
            if self.poisoned:
                raise RecoveryStopped('原生 IPC 会话已超时失效')
            result = func(*args)
            if self.poisoned:
                raise RecoveryStopped('原生 IPC 嵌套调用迟到返回，禁止继续操作')
            return result
        if not self.lock.acquire(blocking=False):
            raise RecoveryStopped('同一 IPC 实例存在并发原生调用')
        try:
            self.ensure_idle()
            result = []
            error = []

            def invoke():
                try:
                    result.append(func(*args))
                except BaseException as exc:
                    error.append(exc)

            self.thread = threading.Thread(target=invoke, name='alas-native-call', daemon=True)
            self.thread.start()
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                self.poisoned = True
                logger.critical('[设备-IPC] 原生调用超时，停止配置；不重试、不关闭仍在使用的句柄')
                raise RecoveryStopped('原生 IPC 超时，需要人工检查')
            if error:
                raise error[0]
            return result[0]
        finally:
            self.lock.release()
