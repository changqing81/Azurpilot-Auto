"""平台层多开安全测试：杀进程必须带实例校验，绝不误杀同系列其它实例。

回归自两处**已确认的必然误杀**（2026-10 审查）：

1. MuMu X 的停止分支把 `Muvm6Headless.exe` / `Muvm6SVC.exe` 和播放器一起
   按进程名正则杀掉。这两个是 Hypervisor 层的共享进程，命令行里**不带实例名**，
   多开时等于杀掉所有 MuMu X 实例。
2. macOS 蓝叠直接 `kill_process_by_regex(r'BlueStacks')`，按进程名一把杀，
   同样会杀掉全部实例。

本测试锁定修复后的三条不变量：

- `kill_process_by_regex` 的 `expect_exe` / `expect_instance` 校验生效，
  且两者同时传入时是 AND 关系；都不传时保持旧行为（向后兼容）。
- MuMu X 的共享 Hypervisor 进程只在「能证明它只服务于本实例」时才终止，
  证明不了就跳过并告警（优先级：不误杀 > 彻底杀干净）。
- 识别不到模拟器实例时，启停入口直接放弃，绝不"猜一个杀掉"。

全部用假进程对象，不碰真实系统进程，不依赖真实模拟器。
"""

import os
import unittest
from unittest.mock import patch

from module.device.env import IS_WINDOWS

if IS_WINDOWS:
    # platform_windows 顶层依赖 winreg，非 Windows 导入即失败
    from module.device.platform import platform_windows
    from module.device.platform.platform_windows import PlatformWindows

WINDOWS_ONLY = unittest.skipUnless(IS_WINDOWS, 'platform_windows 依赖 winreg，仅 Windows 可测')


class FakeProc:
    """psutil.Process 的最小模拟实现，供多开杀进程逻辑测试使用。

    Attributes:
        pid (int): 进程号。
        exe_path (str): 可执行文件绝对路径。
        cmdline_args (list[str]): 命令行参数列表。
        parent_proc (FakeProc | None): 父进程。
        children_procs (list[FakeProc]): 子进程列表。
        killed (bool): 是否已被 kill()。
    """

    def __init__(self, pid, exe_path, cmdline_args, parent_proc=None):
        """构造假进程，并自动挂到父进程的 children 列表上。

        Args:
            pid (int): 进程号。
            exe_path (str): 可执行文件绝对路径。
            cmdline_args (list[str]): 命令行参数列表。
            parent_proc (FakeProc | None): 父进程，None 表示无父进程。
        """
        self.pid = pid
        self.exe_path = exe_path
        self.cmdline_args = list(cmdline_args)
        self.parent_proc = parent_proc
        self.children_procs = []
        self.killed = False
        if parent_proc is not None:
            parent_proc.children_procs.append(self)

    def exe(self):
        """返回可执行文件路径。"""
        return self.exe_path

    def name(self):
        """返回可执行文件名。"""
        return os.path.basename(self.exe_path)

    def cmdline(self):
        """返回命令行参数列表。"""
        return list(self.cmdline_args)

    def parent(self):
        """返回父进程。"""
        return self.parent_proc

    def children(self, recursive=True):
        """返回子进程列表。

        Args:
            recursive (bool): True 时递归返回全部后代，False 时只返回直接子进程。

        Returns:
            list[FakeProc]: 子进程列表。
        """
        result = list(self.children_procs)
        if recursive:
            for child in self.children_procs:
                result.extend(child.children(recursive=True))
        return result

    def kill(self):
        """标记为已终止。"""
        self.killed = True


def make_platform():
    """构造一个不连接设备、不碰真实模拟器的 PlatformWindows 实例。"""
    platform = PlatformWindows.__new__(PlatformWindows)
    platform.config = None
    return platform


