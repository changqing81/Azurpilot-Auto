"""大世界状态追踪模块。

管理大世界（Operation Siren）模式的状态信息，包括海域代币
（黄币/紫币）的 OCR 数值追踪、任务类型识别、子任务冷却（CD）
状态的实时计算，以及相关日志资源的记录。
"""
# 此文件用于管理大世界（Operation Siren）模式下的状态信息。
# 负责海域代币（黄币/紫币）的数值追踪、任务类型识别以及子任务冷却（CD）状态的实时计算。
import threading
import time
import typing as t
from datetime import timedelta

import numpy as np

from module.base.decorator import cached_property
import module.config.server as server
from module.base.timer import Timer
from module.config.config import Function, is_internal_only_modified
from module.config.time_source import now as current_time
from module.config.utils import get_os_next_reset, get_server_next_update
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.ocr.ocr import Digit
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_SHIP
from module.os_shop.assets import OS_SHOP_CHECK, OS_SHOP_PURPLE_COINS, SHOP_PURPLE_COINS, SHOP_YELLOW_COINS
from module.ui.ui import UI
from module.log_res.log_res import LogRes


if server.server != 'jp':
    OCR_SHOP_YELLOW_COINS = Digit(SHOP_YELLOW_COINS, letter=(239, 239, 239), threshold=160, name='OCR_SHOP_YELLOW_COINS')
else:
    OCR_SHOP_YELLOW_COINS = Digit(SHOP_YELLOW_COINS, letter=(201, 201, 201), threshold=200, name='OCR_SHOP_YELLOW_COINS')
OCR_SHOP_PURPLE_COINS = Digit(SHOP_PURPLE_COINS, letter=(255, 255, 255), name='OCR_SHOP_PURPLE_COINS')
OCR_OS_SHOP_PURPLE_COINS = Digit(OS_SHOP_PURPLE_COINS, letter=(255, 255, 255), name='OCR_OS_SHOP_PURPLE_COINS')
# 凭证读数按行动力同样的口径静默：黄币/紫币每次进店、每轮收尾都会读，
# Ocr 自带的耗时行会把日志区刷满；读到的值由调用方自己记一行
OCR_SHOP_YELLOW_COINS.SHOW_LOG = False
OCR_SHOP_PURPLE_COINS.SHOW_LOG = False
OCR_OS_SHOP_PURPLE_COINS.SHOW_LOG = False


