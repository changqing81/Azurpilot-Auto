"""Windows 平台模拟器控制。继承 PlatformBase 和 EmulatorManager，
实现 Windows 上模拟器的启动、窗口聚焦和进程管理。"""

from __future__ import annotations
import ctypes
import json
import os
import re
import subprocess
import threading
import time
from functools import wraps

import psutil

from deploy.Windows.utils import DataProcessInfo
from module.base.decorator import run_once
from module.base.timer import Timer
from module.device.connection_attr import ConnectionAttr
from module.device.platform.platform_base import PlatformBase
from module.device.platform.emulator_windows import Emulator, EmulatorInstance, EmulatorManager
from module.exception import EmulatorOpBusy
from module.logger import logger


class EmulatorUnknown(Exception):
    """未知模拟器类型异常。"""
    pass


# 模拟器冷启动的监视超时（秒），按 emulator_start() 的重试次数依次放宽。
# 实机实测正常冷启动约 20~30 秒，180 秒对正常情况足够；递增是为了兜住慢机器
# （机械盘、多开、首次启动）。重试时要"先关掉再启动"，判早了就是白关一次，
# 多给点时间比过早重启代价小得多。
EMULATOR_START_WATCH_TIMEOUTS = (180, 300, 480)
# 启动监视期间打印进度的间隔（秒）。监视最长可达 480 秒且期间日志是静默的，
# 不打印进度的话，用户无法判断 ALAS 是在耐心等待还是已经卡死。
EMULATOR_START_PROGRESS_INTERVAL = 30
# 启动监视期间检查 MuMu 错误对话框的间隔（秒）。枚举窗口开销较大，不每次循环都做。
EMULATOR_START_DIALOG_CHECK_INTERVAL = 2

# 多开安全约束：MuMu12 的 MuMuNxMain.exe / MuMuPlayer.exe 是 GUI 单例主程序，
# 承载全部实例的窗口，MuMuManager.exe 是共享管理工具。按进程名强杀这些共享
# 进程会把其它实例（含用户手动多开）的窗口一并关掉，表现为"MuMu 整个退出"。
# 因此对 MuMu12 的一切停止/清理动作都只针对单个实例（MuMuManager api -v <id>），
# 不再按进程名杀共享进程。
# 查询 MuMu12 实例状态的轮询间隔（秒）。同时用作无法查询状态时的兜底等待，
# 与旧版"等待2秒让进程状态稳定"保持一致。
MUMU12_STATE_POLL_INTERVAL = 2
# 确认 MuMu12 实例真正关闭的最长等待（秒）。
MUMU12_STOP_WAIT_TIMEOUT = 60

# MuMu X（MuMu9）Hypervisor 层的进程名（小写）。这两个进程是多开共享的，
# 命令行里不带实例名，只能通过「播放器进程的父进程」这条关系反查，
# 并确认它只服务于本实例后才允许终止。
MUMU_X_HYPERVISOR_EXES = ('muvm6headless.exe', 'muvm6svc.exe')
# 从 MuMu X 播放器进程向上找 Hypervisor 父进程时最多回溯的层数。
# 正常进程树是 播放器 -> Muvm6Headless -> Muvm6SVC，3 层足够；
# 留出余量以兼容启动器/中间 shell 进程，但必须设上限避免无限向上找到 explorer.exe。
MUMU_X_PARENT_WALK_DEPTH = 4


def run_mumu_manager(exe, args, timeout=15):
    """执行 MuMuManager 命令并返回其标准输出。

    与 PlatformWindows.execute 的区别：这里需要读取命令输出（如 info 返回的
    JSON），而且会被轮询高频调用，因此不写日志以免刷屏。

    Args:
        exe (str): MuMu 主程序路径，会被转换成同目录的 MuMuManager.exe。
        args (str): MuMuManager 子命令，如 'info -v all'。
        timeout (int): 超时秒数。

    Returns:
        str: 命令的标准输出；执行失败返回空字符串。
    """
    manager = Emulator.single_to_console(exe).replace('\\', '/')
    command = f'"{manager}" {args}'
    try:
        result = subprocess.run(
            command,
            shell=True,
            timeout=timeout,
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
        )
    except subprocess.TimeoutExpired:
        logger.warning(f'[设备-Windows] MuMuManager 命令超时 {timeout} 秒: {command}')
        return ''
    except OSError as e:
        logger.warning(f'[设备-Windows] MuMuManager 命令执行失败: {command} ({e})')
        return ''
    return result.stdout or ''


