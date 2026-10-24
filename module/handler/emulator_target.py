"""恢复操作的实例身份校验及停止确认，未知平台一律拒绝猜测。"""

import os
import subprocess
import time

from module.handler.emulator_recovery_state import RecoveryBlocked
from module.logger import logger


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

    def _command(self, args, timeout=30, check=True):
        """直接执行参数数组，禁止 shell 展开；超时即失败。

        Args:
            check: 非零退出码是否视为失败。停止类命令必须传 False——见 stop()。
        """
        # CREATE_NO_WINDOW 是 Windows 专有常量，Linux/macOS 上直接取属性即报
        # AttributeError（CI 跑 unittest 时整片红）。按 os.name 判断平台，
        # 非 Windows 传 0 表示不设置创建标志，与 module/handler/emulator_health.py
        # 保持同一口径。
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        return subprocess.run(args, timeout=timeout, capture_output=True,
                              creationflags=creationflags, check=check)

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
        """优先厂商按实例退出，蓝叠只终止已核验的专属播放器。

        停止命令一律容忍非零退出码：实例本来就没在运行时，厂商工具会返回
        "player not running"（MuMuManager 是 -2，Windows 侧显示 4294967294），
        这并不是故障——目标状态（已停止）本来就已达成。

        历史缺陷：此前 check=True 把这个返回码当成致命错误，stop() 抛出异常
        后整轮恢复直接中止，永远走不到 start()，表现为"模拟器关了以后再也
        拉不起来"。是否真的停了由紧随其后的 wait_stopped() 查实例状态确认，
        本来就不该依赖命令退出码。
        """
        checkpoint()
        kind, _, name = self.identity
        if kind == 'MuMuPlayer12':
            self._command(
                [self._console(), 'api', '-v', str(self.instance.MuMuPlayer12_id), 'shutdown_player'],
                check=False)
        elif kind.startswith('LDPlayer'):
            self._command([self._console(), 'quit', '--index', str(self.instance.LDPlayer_id)],
                          check=False)
        elif kind in ('NoxPlayer', 'NoxPlayer64'):
            self._command([self.instance.path, f'-clone:{name}', '-quit'], check=False)
        elif kind == 'MEmuPlayer':
            self._command([self._console(), 'stop', '-n', name], check=False)
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

    def _launch_command(self):
        """按平台拼出实例启动命令，参数与旧版 _emulator_start 保持一致。"""
        kind, _, name = self.identity
        if kind == 'MuMuPlayer12':
            return [self._console(), 'api', '-v', str(self.instance.MuMuPlayer12_id), 'launch_player']
        if kind.startswith('LDPlayer'):
            return [self._console(), 'launch', '--index', str(self.instance.LDPlayer_id)]
        if kind == 'MEmuPlayer':
            return [self._console(), 'start', '-n', name]
        if kind == 'MuMuPlayerX':
            return [self.instance.path, '-m', name]
        if kind in ('NoxPlayer', 'NoxPlayer64'):
            return [self.instance.path, f'-clone:{name}']
        return [self.instance.path, '--instance', name]

    def start(self, checkpoint, cancel=None):
        """发出实例启动命令，并按旧版口径用厂商启动监视确认真的起来了。

        历史缺陷：此前只 Popen 一条命令就返回，把"命令发出去了"当成"已经启动"。
        而 MuMuManager 的 launch_player 是异步的——实例状态不干净时它会直接把
        请求丢掉（返回 result=-501），命令本身却"成功"返回，于是恢复流程以为
        已经启动，系统就绪探针空等到超时。旧版正是靠 emulator_start_watch
        （180/300/480 秒递增重试）确认 ADB 上线、shell 响应、包名可查，才稳定
        拉起模拟器，这里把这套口径补回来。
        """
        from module.device.platform.platform_windows import EMULATOR_START_WATCH_TIMEOUTS

        kind = self.identity[0]
        args = self._launch_command()
        last_reason = '模拟器启动未完成'
        for attempt, watch_timeout in enumerate(EMULATOR_START_WATCH_TIMEOUTS):
            checkpoint()
            logger.info(f'[模拟器恢复] 启动实例（第 {attempt + 1} 次）: {os.path.basename(args[0])}')
            # DETACHED_PROCESS 与 CREATE_NO_WINDOW 同为 Windows 专有，
            # 非 Windows 传 0（不设置创建标志）。
            creationflags = (subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS) \
                if os.name == 'nt' else 0
            process = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
                creationflags=creationflags)
            if process.poll() not in (None, 0):
                # 命令本身就没发出去，换实例状态再看一次再决定。
                last_reason = '模拟器启动命令返回非零退出码'
            else:
                # 启动监视：等实例进程起来、ADB 上线、系统可交互。
                if self._wait_android_started(watch_timeout, checkpoint):
                    return
                last_reason = f'实例在 {watch_timeout} 秒内未完成启动'
                logger.warning(f'[模拟器恢复] {last_reason}，停止后重试')
            # 与旧版一致：启动监视失败就先停一次再进入下一轮，避免半启动状态
            # 让下一次启动请求被厂商工具直接丢弃。
            try:
                self.stop(checkpoint)
                if cancel is not None:
                    self.wait_stopped(cancel, checkpoint, timeout=30)
            except RecoveryBlocked:
                raise
            except Exception as exc:
                logger.warning(f'[模拟器恢复] 重试前停止实例失败，继续下一轮: {exc}')

        raise RecoveryBlocked(last_reason)

    def _wait_android_started(self, timeout, checkpoint):
        """按旧版口径等待实例进入可交互状态。

        只要实例进程拉起且 Android 启动完成即可返回；游戏是否登录由后续
        Restart 任务负责，不在这里等。

        冷启动宽容：实例刚 launch 后的头 20-30 秒 MuMuManager 可能完全
        不应答（info 命令超时），这是正常冷启动窗口而非故障。历史缺陷：
        此前对 RecoveryBlocked 一律立即上抛，一次查询超时就把整轮恢复
        判死（2026-10-24 14:53 事故：启动后 27 秒 info 超时 → 恢复失败）。
        "实例状态不可读"属瞬态，继续等到 deadline；身份类阻断（进程身份、
        平台不支持）不是等能等好的，仍立即上抛。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            checkpoint()
            try:
                if self.running() and self._android_started():
                    return True
            except RecoveryBlocked as exc:
                if '不可读' not in str(exc):
                    raise
                logger.warning(f'[模拟器恢复] 实例状态暂不可读（冷启动窗口），继续等待: {exc}')
            except Exception as exc:
                logger.warning(f'[模拟器恢复] 查询实例状态失败，继续等待: {exc}')
            time.sleep(3)
        return False

    def _android_started(self):
        """查询 Android 是否启动完成；非 MuMu 平台退回进程级判断。"""
        kind = self.identity[0]
        if kind != 'MuMuPlayer12':
            return True
        info = self.platform._mumu12_instances(self.instance.emulator.path)
        entry = info.get(str(self.instance.MuMuPlayer12_id)) if isinstance(info, dict) else None
        if not isinstance(entry, dict):
            raise RecoveryBlocked('MuMu 实例状态不可读')
        return bool(entry.get('is_android_started'))
