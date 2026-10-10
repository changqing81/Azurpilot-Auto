"""复用 AP 设备和船坞导航的心智单元扫描任务。"""
from datetime import datetime

import numpy as np
from PIL import Image

import module.config.server as server
from module.base.timer import Timer
from module.base.utils import color_similarity_2d
from module.device.live_drag import LIVE_DRAG_METHODS
from module.exception import GameStuckError, MindCalculatorScanError
from module.logger import logger
from module.retire.assets import DOCK_EMPTY
from module.retire.dock import CARD_GRIDS, DOCK_SCROLL, Dock
from module.runtime.mind_calculator import RESULT_PATH, revision
from module.runtime.mind_recognition import ScanMerger, recognize_cards, row_scroll_offset
from module.ui.page import page_dock


class MindCalculatorScan(Dock):
    def run(self):
        """扫描全部船坞；完成前不覆盖旧结果，中断由任务运行器处理。

        Pages:
            in: Any
            out: page_dock
        """
        if server.server != 'cn':
            raise MindCalculatorScanError('心智单元计算器当前内置国服舰船资料，请在国服实例使用自动扫描')
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
            names = AlOcr(config=self.config, name='ppocr_v6')
            levels = AlOcr(config=self.config, name='azur_lane')
            ships = self._scan_pages(names, levels)
        # 扫描期间用户可能修改数据；复读当前配置避免覆盖手工修正。
        with config_transaction(filepath_config(self.config.config_name)):
            latest = self.config.read_file(self.config.config_name)
            if revision(deep_get(latest, RESULT_PATH, {}).get('ships', [])) != previous_revision:
                raise MindCalculatorScanError('扫描期间舰船数据已修改，保留现有数据；请重新扫描')
            # 同一清单重复保存只会更新日期；通过版本检查后更新本字段基线，
            # 让通用配置保存继续保护其他字段，同时允许提交已完成的扫描。
            if hasattr(self.config, '_loaded_data'):
                import copy
                deep_set(self.config._loaded_data, RESULT_PATH, copy.deepcopy(deep_get(latest, RESULT_PATH, {})))
            self.config.modified[RESULT_PATH] = dict(ships=ships, updated_at=datetime.now().isoformat(timespec='seconds'))
            self.config.save()
        logger.attr('待核对舰船', len(ships))

    def _scroll_thumb(self, allow_occlusion=False):
        """直接检测滑块像素；小滑块也有效，不使用通用控件的 10% 可见门槛。"""
        thumb = np.flatnonzero(DOCK_SCROLL.match_color(self))
        if not len(thumb):
            if allow_occlusion:
                return None
            raise MindCalculatorScanError('无法识别船坞滚动条，保留旧扫描结果')
        if thumb[-1] - thumb[0] + 1 != len(thumb):
            if allow_occlusion:
                return None
            raise MindCalculatorScanError('船坞滚动条被遮挡，无法确认位置，保留旧扫描结果')
        if thumb[0] == 0:
            # 通用按钮框从 76 开始，国服滑块顶部可能延伸到 67；抓取须使用完整滑块中心。
            x1, y1, x2, _ = DOCK_SCROLL.area
            above = self.device.image[y1 - 12:y1, x1:x2]
            mask = np.max(color_similarity_2d(above, DOCK_SCROLL.color), axis=1) > DOCK_SCROLL.color_threshold
            count = 0
            for matched in mask[::-1]:
                if not matched:
                    break
                count += 1
            thumb = np.concatenate((np.arange(-count, 0), thumb))
        return thumb

    @staticmethod
    def _grab(touch, point):
        """横移激活拖动；抓滑块后保持触点在左侧，避开游戏的触控菱形。"""
        touch.down(point)
        if point[0] > 1230:
            # 游戏触控特效约宽 64 px；拖动已由滑块捕获，横移不会改变滚动位置。
            touch.move((point[0] - 64, point[1]))
        else:
            touch.move((point[0] + 20, point[1]))
            touch.move(point)

    def _scan_pages(self, names, levels):
        """按住滚动条逐帧测位移；仍按住时校正，停准后才松手并读取三排。"""
        if self.config.Emulator_ControlMethod not in LIVE_DRAG_METHODS:
            raise MindCalculatorScanError('实时船坞扫描需要 MaaTouch 等支持持续触控的控制方式')
        merger = ScanMerger()
        previous = None
        stable = Timer(.6, count=3).start()
        hold = None
        target = pitch = origin_y = None
        phase = 'top'
        edge_confirmed = False
        mode = None
        gain = None
        bar_origin = None
        checkpoint = None
        recovering = False
        recovery = None
        bar_speed = 4.
        last_speed = None
        actions = 0
        page = 0
        progress = Timer(20, count=10).start()
        occlusion = Timer(1, count=3).start()
        with self.device.live_drag(name='心智单元船坞实时定位') as touch:
            while True:
                touch.check_error()
                self.device.screenshot()
                frame_point = touch.point
                if not self.appear(page_dock.check_button):
                    touch.up()
                    if self.handle_popup_confirm():
                        previous = None
                        stable.reset()
                        continue
                    raise MindCalculatorScanError('实时拖动期间离开船坞，保留旧扫描结果')
                image = Image.fromarray(self.device.image)
                # 不等停稳、不松手：每张新截图都先计算与上一帧的实际重叠位移。
                try:
                    moved = merger.advance(image)
                except ValueError as exc:
                    if recovering:
                        if recovery.reached():
                            raise MindCalculatorScanError('快拖回退后仍无法确认原位置，保留旧扫描结果') from exc
                        continue
                    if mode == 'bar' and touch.active and checkpoint is not None and checkpoint[2] > 1:
                        # 快拖丢失重叠时仍抓着原滑块，回到最后已核实的触点坐标，缩小步长重扫。
                        bar_speed = max(1., min(checkpoint[2] / 2, 150 / (gain or 150)))
                        touch.glide(checkpoint[0], speed=bar_speed)
                        recovering = True
                        recovery = Timer(3, count=3).start()
                        logger.warning(f'船坞快拖失去重叠，平滑回退确认位置；滑块速度降至 {bar_speed:.1f}px/s')
                        continue
                    raise MindCalculatorScanError(str(exc)) from exc
                if moved:
                    progress.reset()
                    # 只有已核实的位移才清除连击历史，停滞仍沿用设备检测和超时恢复。
                    self.device.click_record_clear()
                    self.device.stuck_record_clear()
                    logger.attr('船坞实际位移', f'{moved:+d}px，累计 {merger.offset}px')
                    if mode == 'bar' and not recovering and checkpoint is not None:
                        bar_speed = min(72., 1.5 * bar_speed)
                thumb = self._scroll_thumb(allow_occlusion=touch.active and mode == 'bar')
                if thumb is None:
                    # 按下时的特效可能残留几帧；只等待完整黄色滑块，不猜位置或继续移动。
                    if occlusion.reached():
                        raise MindCalculatorScanError('船坞滚动条持续被遮挡，无法确认位置，保留旧扫描结果')
                    continue
                occlusion.reset()
                if recovering:
                    # 截图与触控并行，返回点可能位于曝光前后；重叠证据恢复后使用实际测得位置。
                    touch.hold()
                    recovering = False
                    checkpoint = None
                    logger.attr('船坞快拖回退', f'已恢复 {merger.offset}px')
                at_top = thumb[0] <= 1
                at_bottom = thumb[-1] >= DOCK_SCROLL.total - 2
                if phase == 'top' and touch.active and mode == 'bar':
                    # 边缘像素可能对应多像素内容；把仍按住的触点移过滑块中心极限，确认夹紧。
                    if at_top and touch.point[1] <= DOCK_SCROLL.area[1] + len(thumb) / 2 - 16:
                        edge_confirmed = True
                if mode == 'bar' and touch.active and bar_origin is not None:
                    pointer_delta = frame_point[1] - bar_origin[1]
                    actual_delta = merger.offset - bar_origin[0]
                    if pointer_delta and actual_delta * pointer_delta > 0:
                        gain = abs(actual_delta / pointer_delta)
                if phase == 'scan' and pitch is not None and not at_bottom and (
                        mode == 'fine' or abs(target - merger.offset) <= max(96, 2 * (gain or 0))):
                    corrected = row_scroll_offset(self.device.image, origin_y, pitch, merger.offset)
                    if corrected is not None and corrected != merger.offset:
                        logger.attr('船坞行对齐', f'{merger.offset}px → {corrected}px')
                        merger.offset = corrected
                reached = edge_confirmed if phase == 'top' else target is None or abs(target - merger.offset) <= 1 or at_bottom
                if reached:
                    if touch.active:
                        if hold is None:
                            touch.hold()
                            hold = Timer(.4, count=2).start()
                        if not hold.reached():
                            continue
                        touch.up()
                        mode = None
                        previous = None
                        stable.reset()
                    pixels = self.device.image[65:720, 80:1230]
                    if previous is None or np.mean(np.abs(pixels.astype(float) - previous.astype(float))) > 1:
                        previous = pixels.copy()
                        stable.reset()
                        continue
                    if not stable.reached():
                        continue
                    if phase == 'top':
                        # 回顶期间也持续追踪，但完整扫描的绝对坐标从已确认顶部重新开始。
                        merger = ScanMerger()
                        merger.advance(image)
                        phase = 'scan'
                        edge_confirmed = False
                        logger.attr('船坞位置', '已确认顶部')
                    page += 1
                    try:
                        cards = recognize_cards(image, f'自动扫描第 {page} 页', name_ocr=names, level_ocr=levels)
                        merger.add(image, cards)
                    except ValueError as exc:
                        raise MindCalculatorScanError(str(exc)) from exc
                    if page == 1:
                        progress.reset()
                    if len(merger.slots) > 5000:
                        raise MindCalculatorScanError('识别卡片超过 5000，请核对船坞布局，保留旧扫描结果')
                    logger.attr('扫描页', page)
                    logger.attr('识别卡片', len(merger.slots))
                    if at_bottom:
                        break
                    rows = sorted({card.y for card in cards})
                    logger.attr('船坞卡片行', rows)
                    if len(rows) != 3:
                        raise MindCalculatorScanError('船坞未显示三排完整船名，无法安全按三排推进')
                    if pitch is None:
                        distances = np.diff(rows)
                        # 卡框圆角与稀有度会使色带起点相差 1–2 px；不能把此误差累积为滚动距离。
                        pitch = int(CARD_GRIDS.delta[1])
                        origin_y = rows[0]
                        if max(abs(distances - pitch)) > 3:
                            raise MindCalculatorScanError('无法确认船坞实际行距，保留旧扫描结果')
                        logger.attr('船坞行距', f'{pitch}px，三排 {3 * pitch}px')
                    if any(abs(y - origin_y - index * pitch) > 6 for index, y in enumerate(rows)):
                        raise MindCalculatorScanError('三排位移与卡片行位置不一致，保留旧扫描结果')
                    target = page * 3 * pitch
                    actions = 0
                    checkpoint = None
                    logger.attr('船坞三排目标', f'{target}px')
                    if page >= 500:
                        raise GameStuckError('船坞扫描未能到达底部')
                if progress.reached():
                    raise GameStuckError('船坞扫描画面没有继续滚动，尚未确认到底')
                if actions >= 1024:
                    raise MindCalculatorScanError('实时船坞定位未能到达三排目标，保留旧扫描结果')
                hold = None
                previous = None
                stable.reset()
                remaining = -1 if phase == 'top' else target - merger.offset
                fine = phase == 'scan' and (mode == 'fine' or remaining < 0 or abs(remaining) <= max(20, gain or 0))
                if touch.active and (mode == 'bar' and fine or mode == 'fine' and not fine):
                    # 大船坞的一像素滑块会对应多像素卡面；卡面内用已激活的同一触点做最后校正。
                    touch.up()
                    mode = None
                if not touch.active:
                    mode = 'fine' if fine else 'bar'
                    point = (1170, 400) if fine else (
                        (DOCK_SCROLL.area[0] + DOCK_SCROLL.area[2]) // 2,
                        round(DOCK_SCROLL.area[1] + float(np.mean(thumb))))
                    self._grab(touch, point)
                    checkpoint = None
                    bar_speed = 4.
                    last_speed = None
                    bar_origin = (merger.offset, point[1]) if mode == 'bar' else None
                    logger.attr('船坞实时控制', '卡面持续触控校正' if fine else '按住滚动条逐帧定位')
                    continue
                if mode == 'bar':
                    # 先用一像素确认倍率；按截图证据渐增速度，触点后台始终逐像素移动。
                    if phase == 'top' and (at_top or gain):
                        destination = (touch.point[0], int(DOCK_SCROLL.area[1] + len(thumb) / 2 - 16))
                    elif gain:
                        distance = max(1, int((remaining - max(32, 2 * gain)) / gain))
                        destination = (touch.point[0], frame_point[1] + distance)
                    else:
                        destination = (touch.point[0], touch.point[1] + (1 if remaining > 0 else -1))
                    speed = min(bar_speed, (600 if phase == 'top' else min(600, max(32, 2 * abs(remaining)))) / gain) if gain else bar_speed
                    if last_speed is None or speed > last_speed * 1.25 or speed < last_speed * .8:
                        logger.attr('船坞拖动速度', f'{speed:.1f}px/s')
                        last_speed = speed
                    checkpoint = (frame_point, merger.offset, speed)
                    touch.glide(destination, speed=speed)
                else:
                    distance = int(np.clip(remaining, -120, 120))
                    destination = (touch.point[0], frame_point[1] - distance)
                    if not 110 <= destination[1] <= 630:
                        touch.up()
                        continue
                    touch.glide(destination, speed=min(120, max(8, 3 * abs(remaining))))
                actions += 1
        return merger.ships()
