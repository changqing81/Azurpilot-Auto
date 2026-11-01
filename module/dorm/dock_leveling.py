"""练级换船：从船坞挑低等级舰船换上已毕业的出击舰位。

由独立调度任务 DockLeveling 周期触发；配置组挂在后宅任务页
（module/config/argument/task.yaml 的 Dorm 任务），开关与目标等级
都在 GUI 的后宅页设置。

语义（对齐需求原话）：
- 不限制编队：1–6 队逐一检查，哪队有毕业舰位就在哪队换。
- 只在船坞里找低于目标等级的舰船：选船页已按等级降序排列，
  翻页寻找未编队（fleet==0）且等级低于目标的卡面。
- 找到了就更换；没找到保持现状：本轮不做任何修改、不停止任务。
"""

from module.exception import FleetManagementError
from module.logger import logger
from module.retire.fleet_management import FleetManagement, position_button
from module.runtime.fleet_formation import VisualDockScanner


class DockLeveling(FleetManagement):
    TARGET_FLEETS = (1, 2, 3, 4, 5, 6)

    def _dock_low_level_card(self, target):
        """在当前等级降序选船页滚动寻找未编队且等级低于目标的卡面。

        Returns:
            Card：可点击的候选卡面；翻完页都没有则返回 None。
        """
        scanner = VisualDockScanner()
        for cards, _ in self._dock_pages(scanner, max_rows=4):
            for card in cards:
                if card.fleet:
                    continue
                level = card.ship.get('level') if isinstance(card.ship, dict) else None
                if isinstance(level, int) and 0 < level < target:
                    return card
        return None

    def _swap_graduated_slot(self, fleet, slot, target):
        """把指定毕业舰位换成低等级船；返回是否换上。

        Pages:
            in: Any
            out: 编队页（选船页已关闭）
        """
        self.fleet_enter(fleet)
        readings = self.prepare_rotation(fleet, slots=(slot,))
        info = readings.get(slot)
        level = info.get('level') if isinstance(info, dict) else None
        if not (isinstance(level, int) and level >= target):
            # 编队快照与判断时不一致，保持现状
            self.finish_rotation()
            return False
        self._open_slot(slot)
        self._rotation_picker = (fleet, slot)
        self._prepare_level_dock()
        card = self._dock_low_level_card(target)
        if card is None:
            self._close_picker()
            self._rotation_picker = None
            return False
        baseline = self.device.image.copy()
        self.device.click(position_button(
            (card.x + 20, card.y + 40, card.x + 115, card.y + 145), '练级候选卡面'))
        self._confirm_replacement(baseline)
        self._rotation_picker = None
        self.finish_rotation()
        logger.info(f'第{fleet}舰队舰位{slot} 已换上低等级舰船（原等级 {level}）')
        return True

    def run(self):
        """执行一轮练级换船：毕业舰位换上船坞低等级舰船。

        Pages:
            in: Any
            out: page_main 或 page_fleet
        """
        target = int(self.config.DockLeveling_TargetLevel or 100)
        logger.hr(f'练级换船：目标等级 {target}', level=1)
        swapped = 0
        for fleet in self.TARGET_FLEETS:
            try:
                readings = self.prepare_rotation(fleet)
            except FleetManagementError as exc:
                logger.warning(f'第{fleet}舰队扫描中断：{exc}')
                self.finish_rotation()
                continue
            slots = sorted(readings.keys()) if isinstance(readings, dict) else []
            graduated = []
            for slot in slots:
                info = readings.get(slot)
                level = info.get('level') if isinstance(info, dict) else None
                if isinstance(level, int) and level >= target:
                    graduated.append(slot)
            logger.attr(f'第{fleet}舰队毕业舰位', graduated)
            self.finish_rotation()
            for slot in graduated:
                try:
                    if self._swap_graduated_slot(fleet, slot, target):
                        swapped += 1
                except FleetManagementError as exc:
                    logger.warning(f'第{fleet}舰队舰位{slot} 换船中断：{exc}')
                    break
        if swapped:
            self._commit_formation()
        logger.attr('本轮换上练级船', swapped)
        # 没找到低等级船时保持现状：不修改、不停止任务，等下次调度再试。
        return True
