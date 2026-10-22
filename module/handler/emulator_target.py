"""恢复操作的实例身份校验及停止确认，未知平台一律拒绝猜测。"""

import os
import subprocess
import time

from module.handler.emulator_recovery_state import RecoveryBlocked


class RecoveryTarget:
    """优先使用实例管理命令；进程方式仅接受精确参数和路径。"""

    def __init__(self, platform):
        self.platform = platform
        self.instance = platform.emulator_instance
        instance = self.instance
        if instance is None:
            raise RecoveryBlocked('无法唯一识别目标模拟器')
        kind, path, name = instance.type, instance.path, instance.name
        if not all(isinstance(v, str) and v for v in (kind, path, name, instance.serial)):
            raise RecoveryBlocked('模拟器类型、路径、名称或序列号缺失')
        if instance.serial != platform.serial:
            raise RecoveryBlocked('实例序列号与当前控制目标不一致')
        self.identity = (kind, os.path.normcase(os.path.realpath(path)), name)
        if os.name != 'nt' or kind not in ('MuMuPlayer12', 'MuMuPlayerX', 'BlueStacks5',
                                          'NoxPlayer', 'NoxPlayer64', 'LDPlayer3', 'LDPlayer4',
                                          'LDPlayer9', 'LDPlayer14', 'MEmuPlayer'):
            raise RecoveryBlocked(f'{kind} 尚无经过实例校验的自动恢复适配，拒绝按进程名关闭')
        if kind == 'MuMuPlayer12' and instance.MuMuPlayer12_id is None:
            raise RecoveryBlocked('MuMu 实例索引无法解析')
        if kind.startswith('LDPlayer') and instance.LDPlayer_id is None:
            raise RecoveryBlocked('雷电实例索引无法解析')
        if not os.path.isfile(path):
            raise RecoveryBlocked('模拟器程序路径不存在')
        # 名称将用于厂商控制台参数；不接受 shell 元字符或路径分隔符。
        if any(char in name for char in (chr(34), chr(92), chr(10), chr(13), ";", "&", "|", "<", ">", "/")):
            raise RecoveryBlocked('模拟器实例名称包含非法字符')

    def _console(self):
        from module.device.platform.emulator_windows import Emulator
        return Emulator.single_to_console(self.instance.emulator.path)

    def _command(self, args, timeout=30):
        """直接执行参数数组，禁止 shell 展开；超时即失败。"""
        return subprocess.run(args, timeout=timeout, capture_output=True,
                              creationflags=subprocess.CREATE_NO_WINDOW, check=True)

    def processes(self):
        """只返回路径和完整实例参数同时匹配的进程，不匹配共享服务。"""
        import psutil
        kind, expected, name = self.identity
        selectors = {
            'BlueStacks5': ('--instance', name), 'MuMuPlayerX': ('-m', name),
            'NoxPlayer': (f'-clone:{name}',), 'NoxPlayer64': (f'-clone:{name}',),
            'MEmuPlayer': (name,),
        }
        selector = selectors.get(kind)
        if selector is None:
            raise RecoveryBlocked('该模拟器必须通过厂商实例状态查询')
        found = []
        for proc in psutil.process_iter():
            try:
                if proc.name().casefold() != os.path.basename(expected).casefold():
                    continue
                if os.path.normcase(os.path.realpath(proc.exe())) != expected:
                    continue
                args = proc.cmdline()
                if kind in ('BlueStacks5', 'MuMuPlayerX') and selector[0] not in args:
                    raise RecoveryBlocked('同路径进程缺少实例参数，无法证明目标已停止')
                if any(tuple(args[i:i + len(selector)]) == selector for i in range(len(args))):
                    found.append(proc)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as exc:
                # 无权限不能证明该进程属于别的实例。
                raise RecoveryBlocked('进程身份不可读，拒绝自动恢复') from exc
        return found

    def running(self):
        """返回明确的实例状态，查询失败抛异常而非假装已停止。"""
        kind = self.identity[0]
        if kind == 'MuMuPlayer12':
            info = self.platform._mumu12_instances(self.instance.emulator.path)
            entry = info.get(str(self.instance.MuMuPlayer12_id)) if isinstance(info, dict) else None
            if not isinstance(entry, dict) or type(entry.get('is_process_started')) is not bool:
                raise RecoveryBlocked('MuMu 实例状态不可读')
            return entry['is_process_started']
        if kind.startswith('LDPlayer'):
            output = self._command([self._console(), 'list2'], timeout=10).stdout.decode('utf-8', errors='replace')
            for row in output.splitlines():
                columns = row.split(',')
                if columns[0] == str(self.instance.LDPlayer_id) and len(columns) >= 7:
                    return any(int(pid) > 0 for pid in columns[5:7])
            raise RecoveryBlocked('雷电实例状态不可读')
        return bool(self.processes())

    def stop(self, checkpoint):
        """优先厂商按实例退出，蓝叠只终止已核验的专属播放器。"""
        checkpoint()
        kind, _, name = self.identity
        if kind == 'MuMuPlayer12':
            self._command([self._console(), 'api', '-v', str(self.instance.MuMuPlayer12_id), 'shutdown_player'])
        elif kind.startswith('LDPlayer'):
            self._command([self._console(), 'quit', '--index', str(self.instance.LDPlayer_id)])
        elif kind in ('NoxPlayer', 'NoxPlayer64'):
            self._command([self.instance.path, f'-clone:{name}', '-quit'])
        elif kind == 'MEmuPlayer':
            self._command([self._console(), 'stop', '-n', name])
        elif kind == 'MuMuPlayerX':
            # 复用已有共享祖先进程排除逻辑，不做宽泛进程树终止。
            self.platform._stop_mumu_x_instance(self.instance)
        else:
            for proc in self.processes():
                checkpoint()
                # psutil 的 Process.kill 会检查 PID 重用；仍重新确认路径和参数。
                if any(p.pid == proc.pid and p.create_time() == proc.create_time() for p in self.processes()):
                    proc.kill()

    def wait_stopped(self, cancel, checkpoint, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            checkpoint()
            if not self.running():
                return True
            if cancel.wait(1):
                return False
        return False

    def start(self, checkpoint):
        """只发一次实例启动命令；系统就绪由独立探针判断。"""
        checkpoint()
        kind, _, name = self.identity
        if kind == 'MuMuPlayer12':
            args = [self._console(), 'api', '-v', str(self.instance.MuMuPlayer12_id), 'launch_player']
        elif kind.startswith('LDPlayer'):
            args = [self._console(), 'launch', '--index', str(self.instance.LDPlayer_id)]
        elif kind == 'MEmuPlayer':
            args = [self._console(), 'start', '-n', name]
        elif kind == 'MuMuPlayerX':
            args = [self.instance.path, '-m', name]
        elif kind in ('NoxPlayer', 'NoxPlayer64'):
            args = [self.instance.path, f'-clone:{name}']
        else:
            args = [self.instance.path, '--instance', name]
        # GUI 进程不等待退出；记录并由系统就绪检查决定成功，不把 Popen 当成功。
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
        if process.poll() not in (None, 0):
            raise RecoveryBlocked('模拟器启动命令失败')
