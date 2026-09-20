"""大世界明石商店模块。

管理大世界（Operation Siren）明石（Akashi）商店的购买逻辑。
提供各服务器（CN/EN/JP/TW）的物品网格配置差异、商品识别、
购买决策以及购买流程的状态循环控制。
"""
from typing import List
from module.base.button import Button, ButtonGrid
from module.base.decorator import Config, cached_property
from module.logger import logger
from module.os_handler.map_event import MapEventHandler
from module.os_handler.os_status import OSStatus
from module.os_shop.selector import Selector
from module.os_shop.ui import OSShopUI
from module.os_shop.item import OSShopItem as Item, OSShopItemGrid as ItemGrid
from datetime import datetime
from module.base.utils import save_image
import os


class AkashiShop(OSStatus, OSShopUI, Selector, MapEventHandler):

    # =========================================================
    # 商店格子配置
    # =========================================================

    @cached_property
    @Config.when(SERVER='tw')
    def os_akashi_shop_items(self) -> ItemGrid:
        shop_grid = ButtonGrid(
            origin=(233, 224),
            delta=(193, 228),
            button_shape=(98, 98),
            grid_shape=(4, 2),
            name='SHOP_GRID'
        )

        shop_items = ItemGrid(
            shop_grid,
            templates={},
            amount_area=(60, 74, 96, 95),
            counter_area=(85, 170, 134, 186),
            price_area=(52, 132, 132, 165)
        )

        shop_items.load_template_folder('./assets/shop/os')
        shop_items.load_cost_template_folder('./assets/shop/os_cost')
        return shop_items

    @cached_property
    @Config.when(SERVER='en')
    def os_akashi_shop_items(self) -> ItemGrid:
        shop_grid = ButtonGrid(
            origin=(231, 222),
            delta=(190, 224),
            button_shape=(98, 98),
            grid_shape=(4, 2),
            name='SHOP_GRID'
        )

        shop_items = ItemGrid(
            shop_grid,
            templates={},
            amount_area=(60, 74, 96, 95),
            counter_area=(85, 170, 134, 186),
            price_area=(52, 132, 132, 165)
        )

        shop_items.load_template_folder('./assets/shop/os')
        shop_items.load_cost_template_folder('./assets/shop/os_cost')
        return shop_items

    @cached_property
    @Config.when(SERVER=None)
    def os_akashi_shop_items(self) -> ItemGrid:
        shop_grid = ButtonGrid(
            origin=(233, 224),
            delta=(193.2, 228),
            button_shape=(98, 98),
            grid_shape=(4, 2),
            name='SHOP_GRID'
        )

        shop_items = ItemGrid(
            shop_grid,
            templates={},
            amount_area=(60, 74, 96, 95),
            counter_area=(85, 170, 134, 186),
            price_area=(52, 132, 132, 165)
        )

        shop_items.load_template_folder('./assets/shop/os')
        shop_items.load_cost_template_folder('./assets/shop/os_cost')
        return shop_items

    # =========================================================
    # AP Box 截图（受 DropRecord_OpsiShopRecord 开关控制）
    # =========================================================

    def save_akashi_ap_box_screenshot(self, snapshot):
        """商店内出现行动力箱时留一张截图。

        由 `DropRecord.OpsiShopRecord` 开关控制（设置 — 智慧港区 — 掉落记录）：
        选「无操作」不做任何事，选「保存」把整张商店截图存到
        `screenshots/opsi_shop/`，文件名带箱子档位与时间戳。
        只用于人工核对，不参与购买决策，也不影响购买流程。

        Args:
            snapshot (dict): 含 `image`（商店截图）与 `items`（识别到的商品）。
        """
        if self.config.DropRecord_OpsiShopRecord == 'do_not':
            return

        items = snapshot["items"]
        image = snapshot["image"]

        # 只筛 AP box
        ap_boxes = [i.name for i in items if i.name.startswith("ActionPoint")]
        if not ap_boxes:
            return

        def get_ap_value(name: str) -> int:
            try:
                return int(name.replace("ActionPoint", "").split("_")[0])
            except Exception:
                return 0

        box_name = max(ap_boxes, key=get_ap_value)

        folder = os.path.join(
            str(self.config.DropRecord_SaveFolder),
            "opsi_shop"
        )
        os.makedirs(folder, exist_ok=True)

        filename = datetime.now().strftime(
            f"{box_name}_%Y%m%d_%H%M%S.png"
        )
        file_path = os.path.join(folder, filename)

        save_image(image, file_path)
        logger.info(f'[AP BOX] 行动力箱截图已保存 -> {file_path}')

    # =========================================================
    # 商品识别
    # =========================================================

    def os_shop_get_items_in_akashi(self) -> List[Item]:
        if self.config.SHOP_EXTRACT_TEMPLATE:
            self.os_akashi_shop_items.extract_template(
                self.device.image,
                './assets/shop/os'
            )

        self.os_akashi_shop_items.predict(self.device.image)
        items = self.os_akashi_shop_items.items

        if items:
            min_row = self.os_akashi_shop_items.grids[0, 0].area[1]
            row = [str(item) for item in items if item.button[1] == min_row]
            logger.info(f'明石商店第 1 行: {row}')
            row = [str(item) for item in items if item.button[1] != min_row]
            logger.info(f'明石商店第 2 行: {row}')
            return items
        else:
            logger.info('未找到明石商店物品')
            return []

    def os_shop_get_item_to_buy_in_akashi(self) -> Item:

        self.os_shop_get_coins()

        items = self.os_shop_get_items_in_akashi()

        snapshot = {
            "image": self.device.image,
            "items": items
        }
        ap_box_captured = False
        # retry保证稳定
        for _ in range(2):
            if not len(items) or any(not item.is_known_item() for item in items):
                logger.warning('明石商店为空或物品为空，正在确认')
                self.device.sleep((0.3, 0.5))
                self.device.screenshot()

                items = self.os_shop_get_items_in_akashi()
                snapshot = {
                    "image": self.device.image,
                    "items": items
                }
                continue
            # 同一次逛店只留一张 AP 箱截图（开关关闭时方法内部直接返回）
            if not ap_box_captured:
                self.save_akashi_ap_box_screenshot(snapshot)
                ap_box_captured = True
            items = self.items_filter_in_akashi_shop(items)

            if not items:
                return None

            return items.pop()

        return None