@WINDOWS_ONLY
class TestKillProcessByRegexInstanceCheck(unittest.TestCase):
    """kill_process_by_regex 的实例校验闸门。"""

    def setUp(self):
        # 同一安装目录下跑着两个实例的播放器进程
        self.exe = r'E:\ProgramFiles\MuMu9\emulator\nemu9\EmulatorShell\NemuPlayer.exe'
        self.other_exe = r'E:\ProgramFiles\MuMu9Copy\emulator\nemu9\EmulatorShell\NemuPlayer.exe'
        self.proc_a = FakeProc(
            100, self.exe,
            [self.exe, '-m', 'nemu-12.0-x64-default', '-s', '0', '-l'],
        )
        self.proc_b = FakeProc(
            200, self.exe,
            [self.exe, '-m', 'nemu-12.0-x64-default2', '-s', '0', '-l'],
        )
        self.proc_other_dir = FakeProc(
            300, self.other_exe,
            [self.other_exe, '-m', 'nemu-12.0-x64-default', '-s', '0', '-l'],
        )

    def _kill(self, regex, **kwargs):
        """在假进程列表上执行 kill_process_by_regex。

        Args:
            regex (str): 命令行正则。
            **kwargs: 透传给 kill_process_by_regex 的校验参数。

        Returns:
            int: 已终止的进程数量。
        """
        procs = [self.proc_a, self.proc_b, self.proc_other_dir]
        with patch.object(platform_windows.psutil, 'process_iter', return_value=procs):
            return PlatformWindows.kill_process_by_regex(regex, **kwargs)

    def test_without_checks_keeps_legacy_behaviour(self):
        """不传校验参数时行为不变：正则命中即杀（向后兼容）。

        这里刻意用带实例名边界的正则——不带边界时 default 会连 default2 一起
        命中，那属于调用方的正则写法问题，不是本函数要负责的事。
        """
        killed = self._kill(r'NemuPlayer\.exe.*-m nemu-12\.0-x64-default(?:\s|$)')

        self.assertEqual(2, killed)
        self.assertTrue(self.proc_a.killed)
        self.assertTrue(self.proc_other_dir.killed)
        # 实例 B 的名字以 default2 结尾，不该被这条正则命中
        self.assertFalse(self.proc_b.killed)

    def test_expect_exe_skips_other_install_directory(self):
        """exe 路径不一致的进程不杀：同名程序来自另一份安装时也必须放过。"""
        killed = self._kill(
            r'NemuPlayer\.exe.*-m nemu-12\.0-x64-default(?:\s|$)',
            expect_exe=self.exe,
        )

        self.assertEqual(1, killed)
        self.assertTrue(self.proc_a.killed)
        self.assertFalse(self.proc_other_dir.killed)

    def test_expect_instance_skips_shared_process_without_instance_name(self):
        """命令行不含实例标识的进程不杀：共享进程正是靠这道闸门拦下的。"""
        shared = FakeProc(400, r'C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6SVC.exe',
                          [r'C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6SVC.exe', '--Embedding'])
        with patch.object(platform_windows.psutil, 'process_iter',
                          return_value=[self.proc_a, shared]):
            killed = PlatformWindows.kill_process_by_regex(
                r'NemuPlayer\.exe|Muvm6SVC\.exe',
                expect_instance='nemu-12.0-x64-default',
            )

        self.assertEqual(1, killed)
        self.assertTrue(self.proc_a.killed)
        self.assertFalse(shared.killed)

    def test_both_checks_are_anded(self):
        """两个校验同时传入时是 AND：任一项不满足都不杀。"""
        killed = self._kill(
            r'NemuPlayer\.exe.*-m nemu-12\.0-x64-default(?:\s|$)',
            expect_exe=self.exe,
            expect_instance='nemu-12.0-x64-default',
        )

        self.assertEqual(1, killed)
        self.assertTrue(self.proc_a.killed)
        self.assertFalse(self.proc_other_dir.killed)

    def test_unreadable_exe_path_is_treated_as_mismatch(self):
        """取不到进程真实路径时按不匹配处理——宁可少杀，不可误杀。"""

        class NoExeProc(FakeProc):
            """exe() 抛异常的假进程，模拟权限不足。"""

            def exe(self):
                """模拟 AccessDenied。"""
                raise platform_windows.psutil.AccessDenied(pid=self.pid)

        proc = NoExeProc(500, self.exe, [self.exe, '-m', 'nemu-12.0-x64-default'])
        with patch.object(platform_windows.psutil, 'process_iter', return_value=[proc]):
            killed = PlatformWindows.kill_process_by_regex(
                r'NemuPlayer\.exe', expect_exe=self.exe,
            )

        self.assertEqual(0, killed)
        self.assertFalse(proc.killed)


