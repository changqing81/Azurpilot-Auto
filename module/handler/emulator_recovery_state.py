"""按物理实例持久化恢复预算，并跨进程串行化整个恢复事务。"""

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

from module.exception import EmulatorOpBusy


class RecoveryBlocked(Exception):
    """恢复预算耗尽、身份不明或记录不可读，必须停止自动恢复。"""


class InstanceLease:
    """锁文件永不删除；操作系统在持有者退出时释放锁。"""

    def __init__(self, identity, root=None):
        if not all(isinstance(part, str) and part for part in identity):
            raise RecoveryBlocked('模拟器身份不完整，拒绝自动重启')
        if root is None:
            base = os.environ.get('LOCALAPPDATA') if os.name == 'nt' else os.environ.get('XDG_STATE_HOME')
            base = Path(base) if base else Path.home() / '.local' / 'state'
            root = base / 'AzurPilot' / 'emulator-recovery'
        self.root = Path(root)
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        self.path = self.root / f'{key}.json'
        self.lock_path = self.root / f'{key}.lock'
        self.stream = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.stream = self.lock_path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.stream.seek(0, 2) == 0:
                    self.stream.write(b'0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise EmulatorOpBusy('另一个进程正在恢复同一模拟器') from exc
        return self

    def __exit__(self, *exc):
        if self.stream is not None:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream = None

    def reserve(self, maximum=3, window=21600, cooldown=600, now=None):
        """动作之前原子记账：失败、崩溃和重启脚本都不能退回额度。"""
        now = time.time() if now is None else now
        try:
            data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []
            if not isinstance(data, list) or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in data):
                raise ValueError('恢复记录格式错误')
            # 时钟回拨时保留未来记录，不能因时间变化获得新额度。
            history = [t for t in data if t >= now - window]
        except (OSError, ValueError) as exc:
            raise RecoveryBlocked('恢复记录不可读，拒绝清空额度后重试') from exc
        if len(history) >= maximum:
            raise RecoveryBlocked('实例恢复次数已达时间窗上限，请人工检查')
        if history and now - max(history) < cooldown:
            raise RecoveryBlocked('实例仍在恢复冷却期，停止本轮自动运行')
        history.append(now)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root,
                                             suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                json.dump(history, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
