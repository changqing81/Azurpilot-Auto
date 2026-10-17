"""掉落物品统计。

从战斗结算的获得物品截图中识别和统计各类掉落物，
支持 1~3 行物品网格的自动检测和数量 OCR 识别。
"""

from module.combat.assets import *
from module.handler.assets import *
from module.statistics.assets import GET_ITEMS_ODD
from module.statistics.item import *
from module.statistics.utils import *

ITEM_GROUP = ItemGrid(None, {}, template_area=(40, 21, 89, 70), amount_area=(60, 71, 91, 92))
ITEM_GRIDS_1_ODD = ButtonGrid(origin=(336, 298), delta=(128, 0), button_shape=(96, 96), grid_shape=(5, 1))
ITEM_GRIDS_1_EVEN = ButtonGrid(origin=(400, 298), delta=(128, 0), button_shape=(96, 96), grid_shape=(4, 1))
ITEM_GRIDS_2 = ButtonGrid(origin=(336, 227), delta=(128, 142), button_shape=(96, 96), grid_shape=(5, 2))
ITEM_GRIDS_3 = ButtonGrid(origin=(336, 223), delta=(128, 149), button_shape=(96, 96), grid_shape=(5, 2))


def merge_get_items(item_list_1, item_list_2):
    """
    Args:
        item_list_1 (list[Item]):
        item_list_2 (list[Item]):

    Returns:
        list[Item]:
    """
    return list(set(item_list_1 + item_list_2))


class GetItemsStatistics:
    # 物品网格。默认共用模块级的 ITEM_GROUP（战斗掉落那份）；科研统计等
    # 需要另一套模板的场景传入自己的 ItemGrid，避免两套模板互相污染。
    grid = None
    amount_area = (60, 71, 91, 92)

    def _target_grid(self):
        """返回本次识别使用的物品网格。

        Returns:
            ItemGrid: 实例自带的网格，未设置时为模块级共享的 ITEM_GROUP。
        """
        return self.grid if self.grid is not None else ITEM_GROUP

    def appear_on(self, image):
        return GET_ITEMS_1.match(image, offset=(20, 20)) or GET_ITEMS_2.match(image, offset=(20, 20))

    @staticmethod
    def _stats_get_items_is_odd(image):
        """
        Args:
            image (np.ndarray):

        Returns:
            bool: If the number of items in row is odd.
        """
        image = crop(image, GET_ITEMS_ODD.area, copy=False)
        return np.mean(rgb2gray(image) > 127) > 0.1

    def _stats_get_items_load(self, image):
        """
        Args:
            image (np.ndarray):
        """
        grid = self._target_grid()
        grid.item_class = Item
        grid.similarity = 0.92
        grid.amount_area = self.amount_area
        grid.grids = None
        if INFO_BAR_1.appear_on(image):
            raise ImageError('Stat image has info_bar')
        elif GET_ITEMS_1.match(image, offset=(5, 0)):
            grid.grids = ITEM_GRIDS_1_ODD if self._stats_get_items_is_odd(image) else ITEM_GRIDS_1_EVEN
        elif GET_ITEMS_2.match(image, offset=(5, 0)):
            grid.grids = ITEM_GRIDS_2
        elif GET_ITEMS_3.match(image, offset=(5, 0)):
            grid.grids = ITEM_GRIDS_3
        else:
            raise ImageError('Stat image is not a get_items image')

    def stats_get_items(self, image, **kwargs):
        """
        Args:
            image (np.ndarray):

        Returns:
            list[Item]:
        """
        self._stats_get_items_load(image)
        grid = self._target_grid()

        if grid.grids is None:
            return []
        else:
            grid.predict(image, **kwargs)
            return grid.items

    def load_template_folder(self, folder):
        """
        Args:
            folder (str): Template folder.
        """
        self._target_grid().load_template_folder(folder)

    def extract_template(self, image, folder):
        """
        Args:
            image:
            folder: Folder to save new templates.
        """
        self._stats_get_items_load(image)
        grid = self._target_grid()
        if grid.grids is not None:
            new = grid.extract_template(image)
            for name, im in new.items():
                save_image(im, os.path.join(folder, f'{name}.png'))
