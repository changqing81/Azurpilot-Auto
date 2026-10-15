"""跨线程、跨进程的配置事务锁，避免运行器与 API 相互覆盖。"""
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def config_transaction(path):
    """对同一个配置文件串行化读改写；进程退出时操作系统自动释放锁。"""
    key = str(Path(path).resolve())
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, 'held', set())
        if key in held:
            yield
            return
        lock_path = Path(key).with_suffix('.json.lock')
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a+b') as file:
            if file.tell() == 0:
                file.write(b'0')
                file.flush()
            deadline = time.monotonic() + 15
            while True:
                try:
                    file.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('等待配置事务锁超时')
                    time.sleep(0.02)
            _local.held = held | {key}
            try:
                yield
            finally:
                _local.held = held
                file.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)
