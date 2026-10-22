"""设备会话失效与已建立资源的定向释放。"""

import socket
from functools import wraps

from module.exception import EmulatorNotRunningError


class DeviceSessionExpired(EmulatorNotRunningError):
    """旧任务必须退出；旧设备永不重新启用。"""


class RecoveryStopped(SystemExit):
    """恢复不再安全时结束当前配置，禁止被普通异常恢复再次重启。"""


def session_guard(func):
    """拒绝旧会话的操作和迟到结果；不隐式构造新设备。"""
    @wraps(func)
    def wrapped(self, *args, **kwargs):
        DeviceSession.check_session(self)
        result = func(self, *args, **kwargs)
        DeviceSession.check_session(self)
        return result
    return wrapped


class DeviceSession:
    """每个 Device 独立失效；只关闭本对象实际建立的资源。"""

    def check_session(self):
        reason = self.__dict__.get('_session_invalid')
        if reason:
            raise DeviceSessionExpired(reason)

    def invalidate_session(self, reason):
        """先封禁新操作，再打断本会话已知 socket；不关闭共享 ADB。"""
        self._session_invalid = str(reason)
        self._scrcpy_alive = False
        names = ('_maatouch_stream', '_maatouch_stream_storage', '_minitouch_client',
                 '_scrcpy_video_socket', '_scrcpy_control_socket', '_scrcpy_server_stream',
                 '_recovery_adb_stream', 'reverse_server')
        for name in names:
            value = self.__dict__.get(name)
            if value is None:
                continue
            connection = getattr(value, 'conn', value)
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass
            try:
                value.close()
            except (OSError, AttributeError):
                pass

    def close_session(self):
        """在旧任务退出后回收线程与本地句柄；回收失败不得启动新实例。"""
        self.invalidate_session('设备会话已关闭')
        for name in ('_scrcpy_stream_loop_thread', '_maatouch_init_thread', '_minitouch_init_thread'):
            thread = self.__dict__.get(name)
            if thread is not None:
                thread.join(timeout=2)
                if thread.is_alive():
                    raise RuntimeError(f'旧设备线程未退出: {name}')
        # 不使用 getattr(self, ...) 触发 cached_property 建立新连接。
        ipc = self.__dict__.get('nemu_ipc')
        if ipc is not None:
            ipc.disconnect()
            self.__dict__.pop('nemu_ipc', None)
        # 清理走独立有界命令，不借旧连接重连，也不执行 remove-all。
        from module.handler.emulator_health import AdbProbe
        serial = self.__dict__.get('serial')
        binary = self.__dict__.get('adb_binary')
        if serial and binary:
            probe = AdbProbe(binary, serial)
            for key, verb in (('_owned_adb_forwards', 'forward'), ('_owned_adb_reverses', 'reverse')):
                for endpoint in self.__dict__.pop(key, set()):
                    ok, _ = probe.run([verb, '--remove', endpoint], timeout=3)
                    if not ok:
                        raise RuntimeError(f'无法释放本设备转发: {endpoint}')
        client = self.__dict__.pop('u2', None)
        if client is not None:
            session = client.__dict__.get('http') or client.__dict__.get('_session')
            if session is not None and hasattr(session, 'close'):
                session.close()
        self.__dict__.pop('ldopengl', None)
        for name in ('adb', 'adb_client', 'image', '_minitouch_builder', '_maatouch_builder'):
            self.__dict__.pop(name, None)
