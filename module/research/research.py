"""
科研任务主处理器。

本模块实现科研系统的完整自动化流程，包括：
- 检测已完成的科研项目并领取奖励
- 根据用户配置的筛选规则选择最优科研项目
- 启动科研项目并加入科研队列
- 填充科研队列（最多 5 个队列槽位 + 1 个队列外项目）
- 处理特殊项目类型（E 系列需拆解装备、T 系列需完成委托）
- 延迟科研策略：资源不足时等待队列自然消耗

核心类 `RewardResearch` 继承自 `ResearchSelector`（项目筛选与选择）、
`ResearchQueue`（队列管理）和 `StorageHandler`（装备拆解），
是 `AzurLaneAutoScript` 中 `research` 任务的执行入口。

术语对照：
    科研队列(Research Queue): 最多容纳 5 个排队项目的队列
    第 6 个项目: 不在队列中、直接运行的额外科研项目
    强制模式(enforce): 当筛选结果为空时，放宽条件选择项目
"""
from datetime import timedelta

import numpy as np

from module.base.timer import Timer
from module.base.utils import rgb2gray
from module.config.time_source import now as current_time
from module.exception import GameTooManyClickError
from module.logger import logger
from module.ocr.ocr import Ocr
from module.research.assets import *
from module.research.project import get_research_finished
from module.research.rqueue import ResearchQueue, parse_display_time
from module.research.selector import RESEARCH_ENTRANCE, ResearchSelector
from module.server_maintain import query_maintain_today
from module.storage.storage import StorageHandler
from module.ui.assets import RESEARCH_CHECK
from module.ui.page import page_research

# 第 6 个项目（队列外，主页面中位卡片）的显示时长，双通道识别，原理同 OCR_QUEUE_TIME_*
OCR_SIXTH_TIME_BRIGHT = Ocr(RESEARCH_LAB_DURATION_REMAIN, letter=(255, 255, 255), threshold=128,
                            alphabet='0123456789:IDSB', name='OCR_SIXTH_TIME_BRIGHT')
OCR_SIXTH_TIME_DIM = Ocr(RESEARCH_LAB_DURATION_REMAIN, letter=(255, 255, 255), threshold=280,
                         alphabet='0123456789:IDSB', name='OCR_SIXTH_TIME_DIM')

# 「维护当天科研」开启时，维护开始前多久跑本次收光 + 填满
RESEARCH_MAINTAIN_LEAD_MINUTES = 60


def batch_all_completed(times, sixth):
    """
    判断批量模式的收获时机是否到来。

    Args:
        times (list[timedelta | None]): 队列 5 槽的显示时长（get_queue_display_times 的返回值）。
        sixth (timedelta | None): 第 6 个项目的显示时长（get_sixth_display_time 的返回值）。

    Returns:
        bool: True 表示全部完成（无任何正时长），可以一次性收取。
    """
    for time in times:
        if time is not None and time.total_seconds() > 0:
            return False
    if sixth is not None and sixth.total_seconds() > 0:
        return False
    return True


def batch_total_remaining(times, sixth):
    """
    计算批量模式距全部完成的剩余总时长。

    队列按 FIFO 顺序执行：队首显示剩余倒计时，等待槽显示项目总时长，
    已完成槽显示 0，非空值相加恰为管线剩余总时长，与槽位顺序无关；
    第 6 个项目排在队尾（等待队列腾出后补位），其显示时长同样累加。
    若游戏实际并行执行导致估计偏差，下一轮调度重新快照即可自愈。

    Args:
        times (list[timedelta | None]): 队列 5 槽的显示时长，None 为空槽。
        sixth (timedelta | None): 第 6 个项目的显示时长，None 为不存在。

    Returns:
        timedelta: 剩余总时长（可能为 0）。
    """
    total = timedelta()
    for time in times:
        if time is not None:
            total += time
    if sixth is not None:
        total += sixth
    return total


