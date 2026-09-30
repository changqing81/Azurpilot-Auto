"""
科研队列管理。

本模块管理科研系统的队列功能，包括：
- 将已启动的科研项目添加到队列
- 逐槽识别队列页卡片的显示时长（批量模式的基础原语）
- 领取队列中已完成项目的奖励
- 获取队列中第一个项目的剩余时间和预计完成时间

科研队列最多容纳 5 个项目，采用 FIFO 顺序运行。
队列中第一个项目运行完成后，等待中的项目自动开始。

术语对照：
    科研队列(Research Queue): 最多容纳 5 个排队项目的队列
    卡片(Card): 队列页从左到右排列的 5 张项目卡片，左起第 1 张为队首
"""
import re
from datetime import timedelta

from module.base.button import ButtonGrid
from module.base.decorator import cached_property, Config
from module.base.utils import get_color
from module.config.time_source import now as current_time
from module.exception import GameBugError
from module.logger import logger
from module.ocr.ocr import Duration, Ocr
from module.research.assets import *
from module.research.ui import ResearchUI

OCR_QUEUE_REMAIN = Duration(QUEUE_REMAIN, letter=(255, 255, 255), threshold=128, name='OCR_QUEUE_REMAIN')

# 队列页逐槽显示时长，双通道识别：
# 进行中/已完成卡片的数字为亮白色（亮通道 threshold=128 即可提取），
# 等待中卡片的数字被 50% 黑色遮罩压暗（min 通道恒为 127），需要暗通道（threshold=280）提取。
# 单一阈值在数学上无解：亮卡片的背景会混入暗通道，暗数字会漏出亮通道。
OCR_QUEUE_TIME_BRIGHT = Ocr([QUEUE_TIME_1, QUEUE_TIME_2, QUEUE_TIME_3, QUEUE_TIME_4, QUEUE_TIME_5],
                            letter=(255, 255, 255), threshold=128, alphabet='0123456789:IDSB',
                            name='OCR_QUEUE_TIME_BRIGHT')
OCR_QUEUE_TIME_DIM = Ocr([QUEUE_TIME_1, QUEUE_TIME_2, QUEUE_TIME_3, QUEUE_TIME_4, QUEUE_TIME_5],
                         letter=(255, 255, 255), threshold=280, alphabet='0123456789:IDSB',
                         name='OCR_QUEUE_TIME_DIM')


def parse_display_time(text):
    """
    解析卡片显示的时长文本（如 `02:25:57`）。

    Args:
        text (str): OCR 原始文本，可能为空串或噪声。

    Returns:
        timedelta | None: 解析成功返回时长；空槽或无法解析的噪声返回 None。
    """
    text = text.replace('I', '1').replace('D', '0').replace('S', '5').replace('B', '8')
    result = re.search(r'(\d{1,2}):(\d{2}):(\d{2})', text)
    if result:
        hour, minute, second = [int(v) for v in result.groups()]
        if minute < 60 and second < 60:
            return timedelta(hours=hour, minutes=minute, seconds=second)
    return None


