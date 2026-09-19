"""MaaTouch 命令序列化回归测试。

历史上 to_maatouch_sync 的 'u'（抬起）无 mode 分支误用 self.ms（默认 10），
会发出 'u 10'——把等待毫秒数当触点索引发给 MaaTouch 二进制：解析不报错，
但抬错触点，多触点批次里会让不该抬起的触点保持按下。现有流程恰好都以
up 结尾（send_sync 给批尾 r/d/m/u 注入 mode），才从未触发。

这里锁定各命令的 to_maatouch_sync 序列化结果，防止回归。
"""
import unittest

from module.device.method.minitouch import Command


class TestMaatouchSyncSerializer(unittest.TestCase):
    def test_up_without_mode_uses_contact(self):
        """'u' 无 mode 必须序列化触点索引，而不是误用 ms。"""
        self.assertEqual(Command('u', contact=0).to_maatouch_sync(), 'u 0\n')
        self.assertEqual(Command('u', contact=3).to_maatouch_sync(), 'u 3\n')

    def test_up_with_mode(self):
        self.assertEqual(Command('u', contact=3, mode=2).to_maatouch_sync(), 'u 3 2\n')

    def test_down_and_move_without_mode(self):
        down = Command('d', contact=0, x=218, y=507, pressure=100)
        self.assertEqual(down.to_maatouch_sync(), 'd 0 218 507 100\n')
        move = Command('m', contact=0, x=152, y=507, pressure=100, mode=2)
        self.assertEqual(move.to_maatouch_sync(), 'm 0 152 507 100 2\n')

    def test_wait_keeps_ms(self):
        self.assertEqual(Command('w', ms=2200).to_maatouch_sync(), 'w 2200\n')


if __name__ == '__main__':
    unittest.main()