@WINDOWS_ONLY
class TestMuMuXInstanceStop(unittest.TestCase):
    """MuMu X 停止分支：共享 Hypervisor 进程只在确属本实例时才终止。"""

    PLAYER = r'E:\ProgramFiles\MuMu9\emulator\nemu9\EmulatorShell\NemuPlayer.exe'
    HYPERVISOR = r'C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6Headless.exe'
    SERVICE = r'C:\Program Files\Muvm6Vbox\Hypervisor\Muvm6SVC.exe'

    def make_instance(self, name):
        """构造一个 MuMu X 实例对象（只用到 name 与 emulator.path）。

        Args:
            name (str): 实例名。

        Returns:
            object: 具备 name / emulator.path 属性的简单对象。
        """

        class Instance:
            """仅提供停止逻辑所需的两个属性的假实例。"""

            def __init__(self, instance_name):
                """记录实例名。

                Args:
                    instance_name (str): 实例名。
                """
                self.name = instance_name
                self.emulator = type('Emu', (), {'path': TestMuMuXInstanceStop.PLAYER})()

        return Instance(name)

    def build_tree(self):
        """构造两个 MuMu X 实例的完整进程树。

        实例名故意选成互为前缀（default 与 default2），用来锁住
        "实例名必须精确匹配到边界"这条不变量。

        Returns:
            dict: 关键进程的字典，便于断言。
        """
        svc = FakeProc(10, self.SERVICE, [self.SERVICE, '--Embedding'])
        head_a = FakeProc(
            11, self.HYPERVISOR,
            [self.HYPERVISOR, '--comment', 'nemu-12.0-x64-default', '--startvm', 'uuid-a'],
            parent_proc=svc,
        )
        head_b = FakeProc(
            12, self.HYPERVISOR,
            [self.HYPERVISOR, '--comment', 'nemu-12.0-x64-default2', '--startvm', 'uuid-b'],
            parent_proc=svc,
        )
        player_a = FakeProc(
            13, self.PLAYER,
            [self.PLAYER, '-m', 'nemu-12.0-x64-default', '-s', '0', '-l'],
            parent_proc=head_a,
        )
        player_b = FakeProc(
            14, self.PLAYER,
            [self.PLAYER, '-m', 'nemu-12.0-x64-default2', '-s', '0', '-l'],
            parent_proc=head_b,
        )
        return {
            'svc': svc, 'head_a': head_a, 'head_b': head_b,
            'player_a': player_a, 'player_b': player_b,
            'all': [svc, head_a, head_b, player_a, player_b],
        }

    def _stop(self, instance, procs):
        """在给定假进程集合上执行 MuMu X 实例停止。

        Args:
            instance: 目标实例。
            procs (list[FakeProc]): 系统中的全部假进程。

        Returns:
            int: 已终止的进程数量。
        """
        platform = make_platform()
        with patch.object(platform_windows.psutil, 'process_iter', return_value=procs):
            return PlatformWindows._stop_mumu_x_instance(platform, instance)

    def test_does_not_kill_shared_hypervisor_of_other_instance(self):
        """核心回归：停止实例 A 时，实例 B 的播放器与 Hypervisor 必须毫发无损。

        旧实现会把 Muvm6Headless.exe / Muvm6SVC.exe 无条件杀掉，
        等于把所有 MuMu X 实例一起关掉。
        """
        tree = self.build_tree()
        self._stop(self.make_instance('nemu-12.0-x64-default'), tree['all'])

        self.assertTrue(tree['player_a'].killed, '本实例的播放器应当被终止')
        self.assertFalse(tree['player_b'].killed, '其它实例的播放器被误杀了')
        self.assertFalse(tree['head_b'].killed, '其它实例的 Hypervisor 被误杀了')
        self.assertTrue(tree['head_a'].killed, '本实例独占的 Hypervisor 应当被终止')

    def test_prefix_instance_name_is_not_treated_as_same_instance(self):
        """实例名互为前缀时不能串味：default 不能匹配到 default2。"""
        tree = self.build_tree()
        self._stop(self.make_instance('nemu-12.0-x64-default2'), tree['all'])

        self.assertTrue(tree['player_b'].killed)
        self.assertFalse(tree['player_a'].killed)
        self.assertFalse(tree['head_a'].killed)
        self.assertTrue(tree['head_b'].killed)

    def test_other_instance_headless_in_shared_subtree_blocks_kill(self):
        """共享 SVC 子树里挂着别的实例的 Headless 时，SVC 不能被当成独占进程杀掉。

        这条信号比播放器更隐蔽：SVC 自己不带 --comment，看起来"干净"，
        但它的子树里有别的实例的 Muvm6Headless，杀掉它同样会误伤。
        """
        svc = FakeProc(30, self.SERVICE, [self.SERVICE, '--Embedding'])
        head_a = FakeProc(
            31, self.HYPERVISOR,
            [self.HYPERVISOR, '--comment', 'nemu-12.0-x64-default', '--startvm', 'uuid-a'],
            parent_proc=svc,
        )
        head_b = FakeProc(
            32, self.HYPERVISOR,
            [self.HYPERVISOR, '--comment', 'nemu-12.0-x64-default2', '--startvm', 'uuid-b'],
            parent_proc=svc,
        )
        player_a = FakeProc(
            33, self.PLAYER,
            [self.PLAYER, '-m', 'nemu-12.0-x64-default', '-s', '0'],
            parent_proc=head_a,
        )

        self._stop(self.make_instance('nemu-12.0-x64-default'), [svc, head_a, head_b, player_a])

        self.assertFalse(svc.killed, '多实例共享的 SVC 被误杀了')
        self.assertTrue(head_a.killed, '本实例独占的 Headless 应当被终止')
        self.assertFalse(head_b.killed, '其它实例的 Headless 被误杀了')
        self.assertTrue(player_a.killed)

    def test_shared_hypervisor_without_comment_evidence_is_skipped(self):
        """没有 --comment 证据、子树里又有别的实例时，共享 Hypervisor 不杀。"""
        svc = FakeProc(20, self.SERVICE, [self.SERVICE, '--Embedding'])
        player_a = FakeProc(
            21, self.PLAYER,
            [self.PLAYER, '-m', 'nemu-12.0-x64-default', '-s', '0'],
            parent_proc=svc,
        )
        player_b = FakeProc(
            22, self.PLAYER,
            [self.PLAYER, '-m', 'nemu-12.0-x64-default2', '-s', '0'],
            parent_proc=svc,
        )

        self._stop(self.make_instance('nemu-12.0-x64-default'), [svc, player_a, player_b])

        self.assertFalse(svc.killed, '多实例共享的 Hypervisor 被误杀了')
        self.assertTrue(player_a.killed)
        self.assertFalse(player_b.killed)

    def test_empty_instance_name_skips_stop(self):
        """实例名为空时无法区分多开实例，直接放弃并返回 0。"""
        tree = self.build_tree()
        killed = self._stop(self.make_instance(''), tree['all'])

        self.assertEqual(0, killed)
        self.assertFalse(any(proc.killed for proc in tree['all']))

    def test_never_kills_hypervisor_by_process_name_alone(self):
        """Hypervisor 进程绝不会仅凭进程名就被终止（旧实现的根因）。"""
        tree = self.build_tree()
        platform = make_platform()
        with patch.object(platform_windows.psutil, 'process_iter', return_value=tree['all']):
            PlatformWindows._stop_mumu_x_instance(
                platform, self.make_instance('nemu-12.0-x64-default')
            )

        for proc in tree['all']:
            if os.path.basename(proc.exe_path).lower() in ('muvm6headless.exe', 'muvm6svc.exe'):
                if proc.killed:
                    self.assertIn(
                        '--comment', ' '.join(proc.cmdline_args),
                        f'Hypervisor 进程 {proc.exe_path} 在没有实例证据的情况下被杀了',
                    )


