"""可在按住期间穿插截图与识别的连续触控会话。"""

from module.exception import ScriptError
from module.logger import logger


LIVE_DRAG_METHODS = ('minitouch', 'MaaTouch', 'uiautomator2', 'scrcpy', 'nemu_ipc')


class LiveDrag:
    """调用方逐帧移动触点；离开上下文时释放，绝不回退为点击。"""

    def __init__(self, device, name):
        self.device = device
        self.name = name
        self.method = device.config.Emulator_ControlMethod
        if self.method not in LIVE_DRAG_METHODS:
            raise ScriptError(f'{self.method} 不支持持续按住期间的截图反馈，请使用 MaaTouch 等控制方式')
        self.active = False
        self.point = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.up()
        except Exception as release_error:
            if exc_type is None:
                raise
            # 保留导致中断的原异常；释放失败也必须留下可诊断信息。
            logger.error(f'[设备-控制] 连续拖拽释放失败: {release_error}')

    def _send(self, operation, point):
        device = self.device
        if self.method in ('minitouch', 'MaaTouch'):
            builder = device.minitouch_builder if self.method == 'minitouch' else device.maatouch_builder
            if operation == 'up':
                builder.up().commit()
            else:
                getattr(builder, operation)(*point).commit()
            if self.method == 'MaaTouch':
                builder.send_sync()
            else:
                builder.send()
        elif self.method == 'uiautomator2':
            getattr(device.u2.touch, operation)(*point)
        elif self.method == 'nemu_ipc':
            if operation == 'up':
                device.nemu_ipc.up()
            else:
                device.nemu_ipc.down(*point)
        else:
            from module.device.method.scrcpy import const
            device.scrcpy_ensure_running()
            action = {'down': const.ACTION_DOWN, 'move': const.ACTION_MOVE, 'up': const.ACTION_UP}[operation]
            # 截图也使用这把锁，只锁单次发送，不能跨整个拖动会话持有。
            with device._scrcpy_control_socket_lock:
                device._scrcpy_control.touch(*point, action)

    def down(self, point):
        if self.active:
            raise ScriptError('连续拖拽已有活动触点')
        self.device.handle_control_check(self.name)
        self.point = tuple(map(int, point))
        self.active = True
        logger.info(f'[设备-控制] 连续拖拽按下 {self.point} @ {self.name}')
        self._send('down', self.point)

    def move(self, point):
        if not self.active:
            raise ScriptError('连续拖拽尚未按下')
        self.point = tuple(map(int, point))
        self._send('move', self.point)

    def up(self):
        if self.active:
            self._send('up', self.point)
            self.active = False
            logger.info(f'[设备-控制] 连续拖拽释放 {self.point} @ {self.name}')
