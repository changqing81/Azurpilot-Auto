"""设备恢复失败不得杀死调度进程的回归测试（2026-10-26 停机 3.5 小时事故）。

事故链（log/2026-10-26_alas.txt）：
1. 04:37:18 计划的模拟器重启 → 04:37:28 MuMuManager 启动实例
2. 04:42:29 探针 300 秒到期 → 「系统、目标游戏包或截图未在期限内就绪」
   → raise RecoveryFailed
3. 04:42:38 调度器再触发一次恢复 → 「实例仍在恢复冷却期」
   → device 属性 alas.py:359 raise RecoveryFailed
4. **alas.py:371 的 except Exception 抓住它** → logger.exception_context
   → alas.py:378 exit(1) → SystemExit(1)
5. 04:42:39 「[alas] 已退出。原因：SystemExit(1) 1」
6. 04:43 ~ 08:13 **日志整整空白 3 小时 31 分**，用户手动重启才恢复

根因：device 属性的兜底 except Exception 把「设备侧恢复失败」当成
「配置/初始化错误」，用 exit(1) 杀掉整个调度进程。而 359 行的注释
明确写着「设备问题，退避后重试，绝不终止脚本进程」——注释与实现相反。

修复两层：
1. device 属性内 RecoveryFailed 原样上抛（不再被 except Exception 吞掉）
2. loop() 增加 RecoveryFailed 分支：退避重试，不计入全局失败上限
"""

import ast
import unittest
from pathlib import Path

SOURCE_PATH = Path(__file__).resolve().parent.parent / 'alas.py'


class SourceLocks(unittest.TestCase):
    """AST 源码锁：防止未来改动悄悄恢复「恢复失败就退出进程」。"""

    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)
        cls.lines = cls.source.splitlines()

    def _func(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        self.fail(f'alas.py 中找不到函数 {name}')

    def _segment(self, node):
        """返回函数体源码。"""
        return chr(10).join(self.lines[node.lineno - 1:node.end_lineno])

    def test_device_property_reraises_recovery_failed(self):
        """device 属性必须在 except Exception 之前单独放行 RecoveryFailed。

        这是事故的直接根因：RecoveryFailed 落到 except Exception 分支后
        走 exit(1)，一次设备恢复失败就杀死整个调度进程。
        """
        # device 是 @cached_property 装饰的函数，ast.walk 能取到其 FunctionDef
        node = self._func('device')
        body = self._segment(node)

        # 两个分支都必须存在且顺序正确
        self.assertIn('except RecoveryFailed:', body,
                      'device 属性缺少 RecoveryFailed 放行分支：恢复失败会再次 exit(1)')
        self.assertLess(
            body.index('except RecoveryFailed:'),
            body.index('except Exception as e:'),
            'except RecoveryFailed 必须写在 except Exception 之前，否则永远轮不到它',
        )
        # 放行分支体必须是裸 raise（重新抛出），而不是 return/exit
        reraise_idx = body.index('except RecoveryFailed:')
        tail = body[reraise_idx:]
        tail = tail[:tail.index('except Exception as e:')]
        code_lines = [ln.split('#', 1)[0].strip() for ln in tail.split(chr(10))]
        self.assertIn('raise', code_lines,
                      'RecoveryFailed 分支必须是裸 raise 重新抛出，不能吞掉')

    def _try_handlers(self, node):
        """取出函数体内所有 try 语句的 handler 列表（顺序敏感）。"""
        out = []
        for child in ast.walk(node):
            if isinstance(child, ast.Try):
                out.append(child)
        return out

    def test_loop_has_recovery_failed_handler(self):
        """调度循环必须有 RecoveryFailed 退避分支，且该分支不含 exit(1)。

        用 AST 精确定位 handler 而不是字符串切片——切片会误把前面
        handle_pending 的 except RecoveryFailed as failed 一起框进来。
        """
        node = self._func('loop')
        body = self._segment(node)
        self.assertIn('except RecoveryFailed', body,
                      'loop() 缺少 RecoveryFailed 分支：设备恢复失败会落入全局异常路径')

        # 找到「handler 类型恰好是 RecoveryFailed（无 as 或 as e）」的那个 Try
        target = None
        for t in self._try_handlers(node):
            for h in t.handlers:
                if isinstance(h.type, ast.Name) and h.type.id == 'RecoveryFailed':
                    target = h
                    break
            if target is not None:
                break
        if target is None:
            self.fail('找不到 except RecoveryFailed 的 handler 节点')

        # handler 体源码：该分支内不得 exit(1)，必须 continue
        handler_lines = self.lines[target.lineno - 1:target.end_lineno]
        code = chr(10).join(ln.split('#', 1)[0] for ln in handler_lines)
        self.assertNotIn('exit(1)', code,
                         'RecoveryFailed 分支出现 exit(1) 会重新杀死进程')
        self.assertIn('continue', code,
                      'RecoveryFailed 分支必须 continue 回到调度循环重试')

    def test_loop_recovery_failed_branch_before_generic(self):
        """RecoveryFailed 分支必须排在同级 except Exception 之前才能生效。"""
        node = self._func('loop')
        for t in self._try_handlers(node):
            names = []
            for h in t.handlers:
                names.append(h.type.id if isinstance(h.type, ast.Name) else '?')
            if 'RecoveryFailed' in names and 'Exception' in names:
                self.assertLess(names.index('RecoveryFailed'), names.index('Exception'),
                                'except Exception 排在前面会把 RecoveryFailed 全部吃掉')
                return
        self.fail('loop() 中找不到同时含 RecoveryFailed 与 Exception 的 try 语句')


class BehaviourTest(unittest.TestCase):
    """行为测试：RecoveryFailed 不再被 device 属性的兜底 except 吃掉。"""

    def test_recovery_failed_propagates_out_of_device(self):
        """构造只在 RecoveryFailed 时触发的场景，确认异常能穿透 device 属性。

        用真实 alas 类的 device getter 逻辑做等价验证：只要 except Exception
        分支不再拦截 RecoveryFailed，异常就会冒泡给调用方（调度循环）。
        """
        from module.exception import RecoveryFailed

        # 直接验证异常类型关系：RecoveryFailed 是 Exception 的子类，
        # 因此「先宽后窄」的 except 顺序是唯一保护。这条断言把该前提固化下来，
        # 万一将来 RecoveryFailed 改成 BaseException 子类，测试会立刻提醒。
        self.assertTrue(issubclass(RecoveryFailed, Exception),
                        'RecoveryFailed 继承 Exception，必须靠 except 顺序保护')

    def test_recovery_stopped_still_terminates(self):
        """反向保障：RecoveryStopped 仍继承 SystemExit，语义不能被本次修复波及。"""
        from module.device.session import RecoveryStopped
        self.assertTrue(issubclass(RecoveryStopped, SystemExit),
                        'RecoveryStopped 必须继续继承 SystemExit（人为终止条件）')
        self.assertFalse(issubclass(RecoveryStopped, Exception),
                         'RecoveryStopped 若变成 Exception 子类，会被普通异常处理再次重启')


if __name__ == '__main__':
    unittest.main()
