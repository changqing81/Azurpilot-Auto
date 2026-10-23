"""军火商专用外观箱购买流程，其他商店继续使用原有购买逻辑。"""

import cv2
import numpy as np

from module.base.decorator import cached_property
from module.base.timer import Timer
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_3
from module.exception import ScriptError
from module.logger import logger
from module.shop.assets import SHOP_BUY_CONFIRM, SHOP_CLICK_SAFE_AREA
from module.shop.skin_box import DEFAULT_SKIN_BOX_LIMITS, SkinBoxLedger
from module.ui.assets import SHOP_BACK_ARROW


class SkinBoxShopMixin:
    """以固定槽位区分类别，以每笔成交的确认信号防止重计和超买。"""

    @cached_property
    def skin_box_ledger(self):
        """每个军火商实例独享账本，非法配置禁止执行购买。"""
        value = getattr(self.config, 'GeneralShop_SkinBoxBuyCounts', DEFAULT_SKIN_BOX_LIMITS)
        try:
            return SkinBoxLedger(value)
        except ValueError as exc:
            logger.error(str(exc))
            raise ScriptError(str(exc)) from exc

    @staticmethod
    def is_skin_box(item):
        """业务规则：只以物资货币和 7000 单价识别外观箱，不依赖模板名称。"""
        return item.cost == 'Coins' and item.price == 7000

    def skin_box_slot(self, item):
        """按完整 5×2 网格取位置；售罄商品消失也不会让后面的槽位前移。"""
        for index, button in enumerate(self.shop_grid.buttons):
            if tuple(item.area) == tuple(button.area):
                return index
        return None

    def shop_get_item_to_buy(self, items):
        """在所有选择入口之前过滤外观箱，防止过滤器或溢出购买绕过配额。"""
        candidates = []
        for item in items:
            if not item.is_valid:
                continue
            if self.is_skin_box(item):
                if not self.config.GeneralShop_BuySkinBox:
                    continue
                if not self.skin_box_ledger.can_buy(self.skin_box_slot(item)):
                    continue
            candidates.append(item)
        return super().shop_get_item_to_buy(candidates)

    def shop_buy(self):
        """外观箱按目标次数推进，普通商品仍受原有十二次购买保护。"""
        if not self.config.GeneralShop_BuySkinBox:
            return super().shop_buy()

        ledger = self.skin_box_ledger
        failures = {}
        normal_purchases = 0
        while 1:
            # 未决交易不能重新点击，也不能进入刷新后把它忘掉。
            if ledger.pending is not None:
                logger.warning('[外观箱] 上一笔交易尚未确认，停止本轮购买和刷新')
                return False
            items = self.shop_get_items()
            self.shop_currency()
            if self._currency <= 0:
                return False
            item = self.shop_get_item_to_buy(items)
            if item is None:
                return True
            if self.is_skin_box(item):
                slot = self.skin_box_slot(item)
                if self.shop_buy_execute(item):
                    failures[slot] = 0
                    continue
                # 明确未成交时允许重试；不确定的结果由 pending 阻止重入。
                failures[slot] = failures.get(slot, 0) + 1
                if failures[slot] >= 3:
                    logger.warning(f'[外观箱] 槽位{slot + 1}连续购买失败，停止本轮')
                    return False
                continue
            if normal_purchases >= 12:
                logger.warning('[商店-购买] 普通商品购买次数达到上限，停止')
                return False
            self.shop_buy_execute(item)
            normal_purchases += 1

    def shop_buy_execute(self, item, skip_first_screenshot=True):
        """每次只提交一笔外观箱购买，收到奖励并回到商店后入账。

        普通提示条不是成功凭据。没有奖励时只有余额未变、目标仍在售且
        商店画面稳定才能释放交易重试；无法确认则保留 pending 停止购买。

        Pages: in: page_munitions, out: page_munitions
        """
        if not self.is_skin_box(item):
            return super().shop_buy_execute(item, skip_first_screenshot=skip_first_screenshot)
        ledger = self.skin_box_ledger
        slot = self.skin_box_slot(item)
        if not self.config.GeneralShop_BuySkinBox or not ledger.begin(slot):
            return False

        self.shop_interval_clear()
        submitted = False
        rewarded = False
        failure_hint = False
        balance_before = self._currency
        stable = Timer(3, count=3).start()
        for _ in self.loop(skip_first=skip_first_screenshot, timeout=30):
            # 奖励弹窗可能停留多帧，只设置布尔状态，回到商店时统一入账。
            if self.appear(GET_ITEMS_1) or self.appear(GET_ITEMS_3):
                if submitted:
                    rewarded = True
                # 只关闭已确认可见的奖励页，不调用可能点击购买确认的通用处理器。
                if self.appear(GET_ITEMS_1, interval=1) or self.appear(GET_ITEMS_3, interval=1):
                    self.device.click(SHOP_CLICK_SAFE_AREA)
                stable.reset()
                continue
            # 信息条只用于辅助判断失败，绝不单独作为成交凭据。
            if submitted and self.info_bar_count():
                failure_hint = True
            if self.appear(SHOP_BACK_ARROW, offset=(30, 30)):
                if stable.reached():
                    current = self.shop_items().item_class(self.device.image, item._button)
                    if rewarded:
                        ledger.confirm(slot, sold_out=not current.is_valid)
                        label = f'{chr(97 + slot % 5)}{slot // 5 + 1}'
                        logger.info(f'购买外观箱[槽位{slot + 1}/{label}]成功，'
                                    f'本槽位进度 {ledger.counts[slot]}/{ledger.limits[slot]}，'
                                    f'本轮累计已购买 {ledger.kinds} 种、{ledger.total} 个')
                        return True
                    if submitted and failure_hint:
                        # 信息条可能报告失败，不能仅凭弹窗消失计数。
                        balance_after = self.status_get_gold_coins()
                        if current.is_valid and balance_after == balance_before:
                            ledger.cancel(slot)
                            logger.warning(f'[外观箱] 槽位{slot + 1}未确认成交，保留重试资格')
                            return False
                if submitted or rewarded:
                    continue
                if self.appear(SHOP_BACK_ARROW, offset=(30, 30), interval=3):
                    self.device.click(item)
                    stable.reset()
                    continue
            else:
                stable.reset()

            # 提交后禁止再次点击确认，先等这一笔的结果，避免网络延迟造成超买。
            if not submitted:
                if self.appear_then_click(SHOP_BUY_CONFIRM, offset=(20, 20), interval=3):
                    submitted = True
                    stable.reset()
                    continue
            # 通用遮挡处理器会点击确认弹窗，不能在已提交后调用，否则网络
            # 延迟期间仍显示的购买框可能被再次确认。这里只等待明确的奖励。
        logger.warning(f'[外观箱] 槽位{slot + 1}交易结果未明，停止购买且不增加计数')
        return False

    def _skin_box_refresh_view(self):
        """保留槽位图标指纹，仅用于校验刷新；不用于合并不同槽位的类别。"""
        view = []
        for button in self.shop_grid.buttons:
            item = self.shop_items().item_class(self.device.image, button)
            # 缩小图标抑制细微动画和像素噪声，不受临时模板编号变化影响。
            icon = cv2.resize(item.image, (8, 8), interpolation=cv2.INTER_AREA).astype(float)
            view.append((item.is_valid, icon))
        return view

    @staticmethod
    def _skin_box_view_changed(before, after):
        """售罄槽位恢复或在售图标明显变化才作为刷新画面的证据。"""
        return any((not old_valid and new_valid)
                   or (old_valid and new_valid and np.mean(np.abs(old_icon - new_icon)) > 25)
                   for (old_valid, old_icon), (new_valid, new_icon) in zip(before, after))

    def shop_refresh(self):
        """免费和付费刷新共用原流程，仅确认货架更新后清空本轮账本。"""
        if not self.config.GeneralShop_BuySkinBox:
            return super().shop_refresh()
        if self.skin_box_ledger.pending is not None:
            return False
        before = self._skin_box_refresh_view()
        if not super().shop_refresh():
            return False
        # 原实现的 True 只表示点击过确认；连续两帧货架更新后才允许重置。
        changed_frames = 0
        for _ in self.loop(skip_first=False, timeout=8):
            if self.appear(SHOP_BACK_ARROW, offset=(30, 30)):
                after = self._skin_box_refresh_view()
                if self._skin_box_view_changed(before, after):
                    changed_frames += 1
                    if changed_frames >= 2:
                        self.skin_box_ledger.reset()
                        logger.info('[外观箱] 商店刷新成功，已清空本轮购买进度和去重记录')
                        return True
                    continue
            changed_frames = 0
        logger.warning('[外观箱] 无法确认货架刷新，保留本轮购买进度')
        return False
