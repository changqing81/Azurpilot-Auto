"""macOS 平台模拟器控制。继承 PlatformBase 和 EmulatorManagerMac，
实现 macOS 上模拟器的启动、停止和进程管理。"""

from __future__ import annotations
import os
import re
import subprocess
import time

import psutil

from module.base.decorator import run_once
from module.base.timer import Timer
from module.device.platform.platform_base import PlatformBase
from module.device.platform.emulator_mac import (
    EmulatorMac,
    EmulatorInstanceMac,
    EmulatorManagerMac,
)
from module.logger import logger


class PlatformMac(PlatformBase, EmulatorManagerMac):
    """
    macOS 平台的模拟器控制接口。
    支持 BlueStacks Air 和 MuMu Pro。
    """

    @classmethod
    def execute(cls, command, wait=True):
        """
        执行外部命令。

        Args:
            command (str): 要执行的命令
            wait (bool): 是否等待命令完成

        Returns:
            subprocess.CompletedProcess 或 subprocess.Popen: 命令执行结果
        """
        # 在 Mac 上使用 shell=True 执行复杂命令
        logger.info(f'[设备-模拟器Mac] 执行: {command}')
        if wait:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True
            )
            return result
        else:
            return subprocess.Popen(command, shell=True)

    @classmethod
    def _is_same_exe(cls, proc, expect_exe: str) -> bool:
        """判断进程的可执行文件是否与期望路径一致。

        macOS 的默认文件系统大小写不敏感，而配置里记录的路径大小写可能与实际
        进程不同，因此统一用 os.path.normcase(os.path.abspath(...)) 归一化后
        再比对。

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
        终止进程名匹配给定正则表达式的进程，可选按实例维度二次校验。

        多开安全背景：macOS 上蓝叠的多开实例共用同一个应用包，只按进程名
        （BlueStacks / HD-Player）杀会连带杀掉其它实例。为此提供两道可选的
        实例校验闸门，只有**全部校验通过**的进程才会被终止：

        - expect_exe：目标可执行文件的绝对路径，用 proc.exe() 取真实路径后
          归一化比对。
        - expect_instance：实例标识（如 --instance Pie64 里的 Pie64），要求进程
          命令行里确实出现该标识。

        两个参数同时传入时是 AND 关系；都不传时保持旧行为（仅按进程名匹配），
        向后兼容既有调用点。

        注意：匹配范围仍然只有**进程名**，不扩到命令行。原因是本函数的旧调用点
        会传入 instance.name 这类通用词（如 'MuMu'），一旦改成同时匹配命令行，
        会命中大量无关进程，反而制造新的误杀。需要按命令行定位的场景请显式传
        expect_instance 做二次校验。

        Args:
            regex (str): 匹配进程名的正则表达式。
            expect_exe (str | None): 期望的进程可执行文件绝对路径；
                None 表示不做 exe 路径校验。
            expect_instance (str | None): 期望出现在命令行中的实例标识；
                None 表示不做实例标识校验。

        Returns:
            int: 已终止的进程数量
        """
        count = 0
        for proc in psutil.process_iter():
            try:
                name = proc.name()
                if not re.search(regex, name, re.IGNORECASE):
                    continue
                # 只有传了 expect_instance 时才需要读命令行（读命令行比读进程名慢，
                # 且权限受限时可能拿不到，因此按需读取）
                if expect_instance:
                    try:
                        cmdline = ' '.join(proc.cmdline())
                    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                        cmdline = ''
                else:
                    cmdline = ''
                # 闸门 1：exe 绝对路径校验（取不到真实路径时按不匹配处理）
                if expect_exe and not cls._is_same_exe(proc, expect_exe):
                    logger.info(
                        f'[设备-模拟器Mac] 跳过 exe 路径不匹配的进程，避免误杀: {name}'
                    )
                    continue
                # 闸门 2：实例标识校验（共享进程的命令行不含实例名，会被这道拦下）
                if expect_instance and expect_instance not in cmdline:
                    logger.info(
                        f'[设备-模拟器Mac] 跳过实例标识不匹配的进程，避免误杀: {name}'
                    )
                    continue
                logger.info(f'[设备-模拟器Mac] 终止模拟器进程: {name}')
                proc.kill()
                count += 1
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        return count

    @classmethod
    def renice_process_by_regex(cls, regex: str, priority: int = -20) -> int:
        """
        修改匹配正则表达式的进程优先级。

        Args:
            regex: 匹配进程名称的正则表达式
            priority: Nice 值（-20 最高优先级，19 最低优先级）

        Returns:
            int: 已修改优先级的进程数量
        """
        count = 0
        for proc in psutil.process_iter():
            try:
                name = proc.name()
                if re.search(regex, name, re.IGNORECASE):
                    pid = proc.pid
                    # 使用 sudo renice 设置优先级（需要管理员密码）
                    result = subprocess.run(
                        f'sudo -n renice -n {priority} -p {pid}',
                        shell=True,
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        logger.info(f'[设备-模拟器Mac] 已调整进程优先级 {name} (PID: {pid}) 到优先级 {priority}')
                        count += 1
                    else:
                        logger.warning(f'[设备-模拟器Mac] 调整优先级失败 {name}: {result.stderr.strip()}')
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        return count

    def boost_emulator_priority(self, instance: EmulatorInstanceMac):
        """
        启动后提升模拟器进程优先级。

        Args:
            instance: 要提升优先级的模拟器实例
        """
        if instance == EmulatorMac.BlueStacksAir:
            time.sleep(3)
            self.renice_process_by_regex(r'BlueStacks', -20)

        elif instance == EmulatorMac.MuMuPro:
            time.sleep(3)
            self.renice_process_by_regex(r'MuMuEmulator|MuMuPlayer', -20)

        else:
            if instance.name:
                time.sleep(3)
                self.renice_process_by_regex(instance.name, -20)

    def boost_running_emulator_priority(self):
        """
        提升当前正在运行的模拟器的进程优先级。
        在 Alas 启动且检测到已有模拟器运行时调用。
        """
        # 尝试提升 MuMu 进程优先级
        count = self.renice_process_by_regex(r'MuMuEmulator|MuMuPlayer', -20)
        if count > 0:
            logger.info(f'[设备-模拟器Mac] 已提升优先级 {count} 个 MuMu 进程')
            return

        # 尝试提升 BlueStacks 进程优先级
        count = self.renice_process_by_regex(r'BlueStacks', -20)
        if count > 0:
            logger.info(f'[设备-模拟器Mac] 已提升优先级 {count} 个 BlueStacks 进程')
            return

        logger.info('[设备-模拟器Mac] 未找到运行中的模拟器进程可提升')

    def _emulator_start(self, instance: EmulatorInstanceMac):
        """
        启动模拟器（不含错误处理）。

        Args:
            instance: 模拟器实例
        """
        exe: str = instance.emulator.path

        if instance == EmulatorMac.BlueStacksAir:
            # 使用 open 命令启动 BlueStacks Air 应用
            # 先查找应用包
            app_path = EmulatorMac.find_app_bundle('BlueStacks')
            if app_path:
                self.execute(f'open -a "{app_path}"', wait=False)
            else:
                raise Exception('BlueStacks Air app not found')

        elif instance == EmulatorMac.MuMuPro:
            # macOS 上的 MuMu 正确启动流程:
            # 1. open -a MuMuPlayer.app - 启动主程序
            # 2. mumutool open <index> - 启动模拟器实例
            app_path = EmulatorMac.find_app_bundle('MuMu')
            if app_path:
                # 步骤 1: 启动 MuMuPlayer 主程序
                self.execute(f'open -a "{app_path}"', wait=False)
                time.sleep(3)
                # 步骤 2: 使用 mumutool 启动指定的模拟器实例
                mumu_bin_path = os.path.join(app_path, 'Contents/MacOS/mumutool')
                if os.path.exists(mumu_bin_path):
                    # 使用 instance.index 打开指定实例
                    instance_index = getattr(instance, 'index', 0)
                    self.execute(f'"{mumu_bin_path}" open {instance_index}', wait=False)
                else:
                    logger.warning(f'[设备-模拟器Mac] mumutool未找到于 {mumu_bin_path}，使用回退方式')
                    # 回退: 尝试 MuMuEmulator.app 结构
                    mumu_emulator_app = os.path.join(app_path, 'Contents/MacOS/MuMuEmulator.app')
                    if os.path.exists(mumu_emulator_app):
                        self.execute(f'open "{mumu_emulator_app}"', wait=False)
            else:
                raise Exception('MuMu Pro app not found')

        elif instance.type == 'SSH':
            self.run_remote_ssh_command(getattr(self.config, 'EmulatorInfo_RemoteStartCommand', ''))

        else:
            # 通用回退: 尝试通过路径打开
            if os.path.exists(exe):
                self.execute(f'open "{exe}"', wait=False)
            else:
                raise Exception(f'Cannot start unknown emulator: {instance}')

    @classmethod
    def _bluestacks_app_bundle(cls, instance: EmulatorInstanceMac) -> str:
        """从实例路径推导蓝叠的 .app 应用包路径。

        实例路径形如 ``/Applications/BlueStacks.app/Contents/MacOS/BlueStacks``，
        这里截到 ``.app`` 为止，用于判断某个进程是否属于同一个蓝叠应用。

        Args:
            instance (EmulatorInstanceMac): 蓝叠实例。

        Returns:
            str: 归一化后的 .app 路径；推导失败返回空字符串。
        """
        path = getattr(instance, 'path', '') or ''
        index = path.find('.app/')
        if index < 0:
            if path.endswith('.app'):
                return os.path.normcase(os.path.abspath(path))
            return ''
        return os.path.normcase(os.path.abspath(path[:index + len('.app')]))

    @classmethod
    def _iter_bluestacks_processes(cls, app_bundle: str) -> list:
        """列出属于指定蓝叠应用包的所有进程。

        按可执行文件路径是否位于 ``<app>.app`` 之内来判断归属，比按进程名
        （BlueStacks / HD-Player）匹配精确得多：同名进程可能来自另一份安装，
        也可能来自其它应用。

        Args:
            app_bundle (str): 归一化后的 .app 路径。

        Returns:
            list[psutil.Process]: 归属该应用包的进程列表。
        """
        processes = []
        if not app_bundle:
            return processes
        prefix = app_bundle + os.sep
        for proc in psutil.process_iter():
            try:
                exe = proc.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
                continue
            if not exe:
                continue
            if os.path.normcase(os.path.abspath(exe)).startswith(prefix):
                processes.append(proc)
        return processes

    @classmethod
    def _process_instance_token(cls, proc) -> str:
        """从进程命令行里解析蓝叠的实例标识（``--instance <name>``）。

        Args:
            proc (psutil.Process): 目标进程。

        Returns:
            str: 实例名；命令行里没有该参数或读取失败时返回空字符串。
        """
        try:
            cmdline = ' '.join(proc.cmdline())
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
            return ''
        res = re.search(r'--instance[= ]"?([^"\s]+)"?', cmdline)
        return res.group(1) if res else ''

    def _stop_bluestacks_air_instance(self, instance: EmulatorInstanceMac) -> int:
        """按实例维度停止 macOS 蓝叠（BlueStacks Air），杜绝多开误杀。

        旧实现直接 ``kill_process_by_regex(r'BlueStacks')``，只要进程名里带
        BlueStacks 就杀——多开时会把**所有**蓝叠实例一并杀掉（已确认的必然误杀）。

        macOS 上蓝叠没有像 Windows 那样的 ``bsconsole quit --name`` 实例级命令行
        （``emulator_mac.py`` 只提供应用包查找与 ``open -a`` 启动），因此这里按
        「进程归属 + 实例标识」两个维度判定，优先级是**不误杀 > 彻底杀干净**：

        1. 只考虑可执行文件位于同一个 ``.app`` 内的进程；
        2. 从命令行解析 ``--instance <name>``，据此把进程归到具体实例；
        3. 只有「本实例名明确匹配」或「全局只有一个实例在跑」时才动手；
        4. 判定为多开且无法确定本实例时，**跳过并告警**，绝不按名字一把杀。

        Args:
            instance (EmulatorInstanceMac): 蓝叠实例。

        Returns:
            int: 已终止的进程数量。
        """
        app_bundle = self._bluestacks_app_bundle(instance)
        processes = self._iter_bluestacks_processes(app_bundle)
        if not processes:
            logger.info('[设备-模拟器Mac] 未发现蓝叠进程，无需停止')
            return 0

        # 按 --instance 标识分组；解析不出标识的进程单独放一组（token 为空）
        groups = {}
        for proc in processes:
            groups.setdefault(self._process_instance_token(proc), []).append(proc)
        tokens = sorted(token for token in groups if token)

        name = (instance.name or '').strip()
        # emulator_mac 枚举蓝叠实例时用的是固定占位名，不代表真实实例名
        placeholder_names = {'', 'BlueStacksAir', 'BlueStacksMIM', 'BlueStacks'}
        target_token = ''
        if name not in placeholder_names and name in tokens:
            target_token = name
        elif len(tokens) == 1:
            # 只有一个实例在运行，不存在"别人的实例"，杀掉它是安全的
            target_token = tokens[0]
        elif not tokens:
            # 没有任何进程带实例标识：可能是单实例模式，也可能是命令行格式不同
            if len(processes) == 1:
                target_token = ''
            else:
                logger.warning(
                    f'[设备-模拟器Mac] 发现 {len(processes)} 个蓝叠进程但都解析不出实例标识，'
                    f'无法区分多开实例，跳过停止以避免误杀'
                )
                return 0
        else:
            # 多开但 --instance 里没有本实例名：退回按命令行实例标识匹配，
            # 仍然要求唯一命中，命中不了就放弃（不误杀 > 彻底杀干净）
            logger.warning(
                f'[设备-模拟器Mac] 检测到多个蓝叠实例在运行（{tokens}），'
                f'本实例名 {name!r} 不在其中，尝试按命令行实例标识匹配'
            )
            return self._stop_bluestacks_air_by_cmdline(instance)

        killed = 0
        for proc in groups.get(target_token, []):
            try:
                proc.kill()
                killed += 1
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError) as e:
                logger.warning(f'[设备-模拟器Mac] 终止蓝叠进程失败: {e}')
        if killed:
            logger.info(
                f'[设备-模拟器Mac] 已终止蓝叠实例 '
                f'{target_token or "(唯一实例)"} 的 {killed} 个进程'
            )

        # 仅在确认「全局最多一个实例」时才用 osascript 兜底：quit app 会退出整个应用，
        # 多开时使用等于杀掉所有实例
        single_instance = len(tokens) <= 1
        if killed == 0 and single_instance:
            self.execute('osascript -e \'tell application "BlueStacks" to quit\'', wait=True)
        elif not single_instance:
            logger.info(
                '[设备-模拟器Mac] 多开环境，跳过 osascript quit（该命令会退出全部实例）'
            )
        return killed

    @classmethod
    def _instance_name_candidates(cls, name: str) -> list:
        """把实例名拆成可用于命令行匹配的候选标识（按可信度从高到低）。

        多开场景下同一个实例在不同地方的名字并不一致，例如 ``--instance``
        参数用 ``Pie64``，而 ADB 端口配置里的键是 ``bst.instance.Pie64``，
        用户配置里也可能写成 ``Pie64_nxt``。这里按分隔符拆出各个片段，
        过滤掉太短的（容易误匹配）与通用词，剩下的用于命令行匹配。

        Args:
            name (str): 实例名。

        Returns:
            list[str]: 候选标识列表；无法拆分时返回空列表。
        """
        name = (name or '').strip()
        if not name:
            return []
        # 通用词不能作为实例标识：它们在所有实例的命令行里都会出现
        generic = {
            'bluestacks', 'bluestacksair', 'bluestacksmim', 'hd-player', 'player',
            'instance', 'emulator', 'android', 'macos', 'contents', 'applications',
        }
        candidates = []
        # 先放完整名称：命令行里直接出现完整实例名是最可信的匹配
        if len(name) >= 3 and name.lower() not in generic:
            candidates.append(name)
        for token in re.split(r'[^0-9A-Za-z]+', name):
            if len(token) < 3:
                continue
            if token.lower() in generic:
                continue
            if token not in candidates:
                candidates.append(token)
        # 用户配置里常写成 Pie64_nxt / Pie64 这类带后缀的形式，补上去掉后缀的
        # 主干名（BlueStacks 命令行里用的是不带 _nxt 的实例名）
        for candidate in list(candidates):
            stem = re.sub(r'_(nxt|cn|global)$', '', candidate, flags=re.IGNORECASE)
            if stem != candidate and len(stem) >= 3 and stem.lower() not in generic:
                if stem not in candidates:
                    candidates.append(stem)
        return candidates

    def _stop_bluestacks_air_by_cmdline(self, instance: EmulatorInstanceMac) -> int:
        """按命令行里的实例标识停止蓝叠实例（拿不到 --instance 时的回退手段）。

        当 ``_stop_bluestacks_air_instance`` 因为「多开且本实例名不在 --instance
        列表里」而放弃时，这里再试一次：用实例名派生的候选标识去匹配命令行。
        为防止标识过于宽泛而误杀，额外要求命中范围**不是全部进程**——若一个候选
        把应用内所有进程都命中了，说明它毫无区分度，一律放弃。

        Args:
            instance (EmulatorInstanceMac): 蓝叠实例。

        Returns:
            int: 已终止的进程数量；无法唯一确定时返回 0。
        """
        candidates = self._instance_name_candidates(getattr(instance, 'name', ''))
        if not candidates:
            logger.warning(
                f'[设备-模拟器Mac] 实例名 {getattr(instance, "name", "")!r} 无法派生出'
                f'可用的命令行标识，跳过停止以避免误杀'
            )
            return 0

        app_bundle = self._bluestacks_app_bundle(instance)
        processes = self._iter_bluestacks_processes(app_bundle)
        if not processes:
            return 0

        matched = []
        for proc in processes:
            try:
                cmdline = ' '.join(proc.cmdline())
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
                continue
            # 用最可信的候选逐个尝试，命中即止，避免同一进程被重复计入
            for candidate in candidates:
                if candidate in cmdline:
                    matched.append(proc)
                    break

        if not matched:
            logger.warning(
                f'[设备-模拟器Mac] 蓝叠实例 {getattr(instance, "name", "")!r} '
                f'在命令行里没有命中任何进程，跳过停止以避免误杀'
            )
            return 0
        if len(matched) >= len(processes):
            # 候选标识把应用内所有进程都命中了，说明它没有区分度（例如实例名就是
            # 应用名），此时杀掉等于杀掉整个应用，多开时会误伤其它实例
            logger.warning(
                f'[设备-模拟器Mac] 实例标识命中了全部 {len(processes)} 个蓝叠进程，'
                f'缺乏区分度，跳过停止以避免误杀'
            )
            return 0

        killed = 0
        for proc in matched:
            try:
                proc.kill()
                killed += 1
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError) as e:
                logger.warning(f'[设备-模拟器Mac] 终止蓝叠进程失败: {e}')
        if killed:
            logger.info(
                f'[设备-模拟器Mac] 按命令行标识终止了蓝叠实例 '
                f'{getattr(instance, "name", "")} 的 {killed} 个进程'
            )
        return killed

    def _emulator_stop(self, instance: EmulatorInstanceMac):
        """
        停止模拟器（不含错误处理）。

        Args:
            instance: 模拟器实例
        """
        if instance == EmulatorMac.BlueStacksAir:
            # 按实例维度停止：多开环境下绝不按进程名一把杀
            self._stop_bluestacks_air_instance(instance)

        elif instance == EmulatorMac.MuMuPro:
            # 使用 mumutool 关闭指定实例
            app_path = EmulatorMac.find_app_bundle('MuMu')
            if app_path:
                mumu_bin_path = os.path.join(app_path, 'Contents/MacOS/mumutool')
                if os.path.exists(mumu_bin_path):
                    # 使用 instance.index 关闭指定实例
                    instance_index = getattr(instance, 'index', 0)
                    self.execute(f'"{mumu_bin_path}" close {instance_index}', wait=True)
                    time.sleep(2)

            # 注意: 不使用 osascript 退出，因为这会关闭所有实例
            # 而是确保指定的进程已停止
            # 仅终止特定的 MuMu 模拟器进程（如果仍在运行）
            # 实例名称可用于定位特定实例的进程

        elif instance.type == 'SSH':
            self.run_remote_ssh_command(getattr(self.config, 'EmulatorInfo_RemoteStopCommand', ''))

        else:
            # 通用回退: 按实例名称终止进程
            if instance.name:
                self.kill_process_by_regex(instance.name)

    def _emulator_function_wrapper(self, func):
        """
        模拟器启停操作的统一包装器，处理异常。

        这里是所有启停（含所有 kill 路径）的唯一入口，因此在此处加一道前置
        闸门：识别不到模拟器实例时**直接放弃本次操作**。

        背景：`emulator_instance` 依赖配置与运行时枚举结果的匹配，多开环境下
        配置不完整时可能返回 None。旧实现会把 None 往下传，各分支要么抛
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
                    '[设备-模拟器Mac] 未能识别模拟器实例（EmulatorInfo 的类型/名称/路径'
                    '与运行时枚举结果不匹配），放弃本次启停操作以避免误杀其它实例。'
                    '请检查「模拟器信息」配置。'
                )
                return False
            func(instance)
            return True
        except Exception as e:
            logger.exception(e)

        logger.error(f'[设备-模拟器Mac] 模拟器函数 {func.__name__}() 执行失败')
        return False

    def emulator_start_watch(self):
        """
        监控模拟器启动过程，等待启动完成。

        Returns:
            bool: True 表示启动完成，False 表示超时
        """
        logger.hr('[设备-模拟器Mac] 模拟器启动', level=2)
        serial = self.emulator_instance.serial

        @run_once
        def show_online(m):
            logger.info(f'[设备-模拟器Mac] 模拟器在线: {m}')

        @run_once
        def show_ping(m):
            logger.info(f'[设备-模拟器Mac] 命令ping: {m}')

        @run_once
        def show_package(m):
            logger.info(f'[设备-模拟器Mac] 找到碧蓝航线应用包: {m}')

        interval = Timer(0.5).start()
        timeout = Timer(180).start()

        while 1:
            interval.wait()
            interval.reset()
            if timeout.reached():
                logger.warning('[设备-模拟器Mac] 模拟器启动超时')
                return False

            try:
                # 检查设备连接
                devices = self.list_device().select(serial=serial)
                if devices:
                    device = devices.first_or_none()
                    if device.status == 'device':
                        pass
                    if device.status == 'offline':
                        self.adb_client.disconnect(serial)
                        self.adb_client.connect(serial)
                        continue
                else:
                    # 尝试连接
                    self.adb_client.connect(serial)
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
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
                logger.info(e)
                continue
            except Exception as e:
                logger.exception(e)
                continue

        logger.info('[设备-模拟器Mac] 模拟器启动完成')
        return True

    def emulator_start(self):
        """启动模拟器，最多重试 3 次。"""
        logger.hr('[设备-模拟器Mac] 模拟器启动', level=1)
        for _ in range(3):
            # 先停止
            if not self._emulator_function_wrapper(self._emulator_stop):
                return False
            # 再启动
            if self._emulator_function_wrapper(self._emulator_start):
                # 成功
                # 提升模拟器进程优先级
                self.boost_emulator_priority(self.emulator_instance)
                if self.emulator_start_watch():
                    return True
                logger.warning('[设备-模拟器Mac] 模拟器启动监视失败，重试中')
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

        logger.error('[设备-模拟器Mac] 尝试3次启动模拟器失败，已停止')
        return False

    def emulator_stop(self):
        """停止模拟器，最多重试 3 次。"""
        logger.hr('[设备-模拟器Mac] 模拟器停止', level=1)
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

        logger.error('[设备-模拟器Mac] 尝试3次停止模拟器失败，已停止')
        return False


if __name__ == '__main__':
    from module.config import AzurLaneConfig
    config = AzurLaneConfig(config_name='alas')
    self = PlatformMac(config)
    d = self.emulator_instance
    print(d)
