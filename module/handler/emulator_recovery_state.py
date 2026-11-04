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


class CoolingDown(RecoveryBlocked):
    """实例仍在恢复冷却期，本轮未执行任何启停动作。

    携带剩余秒数与记录文件路径：原实现只抛一句"仍在恢复冷却期"，
    用户看到日志里几十行重试也不知道在等什么、账记在哪里
    （2026-11-04 手动叉掉模拟器后第二次启动不了，排查全靠翻代码）。

    Attributes:
        remain (float): 距离冷却结束的秒数。
        record (str): 预算记录文件路径。
    """

    def __init__(self, message, remain=0.0, record=None):
        super().__init__(message)
        self.remain = max(0.0, float(remain))
        self.record = record


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
        self.key = key
        # 默认桶沿用原文件名：升级后旧记录（含冷却时间戳）继续生效，
        # 不会因为本次改动凭空多出一批可用额度。
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

    def bucket_path(self, bucket='restart'):
        """按用途返回记录文件：重启（stop+start）与纯启动（只有 start）分开记账。

        Args:
            bucket: 'restart' 走原文件名，其它用途加后缀，互不消耗额度。
        """
        if bucket == 'restart':
            return self.path
        return self.root / f'{self.key}.{bucket}.json'

    def reserve(self, maximum=3, window=21600, cooldown=600, now=None, bucket='restart'):
        """动作之前原子记账：失败、崩溃和重启脚本都不能退回额度。

        Args:
            maximum: 时间窗内允许的次数上限，None 表示只做冷却限速、不设上限。
            window: 次数统计窗口（秒）。
            cooldown: 与上一条记录的最小间隔（秒）。
            now: 测试注入的时间戳。
            bucket: 记账桶，见 bucket_path()。
        """
        now = time.time() if now is None else now
        path = self.bucket_path(bucket)
        try:
            data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
            if not isinstance(data, list) or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in data):
                raise ValueError('恢复记录格式错误')
            # 时钟回拨时保留未来记录，不能因时间变化获得新额度。
            history = [t for t in data if t >= now - window]
        except (OSError, ValueError) as exc:
            raise RecoveryBlocked(f'恢复记录不可读，拒绝清空额度后重试: {path}') from exc
        if maximum is not None and len(history) >= maximum:
            raise RecoveryBlocked(
                f'实例恢复次数已达时间窗上限（{len(history)}/{maximum} 次 / '
                f'{window / 3600:.0f} 小时，记录文件 {path}），请人工检查')
        if history and now - max(history) < cooldown:
            remain = cooldown - (now - max(history))
            raise CoolingDown(
                f'实例仍在恢复冷却期（剩余 {remain:.0f} 秒，记录文件 {path}），停止本轮自动运行',
                remain=remain, record=str(path))
        history.append(now)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root,
                                             suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                json.dump(history, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