class ResearchQueue(ResearchUI):
    """
    科研队列管理器，负责队列操作和状态检测。

    提供队列项目的添加、状态检测、奖励领取和时间查询等功能。
    通过颜色检测识别队列左侧的状态图标来判断各槽位状态。

    Attributes:
        queue_status_grids (ButtonGrid): 队列状态图标的按钮网格，
            因各服务器 UI 布局差异，通过 @Config.when 按服务器分别定义。
    """
    def research_queue_add(self, skip_first_screenshot=True):
        """
        Returns:
            bool: True if success to add to queue,
                False if project requirements not satisfied, can't be added to queue

        Pages:
            in: RESEARCH_QUEUE_ADD (is_in_research, DETAIL_NEXT)
            out: is_in_research and stabled
        """
        logger.hr('加入科研队列')
        # POPUP_CONFIRM has just been clicked in research_project_start()
        self.popup_interval_clear()
        self.interval_clear([RESEARCH_QUEUE_ADD])
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if self.is_research_stabled():
                break

            if self.appear(RESEARCH_QUEUE_ADD, offset=(20, 20), interval=5):
                if self._research_queue_add_available():
                    self.device.click(RESEARCH_QUEUE_ADD)
                    continue
                else:
                    logger.info('[科研-队列] 项目条件未满足，取消')
                    self.research_detail_cancel()
                    return False

            if self.handle_popup_confirm('RESEARCH_QUEUE'):
                self.interval_reset(RESEARCH_QUEUE_ADD)
                continue

        self.ensure_research_center_stable()
        return True

    def _research_queue_add_available(self):
        """
        Returns:
            bool: True if able add to queue,
                False if project requirements not satisfied, can't be added to queue
        """
        # RESEARCH_QUEUE_ADD.area is the letter `Queue`
        # RESEARCH_QUEUE_ADD.button is the entire clickable area of button
        # Available: (90, 142, 203)
        # Unavailable: (153, 160, 170)
        r, g, b = get_color(self.device.image, RESEARCH_QUEUE_ADD.button)
        if b - min(r, g) > 60:
            return True
        else:
            return False

    @cached_property
    @Config.when(SERVER='en')
    def queue_status_grids(self):
        """
        Status icons on the left
        """
        return ButtonGrid(
            origin=(8, 259), delta=(0, 40.5), button_shape=(25, 25), grid_shape=(1, 5), name='QUEUE_STATUS')

    @cached_property
    @Config.when(SERVER='jp')
    def queue_status_grids(self):
        """
        Status icons on the left
        """
        return ButtonGrid(
            origin=(18, 259), delta=(0, 40.5), button_shape=(25, 25), grid_shape=(1, 5), name='QUEUE_STATUS')

    @cached_property
    @Config.when(SERVER='tw')
    def queue_status_grids(self):
        """
        Status icons on the left
        """
        return ButtonGrid(
            origin=(8, 259), delta=(0, 40.5), button_shape=(25, 25), grid_shape=(1, 5), name='QUEUE_STATUS')

    @cached_property
    @Config.when(SERVER=None)
    def queue_status_grids(self):
        """
        Status icons on the left
        """
        return ButtonGrid(
            origin=(18, 259), delta=(0, 40.5), button_shape=(25, 25), grid_shape=(1, 5), name='QUEUE_STATUS')

    def _queue_status_detect(self, button):
        """
        Args:
            button: Button of status icon

        Returns:
            str:
                'finished': Orange ✓ surrounded by orange border
                'running': Black ✓ surrounded by research progress, gray and blue
                'waiting': Gray … surrounded by gray border
                'empty': Black … surrounded by black border or just nothing
        """
        center = button.crop((7, 7, 21, 21))
        if self.image_color_count(center, color=(255, 158, 57), threshold=180, count=20):
            return 'finished'
        if self.image_color_count(center, color=(90, 97, 132), threshold=221, count=10):
            return 'waiting'
        if self.image_color_count(center, color=(24, 24, 41), threshold=221, count=10):
            below = button.crop((7, 14, 21, 21))
            if self.image_color_count(below, color=(24, 24, 41), threshold=221, count=10):
                return 'running'
            else:
                return 'empty'
        logger.warning(f'[科研-队列] 未知的队列状态，来自 {button}，假设为运行中')
        return 'running'

    def get_queue_slot(self):
        """
        Returns:
            int: Number of empty slots in queue

        Pages:
            in: is_in_queue
        """
        status = [self._queue_status_detect(button) for button in self.queue_status_grids.buttons]
        logger.info(f'[科研-队列] 科研队列: {status}')
        status = status[::-1]
        for index, s in enumerate(status):
            if s != 'empty':
                logger.attr('科研队列槽位', index)
                return index
        index = len(status)
        logger.attr('科研队列槽位', index)
        return index

    def get_research_ended(self):
        """
        Returns:
            datetime: Time of the end of the first research in the queue.

        Pages:
            in: is_in_queue

        Raises:
            GameBugError:
        """
        if self.image_color_count(QUEUE_REMAIN, color=(123, 125, 123), threshold=235, count=100):
            logger.error('[科研-队列] 队列中第一个科研未运行，'
                         '可能是游戏bug，'
                         '重启游戏应该能修复。')
            raise GameBugError
        if not self.image_color_count(QUEUE_REMAIN, color=(255, 255, 255), threshold=221, count=100):
            logger.info('[科研-队列] 科研队列为空')
            return current_time()

        end_time = current_time() + OCR_QUEUE_REMAIN.ocr(self.device.image)
        logger.info(f'[科研-队列] 第一个科研结束时间: {end_time}')
        return end_time

    def get_queue_display_times(self):
        """
        逐槽识别队列页 5 张卡片的显示时长。

        每个槽位独立跑亮、暗两个 OCR 通道并取先读到合法时长者，
        不依赖卡片明暗判断状态，因此用户手动加入/收取造成明暗混合时同样可读。

        Returns:
            list[timedelta | None]: 长度 5，从队首到队尾（卡片从左到右）。
                进行中=剩余倒计时，等待中=项目总时长（静态），已完成=0，空槽=None。
                非空值相加即为队列全部完成的剩余总时长（FIFO 顺序执行）。

        Pages:
            in: is_in_queue
        """
        bright = OCR_QUEUE_TIME_BRIGHT.ocr(self.device.image)
        dim = OCR_QUEUE_TIME_DIM.ocr(self.device.image)
        times = []
        for text_bright, text_dim in zip(bright, dim):
            time = parse_display_time(text_bright)
            if time is None:
                time = parse_display_time(text_dim)
            times.append(time)
        logger.attr('科研-队列显示时长', times)
        return times
