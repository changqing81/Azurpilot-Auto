"""网络时间源模块。

通过 NTP 服务器校准本地时间，确保任务调度的时间准确性。
在长时间运行场景下，系统时钟可能存在漂移，
NTP 校时可以保证委托、科研等定时任务的精确触发。

特性：
- 校准完全在后台线程执行，任何调用线程（含 WebUI 首屏）零网络阻塞
- 首次读取即触发后台校准，未完成前使用本机时间
- 支持多个 NTP 服务器，自动故障转移
- 校时失败时回退到本机时间，不影响运行
- 可通过环境变量 AZURPILOT_NTP_DISABLE 禁用
- 可通过环境变量 AZURPILOT_NTP_SERVERS 自定义服务器
"""

import os
import socket
import struct
import threading
import time as time_
from datetime import datetime, timezone


# NTP 协议常量
NTP_EPOCH_DELTA = 2208988800  # NTP 时间纪元与 Unix 时间纪元的差值（秒）
NTP_PORT = 123
NTP_PACKET = b'\x1b' + b'\0' * 47
NTP_SERVERS_ENV = 'AZURPILOT_NTP_SERVERS'
NTP_DISABLE_ENV = 'AZURPILOT_NTP_DISABLE'
# 默认 NTP 服务器列表（中国优先）
DEFAULT_NTP_SERVERS = (
    'ntp.ntsc.ac.cn',
    'ntp.aliyun.com',
    'ntp.tencent.com',
    'cn.pool.ntp.org',
    'pool.ntp.org',
)


