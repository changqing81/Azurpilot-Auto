"""连续触控的按住、逐帧移动及异常释放验证，不连接真实设备。"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from module.device.live_drag import LIVE_DRAG_METHODS, LiveDrag
from module.exception import ScriptError


class LiveDragTests(unittest.TestCase):
    def device(self, method):
        device = Mock()
        device.config = SimpleNamespace(Emulator_ControlMethod=method)
        device._scrcpy_control_socket_lock = threading.Lock()
        builder = Mock()
        for operation in ('down', 'move', 'up', 'commit'):
            getattr(builder, operation).return_value = builder
        device.minitouch_builder = device.maatouch_builder = builder
        return device, builder

    def test_all_supported_backends_keep_one_contact_between_frames(self):
        for method in LIVE_DRAG_METHODS:
            with self.subTest(method=method):
                device, builder = self.device(method)
                with LiveDrag(device, '测试连续拖动') as touch:
                    touch.down((1243, 120))
                    device.screenshot()
                    touch.move((1243, 121))
                    device.screenshot()
                    touch.move((1243, 122))
                    self.assertTrue(touch.active)
                    self.assertEqual(touch.point, (1243, 122))
                    if method in ('minitouch', 'MaaTouch'):
                        builder.up.assert_not_called()
                    elif method == 'uiautomator2':
                        device.u2.touch.up.assert_not_called()
                    elif method == 'nemu_ipc':
                        device.nemu_ipc.up.assert_not_called()
                    else:
                        self.assertEqual([entry.args[2] for entry in device._scrcpy_control.touch.call_args_list],
                                         [0, 2, 2])
                        self.assertTrue(device._scrcpy_control_socket_lock.acquire(blocking=False))
                        device._scrcpy_control_socket_lock.release()
                self.assertFalse(touch.active)
                device.handle_control_check.assert_called_once_with('测试连续拖动')
                device.drag.assert_not_called()
                device.swipe.assert_not_called()
                device.click.assert_not_called()
                if method in ('minitouch', 'MaaTouch'):
                    builder.down.assert_called_once_with(1243, 120)
                    self.assertEqual(builder.move.call_args_list, [call(1243, 121), call(1243, 122)])
                    builder.up.assert_called_once_with()
                    sender = builder.send_sync if method == 'MaaTouch' else builder.send
                    self.assertEqual(sender.call_count, 4)
                elif method == 'uiautomator2':
                    device.u2.touch.up.assert_called_once_with(1243, 122)
                elif method == 'nemu_ipc':
                    device.nemu_ipc.up.assert_called_once_with()
                else:
                    device._scrcpy_control.touch.assert_called_with(1243, 122, 1)

    def test_recognition_errors_and_keyboard_interrupt_release_contact(self):
        for method in LIVE_DRAG_METHODS:
            for error in (ValueError('失去截图重叠'), KeyboardInterrupt()):
                with self.subTest(method=method, error=type(error).__name__):
                    device, builder = self.device(method)
                    touch = LiveDrag(device, '测试异常释放')
                    with self.assertRaises(type(error)) as caught:
                        with touch:
                            touch.down((1243, 120))
                            touch.move((1243, 121))
                            raise error
                    self.assertIs(caught.exception, error)
                    self.assertFalse(touch.active)

    def test_failed_down_still_attempts_release(self):
        device, builder = self.device('MaaTouch')
        builder.send_sync.side_effect = [OSError('发送中断'), None]
        with self.assertRaisesRegex(OSError, '发送中断'):
            with LiveDrag(device, '测试失败按下') as touch:
                touch.down((1243, 120))
        builder.up.assert_called_once_with()

    def test_unsupported_backend_never_falls_back_to_swipe_or_click(self):
        device, _ = self.device('ADB')
        with self.assertRaises(ScriptError):
            LiveDrag(device, '测试不支持')
        device.handle_control_check.assert_not_called()
        device.swipe.assert_not_called()
        device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
