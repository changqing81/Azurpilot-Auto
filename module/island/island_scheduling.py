"""岛屿计划统一调度模块。

把岛屿下的 16 个原子任务收敛为「调度器 → IslandPlan → 岛屿子模块」三层结构：

1. 一轮开始时先把原子任务的独立调度关掉，避免与父任务重复进岛；
2. 按 `IslandPlan.TaskPriority` 清单顺序，挑出 `Scheduler.NextRun` 已到期的子模块；
3. 以子任务身份代跑（配置绑定与 NextRun 都归子任务自己所有），
   子模块既有的「每日一次 / 每周一次 / 固定刷新」时间语义因此原样生效；
4. 一轮结束后延迟 `IslandPlan.IntervalHours` 小时才再次进岛。

Pages:
    in: 任意页面
    out: 岛屿主页面
"""
import importlib
from datetime import datetime

from module.base.filter import Filter
from module.config.config import Function, TaskEnd, name_to_function
from module.config.deep import deep_get
from module.config.time_source import now as current_time
from module.config.utils import DEFAULT_TIME
from module.exception import (
    GameBugError,
    GameStuckError,
    GameTooManyClickError,
    RequestHumanTakeover,
)
from module.island.island import Island
from module.logger import logger
from module.ui.page import page_island


class IslandScheduling(Island):
    """岛屿计划（赤石计划）统一调度器。

    岛屿原子任务名 → (模块路径, 类名)。键集合即 `IslandPlan.TaskPriority` 的合法取值，
    顺序与 argument.yaml 中的默认清单保持一致。
    """

    SUB_TASKS = {
        'IslandAirDrop': ('module.island.island_air_drop', 'IslandAirDrop'),
        'IslandDailyGather': ('module.island.island_daily_gather', 'IslandDailyGather'),
        'IslandCargoPreparation': ('module.island.island_cargo_preparation', 'IslandCargoPreparation'),
        'IslandDailyOrder': ('module.island.island_daily_order', 'IslandDailyOrder'),
        'IslandDailyInteract': ('module.island.island_daily_interact', 'IslandDailyInteract'),
        'IslandFarm': ('module.island.island_farm', 'IslandFarm'),
        'IslandRancher': ('module.island.island_rancher', 'IslandRancher'),
        'IslandMineForest': ('module.island.island_mine_forest', 'IslandMineForest'),
        'IslandPearlSell': ('module.island.island_pearl_sell', 'IslandPearlSell'),
        'IslandManufacture': ('module.island.island_manufacture', 'IslandManufacture'),
        'IslandBusiness': ('module.island.island_business', 'IslandBusiness'),
        'IslandRestaurant': ('module.island.island_restaurant', 'IslandRestaurant'),
        'IslandTeahouse': ('module.island.island_teahouse', 'IslandTeahouse'),
        'IslandGrill': ('module.island.island_grill', 'IslandGrill'),
        'IslandJuuEatery': ('module.island.island_juu_eatery', 'IslandJuuEatery'),
        'IslandJuuCoffee': ('module.island.island_juu_coffee', 'IslandJuuCoffee'),
    }

    # 游戏状态已损坏，继续代跑只会连环失败，需要交给调度器的重启流程处理
    FATAL_EXCEPTIONS = (
        GameBugError,
        GameStuckError,
        GameTooManyClickError,
        RequestHumanTakeover,
        TaskEnd,
    )

    def run(self):
        """岛屿计划入口：一轮运行内代跑所有到期的岛屿子模块。"""
        logger.hr('岛屿计划运行', level=1)
        self._converge_atomic_tasks()

        task_list = self._build_task_list()
        due_tasks = [name for name in task_list if self._is_sub_task_due(name)]
        skipped = [name for name in task_list if name not in due_tasks]
        logger.attr('岛屿计划清单', task_list)
        logger.attr('本轮执行', due_tasks)
        if skipped:
            logger.attr('未到期跳过', skipped)

        if due_tasks:
            self.ui_ensure(page_island, get_ship=False)
            failed = []
            for task_name in due_tasks:
                if not self._run_sub_task(task_name):
                    failed.append(task_name)
            if failed:
                logger.warning(f'[岛屿计划] 本轮失败子任务: {failed}')
            logger.info('[岛屿计划] 一轮运行结束')
        else:
            logger.info('[岛屿计划] 没有到期的岛屿子模块，跳过本次进岛')

        self._delay_next_run()

    # ==================== 调度决策 ====================

    def _build_task_list(self):
        """解析 `IslandPlan.TaskPriority`，返回本轮清单（顺序即执行顺序）。

        未在清单中出现的模块本轮不执行；重复或非法名称由 Filter 自动处理。

        Returns:
            list[str]: 岛屿原子任务名列表。
        """
        objs = [name_to_function(name) for name in self.SUB_TASKS]
        f = Filter(regex=r'(.*)', attr=['command'])
        f.load(self.config.IslandPlan_TaskPriority)
        task_list = [obj.command for obj in f.apply(objs) if isinstance(obj, Function)]
        if not task_list:
            logger.warning('[岛屿计划] TaskPriority 为空或全部非法，本轮不执行任何子模块')
        return task_list

    def _is_sub_task_due(self, task_name):
        """判断子模块的 `Scheduler.NextRun` 是否已到期。

        只看到期时间、不看 `Scheduler.Enable`——原子任务的 Enable 在本轮开始时
        已被收敛为 False，是否执行完全由 TaskPriority 清单与到期时间决定。

        Args:
            task_name (str): 岛屿原子任务名。

        Returns:
            bool: 是否到期。
        """
        next_run = deep_get(
            self.config.data, keys=f'{task_name}.Scheduler.NextRun', default=DEFAULT_TIME
        )
        if not isinstance(next_run, datetime):
            return True
        return next_run <= current_time()

    def _converge_atomic_tasks(self):
        """关闭岛屿原子任务的独立调度，避免与父任务重复进岛。

        一次性写入并保存，避免逐个 `cross_set()` 触发多次配置重载。
        """
        changed = []
        for task_name in self.SUB_TASKS:
            if deep_get(self.config.data, keys=f'{task_name}.Scheduler.Enable', default=False):
                changed.append(task_name)
                self.config.modified[f'{task_name}.Scheduler.Enable'] = False
        if changed:
            self.config.save()
            logger.info(f'[岛屿计划] 已关闭原子任务的独立调度: {changed}')

    def _delay_next_run(self):
        """把岛屿计划自身延迟到 IntervalHours 小时之后。"""
        interval = self.config.IslandPlan_IntervalHours
        self.config.task_delay(minute=interval * 60)
        logger.info(f'[岛屿计划] 下次进岛时间: {self.config.Scheduler_NextRun}（间隔 {interval} 小时）')

    # ==================== 子模块代跑 ====================

    def _run_sub_task(self, task_name):
        """以子任务身份代跑一个岛屿子模块，并隔离非致命异常。

        GameBugError / GameStuckError / GameTooManyClickError / RequestHumanTakeover /
        TaskEnd 直接向上抛，中断本轮；其余异常只记录日志并继续下一个子模块，
        避免单个模块的偶发失败毁掉整轮。

        Args:
            task_name (str): 岛屿原子任务名。

        Returns:
            bool: 子模块是否正常跑完。
        """
        module_path, class_name = self.SUB_TASKS[task_name]
        logger.hr(f'岛屿计划-{task_name}', level=2)
        try:
            self._run_with_island_task_context(task_name, module_path, class_name)
            return True
        except self.FATAL_EXCEPTIONS:
            raise
        except Exception as e:
            logger.error_context(
                title=f'[岛屿计划] 子任务 {task_name} 执行失败',
                reason=f'程序抛出了 {type(e).__name__}: {e}',
                impact='本轮跳过该子模块，继续执行后续子模块。',
                action='请结合下方堆栈与截图定位；若反复失败可先把该模块从 TaskPriority 移除。',
                exc=e,
            )
            return False

    def _run_with_island_task_context(self, task_name, module_path, class_name):
        """以指定岛屿子任务身份执行逻辑。

        与「大世界智能调度+」的 `_run_with_opsi_task_context()` 同构：临时切换
        `config.task` 与配置绑定，让子模块读到自己那一组参数、`task_delay()` 写回
        自己的 `Scheduler.NextRun`。

        与 Opsi 版本的两点刻意差异：
        - 不设置 `_smart_scheduling_context`（那是大世界专用语义）；
        - `_disable_task_switch` 全程为 True——岛屿是一次进岛跑完，中途切走只会
          留下半途状态。

        Args:
            task_name (str): 岛屿原子任务名。
            module_path (str): 子模块的导入路径。
            class_name (str): 子模块的类名。
        """
        previous_task = self.config.task
        previous_bind = getattr(self.config, '_bind_task_override', None)
        previous_disable_task_switch = getattr(self.config, '_disable_task_switch', False)
        previous_task_switch_owner = getattr(self.config, '_task_switch_owner', None)

        self.config._disable_task_switch = True
        self.config._task_switch_owner = previous_task
        self.config.task = self._make_task_function(task_name)
        self.config._bind_task_override = task_name
        self.config.bind(task_name)
        try:
            sub_task = getattr(importlib.import_module(module_path), class_name)
            sub_task(config=self.config, device=self.device).run()
        finally:
            self.config.task = previous_task

            self.config._disable_task_switch = previous_disable_task_switch
            if previous_task_switch_owner is None:
                if hasattr(self.config, '_task_switch_owner'):
                    delattr(self.config, '_task_switch_owner')
            else:
                self.config._task_switch_owner = previous_task_switch_owner

            if previous_bind is None:
                if hasattr(self.config, '_bind_task_override'):
                    delattr(self.config, '_bind_task_override')
                self.config.bind(self.config.task)
            else:
                self.config._bind_task_override = previous_bind
                self.config.bind(previous_bind)

    def _make_task_function(self, task_name):
        """从当前配置数据构造临时代跑任务对象，保证 NextRun 与真实调度一致。"""
        data = deep_get(self.config.data, keys=task_name, default=None)
        if isinstance(data, dict):
            task = Function(data)
            if task.command != 'Unknown':
                return task
        return name_to_function(task_name)