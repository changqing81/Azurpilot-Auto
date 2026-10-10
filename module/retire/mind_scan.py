"""复用 AP 设备和船坞导航的心智单元扫描任务。"""
from datetime import datetime

import numpy as np
from PIL import Image

import module.config.server as server
from module.base.timer import Timer
from module.exception import GameStuckError, RequestHumanTakeover
from module.logger import logger
from module.retire.assets import DOCK_EMPTY
from module.retire.dock import DOCK_SCROLL, Dock
from module.runtime.mind_calculator import RESULT_PATH, revision
from module.runtime.mind_recognition import ScanMerger, recognize_cards
from module.ui.page import page_dock


class MindCalculatorScan(Dock):
    def run(self):
        """扫描全部船坞；完成前不覆盖旧结果，中断由任务运行器处理。

        Pages:
            in: Any
            out: page_dock
        """
        if server.server != 'cn':
            raise RequestHumanTakeover('心智单元计算器当前内置国服舰船资料，请在国服实例使用自动扫描')
        from module.ocr.al_ocr import AlOcr
        from module.config.deep import deep_get, deep_set
        from module.config.transaction import config_transaction
        from module.config.utils import filepath_config
        logger.hr('心智单元船坞扫描', level=0)
        previous_revision = revision(deep_get(self.config.data, RESULT_PATH, {}).get('ships', []))
        self.ui_ensure(page_dock)
        self.dock_reset()
        if self.appear(DOCK_EMPTY):
            ships = []
        else:
            DOCK_SCROLL.set_top(self)
            if DOCK_SCROLL.appear(self):
                if DOCK_SCROLL.at_top(self) or DOCK_SCROLL.length == DOCK_SCROLL.total:
                    logger.attr('船坞位置', '已确认顶部')
                else:
                    raise RequestHumanTakeover('船坞未能回到顶部，保留旧扫描结果')
            else:
                raise RequestHumanTakeover('无法识别船坞滚动条，保留旧扫描结果')
            names = AlOcr(config=self.config, name='ppocr_v6')
            levels = AlOcr(config=self.config, name='azur_lane')
            ships = self._scan_pages(names, levels)
        # 扫描期间用户可能修改数据；复读当前配置避免覆盖手工修正。
        with config_transaction(filepath_config(self.config.config_name)):
            latest = self.config.read_file(self.config.config_name)
            if revision(deep_get(latest, RESULT_PATH, {}).get('ships', [])) != previous_revision:
                raise RequestHumanTakeover('扫描期间舰船数据已修改，保留现有数据；请重新扫描')
            # 同一清单重复保存只会更新日期；通过版本检查后更新本字段基线，
            # 让通用配置保存继续保护其他字段，同时允许提交已完成的扫描。
            if hasattr(self.config, '_loaded_data'):
                import copy
                deep_set(self.config._loaded_data, RESULT_PATH, copy.deepcopy(deep_get(latest, RESULT_PATH, {})))
            self.config.modified[RESULT_PATH] = dict(ships=ships, updated_at=datetime.now().isoformat(timespec='seconds'))
            self.config.save()
        logger.attr('待核对舰船', len(ships))

    def _scan_pages(self, names, levels):
        """每页先确认画面稳定，正向识别滚动条底部后完成。"""
        merger = ScanMerger()
        previous = None
        stable = Timer(.3, count=2).start()
        drag = Timer(2).start()
        scanned = False
        page = 0
        progress = Timer(20, count=10).start()
        while True:
            self.device.screenshot()
            if self.appear(page_dock.check_button):
                pixels = self.device.image[65:520, 80:1230]
                if previous is None or np.mean(np.abs(pixels.astype(float) - previous.astype(float))) > 1:
                    previous = pixels.copy()
                    stable.reset()
                    continue
                if not stable.reached():
                    continue
                if not scanned:
                    image = Image.fromarray(self.device.image)
                    page += 1
                    try:
                        cards = recognize_cards(image, f'自动扫描第 {page} 页', name_ocr=names, level_ocr=levels)
                        moved = merger.add(image, cards)
                    except ValueError as exc:
                        raise RequestHumanTakeover(str(exc)) from exc
                    if page == 1 or moved:
                        progress.reset()
                    if len(merger.slots) > 5000:
                        raise RequestHumanTakeover('识别卡片超过 5000，请核对船坞布局，保留旧扫描结果')
                    logger.attr('扫描页', page)
                    logger.attr('识别卡片', len(merger.slots))
                    scanned = True
                    if DOCK_SCROLL.appear(self):
                        if DOCK_SCROLL.at_bottom(self) or DOCK_SCROLL.length == DOCK_SCROLL.total:
                            break
                    if progress.reached():
                        raise GameStuckError('船坞扫描画面没有继续滚动，尚未确认到底')
                    if page >= 500:
                        raise GameStuckError('船坞扫描未能到达底部')
                if drag.reached():
                    # 滚动条单次操作，不在父状态循环里嵌套 Scroll.set 的循环。
                    self.device.swipe((1170, 470), (1170, 243), name='心智单元船坞翻页')
                    drag.reset()
                    stable.reset()
                    previous = None
                    scanned = False
                continue
            if self.handle_popup_confirm():
                previous = None
                stable.reset()
                continue
        return merger.ships()