class RewardResearch(ResearchSelector, ResearchQueue, StorageHandler):
    """
    科研任务主处理器，负责科研项目的完整生命周期管理。

    通过多重继承组合以下能力：
    - ResearchSelector: 科研项目检测、筛选和优先级排序
    - ResearchQueue: 科研队列的添加、状态检测和奖励领取
    - StorageHandler: 装备拆解（用于 E 系列科研的前提条件）

    科研流程概览：
    1. 导航到科研页面
    2. 进入队列，领取已完成项目的奖励
    3. 处理挂起的 T 系列委托科研
    4. 领取第 6 个（队列外）项目的奖励
    5. 循环填充队列直到 5 个槽位用满
    6. 计算下次调度时间

    批量模式（Research_BatchMode）下流程改为：
    全部科研项目完成后才一次性收取（队列 5 槽 + 第 6 个），
    再一次性填满 6 个；NextRun 由逐槽显示时长求和精确推算，
    过渡态（用户手动加入/收取项目）下逐轮重新快照自然收敛。

    Attributes:
        _research_project_offset (int): 项目列表在屏幕上的偏移量，
            用于在点击非中央位置的项目时进行索引修正。
        _research_finished_index (int): 已完成项目的索引（0-4），
            用于定位已完成项目在屏幕上的位置。
        research_project_started (ResearchProject): 最近一次成功启动的
            科研项目对象，未启动项目时为 None。
        enforce (bool): 是否处于强制模式。当筛选结果为空时，
            自动切换到强制模式以放宽条件选择项目。
        end_time (datetime): 队列中第一个科研项目的预计完成时间，
            用于计算任务调度延迟。
    """
    _research_project_offset = 0
    _research_finished_index = 2
    research_project_started = None  # ResearchProject 对象
    enforce = False
    end_time = None

    def research_has_finished(self):
        """
        已完成的科研项目应自动聚焦到中央位置，但有时由于未知的游戏 bug 未能实现。

        Returns:
            bool: 是否有已完成的科研项目
        """
        index = get_research_finished(self.device.image)
        if index is not None:
            logger.attr('科研已完成', index)
            self._research_finished_index = index
            return True
        else:
            return False

    def research_reset(self, drop=None, skip_first_screenshot=True):
        """
        重置科研项目列表，刷新可用的科研项目。

        仅在重置功能可用时执行（RESET_AVAILABLE 按钮可见）。
        重置后项目列表将更新，之前的筛选结果失效。

        Args:
            drop (DropImage): 掉落记录对象，记录重置前的截图。
            skip_first_screenshot (bool): 是否跳过首次截图。

        Returns:
            bool: 重置是否成功执行。若重置功能不可用则返回 False。
        """
        if not self.appear(RESET_AVAILABLE, threshold=10):
            logger.info('[科研-重置] 科研重置不可用')
            return False

        logger.info('[科研-重置] 科研重置')
        drop.add(self.device.image)
        executed = False
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear_then_click(RESET_AVAILABLE, interval=10, threshold=10):
                continue
            if self.handle_popup_confirm('RESEARCH_RESET'):
                executed = True
                continue

            # 结束条件
            if executed and self.is_in_research():
                self.ensure_no_info_bar(timeout=3)  # 刷新成功
                self.ensure_research_stable()
                break

        self._research_project_offset = 0
        return True

    def research_enforce(self, drop=None, add_queue=True):
        """
        强制选择一个科研项目，忽略部分筛选条件。

        当正常筛选结果为空时，切换到强制模式并使用宽松的筛选规则
        重新选择项目。确保始终有项目在运行。

        Args:
            drop (DropImage): 掉落记录对象。
            add_queue (bool): 是否加入队列。
                第 6 个项目无法加入队列，因此需要此开关。

        Returns:
            bool: 是否成功选择项目。
        """
        if not self.enforce:
            logger.info('[科研-强制] 强制选择科研项目')
            self.enforce = True
            return self.research_select(self.research_sort_filter(self.enforce),
                                        drop=drop, add_queue=add_queue)
        return True

    def research_select(self, priority, drop=None, add_queue=True):
        """
        Args:
            priority (list): ResearchProject 对象和预设字符串的列表，
                如 [object, object, object, 'reset']
            drop (DropImage):
            add_queue (bool): 是否加入队列。
                第 6 个项目无法加入队列，因此需要此开关。

        Returns:
            bool: 如果已重置则返回 False
        """
        if not len(priority):
            logger.info('[科研-选择] 没有符合当前筛选条件的科研项目')
            return self.research_enforce(drop=drop, add_queue=add_queue)
        for project in priority:
            # 优先级示例：['reset', 'shortest']
            if project == 'reset':
                if self.research_reset(drop=drop):
                    return False
                else:
                    continue

            if isinstance(project, str):
                # 优先级示例：['shortest']
                if project == 'shortest':
                    self.research_select(self.research_sort_shortest(self.enforce),
                                         drop=drop, add_queue=add_queue)
                elif project == 'cheapest':
                    self.research_select(self.research_sort_cheapest(self.enforce),
                                         drop=drop, add_queue=add_queue)
                else:
                    logger.warning(f'[科研-选择] 未知的选择方法: {project}')
                return True
            elif project.genre.upper() in ['C', 'T'] and not self.enforce:
                return self.research_enforce(drop=drop, add_queue=add_queue)
            else:
                # 优先级示例：[ResearchProject, ResearchProject,]
                ret = self.research_project_start_with_requirements(project, add_queue=add_queue)
                if ret:
                    return True
                elif ret is not None and self.config.Research_RemainingCommissions > 0:
                    logger.info('[科研-延迟] 因T类科研延迟研究')
                    return True
                elif ret is not None and self.research_delay_check():
                    logger.info('[科研-延迟] 资源不足且队列未空，延迟研究')
                    return True
                else:
                    continue

        logger.info('[科研-选择] 没有启动科研项目')
        return self.research_enforce(drop=drop, add_queue=add_queue)

    def research_delay_check(self):
        """
        检查是否允许延迟科研。

        Returns:
            bool: 是否允许延迟科研
        """
        if self.config.Research_AllowDelay:
            slot = self.get_queue_slot()
            if slot < 4:
                return True
            if slot == 4:
                if self.end_time <= current_time():
                    return True
                elif self.end_time + timedelta(minutes=-10) > current_time():
                    return True

        return False

    def research_project_start(self, project, add_queue=True, skip_first_screenshot=True):
        """
        启动指定项目并将其加入科研队列。

        Args:
            project (ResearchProject, int): 项目对象或项目索引（0 到 4）。
            add_queue (bool): 是否加入队列。
                第 6 个项目无法加入队列，因此需要此开关。
            skip_first_screenshot:

        Returns:
            bool: 启动是否成功。
            None: 要启动的项目不在已知项目列表中。

        Pages:
            in: is_in_research
            out: is_in_research
        """
        logger.hr('开始科研项目', level=2)
        logger.info(f'[科研-启动] 科研项目: {project}')
        if isinstance(project, int):
            index = project
        elif project in self.projects:
            index = self.projects.index(project)
        else:
            logger.warning(f'[科研-启动] 要启动的项目: {project} 不在已知项目列表中')
            return None
        logger.info(f'[科研-启动] 科研项目索引: {index}')
        self.interval_clear([RESEARCH_START])
        self.popup_interval_clear()
        available = False
        click_timer = Timer(10)
        click_count = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            max_rgb = np.max(rgb2gray(self.image_crop(RESEARCH_UNAVAILABLE, copy=False)))

            # 此处不使用 interval，RESEARCH_CHECK 已在 5 秒前出现过
            if click_timer.reached() and self.is_in_research():
                i = (index - self._research_project_offset) % 5
                logger.info(f'[科研-启动] 项目偏移: {self._research_project_offset}, 项目 {index} 位于 {i}')
                self.device.click(RESEARCH_ENTRANCE[i])
                self.ensure_research_stable()
                click_count += 1
                click_timer.reset()
                continue
            if max_rgb > 235 and self.appear_then_click(RESEARCH_START, offset=(5, 20), interval=10):
                available = True
                continue
            if self.handle_popup_confirm('RESEARCH_START'):
                continue

            # 结束条件
            if click_count >= 3:
                logger.error('[科研-启动] 尝试3次后仍无法启动科研项目，'
                             '可能是因为已有科研在运行但条件未满足，'
                             '或科研已完成')
                raise GameTooManyClickError
            if self.appear(RESEARCH_STOP, offset=(20, 20)):
                # RESEARCH_STOP 是半透明按钮，颜色会随背景变化
                if add_queue:
                    if not self.research_queue_add():
                        self.research_project_started = None
                        self._research_project_offset = (index - 2) % 5
                        return False
                else:
                    self.research_detail_quit()
                # self.ensure_no_info_bar(timeout=3)  # 科研已启动
                self.research_project_started = project
                self._research_project_offset = (index - 2) % 5
                return True
            if not available and max_rgb <= 235 \
                    and self.appear(RESEARCH_UNAVAILABLE, offset=(5, 20)):
                logger.info('[科研-启动] 资源不足，无法启动此项目')
                self.research_detail_quit()
                self.research_project_started = None
                self._research_project_offset = (index - 2) % 5
                return False

    def research_project_start_with_requirements(self, project, add_queue=True):
        """
        启动指定项目并将其加入科研队列，同时处理项目所需的前提条件。

        Args:
            project (ResearchProject, int): 项目对象或项目索引（0 到 4）。
            add_queue (bool): 是否加入队列。
                第 6 个项目无法加入队列，因此需要此开关。

        Returns:
            bool: 启动是否成功。
            None: 要启动的项目不在已知项目列表中。

        Pages:
            in: is_in_research
            out: is_in_research
        """
        # 项目索引，直接调用
        if isinstance(project, int):
            return self.research_project_start(project, add_queue=add_queue)
        elif project.genre == 'E' and project.equipment_amount > 0:
            logger.info(f'[科研-E系列] 准备启动E系列科研: {project} '
                        f'并拆解 {project.equipment_amount} 个装备')
            # 启动项目
            self.research_project_start(project, add_queue=False)
            # 拆解装备
            self.storage_disassemble_equipment(amount=project.equipment_amount)
            # 返回科研界面
            self.ui_ensure(page_research)
            self.research_project_list_init()
            # 加入队列
            result = self.research_project_start(project, add_queue=add_queue)
            if result is None:
                logger.error('[科研-E系列] 拆解装备后科研项目丢失')
            return result
        elif project.genre == 'T':
            logger.info(f'[科研-T系列] 准备启动T系列科研: {project}')
            self.research_project_start(project, add_queue=False)
            self.config.Research_RemainingCommissions = project.commission_amount
            self.research_project_started = None
            return False
        else:
            # 普通项目
            return self.research_project_start(project, add_queue=add_queue)

    def research_receive(self, skip_first_screenshot=True):
        """
        领取科研主页中已完成项目的奖励。

        检测已完成的科研项目，点击进入并领取奖励物品，
        支持掉落记录功能。若项目时间已到但条件未满足则跳过。

        Args:
            skip_first_screenshot (bool): 是否跳过首次截图。

        Pages:
            in: page_research, stable, with project finished.
            out: page_research

        Returns:
            bool: 成功领取奖励返回 True。
                  项目条件未满足返回 False。
        """
        logger.hr('领取科研奖励', level=3)
        with self.stat.new(
                genre='research', method=self.config.DropRecord_ResearchRecord
        ) as record:
            # 截取项目列表
            record.add(self.device.image)

            # 点击已完成项目，进入 GET_ITEMS_*
            confirm_timer = Timer(1.5, count=5)
            record_button = None
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                if self.appear(RESEARCH_CHECK, offset=(20, 20), interval=10):
                    if self.research_has_finished():
                        self.device.click(RESEARCH_ENTRANCE[self._research_finished_index])

                if self.appear(RESEARCH_STOP, offset=(20, 20)):
                    logger.info('[科研-领取] 科研时间已到，但条件未满足')
                    self.research_project_started = None
                    self.research_detail_quit()
                    return False
                # 误入其他项目
                if self.appear(RESEARCH_START, offset=(20, 20), interval=5):
                    self.device.click(RESEARCH_DETAIL_QUIT)
                    continue

                appear_button = self.get_items()
                if appear_button is not None:
                    if appear_button == record_button:
                        if confirm_timer.reached():
                            break
                    else:
                        logger.info(f'{appear_button} appeared')
                        record_button = appear_button
                        confirm_timer.reset()

            # 截取奖励物品
            self.drop_record(drop=record)

        # 关闭 GET_ITEMS_*，返回项目列表
        self.ui_click(appear_button=self.get_items, click_button=GET_ITEMS_RESEARCH_SAVE,
                      check_button=self.is_in_research, skip_first_screenshot=True)
        return True

    def queue_receive(self, skip_first_screenshot=True):
        """
        领取科研队列中所有已完成项目的奖励。

        遍历队列页面，依次领取已完成项目的奖励物品，
        支持掉落记录功能用于统计追踪。

        Args:
            skip_first_screenshot (bool): 是否跳过首次截图。

        Pages:
            in: is_in_queue
            out: is_in_queue

        Returns:
            int: 领取奖励的科研项目数量。
        """
        logger.hr('领取队列奖励', level=1)
        total = 0
        with self.stat.new(
                genre='research', method=self.config.DropRecord_ResearchRecord
        ) as drop:
            # 截取项目列表
            drop.add(self.device.image)

            end_confirm = Timer(1, count=3)
            item_confirm = Timer(1.5, count=5)
            item_interval = Timer(0.2, count=0)
            record_button = None
            while 1:
                if skip_first_screenshot:
                    skip_first_screenshot = False
                else:
                    self.device.screenshot()

                # 结束条件
                # 不使用偏移量，仅使用颜色检测
                if self.is_in_queue() and not self.appear(QUEUE_CLAIM_REWARD, offset=None):
                    if end_confirm.reached():
                        break
                else:
                    end_confirm.reset()

                # 获取物品
                if drop:
                    # 记录物品掉落
                    appear_button = self.get_items()
                    if appear_button is not None:
                        if appear_button == record_button:
                            if item_confirm.reached():
                                # 记录掉落并关闭获取物品界面
                                self.drop_record(drop=drop)
                                self.device.click(GET_ITEMS_RESEARCH_SAVE)
                                item_confirm.reset()
                                record_button = None
                                total += 1
                                continue
                        else:
                            logger.info(f'[科研-领取] {appear_button} 出现')
                            record_button = appear_button
                            item_confirm.reset()
                    else:
                        item_confirm.reset()
                        record_button = None
                else:
                    # 不保存掉落，直接点击
                    if item_interval.reached():
                        appear_button = self.get_items()
                        if appear_button is not None:
                            self.device.click(GET_ITEMS_RESEARCH_SAVE)
                            item_interval.reset()
                            total += 1
                            continue

                # 领取奖励
                if self.appear_then_click(QUEUE_CLAIM_REWARD, offset=None, interval=5):
                    continue

            if total <= 0:
                drop.clear()

        logger.info(f'[科研-队列] 从 {total} 个项目领取了奖励')
        return total

    def queue_quit(self, *args, **kwargs):
        super().queue_quit(*args, **kwargs)
        self._research_project_offset = 0

    def research_project_list_init(self, from_queue=False):
        """
        处理进入科研列表：重置偏移量并检测项目。

        Args:
            from_queue (bool): 是否从科研队列切换而来，
                此时已调用过 ensure_research_center_stable()
        """
        self._research_project_offset = 0
        # 处理信息栏，多截一张图以等待 info_bar 残留消退
        if self.handle_info_bar():
            self.device.screenshot()
        if not from_queue:
            self.ensure_research_center_stable()
        self.research_detect()

    def research_queue_append(self, drop=None, add_queue=True):
        """
        从项目列表中选择一个科研项目并启动。

        初始化项目列表、执行筛选和选择，最多尝试 2 次。
        成功启动后记录已启动的项目对象。

        Args:
            drop (DropImage): 掉落记录对象。
            add_queue (bool): 是否加入队列。
                第 6 个项目无法加入队列，因此需要此开关。

        Returns:
            bool: 是否成功启动项目。
        """
        self.research_project_started = None
        project_record = None
        for _ in range(2):
            logger.hr('选择科研项目', level=2)
            self.research_project_list_init(from_queue=True)
            project_record = self.device.image
            priority = self.research_sort_filter()
            result = self.research_select(priority, drop=drop, add_queue=add_queue)
            if result:
                break

        if self.research_project_started is not None:
            if project_record is not None:
                drop.add(project_record)
            return True
        else:
            return False

    def research_fill_queue(self, include_sixth=True):
        """
        持续选择科研项目直到队列填满。

        Args:
            include_sixth (bool): 是否处理第 6 个项目（队列外）。
                批量模式过渡态（未全部完成时的补位）必须为 False，
                避免在已完成未收取的第 6 个项目上覆盖启动新项目。

        Returns:
            int: 加入队列的科研项目数量

        Pages:
            in: is_in_research
        """
        logger.hr('填充科研队列', level=1)
        total = 0
        with self.stat.new(
                genre='research', method=self.config.DropRecord_ResearchRecord
        ) as drop:
            for _ in range(5):
                if self.get_queue_slot() > 0:
                    success = self.research_queue_append(drop=drop)
                    if success:
                        total += 1
                    else:
                        logger.info(f'[科研-队列] 无法启动项目，停止填充队列，已添加: {total}')
                        return total
                else:
                    break

            # 运行第 6 个项目
            if include_sixth:
                status = self.get_research_status(self.device.image)
                if 'waiting' not in status:
                    logger.info('[科研-第6个] 选择第6个科研')
                    self.research_queue_append(drop=drop, add_queue=False)
                else:
                    logger.info('[科研-第6个] 第6个科研已在等待中')
            else:
                logger.info('[科研-队列] 跳过第 6 个项目（批量模式过渡态）')

            logger.info(f'[科研-队列] 科研队列已填满，已添加: {total}')
            return total

    def receive_6th_research(self, skip_first_screenshot=True):
        """
        Returns:
            bool: 是否成功
        """
        logger.hr('领取第6个科研', level=2)

        # 等待动画
        timeout = Timer(2, count=6).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning('[科研-第6个] 等待超时')
                break

            status = self.get_research_status(self.device.image)
            # 项目卡片尚未完全加载
            if 'unknown' in status:
                continue
            # 进入科研界面时，`waiting`（排队中）项目出现在第 2 位，然后移动到第 3 位
            # 从队列领取奖励后返回科研界面时，`waiting` 项目出现在第 4 位，然后移动到第 3 位
            # `waiting`（排队中）项目默认应在第 3 个位置
            if 'waiting' in status:
                if status.index('waiting') == 2:
                    break
                else:
                    continue
            # 没有第 6 个科研项目
            if sum([s == 'detail' for s in status]) == 5:
                break

        # 检查是否已完成
        if self.research_has_finished():
            logger.info(f'[科研-第6个] 第6个科研已完成，位置: {self._research_finished_index}')
            success = self.research_receive()
            if not success:
                return False
        else:
            logger.info('[科研-第6个] 没有科研完成')

        # 检查是否处于等待或运行状态
        status = self.get_research_status(self.device.image)
        if 'waiting' in status:
            if self.get_queue_slot() > 0:
                self.research_project_start(status.index('waiting'))
            else:
                logger.info('[科研-第6个] 队列已满，停止追加等待中的科研')
        if 'running' in status:
            if self.get_queue_slot() > 0:
                self.research_project_start(status.index('running'))
            else:
                logger.info('[科研-第6个] 队列已满，停止追加运行中的科研')

        return True

    def handle_pending_t_research(self):
        """
        处理挂起的 T 系列委托科研项目。

        T 系列科研需要完成委托才能启动。此方法检查是否有待处理的
        T 系列科研，并尝试启动它。启动成功后将委托数写入配置，
        由委托任务模块负责完成。

        Returns:
            bool: True 表示 T 类科研已处理完毕或无需处理，
                  False 表示 T 类科研仍在等待中。
        """
        if self.config.Research_RemainingCommissions <= -1:
            return True
        if self.config.Research_RemainingCommissions > 0:
            return False

        slot = self.get_queue_slot()
        add_queue = slot > 0
        if not self.research_project_start(2, add_queue=add_queue):
            logger.warning('[科研-T系列] 启动挂起的T类科研失败')
            return False

        if add_queue:
            self.config.Research_RemainingCommissions = -1
            return True

        logger.info('[科研-T系列] T类科研正在队列外运行')
        return False

    def get_sixth_display_time(self):
        """
        识别第 6 个项目（队列外，主页面中位卡片）的显示时长。

        存在性由模板状态判定（等待/运行中）或完成状态灯判定，
        避免 5 张卡片全是研发选项时误读选项卡的时长。
        queue_quit 返回时切页动画可能未结束（状态全 unknown，实测
        出现在返回后 0.3-0.9 秒内），先等状态稳定再判定；超时按
        当前画面判定，下一轮调度重新快照即可自愈。

        Returns:
            timedelta | None: 等待中=项目总时长，进行中=剩余倒计时，已完成=0；
                无第 6 个项目（或识别失败）返回 None。

        Pages:
            in: is_in_research
        """
        timeout = Timer(5, count=10).start()
        while 1:
            status = self.get_research_status(self.device.image)
            if 'unknown' not in status:
                break
            if timeout.reached():
                logger.warning('[科研-第6个] 卡片状态识别超时，按当前画面判定')
                break
            self.device.screenshot()
        if 'waiting' in status or 'running' in status:
            time = parse_display_time(OCR_SIXTH_TIME_BRIGHT.ocr(self.device.image))
            if time is None:
                time = parse_display_time(OCR_SIXTH_TIME_DIM.ocr(self.device.image))
            return time
        if self.research_has_finished():
            return timedelta(0)
        return None

    def _batch_snapshot(self):
        """
        拍取一次批量模式快照：队列 5 槽显示时长 + 第 6 个项目显示时长。

        Returns:
            tuple[list, timedelta | None]: get_queue_display_times 与
                get_sixth_display_time 的返回值。

        Pages:
            in: is_in_research
        """
        self.queue_enter()
        times = self.get_queue_display_times()
        self.queue_quit()
        sixth = self.get_sixth_display_time()
        return times, sixth

    def _batch_ready(self):
        """
        判断批量模式的收获时机是否到来。

        Returns:
            bool: True 表示队列 5 槽与第 6 个项目全部完成（或空置）。

        Pages:
            in: is_in_research
        """
        times, sixth = self._batch_snapshot()
        ready = batch_all_completed(times, sixth)
        logger.attr('科研-批量收获时机', ready)
        return ready

    def _batch_schedule(self, maintain=None):
        """
        批量模式调度：NextRun = 全部完成的预计时间 + 3 分钟余量。

        快照读出剩余总时长为 0 时重拍一次再判定：切页/加载动画期间
        逐槽 OCR 可能整体读空（实测会把正在运行的队列误判为全空，
        一路推迟到服务器刷新）；重拍仍为 0 才是真正的全空，推迟到
        服务器刷新。

        传入 maintain（维护当天科研）时，排程不早于维护开始时刻：收光
        填满后剩余总时长仍可能把下次运行排在维护开始之前，钳到维护
        开始即可——醒来时维护中，由调度器的服务器状态检查拦到维护结束
        后再收，窗口内不会重复进游戏。

        Args:
            maintain (datetime.datetime | None): 今天维护的开始时间，None 表示不钳制。

        Pages:
            in: is_in_research
        """
        total = batch_total_remaining(*self._batch_snapshot())
        if total == timedelta(0):
            logger.info('[科研-批量] 剩余总时长为 0，重拍一次快照再判定')
            total = batch_total_remaining(*self._batch_snapshot())
        logger.attr('科研-批量剩余总时长', total)
        if total > timedelta(0):
            target = current_time() + total + timedelta(minutes=3)
            if maintain is not None and target < maintain:
                logger.info(f'[科研-维护] 排程 {target} 早于维护开始，钳到 {maintain}')
                target = maintain
            self.config.task_delay(target=target)
        else:
            self.config.task_delay(server_update=True)

    def _run_batch(self):
        """
        批量模式主流程：全部科研完成后一次性收取，再一次性填满 6 个。

        收获时机由逐槽显示时长判定（不依赖项目身份），
        用户手动加入项目、手动收取部分项目均可在下一轮快照中自然收敛。

        Pages:
            in: page_research
            out: page_research
        """
        logger.hr('科研批量模式', level=1)
        # research_delay_check 在填充过程中可能访问 end_time，批量模式未经过
        # 单槽调度赋值，这里置为当前时间以保证资源不足延迟判断可用
        self.end_time = current_time()

        if self._batch_ready():
            # 全部完成：一次性收光，再一次性填满
            self.queue_enter()
            self.queue_receive()
            self.queue_quit()
            if self.handle_pending_t_research():
                self.receive_6th_research()
                self.research_fill_queue()
        else:
            # 未全部完成：不收取（保证一次性收完），仅补满空槽；
            # 第 6 个槽留待批量收取，避免覆盖已完成未收的项目
            self.research_fill_queue(include_sixth=False)

        self._batch_schedule()

    def _run_batch_maintain(self, maintain):
        """
        维护当天科研：维护前最后一次运行，收光已完成项目并拉满队列。

        与普通批量模式的区别：不等「全部完成」的收获时机——已完成的直接
        收，队列（含第 6 个）尽量填满，让停服期间与维护结束后立即有项目
        在跑；排程不早于维护开始时刻，维护中的运行由调度器的服务器状态
        检查拦到维护结束后再收。

        Args:
            maintain (datetime.datetime): 今天维护的开始时间（未开始）。

        Pages:
            in: page_research
            out: page_research
        """
        logger.hr('科研批量模式（维护前）', level=1)
        # research_delay_check 在填充过程中可能访问 end_time，批量模式未经过
        # 单槽调度赋值，这里置为当前时间以保证资源不足延迟判断可用
        self.end_time = current_time()

        self.queue_enter()
        self.queue_receive()
        self.queue_quit()
        if self.handle_pending_t_research():
            self.receive_6th_research()
            self.research_fill_queue()

        self._batch_schedule(maintain=maintain)

    def run(self):
        """
        Pages:
            in: Any page
            out: page_research, with research project information, but it's still page_research.
                    or page_main
        """
        self.ui_ensure(page_research)

        if self.config.Research_BatchMode:
            if self.config.Research_MaintainFill:
                maintain, reason, queried = query_maintain_today(self.config.SERVER)
                logger.attr('维护当天科研', reason if queried else f'未查到公告（{reason}）')
                if maintain is not None and maintain > current_time():
                    run_at = maintain - timedelta(minutes=RESEARCH_MAINTAIN_LEAD_MINUTES)
                    if current_time() < run_at:
                        logger.info(f'[科研-维护] 还没到维护前的运行时间，推迟到 {run_at}')
                        self.config.task_delay(target=run_at)
                        return
                    logger.info(f'[科研-维护] 到维护前 {RESEARCH_MAINTAIN_LEAD_MINUTES} 分钟了，'
                                f'收光已完成项目并拉满科研队列')
                    self._run_batch_maintain(maintain)
                    return
            self._run_batch()
            return

        # 检查队列
        self.queue_enter()
        self.queue_receive()
        self.end_time = self.get_research_ended()
        self.queue_quit()

        # 处理挂起的T类科研
        if self.handle_pending_t_research():
            # 检查第 6 个项目（在队列之外）
            self.receive_6th_research()
            # 填充队列
            self.research_fill_queue()

        slot = self.get_queue_slot()
        # 调度
        if slot == 5:
            # 队列为空，无法启动任何科研
            self.config.task_delay(server_update=True)
            return
        elif self.end_time <= current_time():
            # 获取新启动项目的剩余时间
            self.queue_enter()
            self.end_time = self.get_research_ended()
            self.queue_quit()
        if slot == 4:
            # 队列即将为空，因资源不足放弃科研，提前 10 分钟以避免科研闲置
            self.end_time = self.end_time + timedelta(minutes=-10)
        self.config.task_delay(target=self.end_time)