@WINDOWS_ONLY
class TestEmulatorInstanceGate(unittest.TestCase):
    """启停入口的实例闸门：识别不到实例时放弃，绝不猜一个杀掉。"""

    def test_stop_is_skipped_when_instance_is_none(self):
        platform = make_platform()
        platform.emulator_instance = None
        calls = []
        platform._emulator_stop = lambda instance: calls.append(instance)

        result = PlatformWindows._emulator_function_wrapper(platform, platform._emulator_stop)

        self.assertFalse(result)
        self.assertEqual([], calls, '识别不到实例时不能执行任何启停动作')

    def test_start_is_skipped_when_instance_is_none(self):
        platform = make_platform()
        platform.emulator_instance = None
        calls = []
        platform._emulator_start = lambda instance: calls.append(instance)

        result = PlatformWindows._emulator_function_wrapper(platform, platform._emulator_start)

        self.assertFalse(result)
        self.assertEqual([], calls)

    def test_wrapper_still_runs_when_instance_resolved(self):
        """正常识别到实例时照常执行，闸门不影响既有流程。"""
        platform = make_platform()
        instance = object()
        platform.emulator_instance = instance
        calls = []
        platform._emulator_stop = lambda target: calls.append(target)

        result = PlatformWindows._emulator_function_wrapper(platform, platform._emulator_stop)

        self.assertTrue(result)
        self.assertEqual([instance], calls)


if __name__ == '__main__':
    unittest.main()