class OSStatus(UI):
    _shop_yellow_coins = 0
    _shop_purple_coins = 0
    _cache_lock = threading.Lock()
    _last_yellow_coins = 0
    # 连续判为残缺的次数；读到正常值或承认读数后清零
    _yellow_coins_residual_count = 0

    # ==================== 黄币读数残缺防护 ====================
    # 顶栏数字动画/加载/遮挡期间只画出末尾几位时，OCR 会把残缺字形当成完整
    # 数字，读出「4」这种量级完全丢失的值（2026-11-11 事故：真值 218990 读成 4，
    # 一次脏读数让智能调度连补 3 小时黄币）。OCR 只会把数字读短、不会读长，
    # 因此用「位数明显减少」判定残缺，不按跌幅判定——买行动力/清港口/商店进货
    # 都会让黄币合法暴跌，按跌幅判会大量误杀。
    #
    # 判定为可疑后不是拦下，而是连读多帧取最大值：真实余额在 1 秒内不会跳变，
    # 取最大只会修掉残缺值，不会改动合法值。

    # 可疑判定：位数比基线少这么多位就算残缺（例：218990 六位 → 4 一位，少 5 位）
    YELLOW_COINS_SHORTEN_DIGITS = 2
    # 基线未知时的兜底下限：黄币量级长期在五~六位，低于这个值的个位/两位数
    # 一律先判可疑（若确实如此，复查稳定后照样采信，不会硬拦）
    YELLOW_COINS_SUSPICIOUS_FLOOR = 1000
    # 可疑时最多连读多少帧，取其中最大值
    YELLOW_COINS_RECHECK_FRAMES = 3
    # 低读数连续无法确认画面稳定时的兜底次数上限。
    #
    # 真正的判据是「画面是否静止」：OCR 残缺只发生在画面还在动的时候，
    # 而黄币真的变少之后画面是静止的（见 _yellow_coins_reading_is_confirmed）。
    # 这个次数只是防止「画面长时间不稳定导致永远沿用基线」，正常路径用不到。
    #
    # 为什么不能用秒判定：get_yellow_coins() 只在调度决策 / 资源检查 / 商店
    # 记账时被调用，实测相邻两次间隔 p50=107s、p90=346s，被 Hard / Main2 等
    # 其他任务插队时最长 1903s（31.7 分钟）。任何按时间的阈值都会失准。
    YELLOW_COINS_RESIDUAL_TOLERANCE = 3
    # 送入模型的文本宽度低于此值（像素）视为残缺：正常五~六位数字宽 50~70px，
    # 只剩末尾一两位时只有十几像素（离线实测：完整 6 位数宽 59px，只露末位 13px）
    YELLOW_COINS_MIN_TEXT_WIDTH = 24

    @property
    def is_in_task_explore(self) -> bool:
        return self.config.task.command == 'OpsiExplore'

    @property
    def is_in_task_cl1_leveling(self) -> bool:
        return self.config.task.command == 'OpsiHazard1Leveling'

    @property
    def is_running_cl1_leveling(self) -> bool:
        """判断当前执行上下文是否是侵蚀1练级。"""
        return (
            self.is_in_task_cl1_leveling
            or getattr(self.config, '_bind_task_override', None) == 'OpsiHazard1Leveling'
        )

    @property
    def is_in_task_meow(self) -> bool:
        """判断当前任务是否是短猫相接任务"""
        return self.config.task.command == 'OpsiMeowfficerFarming'

    @property
    def is_cl1_enabled(self) -> bool:
        return self.config.is_task_enabled('OpsiHazard1Leveling')

    @property
    def is_cl1_mode_enabled(self) -> bool:
        """判断侵蚀1相关策略是否启用，包括智能调度代理模式。"""
        is_smart_scheduling_enabled = getattr(self, 'is_smart_scheduling_enabled', None)
        return self.is_cl1_enabled or (
            is_smart_scheduling_enabled is not None
            and is_smart_scheduling_enabled()
        )

    @property
    def is_meow_enabled(self) -> bool:
        """判断短猫相接任务是否启用"""
        return self.config.is_task_enabled('OpsiMeowfficerFarming')

    @property
    def cl1_enough_yellow_coins(self) -> bool:
        return self.get_yellow_coins() >= self.config.cross_get(
            keys='OpsiHazard1Leveling.OpsiHazard1Leveling.OperationCoinsPreserve')

    @property
    def nearest_task_cooling_down(self) -> t.Optional[Function]:
        """
        获取一小时内结束冷却的大世界任务，例如侦察扫描、潜艇呼叫冷却。

        已到期任务不属于冷却任务，不能将其过去的运行时间传给代理任务，
        否则防止行动力溢出等高优先级任务会立即重跑并阻塞其他任务。
        """
        now = current_time()
        update = get_server_next_update('00:00')
        cd_tasks = [
            'OpsiObscure',
            'OpsiAbyssal',
            'OpsiStronghold',
            'OpsiDaily',
        ]

        def func(task: Function):
            if task.command in cd_tasks and task.enable:
                if task.next_run != update and now < task.next_run <= now + timedelta(minutes=60):
                    return True

            return False

        tasks = SelectedGrids(self.config.pending_task + self.config.waiting_task).filter(func).sort('next_run')
        return tasks.first_or_none()

    @property
    def bought_all_yellow_coin_items_in_port_shop(self) -> bool:
        return self.config.cross_get("OpsiShop.Storage.Storage.BoughtAllYellowCoinItems", False)

    @cached_property
    def yellow_coins_preserve(self):
        if self.is_cl1_enabled and not self.bought_all_yellow_coin_items_in_port_shop:
            return 100000
        else:
            return 35000

    # ==================== 黄币读数残缺防护 ====================

    @staticmethod
    def _yellow_coins_digits(value):
        """取读数的位数（正整数）。用于残缺判定，非正数返回 0。"""
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 0
        if value <= 0:
            return 0
        return len(str(value))

    def _is_yellow_coins_reading_suspicious(self, value, baseline, text_width=None):
        """判断一次黄币读数是否疑似「画面没画完」。

        两个判据，任一命中即疑似：
        1. 文本包围盒过窄：数字只渲染出末尾一两位时，送入模型的图很窄；
        2. 位数比基线少 YELLOW_COINS_SHORTEN_DIGITS 位以上，或基线未知时读数
           低于兜底下限。

        刻意不按「跌幅」判定：买行动力、清港口、商店进货、月末清仓都会让
        黄币合法暴跌到很小的数，按跌幅判会大量误判。

        Args:
            value (int): 本次读数。
            baseline (int | None): 上一有效读数；None 表示还没有基线。
            text_width (int | None): 送入模型的文本宽度（像素）。

        Returns:
            bool: 疑似残缺返回 True。
        """
        digits = self._yellow_coins_digits(value)
        if digits == 0:
            # 0/负数属于既有的「未加载」路径，交给原逻辑处理
            return False
        # text_width 可能来自被 mock 的 OCR 实例（非 int），一律先窄化
        if isinstance(text_width, (int, float)) and 0 < text_width < self.YELLOW_COINS_MIN_TEXT_WIDTH:
            return True
        if baseline:
            baseline_digits = self._yellow_coins_digits(baseline)
            if baseline_digits - digits >= self.YELLOW_COINS_SHORTEN_DIGITS:
                return True
            return False
        # 没有基线（脚本刚启动）：退化为绝对下限，只挡住个位/两位/三位数
        return value < self.YELLOW_COINS_SUSPICIOUS_FLOOR

    def _is_os_reset_window(self):
        """判断当前是否处于大世界重置前的清仓窗口（最后 24 小时）。

        月末时脚本会主动把黄币花到只剩 100（os_shop.get_coins_no_limit 的
        get_os_reset_remain()==0 分支），读数合法地变成三位数甚至更小。
        这个窗口内整段跳过残缺判定，否则会把合法的清仓读数当成脏数据反复复查。

        Returns:
            bool: 处于重置前 24 小时内返回 True。
        """
        try:
            next_reset = get_os_next_reset()
            return (next_reset - current_time()).total_seconds() <= 86400
        except Exception:
            return False

    def _yellow_coins_reading_is_confirmed(self, value, text_width=None):
        """判断小数字读数是不是「画面稳定时读到的」。

        关键区别：OCR 残缺一定是画面还在动（数字动画 / 加载 / 遮挡）时发生的，
        此时连续两帧的像素不会一样；而黄币真的变少之后，画面是静止的，
        连续两帧像素完全一致。

        这个判据不受调用间隔影响——不管隔了 1 秒还是 31 分钟（任务插队），
        画面稳不稳只取决于「此刻这一帧和下一帧是否相同」，因此任务插队
        再多也不会把真值误判成残缺，也不会把残缺误判成真值。

        Args:
            value (int): 本次读数。
            text_width (int | None): 送入模型的文本宽度（像素）。

        Returns:
            bool: 画面稳定且读数完整时返回 True。
        """
        # 文本宽度和位数不匹配（窄了）说明只画出末几位，画面一定没稳定
        if isinstance(text_width, (int, float)) and 0 < text_width:
            expected = self._yellow_coins_text_width(value)
            if expected and text_width < expected * 0.8:
                return False
        try:
            # 连拍两帧：数字在动画中会变，真实静止画面两帧像素完全一致
            self.device.screenshot()
            first = self.image_crop(SHOP_YELLOW_COINS)
            self.device.sleep(0.15)
            self.device.screenshot()
            second = self.image_crop(SHOP_YELLOW_COINS)
        except Exception:
            logger.debug('[大世界处理-状态] 黄币确认拍帧失败，按未确认处理')
            return False
        if first is None or second is None:
            return False
        if not np.array_equal(first, second):
            # 两帧区域不一致：画面在动，读数不可信
            return False
        # 画面静止，再用同一帧复读一次，读数一致才确认
        try:
            confirm = OCR_SHOP_YELLOW_COINS.ocr(self.device.image)
        except Exception:
            return False
        return confirm == value

    @staticmethod
    def _yellow_coins_text_width(value):
        """按位数估算该读数的文本宽度（像素），用于和实测宽度比对。

        离线实测（1280x720、游戏字体）：位数与宽度近似线性，
        1 位≈11px、3 位≈29px、6 位≈56~59px，取每字 9.6px + 1px 边距。
        """
        try:
            digits = len(str(int(value)))
        except (TypeError, ValueError):
            return 0
        if digits <= 0:
            return 0
        return int(digits * 9.6) + 1

    def _get_yellow_coins_baseline(self):
        """取黄币残缺判定的基线。

        优先用本进程上一次有效读数；没有（刚启动）则退回仪表盘里持久化的
        上一次读数，这样重启后第一帧也有量级参考，不用靠绝对下限硬挡。

        Returns:
            int | None: 基线值；无从得知时返回 None。
        """
        if self._last_yellow_coins:
            return self._last_yellow_coins
        try:
            group = LogRes(self.config).group('YellowCoin')
            value = group.get('Value') if isinstance(group, dict) else None
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            return value if value > 0 else None
        except Exception:
            return None

    def get_yellow_coins(self, use_cache=True) -> int:
        """读取黄币数量。

        顶栏数字动画/加载/弹窗遮挡期间可能只画出末尾几位，OCR 会把它当成
        完整数字读出「4」这种量级丢失的值。这里先用位数/文本宽度判疑似，
        疑似时连读几帧取最大值——真实余额不会在一秒内跳变，取最大只会修掉
        残缺值，不会改动合法值。月末清仓窗口（黄币合法花到三位数）跳过判定。

        Args:
            use_cache: 是否允许使用 30 秒短缓存。商店记账等必须读新鲜值
                的调用方传 False；购买完成点负责清除缓存。

        Returns:
            int: 黄币数量。
        """
        # 30 秒短 TTL 缓存：一轮结束流程会连续读 3 次黄币（调度决策读 /
        # 资源检查 / 月度统计），期间没有购买行为，每次 ~1s 的双读验证
        # 纯属重复。购买完成点（os_shop_buy）负责清除缓存。
        if use_cache:
            cache = getattr(self.config, '_yellow_coins_cache', None)
            if cache is not None:
                value, cached_at = cache
                if time.time() - cached_at < 30:
                    # 同一份缓存只报一次：一轮结束会连读三次，其余命中降为
                    # debug，避免同一数值反复刷行（新缓存写入会有新的时间戳）。
                    if getattr(self.config, '_yellow_coins_cache_logged_at', None) != cached_at:
                        self.config._yellow_coins_cache_logged_at = cached_at
                        logger.info(f'[大世界处理-状态] 黄币(缓存): {value}')
                    else:
                        logger.debug(f'[大世界处理-状态] 黄币(缓存命中): {value}')
                    return value
        yellow_coins = 0
        timeout = Timer(5, count=10).start()  # 增加超时时间和重试次数
        last_valid_value = None
        # 残缺防护：重置清仓窗口内黄币会合法跌到三位数，整段关掉判定
        guard_enabled = not self._is_os_reset_window()
        baseline = self._get_yellow_coins_baseline() if guard_enabled else None
        # 可疑读数收集：连读多帧后取最大值。真实余额一秒内不会跳变，
        # 取最大只会修掉残缺值，不会把合法的小值改大。
        suspicious_values = []

        for _ in self.loop():
            # End
            if self.appear_then_click(GET_ITEMS_1, offset=True, interval=1):
                timeout.reset()
                continue
            if self.appear_then_click(GET_ITEMS_2, offset=True, interval=1):
                timeout.reset()
                continue
            if self.appear_then_click(GET_SHIP, interval=1):
                timeout.reset()
                continue

            current_value = OCR_SHOP_YELLOW_COINS.ocr(self.device.image)
            text_width = getattr(OCR_SHOP_YELLOW_COINS, 'last_text_width', 0)
            if not isinstance(text_width, (int, float)):
                # 被 mock / 未初始化时不是数字，当作宽度未知
                text_width = 0
            if timeout.reached():
                logger.warning('[大世界处理-状态] 获取黄币超时')
                break

            if current_value == 0:
                # OCR may get 0 when amount is not immediately loaded
                # Or when popups are obscuring the top bar
                logger.info('[大世界处理-状态] 黄币为 0，可能是 OCR 错误或界面未加载')
                continue

            # 疑似残缺（数字没画全）：不采信单帧值，攒起来继续读下一帧。
            # 复查次数用完后必须退出循环交给兜底逻辑——继续留在循环里会让
            # 下一帧同样的残缺读数走正常双读路径，兜底等于没做。
            if guard_enabled and self._is_yellow_coins_reading_suspicious(
                    current_value, baseline, text_width=text_width):
                suspicious_values.append(current_value)
                if len(suspicious_values) >= self.YELLOW_COINS_RECHECK_FRAMES:
                    logger.warning(
                        f'[大世界处理-状态] 黄币复查 {len(suspicious_values)} 帧仍疑似残缺: '
                        f'{suspicious_values}（基线={baseline}），停止复查'
                    )
                    break
                logger.warning(
                    f'[大世界处理-状态] 黄币读数疑似残缺: {current_value}'
                    f'（基线={baseline}, 文本宽度={text_width}px），'
                    f'复查 {len(suspicious_values)}/{self.YELLOW_COINS_RECHECK_FRAMES}'
                )
                self.device.sleep(0.2)
                continue

            # 验证识别稳定性：连续两次识别相同才确认
            if last_valid_value is None:
                last_valid_value = current_value
                self.device.sleep(0.2)  # 短暂等待后再次验证
            elif last_valid_value == current_value:
                yellow_coins = current_value
                break
            else:
                last_valid_value = current_value
                self.device.sleep(0.2)

        # 复查多帧仍全部疑似残缺：要判断这是「画面没画完」还是「黄币真变少了」。
        #
        # 这两者无法用位数、秒数或调用次数区分（实测调用间隔 p50=107s、p90=346s，
        # 被 Hard/Main2 等任务插队时最长 1903s，任何按时间/次数的阈值都站不住）。
        # 能区分的只有画面本身：OCR 残缺必然发生在画面还在动的时候，
        # 而黄币真的变少之后画面是静止的。因此直接拍两帧比对像素 + 复读一次。
        if yellow_coins == 0 and suspicious_values:
            fallback = max(suspicious_values)
            if self._yellow_coins_reading_is_confirmed(fallback):
                # 画面静止且复读一致：黄币真的变少了（用户清港口 / 脚本清仓 /
                # 重置）。此时必须采信，否则脚本会永远以为还有钱。
                yellow_coins = fallback
                self._yellow_coins_residual_count = 0
                logger.warning(
                    f'[大世界处理-状态] 黄币低位读数 {fallback} 已确认为真值'
                    f'（画面静止且复读一致，基线原为 {baseline}），采信该读数'
                )
            elif baseline and self._yellow_coins_residual_count < self.YELLOW_COINS_RESIDUAL_TOLERANCE:
                # 画面在动：先当残缺，沿用基线（2026-11-11 事故场景）
                self._yellow_coins_residual_count += 1
                yellow_coins = baseline
                logger.warning(
                    f'[大世界处理-状态] 黄币复查 {len(suspicious_values)} 帧均疑似残缺 '
                    f'{suspicious_values}，画面未稳定，沿用基线 {baseline}'
                    f'（连续第 {self._yellow_coins_residual_count} 次）'
                )
            elif baseline:
                # 画面一直不稳且连续多次如此：按兜底采信读数，避免永远以为有钱
                yellow_coins = fallback
                self._yellow_coins_residual_count = 0
                logger.warning(
                    f'[大世界处理-状态] 黄币连续 {self.YELLOW_COINS_RESIDUAL_TOLERANCE} 次读数偏低'
                    f'且无法确认画面稳定，按当前读数 {fallback} 采信（基线 {baseline}）'
                )
            else:
                # 连基线都没有（全新实例）：没有依据判断真假，采信读数
                yellow_coins = fallback
                logger.warning(
                    f'[大世界处理-状态] 黄币复查 {len(suspicious_values)} 帧均疑似残缺 '
                    f'{suspicious_values}，无基线可依，采信最大值 {yellow_coins}'
                )
            # 沿用/兜底值不代表当前余额，不写 30s TTL 缓存
            yellow_coins_from_recheck = True
        else:
            yellow_coins_from_recheck = False
            self._yellow_coins_residual_count = 0
            if suspicious_values:
                # 复查期间读到了正常值：正常值才是真值，之前的可疑读数丢弃
                logger.info(
                    f'[大世界处理-状态] 黄币复查后读到正常值 {yellow_coins}'
                    f'（此前疑似残缺 {suspicious_values}）'
                )

        # 如果最终仍未获取到有效数值，使用上次缓存的值（线程安全）
        with self._cache_lock:
            if yellow_coins == 0:
                logger.info(f'[大世界处理-状态] 使用缓存的黄币值: {self._last_yellow_coins}')
                yellow_coins = self._last_yellow_coins
            elif not yellow_coins_from_recheck:
                # 新读到真值才写 30s TTL 缓存（降级旧值不代表当前余额）
                self.config._yellow_coins_cache = (yellow_coins, time.time())

            # 缓存当前值用于降级
            self._last_yellow_coins = yellow_coins
        
        LogRes(self.config).YellowCoin = yellow_coins
        logger.info(f'[大世界处理-状态] 黄币: {yellow_coins}')

        return yellow_coins

    def get_purple_coins(self) -> int:
        if self.appear(OS_SHOP_CHECK):
            purple_coins = OCR_OS_SHOP_PURPLE_COINS.ocr(self.device.image)
        else:
            purple_coins = OCR_SHOP_PURPLE_COINS.ocr(self.device.image)
        LogRes(self.config).PurpleCoin = purple_coins
        return purple_coins

    def os_shop_get_coins(self):
        # 商店记账必须用新鲜值（购买后余额已变），绕过黄币 30s TTL 缓存；
        # 读到的新鲜值会顺带刷新缓存
        self._shop_yellow_coins = self.get_yellow_coins(use_cache=False)
        self._shop_purple_coins = self.get_purple_coins()
        logger.info(f'[大世界处理-状态] 黄币: {self._shop_yellow_coins}, 紫币: {self._shop_purple_coins}')

        # 记录凭证快照到数据库（用于 WebUI 凭证变化曲线图）
        try:
            instance_name = getattr(self.config, 'config_name', 'default')
            source = 'cl1' if self.is_running_cl1_leveling else ('meow' if self.is_in_task_meow else 'other')
            from module.statistics.cl1_database import db as cl1_db
            cl1_db.add_coins_snapshot(
                instance_name,
                self._shop_yellow_coins,
                self._shop_purple_coins,
                source=source
            )
            # LogRes 已将值写入 config.modified，在此持久化；
            # 只含总览显示值（黄币/紫币）时静默保存，不刷「保存配置」行
            self.config.save(quiet=is_internal_only_modified(self.config.modified))
        except Exception:
            logger.exception('[大世界处理-状态] 记录凭证快照失败')
