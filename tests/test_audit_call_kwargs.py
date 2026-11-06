"""调用签名自检脚本（dev_tools/audit_call_kwargs.py）的回归测试。

背景：审计按名字匹配函数定义，函数体内定义的嵌套函数不在收集范围内，
于是 `query(args, binary=False)` 这种局部函数调用会被错配到别处的同名
模块级函数上，报出「传了目标不接受的关键字」的假警报（CI 直接变红）。
修复后解析按调用点作用域由内向外查找，局部定义优先。

这里用临时目录跑真实脚本（子进程），既验证假警报已消失，也验证没有
因为放宽而漏掉真问题。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AUDIT = Path(__file__).resolve().parent.parent / 'dev_tools' / 'audit_call_kwargs.py'

# 同名模块级函数：关键字与局部函数完全不同，用它复现「错配」场景
DECOY = '''
def query(default=None, error=None, fn=None, now=None, payload=None):
    return None
'''

# 类方法里定义了嵌套 query()，调用时传了它的 binary 参数
CASE_LOCAL_ACCEPTS = '''
class AdbProbe:
    def ready(self, cancel):
        def query(args, binary=False):
            return False, b'' if binary else ''

        ok, data = query(['exec-out', 'screencap', '-p'], binary=True)
        return ok, data
'''

# 同样的局部函数，但调用传了它不接受的关键字 —— 必须报出来
CASE_LOCAL_REJECTS = '''
class AdbProbe:
    def ready(self, cancel):
        def query(args, binary=False):
            return False, b'' if binary else ''

        ok, data = query(['exec-out', 'screencap'], unknown_kw=True)
        return ok, data
'''


def run_audit(root):
    """在指定根目录上运行审计脚本，返回 (退出码, stdout+stderr)。"""
    env = dict(os.environ)
    env['PYTHONUTF8'] = '1'
    proc = subprocess.run(
        [sys.executable, '-X', 'utf8', str(AUDIT), str(root)],
        check=False, capture_output=True, text=True, encoding='utf-8', errors='replace', env=env,
    )
    return proc.returncode, (proc.stdout or '') + (proc.stderr or '')


class AuditCallKwargsTests(unittest.TestCase):
    def _run(self, *sources):
        with tempfile.TemporaryDirectory() as tmp:
            for index, source in enumerate(sources):
                Path(tmp, f'module_{index}.py').write_text(source, encoding='utf-8')
            return run_audit(tmp)

    def test_nested_function_keyword_is_not_a_false_positive(self):
        """局部函数接受的参数不能因为同名全局函数的签名不同而报警。"""
        code, output = self._run(DECOY, CASE_LOCAL_ACCEPTS)
        self.assertEqual(code, 0, output)
        self.assertNotIn('binary', output)

    def test_nested_function_rejecting_keyword_is_still_reported(self):
        """放宽不能变成漏报：局部函数确实不接受的关键字必须报出来。"""
        code, output = self._run(DECOY, CASE_LOCAL_REJECTS)
        self.assertEqual(code, 1, output)
        self.assertIn('unknown_kw', output)


if __name__ == '__main__':
    unittest.main()