class NetworkTimeSource:
    """通过 NTP 服务器校准本地时间。

    只缓存本机时间与 NTP 时间的偏移量，后续读取不会频繁访问网络。
    所有联网操作都发生在一次性后台线程中，读取路径（timestamp/now/status）
    绝不联网、绝不长持锁——WebUI 与调度器在未同步期间直接使用本机时间，
    后台同步完成后自动切换到校准时间。
    """

    def __init__(self):
        self.offset = 0.0
        self.base_timestamp = 0.0
        self.base_monotonic = 0.0
        self.server = None
        self.synced = False
        self.last_sync_monotonic = 0.0
        self.retry_after_monotonic = 0.0
        self.refresh_interval = 30 * 60
        self.retry_interval = 10 * 60
        self.timeout = 1.0
        self._lock = threading.RLock()
        self._warned = False
        # 后台同步调度：_sync_lock 只保护线程启动判定（微秒级），
        # 联网查询不持任何读者锁，失败退避由 retry_after_monotonic 控制。
        self._sync_lock = threading.Lock()
        self._sync_thread = None

    @property
    def enabled(self):
        value = os.environ.get(NTP_DISABLE_ENV, '').strip().lower()
        return value not in {'1', 'true', 'yes', 'on'}

    @property
    def servers(self):
        value = os.environ.get(NTP_SERVERS_ENV, '').strip()
        if value:
            servers = [item.strip() for item in value.replace(';', ',').split(',')]
            servers = [item for item in servers if item]
            if servers:
                return servers

        return list(DEFAULT_NTP_SERVERS)

    def _query_server(self, host):
        last_error = None
        addresses = socket.getaddrinfo(host, NTP_PORT, type=socket.SOCK_DGRAM)
        for family, socktype, proto, _, sockaddr in addresses:
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(self.timeout)
                try:
                    sent = time_.time()
                    sock.sendto(NTP_PACKET, sockaddr)
                    data, _ = sock.recvfrom(48)
                    received = time_.time()
                except OSError as e:
                    last_error = e
                    continue

            if len(data) < 48:
                continue

            mode = data[0] & 0b00000111
            stratum = data[1]
            if mode not in {4, 5} or not 1 <= stratum <= 15:
                continue

            seconds, fraction = struct.unpack('!II', data[40:48])
            ntp_timestamp = seconds - NTP_EPOCH_DELTA + fraction / 2 ** 32
            local_timestamp = (sent + received) / 2
            if ntp_timestamp < 1577836800:  # 2020-01-01
                continue

            return ntp_timestamp - local_timestamp

        if last_error is not None:
            raise last_error
        raise OSError(f'无效的 NTP 响应: {host}')

    def refresh(self, force=False):
        """调度一次后台校时并立即返回；调用线程绝不联网。

        Args:
            force: 忽略失败退避，立即重新校时。

        Returns:
            bool: 当前是否已有有效校准结果。
        """
        if not self.enabled:
            return False
        self._schedule_sync(force=force)
        with self._lock:
            return self.synced

    def _schedule_sync(self, force=False):
        """确保有一个后台线程正在（或即将）执行校时。"""
        now_mono = time_.monotonic()
        with self._sync_lock:
            if force:
                # 强制刷新：清除失败退避，允许立即重试
                self.retry_after_monotonic = 0.0
            elif self.synced and now_mono - self.last_sync_monotonic < self.refresh_interval:
                return
            elif now_mono < self.retry_after_monotonic:
                return
            if self._sync_thread is not None and self._sync_thread.is_alive():
                return
            self._sync_thread = threading.Thread(
                target=self._sync_worker, name='NtpTimeSync', daemon=True
            )
            self._sync_thread.start()

    def _sync_worker(self):
        """后台执行一次完整校时：逐个服务器尝试，成功或进入退避后线程退出。"""
        errors = []
        for server in self.servers:
            try:
                offset = self._query_server(server)
            except OSError as e:
                errors.append(f'{server}: {e}')
                continue

            with self._lock:
                self.offset = offset
                self.base_timestamp = time_.time() + offset
                self.base_monotonic = time_.monotonic()
                self.server = server
                self.synced = True
                self.last_sync_monotonic = time_.monotonic()
                self.retry_after_monotonic = 0.0
                self._warned = False
            self._log_info(f'网络时间已校准: {server}, offset={offset:.3f}s')
            return

        with self._lock:
            self.retry_after_monotonic = time_.monotonic() + self.retry_interval
            if not self._warned:
                detail = '; '.join(errors) if errors else '没有可用服务器'
                self._log_warning(f'NTP 校时失败，暂时使用本机时间: {detail}')
                self._warned = True

    def timestamp(self):
        """返回校准时间戳；未同步或同步未完成时返回本机时间，零网络调用。"""
        self._schedule_sync()
        with self._lock:
            if self.synced:
                return self.base_timestamp + (time_.monotonic() - self.base_monotonic)
            return time_.time() + self.offset

    def now(self, tz=None):
        return datetime.fromtimestamp(self.timestamp(), tz=tz)

    def status(self):
        """返回校时状态快照，只读缓存，不触发网络调用。"""
        self._schedule_sync()
        with self._lock:
            synced = self.synced
            server = self.server
            offset = self.offset
            last_sync_monotonic = self.last_sync_monotonic
        return {
            'enabled': self.enabled,
            'synced': synced,
            'server': server or '-',
            'offset': offset,
            'refresh_interval': self.refresh_interval,
            'last_sync_elapsed': (
                time_.monotonic() - last_sync_monotonic
                if synced else None
            ),
        }

    @staticmethod
    def monotonic():
        return time_.monotonic()

    @staticmethod
    def sleep(seconds):
        time_.sleep(seconds)

    @staticmethod
    def _log_info(message):
        try:
            from module.logger import logger
            logger.info(message)
        except Exception:
            pass

    @staticmethod
    def _log_warning(message):
        try:
            from module.logger import logger
            logger.warning(message)
        except Exception:
            pass


network_time = NetworkTimeSource()


def refresh_time(force=False):
    return network_time.refresh(force=force)


def now(tz=None):
    return network_time.now(tz=tz)


def utcnow():
    return now(timezone.utc)


def timestamp():
    return network_time.timestamp()


def status():
    return network_time.status()


def monotonic():
    return network_time.monotonic()


def sleep(seconds):
    network_time.sleep(seconds)