def emulator_op_exclusive(name):
    """装饰器：保证同一时刻只有一个模拟器启停操作真正作用于模拟器。

    emulator_stop / emulator_start 不只是发命令，它们会真的关闭并重启模拟器
    （关窗口、杀进程、反复重试）。两个操作并发执行时必然互相踩踏：一个线程
    刚发出启动命令，另一个线程随即 shutdown，模拟器在冷启动完成前被反复
    打断，表现为"窗口一直卡在加载、永远起不来"。
    拿不到锁的一方直接抛 EmulatorOpBusy，由调用方跳过本轮。

    注意：锁必须覆盖操作的**全过程**（含内部 3 次重试与最长数分钟的启动
    监视），而不是只包住发命令那一下——否则超时后被放弃的调用线程仍会
    继续关/开模拟器，踩踏依旧。

    Args:
        name (str): 操作名称，用于日志与异常信息。

    Raises:
        EmulatorOpBusy: 已有启停操作在进行，本次被跳过。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if not self._emulator_op_lock.acquire(blocking=False):
                logger.warning(f'[设备-Windows] 已有模拟器启停操作正在进行，跳过本次{name}')
                raise EmulatorOpBusy(f'{name}: 另一个模拟器启停操作正在进行')
            try:
                return func(self, *args, **kwargs)
            finally:
                self._emulator_op_lock.release()
        return wrapper
    return decorator


def get_focused_window():
    """获取当前前台窗口的句柄。"""
    return ctypes.windll.user32.GetForegroundWindow()


def set_focus_window(hwnd):
    """将指定窗口设置为前台窗口。"""
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def get_window_text(hwnd):
    """获取窗口标题文本。"""
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ''
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def check_mumu_error_dialog():
    """
    检测 MuMu 模拟器的错误对话框（如权限冲突）。

    Returns:
        bool: True 表示检测到错误对话框
    """
    # MuMu12 错误对话框的窗口标题包含 "MuMu" 或 "NemuWindow"
    # 权限冲突对话框标题通常为 "MuMuPlayer" 或类似
    found = False

    def enum_callback(hwnd, _):
        nonlocal found
        text = get_window_text(hwnd)
        if text and ('MuMu' in text or 'Nemu' in text):
            # 检查是否为错误对话框（通常有较短标题且是弹出窗口）
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                # 枚举子窗口查找包含 "无法启动" 或 "冲突" 的文本
                child_found = [False]

                def child_callback(child_hwnd, __):
                    child_text = get_window_text(child_hwnd)
                    if child_text and ('无法启动' in child_text or '冲突' in child_text
                                       or 'error' in child_text.lower()
                                       or 'cannot' in child_text.lower()):
                        child_found[0] = True
                    return True

                ctypes.windll.user32.EnumChildWindows(
                    hwnd,
                    ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(child_callback),
                    0
                )
                if child_found[0]:
                    found = True
                    logger.warning(f'[设备-Windows] 检测到MuMu错误对话框: "{text}"')
        return True

    try:
        ctypes.windll.user32.EnumWindows(
            ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(enum_callback),
            0
        )
    except Exception as e:
        logger.warning(f'[设备-Windows] 检查MuMu错误对话框失败: {e}')
    return found


def minimize_window(hwnd):
    """最小化指定窗口。"""
    ctypes.windll.user32.ShowWindow(hwnd, 6)


def get_window_title(hwnd):
    """
    获取指定窗口的标题文本。

    Args:
        hwnd: 窗口句柄

    Returns:
        str: 窗口标题
    """
    text_len_in_characters = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    string_buffer = ctypes.create_unicode_buffer(
        text_len_in_characters + 1)  # +1 用于 null 终止符 \0
    ctypes.windll.user32.GetWindowTextW(hwnd, string_buffer, text_len_in_characters + 1)
    return string_buffer.value


def flash_window(hwnd, flash=True):
    """闪烁指定窗口以吸引注意力。"""
    ctypes.windll.user32.FlashWindow(hwnd, flash)


class PlatformWindows(PlatformBase, EmulatorManager):
    """Windows 平台的模拟器控制接口。"""

    # 进程内模拟器启停互斥锁，见 emulator_op_exclusive。
    # 类属性：alas.py 每轮恢复都会新建 Platform 实例，甚至绕过 Platform
    # 直接调用 Device.emulator_start()，只有挂在类上才能让所有调用方共享
    # 同一把锁。WebUI / MCP 等独立进程各有一把，互不影响。
    _emulator_op_lock = threading.Lock()

    def __init__(self, config, *, connect: bool = True):
        """
        Args:
            config: AzurLaneConfig 实例或配置名称
            connect: 是否立即建立 ADB 连接。
                     AlasPlus 在仅需要模拟器发现/启停控制
                     且模拟器当前离线时使用 connect=False，
                     以避免过早抛出 EmulatorNotRunningError。
        """
        if connect:
            # 原始行为：走完整的 Connection.__init__ 流程，
            # 包括 detect_device() 和 adb_connect()
            super().__init__(config)
        else:
            # 轻量初始化：仅准备 config/adb_client/serial，
            # 不调用 adb_connect()，因此可以在模拟器尚未运行时
            # 安全使用 emulator_instance/emulator_start()
            ConnectionAttr.__init__(self, config)

    @classmethod
    def execute(cls, command, wait=False, timeout=30):
        """
        执行外部命令。

        Args:
            command (str): 要执行的命令
            wait (bool): 是否同步等待命令完成（默认False异步执行）
            timeout (int): 同步执行时的超时秒数（默认30秒）

        Returns:
            subprocess.Popen: 异步执行时返回子进程对象
            subprocess.CompletedProcess: 同步执行时返回完成结果
        """
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        logger.info(f'[设备-Windows] 执行: {command}')

        if wait:
            # 同步执行，等待命令完成
            # 用于需要确保命令执行完毕的场景（如MuMu12的shutdown_player）
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    timeout=timeout,
                    close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                logger.info(f'[设备-Windows] 命令完成，返回码: {result.returncode}')
                return result
            except subprocess.TimeoutExpired:
                logger.warning(f'[设备-Windows] 命令超时 {timeout} 秒')
                return None
        else:
            # 异步执行，不等待完成
            # 通过 `cmd /c start` 启动进程，使其脱离 Alas 进程树。
            # 之前使用的 `start_new_session=True` 在 Windows 上仅等同于
            # `CREATE_NEW_PROCESS_GROUP`，不会改变父子进程关系，
            # `taskkill /T` 仍会终止子进程，导致关闭 Alas 时模拟器被一并关闭。
            # 使用 `cmd /c start` 后，cmd.exe 会立即退出，
            # 目标进程的父进程变为已退出的 cmd.exe，从而脱离 Alas 进程树。
            proc = subprocess.Popen(
                f'start "" /b {command}',
                shell=True,
                close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # 等待 cmd.exe 退出，确保目标进程已脱离 Alas 进程树
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(f'[设备-Windows] 启动命令未在 5 秒内退出: {command}')
            return proc

    @classmethod
    def _is_same_exe(cls, proc, expect_exe: str) -> bool:
        """判断进程的可执行文件是否与期望路径一致。

        Windows 路径大小写不敏感，而注册表 / MuiCache / 用户配置里记录的大小写
        常常与实际进程不同（如 MuMuPlayer.EXE 与 MuMuPlayer.exe），因此统一用
        os.path.normcase(os.path.abspath(...)) 归一化后再比对。

        Args:
            proc (psutil.Process): 待校验的进程。
            expect_exe (str): 期望的可执行文件绝对路径。

        Returns:
            bool: 路径一致返回 True；取不到进程路径或路径不一致返回 False。
                取不到路径时按"不一致"处理——宁可少杀，不可误杀。
        """
        if not expect_exe:
            return False
        try:
            actual = proc.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            return False
        if not actual:
            return False
        return (
            os.path.normcase(os.path.abspath(actual))
            == os.path.normcase(os.path.abspath(expect_exe))
        )

    @classmethod
    def kill_process_by_regex(cls, regex: str, expect_exe: str = None,
                              expect_instance: str = None) -> int:
        """
        终止命令行匹配给定正则表达式的进程，可选按实例维度二次校验。

        多开安全背景：同一模拟器家族的多开实例共用同一份程序文件，有的还共用
        Hypervisor 进程。只按命令行正则杀进程时，正则写得稍宽（或共享进程的
        命令行里根本不含实例名）就会把**别的实例**一并杀掉。为此提供两道可选的
        实例校验闸门，只有**全部校验通过**的进程才会被终止：

        - expect_exe：目标可执行文件的绝对路径。用 proc.exe() 取进程真实路径后
          归一化比对，杜绝"同名不同目录 / 不同实例同 exe 名"的误杀。
        - expect_instance：实例标识（如 MuMu X 的 nemu-12.0-x64-default）。要求
          进程命令行里确实出现该标识，杜绝"共享进程命令行不带实例名"的误杀。

        两个参数同时传入时是 AND 关系。都不传时保持旧行为（仅按正则匹配），
        向后兼容既有调用点。

        Args:
            regex (str): 命令行正则表达式。
            expect_exe (str | None): 期望的进程可执行文件绝对路径；
                None 表示不做 exe 路径校验。
            expect_instance (str | None): 期望出现在命令行中的实例标识；
                None 表示不做实例标识校验。

        Returns:
            int: 已终止的进程数量
        """
        count = 0

        for proc in psutil.process_iter():
            cmdline = DataProcessInfo(proc=proc, pid=proc.pid).cmdline
            if not re.search(regex, cmdline):
                continue
            # 闸门 1：exe 绝对路径校验（取不到真实路径时按不匹配处理）
            if expect_exe and not cls._is_same_exe(proc, expect_exe):
                logger.info(
                    f'[设备-Windows] 跳过 exe 路径不匹配的进程，避免误杀: {cmdline}'
                )
                continue
            # 闸门 2：实例标识校验（共享进程的命令行不含实例名，会被这道拦下）
            if expect_instance and expect_instance not in cmdline:
                logger.info(
                    f'[设备-Windows] 跳过实例标识不匹配的进程，避免误杀: {cmdline}'
                )
                continue
            logger.info(f'[设备-Windows] 终止模拟器: {cmdline}')
            proc.kill()
            count += 1

        return count

    @classmethod
    def _process_exe_name(cls, proc) -> str:
        """安全获取进程的可执行文件名（小写，不含目录）。

        proc.exe() / proc.name() 在进程退出或权限不足时都会抛异常，这里统一
        吞掉并返回空字符串，由调用方按"无法识别"处理。

        Args:
            proc (psutil.Process): 目标进程。

        Returns:
            str: 小写的 exe 文件名，如 muvm6headless.exe；取不到返回空字符串。
        """
        try:
            exe = proc.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            exe = None
        if exe:
            return os.path.basename(exe).lower()
        try:
            return (proc.name() or '').lower()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            return ''

    @classmethod
    def _iter_mumu_x_players(cls, instance_name: str, expect_exe: str = None) -> list:
        """找出属于指定实例的 MuMu X 播放器进程。

        MuMu X 的多开实例各自有一个 NemuPlayer.exe，命令行形如::

            "...\\nemu9\\EmulatorShell\\NemuPlayer.exe" -m nemu-12.0-x64-default -s 0 -l

        实例名是这里唯一可靠的实例标识，因此按 "-m <实例名>" 精确匹配，并要求
        实例名之后紧跟空白或行尾——否则 nemu-12.0-x64-default 会连带匹配到
        nemu-12.0-x64-default2 这类前缀相同的实例，照样误杀。

        Args:
            instance_name (str): MuMu X 实例名，如 nemu-12.0-x64-default。
            expect_exe (str | None): 期望的 exe 绝对路径，用于排除其它安装目录
                下的同名程序。

        Returns:
            list: 匹配到的播放器进程列表（psutil.Process）。
        """
        players = []
        if not instance_name:
            return players
        pattern = re.compile(
            rf'NemuPlayer\.exe.*-m\s+{re.escape(instance_name)}(?:\s|$)'
        )
        for proc in psutil.process_iter():
            cmdline = DataProcessInfo(proc=proc, pid=proc.pid).cmdline
            if not pattern.search(cmdline):
                continue
            if expect_exe and not cls._is_same_exe(proc, expect_exe):
                continue
            players.append(proc)
        return players

    @classmethod
    def _mumu_x_comment_name(cls, proc) -> str:
        """从 Hypervisor 进程的命令行里解析 --comment 携带的实例名。

        Muvm6Headless.exe 的命令行形如::

            "...\\Muvm6Headless.exe" --comment nemu-12.0-x64-default --startvm <uuid>

        ``--comment`` 的值就是它所属的实例名，是判断"这个父进程属于哪个实例"
        最直接的证据；Muvm6SVC.exe 这类共享进程通常没有该参数，返回空字符串。

        Args:
            proc (psutil.Process): 待检查的进程。

        Returns:
            str: --comment 的值；命令行里没有该参数时返回空字符串。
        """
        try:
            cmdline = ' '.join(proc.cmdline())
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            return ''
        res = re.search(r'--comment\s+("[^"]*"|\S+)', cmdline)
        if not res:
            return ''
        return res.group(1).strip('"')

    @classmethod
    def _mumu_x_subtree_instances(cls, parent) -> tuple:
        """统计某个 Hypervisor 进程的子树里出现过的 MuMu X 实例名。

        用于判断"这个父进程是不是只服务于本实例"：只要子树里出现别的实例名，
        或者存在解析不出实例名的播放器进程，就说明它可能是多实例共享进程，
        不能杀。

        Args:
            parent (psutil.Process): 待检查的 Hypervisor 进程
                （Muvm6Headless.exe / Muvm6SVC.exe）。

        Returns:
            tuple[set, bool]: (子树里出现过的实例名集合, 是否所有播放器进程都能
                解析出实例名)。第二个值为 False 时调用方必须放弃杀父进程。
                集合里同时包含播放器的 -m 参数与 Hypervisor 的 --comment 参数，
                两者任一出现别的实例名都说明该父进程是多实例共享的。
        """
        names = set()
        resolved = True
        try:
            children = parent.children(recursive=True)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            return names, False
        for child in children:
            cmdline = DataProcessInfo(proc=child, pid=child.pid).cmdline
            if 'NemuPlayer.exe' in cmdline:
                # 播放器进程：实例名在 -m 参数里
                res = re.search(r'-m\s+(\S+)', cmdline)
                if res:
                    names.add(res.group(1))
                else:
                    resolved = False
                continue
            # Hypervisor 进程：实例名在 --comment 参数里。这条同样重要——共享的
            # Muvm6SVC 子树里可能挂着**别的实例**的 Muvm6Headless，只统计播放器
            # 会漏掉这种情况，从而误判成"只服务本实例"
            if cls._process_exe_name(child) in MUMU_X_HYPERVISOR_EXES:
                comment_name = cls._mumu_x_comment_name(child)
                if comment_name:
                    names.add(comment_name)
        return names, resolved

    def _stop_mumu_x_instance(self, instance: EmulatorInstance) -> int:
        """按实例维度停止 MuMu X（MuMu9）实例，杜绝多开误杀。

        一个 MuMu X 实例对应三个进程::

            "...\\nemu9\\EmulatorShell\\NemuPlayer.exe" -m nemu-12.0-x64-default -s 0 -l
            "C:\\Program Files\\Muvm6Vbox\\Hypervisor\\Muvm6Headless.exe" --comment ... --startvm ...
            "C:\\Program Files\\Muvm6Vbox\\Hypervisor\\Muvm6SVC.exe" --Embedding

        只有 NemuPlayer.exe 的命令行带实例名；Muvm6Headless.exe / Muvm6SVC.exe
        是 Hypervisor 层的共享进程，命令行不带实例名。旧实现把它们和播放器一起
        按进程名正则杀掉，等于杀掉**所有** MuMu X 实例——这是已确认的必然误杀。

        处理策略（优先级：不误杀 > 彻底杀干净）::

            1. 只杀命令行含 "-m <实例名>" 的 NemuPlayer.exe（并校验 exe 路径）；
            2. 顺着播放器的 PPID 链向上找 Muvm6Headless.exe / Muvm6SVC.exe；
            3. 只有当该祖先进程的子树里"只出现本实例这一个实例名"时才杀它；
            4. 任何一步判断不了（读不到命令行、找不到实例名、发现别的实例），
               一律跳过并告警，绝不冒险。

        Args:
            instance (EmulatorInstance): 目标模拟器实例。

        Returns:
            int: 已终止的进程数量（父进程 + 播放器）。
        """
        instance_name = instance.name
        if not instance_name:
            logger.warning(
                '[设备-Windows] MuMu X 实例名为空，无法区分多开实例，'
                '跳过停止以避免误杀其它实例'
            )
            return 0

        expect_exe = instance.emulator.path
        players = self._iter_mumu_x_players(instance_name, expect_exe)
        if not players:
            logger.warning(
                f'[设备-Windows] 未找到 MuMu X 实例 {instance_name} 的播放器进程，'
                f'跳过停止（可能已关闭，或实例名与进程命令行不一致）'
            )
            return 0

        # 先沿 PPID 链把 Hypervisor 祖先进程全部收集起来再统一判定，
        # 避免边杀边查导致进程树变化、判定结果漂移
        ancestors = []
        seen_pids = set()
        for player in players:
            proc = player
            for _ in range(MUMU_X_PARENT_WALK_DEPTH):
                try:
                    proc = proc.parent()
                except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
                    proc = None
                if proc is None:
                    break
                if self._process_exe_name(proc) in MUMU_X_HYPERVISOR_EXES:
                    if proc.pid not in seen_pids:
                        seen_pids.add(proc.pid)
                        ancestors.append(proc)
                    break

        killed = 0
        for ancestor in ancestors:
            ancestor_name = self._process_exe_name(ancestor)
            # 证据 1：--comment 直接写明所属实例名（Muvm6Headless 有，Muvm6SVC 通常没有）
            comment_name = self._mumu_x_comment_name(ancestor)
            if comment_name:
                if comment_name != instance_name:
                    logger.warning(
                        f'[设备-Windows] MuMu X 父进程 {ancestor_name}(PID={ancestor.pid}) '
                        f'的 --comment 为 {comment_name!r}，不属于本实例 '
                        f'{instance_name!r}，跳过不杀'
                    )
                    continue
            else:
                # 证据 2：没有 --comment 时退回子树统计，要求子树里只出现本实例
                names, resolved = self._mumu_x_subtree_instances(ancestor)
                if not resolved or names != {instance_name}:
                    logger.warning(
                        f'[设备-Windows] MuMu X 父进程 {ancestor_name}(PID={ancestor.pid}) '
                        f'既没有 --comment 证据，子树实例也不唯一（'
                        f'{sorted(names) if names else "无法解析"}），跳过不杀以避免误杀'
                    )
                    continue
            try:
                ancestor.kill()
                killed += 1
                logger.info(
                    f'[设备-Windows] 终止 MuMu X 实例 {instance_name} 独占的 '
                    f'父进程 {ancestor_name}(PID={ancestor.pid})'
                )
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError) as e:
                logger.warning(
                    f'[设备-Windows] 终止 MuMu X 父进程 {ancestor_name} 失败: {e}'
                )

        # 播放器进程已在上面的 _iter_mumu_x_players 里通过「-m <实例名> 精确匹配 +
        # exe 路径校验」筛过一遍，直接杀这批进程即可，不再二次全表扫描
        for player in players:
            try:
                player.kill()
                killed += 1
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError) as e:
                # 父进程被杀后播放器可能已经随之退出，这属于正常情况，只记 debug
                logger.debug(f'[设备-Windows] 终止 MuMu X 播放器进程失败（可能已退出）: {e}')
        if not ancestors:
            logger.warning(
                f'[设备-Windows] 未找到 MuMu X 实例 {instance_name} 的 Hypervisor 父进程，'
                f'仅终止了播放器进程；Hypervisor 进程留给启动监视与下一轮重试兜底'
            )
        return killed

    def _emulator_start(self, instance: EmulatorInstance):
        """
        启动模拟器（不含错误处理）。

        Args:
            instance: 模拟器实例
        """
        exe: str = instance.emulator.path
        if instance == Emulator.MuMuPlayer:
            # NemuPlayer.exe
            # 路径可能包含空格，需要引号包裹以便 `cmd /c start` 正确解析
            self.execute(f'"{exe}"')
        elif instance == Emulator.MuMuPlayerX:
            # NemuPlayer.exe -m nemu-12.0-x64-default
            self.execute(f'"{exe}" -m {instance.name}')
        elif instance == Emulator.MuMuPlayer12:
            # MuMuManager.exe api -v 0 launch_player
            # Launch via MuMuManager instead of MuMuPlayer.exe/MuMuNxMain.exe.
            # MuMuNxMain.exe is a GUI singleton, if two instances get launched at the same time,
            # the second launch request is handed over to a MuMuNxMain.exe that is still initializing
            # and gets silently dropped, while MuMuManager queues requests in backend service.
            if instance.MuMuPlayer12_id is None:
                # 实例号未知时禁止发命令：无效的 -v 会被 MuMuManager 回退到
                # 实例 0，可能把别人的实例拉起来
                raise EmulatorUnknown(
                    f'无法从实例名称解析 MuMu12 实例号，拒绝启动: {instance.name}'
                )
            self.execute(f'"{Emulator.single_to_console(exe)}" api -v {instance.MuMuPlayer12_id} launch_player')
        elif instance == Emulator.LDPlayer14 or instance == Emulator.LDPlayer9:
            # ldconsole.exe launch --index 0 --mini
            # LDPlayer above 9 has `--mini` to start as minimized window, `--hide` to start with no frontend window
            self.execute(f'"{Emulator.single_to_console(exe)}" launch --index {instance.LDPlayer_id} --mini')
        elif instance == Emulator.LDPlayerFamily:
            # ldconsole.exe launch --index 0
            self.execute(f'"{Emulator.single_to_console(exe)}" launch --index {instance.LDPlayer_id}')
        elif instance == Emulator.NoxPlayerFamily:
            # Nox.exe -clone:Nox_1
            self.execute(f'"{exe}" -clone:{instance.name}')
        elif instance == Emulator.BlueStacks5:
            # HD-Player.exe --instance Pie64
            self.execute(f'"{exe}" --instance {instance.name}')
        elif instance == Emulator.BlueStacks4:
            # Bluestacks.exe -vmname Android_1
            self.execute(f'"{exe}" -vmname {instance.name}')
        elif instance == Emulator.MEmuPlayer:
            # MEmu.exe MEmu_0
            self.execute(f'"{exe}" {instance.name}')
        elif instance.type == 'SSH':
            logger.info('[设备-Windows] 通过远程命令启动SSH模拟器')
            self.run_remote_ssh_command(getattr(self.config, 'EmulatorInfo_RemoteStartCommand', ''))
        else:
            raise EmulatorUnknown(f'Cannot start an unknown emulator instance: {instance}')

    def _emulator_stop(self, instance: EmulatorInstance):
        """
        停止模拟器（不含错误处理）。

        Args:
            instance: 模拟器实例
        """
        exe: str = instance.emulator.path
        if instance == Emulator.MuMuPlayer:
            # MuMu6 没有多实例功能，终止一个意味着终止全部
            #
            # 注意：这里按进程名杀是安全的，与 MuMu X 的共享进程问题不同——
            # MuMu6 根本不支持多开，NemuHeadless.exe / NemuSVC.exe 只服务于
            # 唯一那个实例，不存在"杀掉会误伤别的实例"的情况。
            # 共有 4 个进程:
            # "C:\Program Files\NemuVbox\Hypervisor\NemuHeadless.exe" --comment nemu-6.0-x64-default --startvm
            # "E:\ProgramFiles\MuMu\emulator\nemu\EmulatorShell\NemuPlayer.exe"
            # E:\ProgramFiles\MuMu\emulator\nemu\EmulatorShell\NemuService.exe
            # "C:\Program Files\NemuVbox\Hypervisor\NemuSVC.exe" -Embedding
            self.kill_process_by_regex(
                rf'('
                rf'NemuHeadless.exe'
                rf'|NemuPlayer.exe\"'
                rf'|NemuPlayer.exe$'
                rf'|NemuService.exe'
                rf'|NemuSVC.exe'
                rf')'
            )
        elif instance == Emulator.MuMuPlayerX:
            # MuMu X 有 3 个进程:
            # "E:\ProgramFiles\MuMu9\emulator\nemu9\EmulatorShell\NemuPlayer.exe" -m nemu-12.0-x64-default -s 0 -l
            # "C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6Headless.exe" --comment nemu-12.0-x64-default --startvm xxx
            # "C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6SVC.exe" --Embedding
            #
            # 多开安全：只有 NemuPlayer.exe 的命令行带实例名，Muvm6Headless.exe /
            # Muvm6SVC.exe 是 Hypervisor 层共享进程、命令行不带实例名。旧实现把三者
            # 一起按进程名正则杀掉，多开时等于杀掉所有 MuMu X 实例（已确认的必然误杀）。
            # 现改为按实例维度停止，判断不了就跳过并告警（不误杀 > 彻底杀干净）。
            self._stop_mumu_x_instance(instance)
        elif instance == Emulator.MuMuPlayer12:
            # MuMuManager.exe api -v 1 shutdown_player
            # 使用同步执行等待关闭完成，避免异步执行导致的实例查找失败
            if instance.MuMuPlayer12_id is None:
                # 实例号未知时禁止发命令：无效的 -v 会被 MuMuManager 回退到
                # 实例 0，导致关掉别人的实例而不是本实例
                logger.warning(
                    f'[设备-Windows] 无法从名称 {instance.name} 获取MuMu实例索引，'
                    f'跳过关闭以避免误伤其它实例'
                )
                return
            logger.info('[设备-Windows] MuMuPlayer12 关闭: 使用同步执行')
            self.execute(
                f'"{Emulator.single_to_console(exe)}" api -v {instance.MuMuPlayer12_id} shutdown_player',
                wait=True,
                timeout=30
            )
        elif instance == Emulator.LDPlayerFamily:
            # ldconsole.exe quit --index 0
            self.execute(f'"{Emulator.single_to_console(exe)}" quit --index {instance.LDPlayer_id}')
        elif instance == Emulator.NoxPlayerFamily:
            # Nox.exe -clone:Nox_1 -quit
            self.execute(f'"{exe}" -clone:{instance.name} -quit')
        elif instance == Emulator.BlueStacks5:
            # BlueStacks 有 2 个进程:
            # C:\Program Files\BlueStacks_nxt_cn\HD-Player.exe --instance Pie64
            # C:\Program Files\BlueStacks_nxt_cn\BstkSVC.exe -Embedding
            #
            # 多开安全：正则里已带 --instance <实例名>，但仍补上 exe 路径校验，
            # 避免命中另一份安装（或别的程序）里同名的 HD-Player.exe。
            # BstkSVC.exe 是共享服务进程，不在此处终止。
            self.kill_process_by_regex(
                rf'('
                rf'HD-Player.exe.*"--instance" "{instance.name}"'
                rf')',
                expect_exe=exe,
            )
        elif instance == Emulator.BlueStacks4:
            # E:\Program Files (x86)\BluestacksCN\bsconsole.exe quit --name Android
            self.execute(f'"{Emulator.single_to_console(exe)}" quit --name {instance.name}')
        elif instance == Emulator.MEmuPlayer:
            # F:\Program Files\Microvirt\MEmu\memuc.exe stop -n MEmu_0
            self.execute(f'"{Emulator.single_to_console(exe)}" stop -n {instance.name}')
        elif instance.type == 'SSH':
            logger.info('[设备-Windows] 通过远程命令停止SSH模拟器')
            self.run_remote_ssh_command(getattr(self.config, 'EmulatorInfo_RemoteStopCommand', ''))
        else:
            raise EmulatorUnknown(f'Cannot stop an unknown emulator instance: {instance}')

    def _emulator_function_wrapper(self, func: callable):
        """
        模拟器启停操作的统一包装器，处理异常。

        这里是所有启停（含所有 kill 路径）的唯一入口，因此在此处加一道前置
        闸门：识别不到模拟器实例时**直接放弃本次操作**。

        背景：`emulator_instance` 的查找依赖配置里的类型/名称/路径与运行时
        枚举结果的匹配。多开环境下若配置不完整（比如用户手填了错的实例名），
        查找会返回 None。旧实现在这种情况下会把 None 往下传，各分支要么抛
        AttributeError、要么退化成"猜一个进程杀掉"，后者在多开时就是误杀。
        宁可这一轮不停止（交给启动监视与下一轮重试兜底），也绝不猜。

        Args:
            func (callable): _emulator_start 或 _emulator_stop

        Returns:
            bool: 是否成功
        """
        try:
            instance = self.emulator_instance
            if instance is None:
                logger.warning(
                    '[设备-Windows] 未能识别模拟器实例（EmulatorInfo 的类型/名称/路径'
                    '与运行时枚举结果不匹配），放弃本次启停操作以避免误杀其它实例。'
                    '请检查「模拟器信息」配置。'
                )
                return False
            func(instance)
            return True
        except OSError as e:
            msg = str(e)
            # OSError: [WinError 740] 请求的操作需要提升。
            if 'WinError 740' in msg:
                logger.error('[设备-Windows] 启动/停止MuMu需要以管理员身份运行')
        except EmulatorUnknown as e:
            logger.error(e)
        except Exception as e:
            logger.exception(e)

        logger.error(f'[设备-Windows] 模拟器函数 {func.__name__}() 失败')
        return False

    def _mumu12_instances(self, exe):
        """查询 MuMu12 全部实例的信息。

        MuMuManager 的 info 子命令返回 JSON，字段包括 is_process_started、
        is_android_started、player_state、pid、headless_pid 等，用于按实例
        精确判断状态，而不是靠进程名猜测。

        Args:
            exe (str): MuMu 主程序路径。

        Returns:
            dict | None: {实例号: 信息}；命令不可用或输出无法解析时返回 None
                （旧版 MuMu 没有 info 子命令，调用方需回退旧逻辑）。
        """
        text = run_mumu_manager(exe, 'info -v all')
        start = text.find('{')
        if start < 0:
            return None
        try:
            data = json.loads(text[start:])
        except ValueError:
            logger.warning(f'[设备-Windows] 无法解析 MuMuManager info 输出: {text[:200]}')
            return None
        if not isinstance(data, dict):
            return None
        # 兼容 info -v <单个实例> 的返回形式（实例字段直接铺在顶层）
        if 'index' in data:
            return {str(data['index']): data}
        return data

    def _ensure_mumu12_instance_stopped(self, exe, index):
        """确保本实例的进程已退出，仍在运行时补发关闭命令。

        多开安全：MuMu 各实例共用主程序进程（见文件头多开安全约束），
        按进程名清理会误伤正在运行的其它实例。因此这里只针对本实例操作：
        查询实例状态，进程仍在时补发一次 shutdown_player；
        共享主程序一律不动。

        Args:
            exe (str): MuMu 主程序路径。
            index (int | None): 本实例的实例号。
        """
        if index is None:
            logger.warning(
                '[设备-Windows] MuMuPlayer12 实例号未知，跳过停止前确认，'
                '交由启动监视与下一轮重试兜底'
            )
            return
        info = self._mumu12_instances(exe)
        if info is None:
            # 查询不可用（旧版 MuMu / 命令失败）：交给 _mumu12_wait_stopped 处理
            return
        entry = info.get(str(index))
        if not (isinstance(entry, dict) and entry.get('is_process_started')):
            return
        logger.warning(
            f'[设备-Windows] MuMuPlayer12 实例 {index} 进程仍在运行，补发关闭命令'
        )
        self.execute(
            f'"{Emulator.single_to_console(exe)}" api -v {index} shutdown_player',
            wait=True,
            timeout=30
        )

    def _mumu12_wait_stopped(self, exe, index):
        """等待 MuMu12 实例真正关闭后再返回。

        MuMuManager 的 shutdown 是异步的：命令 1 秒内就返回，实例还要 2~3 秒
        才真正停止。若在此期间发出 launch，启动请求会被吞掉——命令报告成功
        （player launch: result=0），实例却永远起不来。
        实机复现：shutdown 后等 2 秒启动必失败，等确认关闭后启动 17 秒成功。

        Args:
            exe (str): MuMu 主程序路径。
            index (int | None): 本实例的实例号。

        Returns:
            bool: True 表示已确认关闭（或无法查询状态，调用方按旧逻辑继续）；
                False 表示等待超时，实例仍未关闭。
        """
        if index is None:
            return True
        deadline = time.monotonic() + MUMU12_STOP_WAIT_TIMEOUT
        while 1:
            info = self._mumu12_instances(exe)
            if info is None:
                # 查询不可用（旧版 MuMu / 命令失败）：保持旧行为，短暂等待即可
                time.sleep(MUMU12_STATE_POLL_INTERVAL)
                return True
            entry = info.get(str(index))
            if not isinstance(entry, dict) or not entry.get('is_process_started'):
                logger.info(f'[设备-Windows] MuMuPlayer12 实例 {index} 已确认关闭')
                return True
            if time.monotonic() >= deadline:
                logger.warning(
                    f'[设备-Windows] MuMuPlayer12 实例 {index} 在 '
                    f'{MUMU12_STOP_WAIT_TIMEOUT} 秒内仍未关闭，继续启动流程'
                )
                return False
            time.sleep(MUMU12_STATE_POLL_INTERVAL)

    def emulator_start_watch(self, timeout=None):
        """
        监控模拟器启动过程，等待启动完成。

        Args:
            timeout (int | float | None): 本次监视的等待秒数。
                默认取 EMULATOR_START_WATCH_TIMEOUTS 的首项（首次尝试）。

        Returns:
            bool: True 表示启动完成，False 表示超时
        """
        if timeout is None:
            timeout = EMULATOR_START_WATCH_TIMEOUTS[0]
        logger.hr('模拟器启动', level=2)
        logger.info(f'[设备-Windows] 模拟器启动监视开始，最长等待 {timeout} 秒')
        current_window = get_focused_window()
        serial = self.emulator_instance.serial
        logger.info(f'[设备-Windows] 当前窗口: {current_window}')

        def adb_connect():
            m = self.adb_client.connect(self.serial)
            if 'connected' in m:
                # Connected to 127.0.0.1:59865
                # Already connected to 127.0.0.1:59865
                return False
            elif '(10061)' in m:
                # cannot connect to 127.0.0.1:55555:
                # No connection could be made because the target machine actively refused it. (10061)
                return False
            else:
                return True

        @run_once
        def show_online(m):
            logger.info(f'[设备-Windows] 模拟器在线: {m}')

        @run_once
        def show_ping(m):
            logger.info(f'[设备-Windows] 命令ping: {m}')

        @run_once
        def show_package(m):
            logger.info(f'[设备-Windows] 找到碧蓝航线应用包: {m}')

        interval = Timer(0.5).start()
        timeout_timer = Timer(timeout).start()
        progress = Timer(EMULATOR_START_PROGRESS_INTERVAL).start()
        dialog_check = Timer(EMULATOR_START_DIALOG_CHECK_INTERVAL).start()
        new_window = 0
        while 1:
            interval.wait()
            interval.reset()
            if timeout_timer.reached():
                logger.warning(
                    f'[设备-Windows] 模拟器启动超时'
                    f'（已等待 {round(timeout_timer.current_time())} 秒）'
                )
                return False

            # 定期打印进度：启动过程可能静默数分钟，需要让日志体现
            # "确实还在等"，否则看起来像卡死
            if progress.reached_and_reset():
                logger.info(
                    f'[设备-Windows] 等待模拟器启动中，'
                    f'已等待 {round(timeout_timer.current_time())}/{timeout} 秒'
                )

            # MuMu 权限冲突等错误对话框检测
            # 检测到错误对话框时立即终止等待，返回 False 触发重试，
            # 不必白白等满整个监视超时
            if dialog_check.reached_and_reset() and check_mumu_error_dialog():
                logger.warning('[设备-Windows] 检测到MuMu错误对话框，中止启动监视')
                return False

            try:
                # 检查模拟器窗口是否弹出
                if current_window != 0 and new_window == 0:
                    new_window = get_focused_window()
                    if current_window != new_window:
                        logger.info(f'[设备-Windows] 新窗口出现: {new_window}，焦点返回')
                        set_focus_window(current_window)
                    else:
                        new_window = 0

                # 检查设备连接
                devices = self.list_device().select(serial=serial)
                if devices:
                    device = devices.first_or_none()
                    if device.status == 'device':
                        # 模拟器已上线
                        pass
                    if device.status == 'offline':
                        self.adb_client.disconnect(serial)
                        adb_connect()
                        continue
                else:
                    # 尝试连接
                    adb_connect()
                    continue
                show_online(devices.first_or_none())

                # 检查命令可用性
                try:
                    pong = self.adb_shell(['echo', 'pong'])
                except Exception as e:
                    logger.info(e)
                    continue
                show_ping(pong)

                # 检查碧蓝航线包名
                packages = self.list_known_packages(show_log=False)
                if len(packages):
                    pass
                else:
                    continue
                show_package(packages)

                # 所有检查通过
                break
            except (ConnectionResetError, ConnectionAbortedError) as e:
                # [WinError 10054] 远程主机强迫关闭了一个现有的连接。
                # 模拟器启动期间经常出现
                logger.info(e)
                continue
            except Exception as e:
                logger.exception(e)
                continue

        if new_window != 0 and new_window != current_window:
            logger.info(f'[设备-Windows] 最小化新窗口: {new_window}')
            minimize_window(new_window)
        if current_window:
            logger.info(f'[设备-Windows] 取消闪烁当前窗口: {current_window}')
            flash_window(current_window, flash=False)
        if new_window:
            logger.info(f'[设备-Windows] 闪烁新窗口: {new_window}')
            flash_window(new_window, flash=True)
        logger.info('[设备-Windows] 模拟器启动完成')
        return True

    @emulator_op_exclusive('启动模拟器')
    def emulator_start(self):
        """
        启动模拟器，最多重试 3 次。
        针对 MuMu12 等模拟器添加实例查找失败后的等待重试机制，
        以及权限冲突时的强制进程清理。

        整个重试过程持有模拟器启停锁（见 emulator_op_exclusive）：
        若已有启停操作在跑，直接抛 EmulatorOpBusy，不做任何动作——
        这正是避免"刚启动就被另一个线程关掉"的关键。
        """
        logger.hr('模拟器启动', level=1)

        # 检查是否为 MuMuPlayer12，添加实例查找失败的处理逻辑
        emulator_type = getattr(self.config, 'EmulatorInfo_Emulator', '')
        # 历史缺陷：这里曾写成 hasattr(self, '_emulator_instance')，但实例缓存
        # 属性的实际名字是 emulator_instance（无下划线，定义在 PlatformBase），
        # hasattr 永远返回 False，后半段判断从未生效，只因前半段读配置才碰巧可用。
        # 注意：不能改成直接读取 self.emulator_instance —— 它是 cached_property，
        # 首次访问会触发实例解析（注册表/进程扫描 + pydantic 构造），在离线对象
        # （如单测的 Mock 配置）上会抛异常，且启动前也不该提前解析实例。
        # 改为只读已解析的缓存值：有缓存用缓存，没缓存就只信配置。
        is_mumu12 = emulator_type == 'MuMuPlayer12'
        if not is_mumu12:
            instance = self.__dict__.get('emulator_instance', None)
            is_mumu12 = instance is not None and instance.type == 'MuMuPlayer12'

        for attempt in range(3):
            # 先停止（MuMu12 已使用同步执行确保关闭完成）
            if not self._emulator_function_wrapper(self._emulator_stop):
                return False

            # MuMu12: 清场并确认实例真的停下来了再启动
            if is_mumu12:
                index = self.emulator_instance.MuMuPlayer12_id
                exe = self.emulator_instance.emulator.path
                # 本实例进程仍在时补发关闭命令（按实例操作，不碰共享主程序）
                self._ensure_mumu12_instance_stopped(exe, index)
                # shutdown 是异步的，必须确认实例真的停了再启动，
                # 否则启动请求会被吞掉（命令报成功、实例起不来）
                self._mumu12_wait_stopped(exe, index)

            # 再启动
            if self._emulator_function_wrapper(self._emulator_start):
                # 成功
                watch_timeout = EMULATOR_START_WATCH_TIMEOUTS[
                    min(attempt, len(EMULATOR_START_WATCH_TIMEOUTS) - 1)
                ]
                if self.emulator_start_watch(timeout=watch_timeout):
                    return True
                logger.warning(
                    f'[设备-Windows] 模拟器启动监视失败（第 {attempt + 1}/3 次，'
                    f'已等待 {watch_timeout} 秒），重试中'
                )
                if self._emulator_function_wrapper(self._emulator_stop):
                    continue
                else:
                    return False
            else:
                # 启动失败，停止后重试
                if self._emulator_function_wrapper(self._emulator_stop):
                    continue
                else:
                    return False

        logger.error('[设备-Windows] 尝试3次启动模拟器失败，已停止')
        return False

    @emulator_op_exclusive('停止模拟器')
    def emulator_stop(self):
        """停止模拟器，最多重试 3 次。

        同样持有模拟器启停锁：不做"别人正在启动、我顺手关掉"的事。
        """
        logger.hr('模拟器停止', level=1)
        for _ in range(3):
            # 停止
            if self._emulator_function_wrapper(self._emulator_stop):
                # 成功
                return True
            else:
                # 停止失败，启动后重试
                if self._emulator_function_wrapper(self._emulator_start):
                    continue
                else:
                    return False

        logger.error('[设备-Windows] 尝试3次停止模拟器失败，已停止')
        return False


if __name__ == '__main__':
    self = PlatformWindows('alas')
    d = self.emulator_instance
    print(d)
