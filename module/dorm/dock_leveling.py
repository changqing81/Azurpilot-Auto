"""练级换船：从船坞挑低等级舰船换上出击编队，毕业舰船自动让位。

由独立调度任务 DockLeveling 周期触发；配置组挂在后宅任务页
（module/config/argument/task.yaml 的 Dorm 任务），开关与目标等级
都在 GUI 的后宅页设置。

流程：
1. 进入第 1 舰队编队页并实读各舰位等级（prepare_rotation）。
2. 编队中存在低于目标等级的舰位时，说明还在练级途中——等下次触发。
3. 编队中全部舰位达到目标等级（毕业）时，在等级降序船坞中
   逐舰位挑选未编队且低于目标等级的舰船换上（换人即练级开始）。
4. 船坞已无低于目标等级的未编队舰船时停止调度任务。
"""

from module.exception import FleetManagementError, ScriptEnd
from module.logger import logger
from module.retire.fleet_management import FleetManagement, position_button


class DockLeveling(FleetManagement):
    def _dock_low_level_card(self, target):
        """在当前等级降序选船页滚动寻找未编队且等级低于目标的卡面。

        Returns:
            卡面定位（可点击）或 None（翻完页都没有）。
        """
        from module.runtime.fleet_formation import VisualDockScanner
        scanner = VisualDockScanner()
        seen = set()
        for cards, _ in self._dock_pages(scanner, max_rows=4):
            for card in cards:
                if card.fleet:
                    # 已编队舰船不作为练级候选
                    continue
                ship = scanner.read_card(self.device.image, card) if hasattr(scanner, 'read_card') else None
                level = ship.get('level') if isinstance(ship, dict) else None
                if not isinstance(level, int) or level >= target:
                    continue
                key = (card.x, card.y)
                if key in seen:
                    continue
                seen.add(key)
                return card
        return None

    def run(self):
        """执行一次练级换船检查与替换。

        Pages:
            in: Any
            out: page_main
        """
        target = int(self.config.DockLeveling_TargetLevel or 100)
        logger.hr(f'练级换船：目标等级 {target}', level=1)
        self.fleet_enter(1)
        readings = self.prepare_rotation(1)
        slots = sorted(readings.keys()) if isinstance(readings, dict) else []
        current = {}
        for slot in slots:
            info = readings.get(slot)
            if isinstance(info, dict):
                current[slot] = info.get('level')
        graduated = [slot for slot, level in current.items()
                     if isinstance(level, int) and level >= target]
        training = [slot for slot, level in current.items()
                    if isinstance(level, int) and level < target]
        logger.attr('毕业舰位', graduated)
        logger.attr('练级中舰位', training)
        if not graduated:
            # 编队里还有低等级船在练级，或已全部低于目标——本轮无需换船
            self.finish_rotation()
            if not training:
                logger.warning('编队为空或等级未读全，本轮跳过')
            return False
        # 全部毕业 → 逐舰位换上低等级船
        self.finish_rotation()
        swapped = 0
        for slot in graduated:
            try:
                self._open_slot(slot)
                self._rotation_picker = (1, slot)
                self._prepare_level_dock()
                card = self._dock_low_level_card(target)
                if card is None:
                    self._close_picker()
                    self._rotation_picker = None
                    continue
                baseline = self.device.image.copy()
                self.device.click(position_button(
                    (card.x + 20, card.y + 40, card.x + 115, card.y + 145), '练级候选卡面'))
                self._confirm_replacement(baseline)
                self._rotation_picker = None
                swapped += 1
            except FleetManagementError as exc:
                logger.warning(f'舰位 {slot} 换船中断：{exc}')
                break
        self.finish_rotation()
        self._commit_formation()
        logger.attr('本轮换上练级船', swapped)
        if swapped == 0:
            # 船坞已无可练的低等级船：停止调度，避免空转
            logger.info('船坞已无低于目标等级的未编队舰船，练级换船任务停止')
            self.config.Scheduler_Enable = False
            self.config.task_stop()
        return True
