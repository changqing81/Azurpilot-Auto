"""战役状态检测模块。

通过 OCR 读取战役页面上的数值信息，包括：
- 物资（金币）数量
- 石油数量
- 活动 PT（点数）
- 石油和物资限制检测

这些信息用于判断是否满足停止条件（如石油耗尽、物资溢出等）。

PtOcr 类专门处理活动 PT 数字的 OCR 识别，
需要特殊的图像预处理（反色、背景去除等）。

继承自 UI，利用页面导航能力。
"""

import datetime
import re

import cv2
import numpy as np

import module.config.server as server

from module.base.timer import Timer
from module.campaign.assets import OCR_EVENT_PT, OCR_COIN, OCR_OIL, OCR_COIN_LIMIT, OCR_OIL_LIMIT, OCR_OIL_CHECK
from module.base.utils import color_similar, get_color
from module.logger import logger
from module.ocr.ocr import Digit, Ocr
from module.ui.ui import UI
from module.log_res import LogRes

#if server.server != 'jp':
#    OCR_COIN = Digit(OCR_COIN, name='OCR_COIN', letter=(239, 239, 239), threshold=128)
#else:
#    OCR_COIN = Digit(OCR_COIN, name='OCR_COIN', letter=(201, 201, 201), threshold=128)

class PtOcr(Ocr):
    """活动 PT 数字 OCR。

    PT 数字显示在活动界面右上角的追踪条上（OCR_EVENT_PT 区域），识别的
    字母表只含 `X` 与数字，避免把追踪条内的其它字符识别成数字。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, lang='azur_lane', alphabet='X0123456789', **kwargs)
        # 本次识别结果里是否含数字，由 after_process 维护，供调用方区分
        # “读到 0”与“区域里什么都没读到”
        self.last_valid = False

    def pre_process(self, image):
        """
        对 PT 数字图像进行预处理。

        Args:
            image (np.ndarray): 形状为 (height, width, channel) 的图像。

        Returns:
            np.ndarray: 形状为 (width, height) 的灰度图像。
        """
        # 取 RGB 三通道的最大值（等价于反色图取最小值后取反，避免分配中间数组）
        r, g, b = cv2.split(image)
        cv2.max(r, g, dst=r)
        cv2.max(r, b, dst=r)
        cv2.bitwise_not(r, dst=r)
        # 去除背景，将 0-192 映射到 0-255
        cv2.convertScaleAbs(r, alpha=255 / 192, dst=r)
        return r

    def after_process(self, result):
        """记录识别区域里是否读到数字，统一在此做格式门，与所用 OCR 引擎无关。

        Args:
            result (str): OCR 原始识别字符串。

        Returns:
            str: 原样返回识别字符串，数值解析由调用方完成。
        """
        # 识别区域无内容（被遮挡、界面未加载、切页动画残影）时模型输出空串，
        # 空串与“真读到 0”必须区分：前者不能当作有效 PT 写进仪表盘。
        self.last_valid = bool(re.search(r'\d', str(result)))
        return super().after_process(result)


OCR_PT = PtOcr(OCR_EVENT_PT)


class CampaignStatus(UI):
    # 单帧骤降防抖：新读数低于上一次有效 PT 的该比例（即骤降超过 95%）时，
    # 判为疑似脏数据，延迟重截一帧复核。这里只比较相对比例、不设绝对门槛——
    # 活动 PT 的量级随活动变化（几千到几十万），写死任何具体数值都会在
    # 下一个活动上失效。
    PT_DROP_CONFIRM_RATIO = 0.05

    def _ocr_pt_once(self):
        """读取当前截图里的活动 PT（单帧）。

        Returns:
            tuple[int, bool, str]: (PT 数值, 是否解析出数字, OCR 原始字符串)。
                识别区域无内容或格式无法解析时数值为 0、布尔为 False。
        """
        pt = OCR_PT.ocr(self.device.image)
        if not OCR_PT.last_valid:
            return 0, False, pt

        # 首选匹配带前缀 X 的格式（历史上部分活动使用 ‘X1234’）
        res = re.search(r'X(\d+)', pt)
        if res:
            num = int(res.group(1))
            logger.attr('活动PT', num)
            return num, True, pt
        # 回退：若 OCR 返回纯数字也接受（保留警告以便回溯）
        res2 = re.search(r'(\d+)', pt)
        if res2:
            num = int(res2.group(1))
            logger.warning(f"无效的PT结果格式 (missing 'X'): {pt}; fallback to digits: {num}")
            logger.attr('活动PT_回退', num)
            return num, True, pt
        return 0, False, pt

    def _last_valid_pt(self):
        """取仪表盘里上一次记录成功的 PT，作为骤降防抖的基线。

        未记录过（值为 0 或非整数）时返回 None：没有基线就不做骤降判断，
        避免把活动刚开时读到的小数值当成异常拦下。

        Returns:
            int | None: 上一有效 PT；无基线时返回 None。
        """
        key = 'Dashboard.Pt.Value'
        if key in self.config.modified:
            value = self.config.modified[key]
        else:
            group = LogRes(self.config).group('Pt')
            value = group.get('Value') if isinstance(group, dict) else None
        # bool 是 int 的子类，单独排除，避免 True 被当成 1 的基线
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if value > 0 else None

    def get_event_pt(self, update=False):
        """
        获取活动 PT 数量。

        单帧读数若相对上一有效值骤降（低于 PT_DROP_CONFIRM_RATIO），先按脏数据
        处理：延迟 0.5 秒后重截一帧复核。两次读数都骤降才认为更新有效（真实的
        PT 消耗，如活动商店兑换），否则保留上一有效值并告警，不写仪表盘。

        Returns:
            int: PT 数量，识别区域无内容或解析失败返回 0。
        """
        pt, valid, raw = self._ocr_pt_once()
        if not valid:
            logger.warning(f'无效的PT结果: {raw}')
            return 0

        previous = self._last_valid_pt()
        if previous is not None and pt < previous * self.PT_DROP_CONFIRM_RATIO:
            # 骤降：单帧读数不足以判定，先复核再决定是否写仪表盘
            logger.warning(
                f'PT 读取异常（识别值 {pt}/首帧值 {previous}），已触发二次校验，'
                f'保留上一有效值 {previous}'
            )
            # 界面切换/加载动画下的残影帧会读出假数字，等画面稳定后重截一帧
            self.device.sleep(0.5)
            self.device.screenshot()
            confirm, confirm_valid, _ = self._ocr_pt_once()
            if confirm_valid and confirm < previous * self.PT_DROP_CONFIRM_RATIO:
                # 二次读数同样骤降：判为真实变化，按新值更新
                logger.info(f'PT 二次读数 {confirm} 同样骤降，确认更新')
                pt = confirm
            else:
                logger.warning(
                    f'PT 二次读数 {confirm if confirm_valid else "空"} 与首帧不一致，'
                    f'保留上一有效值 {previous}'
                )
                if update:
                    self.config.update()
                return previous

        LogRes(self.config).Pt = pt
        if update:
            self.config.update()
        return pt

    def get_coin(self, skip_first_screenshot=True, update=False):
        """
        获取金币数量。

        Returns:
            int: 金币数量。
        """
        _coin = {}
        timeout = Timer(1, count=2).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning('获取物资超时')
                break

            _coin = {
                'Value': self._get_num(OCR_COIN, 'OCR_COIN', (239, 239, 239)),
                'Limit': self._get_num(OCR_COIN_LIMIT, 'OCR_COIN_LIMIT', (239, 239, 239))
            }
            if _coin['Value'] >= 100:
                break
        LogRes(self.config).Coin = _coin
        if update:
            self.config.update()

        return _coin['Value']

    def _get_num(self, _button, name, letter=(247, 247, 247)):
        # 更新偏移量
        _ = self.appear(OCR_OIL_CHECK)

        color = get_color(self.device.image, OCR_OIL_CHECK.button)
        if color_similar(color, OCR_OIL_CHECK.color):
            # 原始颜色
            if isinstance(_button, Ocr):
                ocr = _button
            else:
                if server.server != 'jp':
                    ocr = Digit(_button, name=name, letter=letter, threshold=128)
                else:
                    ocr = Digit(_button, name=name, letter=(201, 201, 201), threshold=128)
        elif color_similar(color, (59, 59, 64)):
            # 带黑色遮罩
            ocr = Digit(_button, name=name, letter=(165, 165, 165), threshold=128)
        else:
            logger.warning('[战役-状态] 意外的OCR_OIL_CHECK颜色')
            ocr = Digit(_button, name=name, letter=(247, 247, 247), threshold=128)

        return ocr.ocr(self.device.image)

    def get_oil(self, skip_first_screenshot=True, update=False):
        """
        获取石油数量。

        Returns:
            int: 石油数量。
        """
        _oil = {}
        timeout = Timer(1, count=2).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if not self.appear(OCR_OIL_CHECK, offset=(10, 2)):
                logger.info('无石油图标')
                self.device.sleep(1)

            if timeout.reached():
                logger.warning('获取石油超时')
                break

            _oil = {
                'Value': self._get_num(OCR_OIL, 'OCR_OIL', (247, 247, 247)),
                'Limit': self._get_num(OCR_OIL_LIMIT, 'OCR_OIL_LIMIT', (247, 247, 247))
            }
            if _oil['Value'] >= 100:
                break
        LogRes(self.config).Oil = _oil
        if update:
            self.config.update()

        return _oil['Value']

    def is_balancer_task(self):
        """
        判断当前任务是否为活动任务（排除每日活动任务）。

        Returns:
            bool: 是否为活动任务。
        """
        tasks = [
            'Event',
            'Event2',
            'Raid',
            'Coalition',
            'GemsFarming',
            'ThreeOilLowCost',
        ]
        command = self.config.Scheduler_Command
        if command in tasks:
            if self.config.Campaign_Event == 'campaign_main':
                return False
            else:
                return True
        else:
            return False
