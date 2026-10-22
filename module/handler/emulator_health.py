"""独立、限定目标和期限的 ADB 健康探测，不复用业务连接。"""

import io
import ipaddress
import os
import subprocess
import time

from module.config.server import VALID_CHANNEL_PACKAGE, VALID_PACKAGE


class AdbProbe:
    """固定恢复目标，禁止 auto 在多开环境下选中另一设备。"""

    def __init__(self, binary, serial, package='auto'):
        self.binary = binary
        self.serial = serial
        self.package = package

    def run(self, args, timeout=5, binary=False):
        """独立命令有实际超时，失败不重启共享 ADB server。"""
        if not isinstance(self.serial, str) or self.serial in ('', 'auto', 'disabled', 'None'):
            return False, b'' if binary else ''
        if timeout <= 0:
            return False, b'' if binary else ''
        try:
            result = subprocess.run(
                [self.binary, '-s', self.serial, *args], timeout=timeout,
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            output = result.stdout if binary else result.stdout.decode('utf-8', errors='replace').strip()
            return result.returncode == 0, output
        except (OSError, subprocess.TimeoutExpired):
            return False, b'' if binary else ''

    def heartbeat(self, timeout=5):
        """只接受完整应答，错误文本中的子串不能证明设备存活。"""
        ok, output = self.run(['shell', 'echo', 'pong'], timeout)
        return ok and output == 'pong'

    def status(self, timeout=5):
        ok, output = self.run(['get-state'], timeout)
        return output if ok else 'unknown'

    def ready(self, cancel, timeout=300):
        """在同一总期限内确认系统、目标包和一张有效的 1280×720 截图。

        游戏登录与控制后端验证仍由新 Device 和 Restart 任务执行，
        本方法成功只代表系统就绪，不能报告业务已恢复。
        """
        deadline = time.monotonic() + timeout
        known = set(VALID_PACKAGE) | set(VALID_CHANNEL_PACKAGE)
        expected = {self.package} if self.package != 'auto' else known
        while not cancel.is_set() and time.monotonic() < deadline:
            def query(args, binary=False):
                if cancel.is_set():
                    return False, b'' if binary else ''
                return self.run(args, min(5, deadline - time.monotonic()), binary=binary)

            ok, state = query(['get-state'])
            if not ok or state != 'device':
                # TCP 模拟器重启后可能需要重新注册，但只连接已经冻结的本机端口。
                try:
                    host, port = self.serial.rsplit(':', 1)
                    local = ipaddress.ip_address(host).is_loopback and 0 < int(port) < 65536
                except (ValueError, AttributeError):
                    local = False
                if local:
                    query(['connect', self.serial])
            if ok and state == 'device':
                ok, boot = query(['shell', 'getprop', 'sys.boot_completed'])
                if ok and boot == '1':
                    ok, packages = query(['shell', 'pm', 'list', 'packages'])
                    installed = {line.removeprefix('package:').strip() for line in packages.splitlines()}
                    if ok and expected.intersection(installed):
                        ok, data = query(['exec-out', 'screencap', '-p'], binary=True)
                        if ok:
                            try:
                                from PIL import Image
                                with Image.open(io.BytesIO(data)) as image:
                                    image.load()
                                    if image.size == (1280, 720) and not cancel.is_set():
                                        return True
                            except (OSError, ValueError):
                                pass
            if cancel.wait(min(2, max(0, deadline - time.monotonic()))):
                return False
        return False
