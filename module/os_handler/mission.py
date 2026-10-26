"""大世界任务管理模块。

处理大世界（Operation Siren）的任务系统，包括任务提交、
任务奖励领取、任务结算界面导航以及月度 Boss 任务的特殊处理。
通过 OCR 和图像匹配检测任务状态，自动完成任务流程。
"""
from datetime import timedelta

from module.base.timer import Timer
from module.base.utils import *
from module.config.utils import DEFAULT_TIME, get_os_next_reset
from module.logger import logger
from module.map_detection.utils import fit_points
from module.os.assets import GLOBE_GOTO_MAP
from module.os.globe_detection import GLOBE_MAP_SHAPE
from module.os.globe_operation import GlobeOperation
from module.os.globe_zone import Zone, ZoneManager
from module.os_handler.assets import *


class MissionAtCurrentZone(Exception):
    """当前海域有任务异常。"""
    pass


class MissionHandler(GlobeOperation, ZoneManager):
    _os_mission_submitted = False

    def _os_find_checkout_offset_skip_monthly_boss(self, checkout_offset):
        """
        查找非月度Boss的任务结算行。

        Args:
            checkout_offset (tuple): 初始结算按钮偏移量。

        Returns:
            tuple | None: 非月度Boss任务行的偏移量，如果未找到则返回 None。
        """
        # 直接扫描全屏定位「查看」，不依赖固定行距。
        # 旧实现按 110 像素步进，行距不同时会漏掉后面的任务行。
        for y, _ in self._os_mission_scan_rows(MISSION_CHECKOUT, similarity=0.78):
            row_offset = area_offset(checkout_offset, (0, y - MISSION_CHECKOUT.area[1]))
            if not self.appear(MISSION_MONTHLY_BOSS, offset=row_offset):
                return row_offset

        return None

    def get_mission_zone(self):
        """
        获取任务所在的海域。

        Returns:
            Zone: 任务海域对象。
        """
        area = (341, 72, 1217, 648)
        # 黄色 `!` 的点
        image = color_similarity_2d(self.image_crop(area, copy=False), color=(255, 207, 66))
        points = np.array(np.where(image > 235)).T[:, ::-1]
        if not len(points):
            logger.warning('无法在大世界任务地图中找到任务')

        point = fit_points(points, mod=(1000, 1000), encourage=5) + (0, 11)
        # 海域位置
        # (2570, 1694) 是 os_globe_map.png 的形状
        point *= np.array(GLOBE_MAP_SHAPE) / np.subtract(area[2:], area[:2])

        zone = self.camera_to_zone(tuple(point))
        return zone

    def is_in_os_mission(self):
        return self.appear(MISSION_CHECK, offset=(20, 20))

    def os_mission_enter(self, skip_siren_mission=False, skip_first_screenshot=True):
        """
        进入任务列表并领取任务奖励。

        Args:
            skip_siren_mission (bool): 是否跳过塞壬研究任务。
            skip_first_screenshot (bool): 是否跳过第一次截图。

        Returns:
            tuple: MISSION_CHECKOUT 的按钮偏移量。

        Pages:
            in: MISSION_ENTER
            out: MISSION_CHECK
        """
        logger.info('[大世界处理-任务] 进入大世界任务')
        checkout_offset = (-20, -20, 20, 20)
        confirm_timer = Timer(2, count=6).start()
        for _ in self.loop():
            # 结束
            # 任务行可能不含「领取」，需同时扫描塞壬研究行，否则只剩塞壬研究时
            # 会误判成「没有任务」而提前退出。
            if self.is_in_os_mission() \
                    and not self._os_mission_scan_rows(MISSION_FINISH) \
                    and not self._os_mission_scan_rows(MISSION_SIREN_RESEARCH) \
                    and not self.match_template_color(MISSION_CHECKOUT, offset=checkout_offset, similarity=0.78):
                # 未找到任务，等待确认。任务可能加载较慢。
                if confirm_timer.reached():
                    logger.info('[大世界处理-任务] 未找到大世界任务')
                    break
            elif self.is_in_os_mission() \
                    and self.match_template_color(MISSION_CHECKOUT, offset=checkout_offset, similarity=0.78):
                # 找到至少一个任务
                logger.info('[大世界处理-任务] 至少找到一个大世界任务')
                break
            else:
                confirm_timer.reset()

            # 点击
            # 保留海域中断自律并退出战斗后，结算「合计获得奖励」会延迟弹出，
            # 可能到进入任务列表这一步才盖住界面；不处理会一直等不到任务列表
            if self.appear_then_click(AUTO_SEARCH_REWARD, offset=(50, 50), interval=3):
                confirm_timer.reset()
                continue
            if self.appear_then_click(MISSION_ENTER, offset=(200, 5), interval=5):
                confirm_timer.reset()
                continue
            if skip_siren_mission:
                # 扫描全屏比对行坐标，不依赖固定行距，避免漏判导致误领塞壬研究
                if self._os_mission_claim_skip_siren():
                    confirm_timer.reset()
                    continue
                # 剩余任务全是塞壬研究：没有可领取项，退出循环避免空转
                if self._os_mission_scan_rows(MISSION_SIREN_RESEARCH):
                    logger.info('[大世界处理-任务] 剩余任务均为塞壬研究，跳过不领取')
                    break
            else:
                if self.appear_then_click(MISSION_FINISH, offset=checkout_offset, interval=2):
                    confirm_timer.reset()
                    continue

            # 弹窗与结算处理对所有分支生效，跳过塞壬研究时同样需要处理
            if self.handle_popup_confirm('MISSION_FINISH'):
                confirm_timer.reset()
                continue
            if self.handle_map_get_items():
                confirm_timer.reset()
                continue
            if self.handle_info_bar():
                confirm_timer.reset()
                continue

            if self.appear_then_click(GLOBE_GOTO_MAP, offset=(20, 20), interval=2):
                # 意外进入地球仪
                confirm_timer.reset()
                continue
        return checkout_offset

    def _os_mission_scan_rows(self, button, similarity=0.85):
        """扫描全屏，返回某个按钮所有匹配位置的坐标。

        用于在不依赖固定行距的前提下，一次性定位任务列表中的全部目标行。
        相比逐行累加偏移量，本方法不会因行距变化而漏判。

        Args:
            button (Button): 待扫描的按钮，需有可加载的模板图像。
            similarity (float): 模板匹配相似度阈值。

        Returns:
            list[tuple[int, int]]: 每个匹配位置的 (y, x)，按 y 升序排列。

        Pages:
            in: MISSION_CHECK
            out: MISSION_CHECK
        """
        similarity = lower_template_match_similarity(similarity)
        button.ensure_template()
        image = getattr(button, 'image', None)
        # GIF 模板（多帧）不做多目标扫描，仅支持静态模板
        if not isinstance(image, np.ndarray):
            return []

        result = cv2.matchTemplate(self.device.image, image, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result > similarity)
        if not len(ys):
            return []

        # 按相似度降序聚类，避免同一按钮的相邻像素被重复计入
        order = np.argsort(-result[ys, xs])
        height, width = image.shape[:2]
        rows = []
        for index in order:
            y, x = int(ys[index]), int(xs[index])
            if not any(abs(row[0] - y) <= height // 2 and abs(row[1] - x) <= width // 2 for row in rows):
                rows.append((y, x))
        return sorted(rows)

    def _os_mission_claim_skip_siren(self):
        """只领取非塞壬研究任务，跳过所有带「研究」的任务行。

        塞壬研究任务使用黄币兑换紫币，开启「不做塞壬研究任务」后应保持不领取。
        现有实现依赖「命中塞壬行后偏移 +110 像素」的逐行推进，行距变化或塞壬研究
        不在首行时都会漏判，导致误领。本方法改为扫描全屏后按 y 坐标比对：
        与任意塞壬研究行纵向重叠的「领取」按钮一律跳过。

        Returns:
            bool: 是否点击了某个领取按钮。

        Pages:
            in: MISSION_CHECK
            out: MISSION_CHECK
        """
        siren_rows = self._os_mission_scan_rows(MISSION_SIREN_RESEARCH)
        finish_rows = self._os_mission_scan_rows(MISSION_FINISH)

        for y, _ in finish_rows:
            # 「研究」文字与同一行「领取」按钮的中心纵向相差约 21 像素，取 45 像素容差
            if any(abs(y - siren_y) < 45 for siren_y, _ in siren_rows):
                logger.info('[大世界处理-任务] 跳过塞壬研究任务')
                continue
            # 名称带行号，避免多行领取共用同名按钮时触发点击洪水保护
            target = MISSION_FINISH.move((0, y - MISSION_FINISH.area[1]), name=f'MISSION_FINISH_Y{y}')
            self.device.click(target)
            logger.info('[大世界处理-任务] 领取非塞壬研究任务')
            return True

        return False

    def os_mission_quit(self):
        """
        退出任务列表。
        """
        logger.info('[大世界处理-任务] 退出大世界任务')
        for _ in self.loop():
            # 结束
            # 有时任务弹窗没有黑色模糊背景
            # MISSION_QUIT 和 is_in_map 同时出现
            if not self.appear(MISSION_QUIT, offset=(20, 20)):
                if self.is_in_map():
                    break
            # 点击
            if self.appear_then_click(MISSION_QUIT, offset=(20, 20), interval=3):
                continue

    def os_get_next_mission(self, skip_siren_mission=False):
        """
        获取下一个大世界任务。

        点击 MISSION_CHECKOUT 后，AL 会直接切换到目标海域，而非显示无意义的地图。
        如果已在目标海域，则显示信息栏并关闭任务列表。

        Args:
            skip_siren_mission (bool): 是否跳过塞壬研究任务。

        Returns:
            str: pinned_at_mission_zone、already_at_mission_zone、pinned_at_archive_zone，
                如果没有更多任务则返回 False。
        """
        checkout_offset = self.os_mission_enter(skip_siren_mission=skip_siren_mission)
        checkout_offset = self._os_find_checkout_offset_skip_monthly_boss(checkout_offset)
        if checkout_offset is None:
            logger.info('[大世界处理-任务] 没有更多非月度Boss的大世界任务')
            self.os_mission_quit()
            return False

        if self.is_in_opsi_explore():
            logger.info('[大世界处理-任务] 每月开荒正在运行，仅接取任务并领取奖励')
            self.os_mission_quit()
            return False

        logger.info('[大世界处理-任务] 接取大世界任务')
        for _ in self.loop():
            # 结束
            if self.is_zone_pinned():
                if self.get_zone_pinned_name() == 'ARCHIVE':
                    logger.info('[大世界处理-任务] 固定在档案区域')
                    self.globe_enter(zone=self.name_to_zone(72))
                    return 'pinned_at_archive_zone'
                else:
                    logger.info('[大世界处理-任务] 固定在任务区域')
                    self.globe_enter(zone=self.name_to_zone(72))
                    return 'pinned_at_mission_zone'
            if self.is_in_map() and self.info_bar_count():
                logger.info('[大世界处理-任务] 已在任务区域')
                return 'already_at_mission_zone'

            # 保留海域中断自律后的结算弹窗也会延迟到这一步才弹出，先点掉
            if self.appear_then_click(AUTO_SEARCH_REWARD, offset=(50, 50), interval=3):
                continue
            if self.appear_then_click(MISSION_CHECKOUT, offset=checkout_offset, interval=2, similarity=0.78):
                continue
            if self.handle_popup_confirm('OS_MISSION_CHECKOUT'):
                # 弹窗：退出当前海域后潜艇将撤退
                continue

    def os_mission_overview_accept(self, skip_siren_mission=False, skip_first_screenshot=True):
        """
        在任务总览中接受所有任务。

        Args:
            skip_siren_mission (bool): 是否跳过塞壬研究任务。
            skip_first_screenshot (bool): 是否跳过第一次截图。

        Returns:
            bool: 所有任务已接受或未找到任务时返回 True，无法接受更多任务时返回 False。

        Pages:
            in: is_in_map
            out: is_in_map
        """
        logger.hr('大世界任务总览接取', level=1)
        # is_in_map
        self.os_map_goto_globe(unpin=False)
        # is_in_globe
        self.ui_click(MISSION_OVERVIEW_ENTER, check_button=MISSION_OVERVIEW_CHECK,
                      offset=(200, 20), retry_wait=3, additional=self.handle_manjuu,
                      skip_first_screenshot=True)

        timeout = 5
        accept_button_timer = Timer(timeout)
        self.interval_timer[MISSION_OVERVIEW_ACCEPT_SINGLE.name] = accept_button_timer
        self.interval_timer[MISSION_OVERVIEW_ACCEPT.name] = accept_button_timer
        # MISSION_OVERVIEW_CHECK
        success = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 结束
            if self.handle_manjuu():
                continue
            if self.info_bar_count():
                if skip_siren_mission:
                    logger.info('[大世界处理-任务] 无法接受任务，存在多个同名塞壬研究任务')
                    success = True
                else:
                    logger.info('[大世界处理-任务] 无法接受任务，已达任务数量上限')
                    success = False
                break
            if self.appear(MISSION_OVERVIEW_EMPTY, offset=(20, 20)):
                logger.info('[大世界处理-任务] 无更多任务可接受')
                success = True
                break

            if self.appear_then_click(MISSION_OVERVIEW_ACCEPT, offset=(20, 20), interval=2):
                self.interval_reset(MISSION_OVERVIEW_ACCEPT_SINGLE)
                continue
            if self.appear_then_click(MISSION_OVERVIEW_ACCEPT_SINGLE, offset=(20, 20), interval=2):
                self.interval_reset(MISSION_OVERVIEW_ACCEPT)
                continue

        # is_in_globe
        self.ui_back(appear_button=MISSION_OVERVIEW_CHECK, check_button=self.is_in_globe,
                     skip_first_screenshot=True)
        # is_in_map
        self.os_globe_goto_map()
        return success

    def is_in_opsi_explore(self):
        """
        判断任务每月开荒是否正在调度中。

        Returns:
            bool: 每月开荒是否正在调度中。
        """
        enable = self.config.is_task_enabled('OpsiExplore')
        next_run = self.config.cross_get(keys='OpsiExplore.Scheduler.NextRun', default=DEFAULT_TIME)
        next_reset = get_os_next_reset()
        logger.attr('大世界下次重置', next_reset)
        logger.attr('每月开荒', (enable, next_run))
        # -12 小时以处理夏令时
        # `next_run` 可能在夏令时之前计算，但现在是夏令时
        # 2023-03-14 11:15:28.423 | INFO | [OpsiNextReset] 2023-04-01 03:00:00
        # 2023-03-14 11:15:28.425 | INFO | [OpsiExplore] (True, datetime.datetime(2023, 4, 1, 2, 0))
        # 2023-03-14 11:15:28.426 | INFO | 每月开荒仍在运行，仅接取任务...
        if enable and next_run < next_reset - timedelta(hours=12):
            logger.info('每月开荒仍在运行，仅接取任务。每月开荒访问所有区域时会完成这些任务，不必担心遗漏。')
            return True
        else:
            logger.info('未处于每月开荒，可以执行大世界每日')
            return False
