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

from module.island.island_cargo_preparation import cargo_can_start


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


if __name__ == '__main__':
    unittest.main()
