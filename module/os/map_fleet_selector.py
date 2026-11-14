"""大世界舰队选择器模块。

管理大世界地图中的舰队选择操作。
大世界支持 4 支舰队（而非主线的 2 支），
通过下拉菜单在舰队间切换。

FleetOperator 的大世界版本，适配大世界特有的 UI 布局。
"""

from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import *
from module.logger import logger
from module.os.assets import *
from module.os_handler.assets import *
from module.os_handler.map_event import MapEventHandler


class FleetSelector:
    """大世界舰队选择器。

    管理大世界地图中的舰队切换操作。

    Attributes:
        FLEET_BAR_SHAPE_Y (int): 舰队选择条的高度像素。
        FLEET_BAR_MARGIN_Y (int): 舰队选择条的间距像素。
        FLEET_BAR_ACTIVE_STD (int): 活跃状态的标准差阈值。
        FLEET_LIST (list): 可选舰队列表（FLEET_1 到 FLEET_4）。
    """
    FLEET_BAR_SHAPE_Y = 42
    FLEET_BAR_MARGIN_Y = 11
    FLEET_BAR_ACTIVE_STD = 45  # Active: 67, inactive: 12.
    FLEET_LIST = [FLEET_1, FLEET_2, FLEET_3, FLEET_4]

    def __init__(self, main):
        """
        Args:
            main (OSFleetSelector): Alas module
        """
        self._choose = FLEET_CHOOSE
        self._bar = FLEET_BAR
        self.main = main

    def get(self):
        """
        Returns:
            int: Index of current fleet, 1 to 4. return 0 if unrecognized.
        """
        for index, button in enumerate(self.FLEET_LIST):
            if self.main.appear(button, offset=(20, 20), similarity=0.75):
                return index + 1

        logger.info('[大世界-舰队选择] 未知的大世界舰队')
        return 0

    def bar_opened(self):
        # Check the 3-13 column
        area = self._bar.area
        area = (area[0] + 3, area[1], area[0] + 13, area[3])
        # Should have at least 2 gray option and 1 blue option.
        return self.main.image_color_count(area, color=(239, 243, 247), threshold=221, count=400) \
               and self.main.image_color_count(area, color=(66, 125, 231), threshold=221, count=150)

    def parse_fleet_bar(self, image):
        """
        Args:
            image (np.ndarray): Image of dropdown menu.

        Returns:
            list: List of int. Currently selected fleet ranges from 1 to 4.
        """
        width, height = image_size(image)
        result = []
        for index, y in enumerate(range(0, height, self.FLEET_BAR_SHAPE_Y + self.FLEET_BAR_MARGIN_Y)):
            area = (0, y, width, y + self.FLEET_BAR_SHAPE_Y)
            mean = get_color(image, area)
            if np.std(mean, ddof=1) > self.FLEET_BAR_ACTIVE_STD:
                result.append(4 - index)

        logger.info(f'[大世界-舰队选择] 当前选择: {result}')
        return result

    def selected(self):
        """
        Returns:
            list: List of int. Currently selected fleet ranges from 1 to 4.
        """
        data = self.parse_fleet_bar(self.main.image_crop(self._bar, copy=False))
        return data

    def get_button(self, index, numbers=5):
        """
        Convert fleet index to the Button object on dropdown menu.

        Args:
            index (int): Fleet index, 1-4.

        Returns:
            Button: Button instance.
        """
        index = numbers - index
        area = area_offset(area=(
            0,
            (self.FLEET_BAR_SHAPE_Y + self.FLEET_BAR_MARGIN_Y) * (index - 1),
            self._bar.area[2] - self._bar.area[0],
            (self.FLEET_BAR_SHAPE_Y + self.FLEET_BAR_MARGIN_Y) * (index - 1) + self.FLEET_BAR_SHAPE_Y
        ), offset=(self._bar.area[0:2]))
        area = area_pad(area, pad=3)
        index = numbers - index
        return Button(area=(), color=(), button=area, name=f'{self._bar}_INDEX_{index}')

    def open(self):
        """
        Activate dropdown menu for fleet selection.
        """
        main = self.main
        click_timer = Timer(3, count=6)
        for _ in main.loop():
            if main.handle_map_event():
                click_timer.reset()
                continue

            # End
            if self.bar_opened():
                break

            # Click
            if click_timer.reached():
                main.device.click(self._choose)
                click_timer.reset()

    def close(self):
        """
        Deactivate dropdown menu for fleet selection.
        """
        main = self.main
        click_timer = Timer(3, count=6)
        for _ in main.loop():
            # End
            if not self.bar_opened():
                break

            # Click
            if click_timer.reached():
                main.device.click(self._choose)
                click_timer.reset()

    def switch_click_record_names(self):
        """本次换队可能产生的点击按钮名。

        下拉条里每一行都是用 get_button() 动态生成的 Button，名字形如
        <BAR>_INDEX_<n>，因此这里按名字重新拼一遍，供清记录时使用。
        潜艇舰队（StorageFleetSelector）比大世界多一行，靠 FLEET_LIST 长度区分。

        Returns:
            list[str]: 需要清理的按钮名列表。
        """
        names = [str(self._choose)]
        names.extend(f'{self._bar}_INDEX_{index}' for index in range(1, len(self.FLEET_LIST) + 1))
        return names

    def clear_switch_click_record(self):
        """确认换队成功后，清掉本次换队产生的点击记录。

        为什么需要清：一次正常换队固定点两下（FLEET_CHOOSE + FLEET_BAR_INDEX_n），
        这两个按钮的点击中心都落在设备层 4x3 网格的同一格，12/15 次就触发
        GameTooManyClickError。大世界重扫一轮固定要换 5 次队（见 map_rescan 的
        range(5)），逐队尝试装置/明石还要再换 3~4 次，累计必然超过阈值。
        换队已经确认成功，说明点击有效，不该再算进「点击洪水」。

        注意：只能在确认切换成功之后调用。open() / click() 的重试路径里唯一的
        兜底就是点击频率检测（每次点击都会 stuck_record_clear()，计时器永远
        重置），在重试路径里清记录会让真正的死循环失去保护。

        Returns:
            None
        """
        device = self.main.device
        for name in self.switch_click_record_names():
            device.click_record_remove(name)

    def click(self, index):
        """
        Choose a fleet on dropdown menu, and dropdown deactivated.

        Args:
            index (int): Fleet index, 1-6.
        """
        main = self.main
        button = self.get_button(index)
        click_timer = Timer(3, count=6)
        for _ in main.loop():
            if main.handle_map_event():
                click_timer.reset()
                continue

            if not self.bar_opened():
                # End
                if self.get() == index:
                    # 确认换队成功：下拉条已收起 + 当前舰队编号等于目标编号。
                    # 此时才清点击记录；失败路径必须保留累积，否则点不开菜单的
                    # 死循环会失去唯一的保护（见 clear_switch_click_record 注释）。
                    self.clear_switch_click_record()
                    break
                # Game can't response that fast
                elif click_timer.reached():
                    self.open()

            # Click
            if click_timer.reached():
                main.device.click(button)
                click_timer.reset()

    def ensure_to_be(self, index):
        """
        Set to a specific fleet.

        Args:
            index (int): Fleet index, 1-4.

        Returns:
            bool: If fleet switched.
        """
        confirm_timer = Timer(1.5, count=5).start()
        main = self.main
        for _ in main.loop():
            if confirm_timer.reached():
                break

            if main.handle_map_event():
                confirm_timer.reset()
                continue

            current = self.get()
            if current == index:
                logger.info(f'[大世界-舰队选择] 当前已是舰队 {index}')
                return False
            elif current > 0:
                logger.info(f'[大世界-舰队选择] 切换到舰队 {index}')
                self.open()
                self.click(index)
                return True

        logger.warning('[大世界-舰队选择] 未知的大世界舰队, 使用当前舰队')
        return False

class StorageFleetSelector(FleetSelector):
    FLEET_LIST = [STORAGE_FLEET_1, STORAGE_FLEET_2, STORAGE_FLEET_3, STORAGE_FLEET_4, STORAGE_FLEET_5]
    SUBMARINE_FLEET = 5

    def __init__(self, main):
        """
        Args:
            main (OSFleetSelector): Alas module
        """
        self._choose = STORAGE_FLEET_CHOOSE
        self._bar = STORAGE_FLEET_BAR
        self.main = main

    def bar_opened(self):
        # Check the 3-13 column
        area = self._bar.area
        area = (area[0] + 3, area[1], area[0] + 13, area[3])
        # Should have at least 2 gray option and 1 orange option.
        return self.main.image_color_count(area, color=(200, 207, 231), threshold=221, count=400) \
               and self.main.image_color_count(area, color=(214, 150, 96), threshold=221, count=150)

    def get_button(self, index):
        return super().get_button(index, 6)


class OSFleetSelector(MapEventHandler):
    @cached_property
    def fleet_selector(self):
        return FleetSelector(main=self)

    @cached_property
    def storage_fleet_selector(self):
        return StorageFleetSelector(main=self)
