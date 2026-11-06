"""岛屿货运委托启动判定的单元测试。

这些用例借鉴自 wess09/AzurPilot 提交 bb64569d
（fix(island): 兼容货运委托空货物槽并自动装载）。

背景：本地旧逻辑要求「所有有效货物格都已装载」才允许启动
（self.start = self.items.select(load=True).count == self.items.count），
当委托尾部存在空槽、或货物尚未装载时该等式恒为假，委托永远不会被提交。
现在改为 cargo_can_start()：只要求有实际货物格、按钮可用、未命中黑名单，
未装载的货物交给按钮自身的「全部装载」补齐。
"""

import unittest

from module.island.island_cargo_preparation import cargo_can_start, should_replace_unfillable


class CargoPreparationStartTests(unittest.TestCase):
    """验证货物槽为空或未装载时不会阻止货运委托启动。"""

    def test_trailing_empty_slots_do_not_block_start(self):
        """尾部空槽不算有效货物格，只剩 1 个货物格时仍应允许启动。"""
        self.assertTrue(
            cargo_can_start(
                available_item_count=1,
                action_button_active=True,
                refresh=False,
            )
        )

    def test_unloaded_available_items_delegate_loading_to_button(self):
        """实际货物格是否存在才是启动条件；未装载由「全部装载」按钮补齐。"""
        self.assertTrue(
            cargo_can_start(
                available_item_count=2,
                action_button_active=True,
                refresh=False,
            )
        )

    def test_no_available_item_blocks_start(self):
        """没有任何有效货物格时不能启动。"""
        self.assertFalse(
            cargo_can_start(
                available_item_count=0,
                action_button_active=True,
                refresh=False,
            )
        )

    def test_blacklisted_cargo_blocks_start(self):
        """命中黑名单的委托要先更换，不能直接启动。"""
        self.assertFalse(
            cargo_can_start(
                available_item_count=2,
                action_button_active=True,
                refresh=True,
            )
        )

    def test_inactive_action_button_blocks_start(self):
        """右侧按钮不可用（灰化）时不能启动。"""
        self.assertFalse(
            cargo_can_start(
                available_item_count=2,
                action_button_active=False,
                refresh=False,
            )
        )


class CargoPreparationReplaceTests(unittest.TestCase):
    """更换委托会扣除运输次数且不退还已提交货物，只更换尚未提交、仍缺货的委托。"""

    def test_untouched_short_commission_is_replaced(self):
        """尚未提交任何货物且缺货的委托应当更换。"""
        self.assertTrue(should_replace_unfillable(short=True, delivered=False, refresh=False))

    def test_partly_delivered_commission_is_kept(self):
        """已提交部分货物的委托更换后不退还，必须保留等待补齐。"""
        self.assertFalse(should_replace_unfillable(short=True, delivered=True, refresh=False))

    def test_fillable_commission_is_kept(self):
        """货物能凑齐的委托不需要更换。"""
        self.assertFalse(should_replace_unfillable(short=False, delivered=False, refresh=False))

    def test_blacklisted_commission_uses_blacklist_path(self):
        """命中黑名单的委托走黑名单更换路径，不重复计入缺货更换。"""
        self.assertFalse(should_replace_unfillable(short=True, delivered=False, refresh=True))


if __name__ == '__main__':
    unittest.main()
