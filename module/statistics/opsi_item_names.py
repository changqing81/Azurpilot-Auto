"""大世界掉落物品的名称与类别表。

模板名（如 ``PlatePlaneT4``）到中文名 / 稀有度 / 类别的静态映射，数据落在
``assets/stats/opsi_item_names.json``。

上游只覆盖金菜与彩图纸共 13 条（见 92ad760a），本地统计页要显示识别层认得的
全部物品——材料、黄币、猫箱、机密报告都会进表，所以这里按
``assets/stats/opsi_items/`` 的模板清单补全到 46 条。

``category`` 决定明细表里的分组，取值与顺序见 ``CATEGORY_ORDER``；表里查不到的
物品落到 ``other``，不会丢数据。
"""

import json

from module.logger import logger


NAME_TABLE_PATH = './assets/stats/opsi_item_names.json'

CATEGORY_PLATE = 'plate'
CATEGORY_DESIGN = 'design'
CATEGORY_REPORT = 'report'
CATEGORY_MATERIAL = 'material'
CATEGORY_CURRENCY = 'currency'
CATEGORY_CAT = 'cat'
CATEGORY_COORDINATE = 'coordinate'
CATEGORY_OTHER = 'other'

# 明细表的类别顺序：贵重物在前、材料与货币在后，界面分组按这个走
CATEGORY_ORDER = (
    CATEGORY_PLATE,
    CATEGORY_DESIGN,
    CATEGORY_REPORT,
    CATEGORY_CAT,
    CATEGORY_COORDINATE,
    CATEGORY_MATERIAL,
    CATEGORY_CURRENCY,
    CATEGORY_OTHER,
)

_name_table = None


def load_name_table() -> dict:
    """读取模板名到名称/类别表的映射（进程内缓存）。

    Returns:
        dict: {模板名: {'zh': ..., 'en': ..., 'rarity': ..., 'category': ...}}；
            表缺失或损坏时返回空字典，调用方用模板名兜底。
    """
    global _name_table
    if _name_table is None:
        try:
            with open(NAME_TABLE_PATH, encoding='utf-8') as f:
                _name_table = json.load(f)
        except (OSError, ValueError):
            logger.warning(f'[大世界掉落] 物品名称表不存在或损坏: {NAME_TABLE_PATH}')
            _name_table = {}
    return _name_table


def item_info(template_name: str) -> dict:
    """取物品的中文名、英文名、稀有度与类别。

    Args:
        template_name (str): 模板名（不含扩展名与数字后缀），如 'PlateGeneralT4'。

    Returns:
        dict: {'zh', 'en', 'rarity', 'category'}；查不到时中文名回落到模板名，
            category 回落到 'other'。
    """
    name = str(template_name or '')
    info = load_name_table().get(name)
    if info:
        return {
            'zh': info.get('zh') or name,
            'en': info.get('en') or name,
            'rarity': info.get('rarity'),
            'category': info.get('category') or CATEGORY_OTHER,
        }
    return {'zh': name, 'en': name, 'rarity': None, 'category': CATEGORY_OTHER}


def category_of(template_name: str) -> str:
    """取物品所属类别（界面分组用）。

    Args:
        template_name (str): 模板名。

    Returns:
        str: CATEGORY_* 之一；查不到时为 CATEGORY_OTHER。
    """
    return item_info(template_name)['category']
