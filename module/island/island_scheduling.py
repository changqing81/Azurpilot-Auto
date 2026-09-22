"""岛屿计划统一调度模块。

把岛屿下的 16 个原子任务收敛为「调度器 → IslandPlan → 岛屿子模块」三层结构：

1. 一轮开始时先把原子任务的独立调度关掉，避免与父任务重复进岛；
2. 按 `IslandPlan` 组里的 16 个 `EnableXxx` 开关挑出要跑的模块（可选 `TaskOrder` 只调整顺序），
   再从中筛出 `Scheduler.NextRun` 已到期的子模块；
3. 以子任务身份代跑（配置绑定与 NextRun 都归子任务自己所有），
   子模块既有的「每日一次 / 每周一次 / 固定刷新」时间语义因此原样生效；
4. 一轮结束后把下一轮排在 `IslandPlan.IntervalHours` 小时之后（收敛到 1~24 小时）；
   若开启 `IslandPlan.RespectSubTaskTimes`（默认开），则当某个子模块在 60 分钟以外到期时
   会提前进岛去接那个时刻，否则完全按固定间隔。

Pages:
    in: 任意页面
    out: 岛屿主页面
"""
import importlib
from datetime import datetime, timedelta

from module.base.filter import Filter
from module.config.config import Function, TaskEnd, name_to_function
from module.config.deep import deep_get
from module.config.redirect_utils.utils import (
    ISLAND_PLAN_INTERVAL_DEFAULT,
    ISLAND_PLAN_INTERVAL_MAX,
    ISLAND_PLAN_INTERVAL_MIN,
    clamp_island_plan_interval,
)
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

    岛屿原子任务名 → (模块路径, 类名)。键集合即 `IslandPlan` 组里 `EnableXxx` 开关的
    取值范围，声明顺序同时是执行顺序（快 / 时间敏感在前，耗时的餐饮家族随后，
    最耗时的经营模块放最后）。与 `argument.yaml` 里 `EnableXxx` 的排列保持一致。
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
        'IslandRestaurant': ('module.island.island_restaurant', 'IslandRestaurant'),
        'IslandTeahouse': ('module.island.island_teahouse', 'IslandTeahouse'),
        'IslandGrill': ('module.island.island_grill', 'IslandGrill'),
        'IslandJuuEatery': ('module.island.island_juu_eatery', 'IslandJuuEatery'),
        'IslandJuuCoffee': ('module.island.island_juu_coffee', 'IslandJuuCoffee'),
        # 经营模块最耗时（分批逛商店，整轮超时的主要来源），排最后
        'IslandBusiness': ('module.island.island_business', 'IslandBusiness'),
    }

    # 运行间隔（小时）：指向配置层的同一套边界（config 层不许 import module.island，
    # 所以只能由配置层持有常量、这里引用）
    MIN_INTERVAL_HOURS = ISLAND_PLAN_INTERVAL_MIN
    MAX_INTERVAL_HOURS = ISLAND_PLAN_INTERVAL_MAX
    DEFAULT_INTERVAL_HOURS = ISLAND_PLAN_INTERVAL_DEFAULT
    # 岛屿内容每天 03:00 刷新，进岛时间到点后多等几分钟，避免和刷新抢跑
    RESET_BUFFER_MINUTES = 5
    # 只有「距现在超过这个时长」的子模块 NextRun 才允许把下一轮拉近。
    # 餐馆家族在开完店后会写 `task_delay(minute=0)`（立刻回头补货），每日订单 / 有鱼餐馆 /
    # 白熊饮品等也会把 NextRun 指到几十分钟内。这类「近期待重检」几乎每轮都出现，
    # 若也算作提前进岛的理由，IntervalHours 会被一直压到下界（2026-09-22 实测：
    # 配 12 小时，实际每 33 分钟进一次岛，一天跑了 18 轮）。
    # 阈值同时替代了早年那个 30 分钟的下界：任何留下来的候选都必然 ≥ 60 + 5 分钟。
    EARLY_TRIGGER_MIN_MINUTES = 60

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
        finished = False
        try:
            self._converge_atomic_tasks()

            task_list = self._build_task_list()
            due_tasks = [name for name in task_list if self._is_sub_task_due(name)]
            skipped = [name for name in task_list if name not in due_tasks]
            logger.attr('岛屿计划清单', task_list)
            logger.attr('本轮执行', due_tasks)
            if skipped:
                logger.attr('未到期跳过', skipped)

            if due_tasks:
                # 岛屿内导航一律 get_ship=False：岛屿/管理页与「获得舰船」弹窗的特征容易
                # 互认，交给 UI 去处理会点错东西。注意 get_ship 是 ui_goto 的参数，
                # ui_ensure 的签名里没有（曾因此抛 TypeError 让整轮任务直接失败）。
                self.ui_goto(page_island, get_ship=False)
                failed = []
                for task_name in due_tasks:
                    if not self._run_sub_task(task_name):
                        failed.append(task_name)
                if failed:
                    logger.warning(f'[岛屿计划] 本轮失败子任务: {failed}')
                logger.info('[岛屿计划] 一轮运行结束')
            else:
                logger.info('[岛屿计划] 没有到期的岛屿子模块，跳过本次进岛')
            finished = True
        finally:
            # 排期必须放在 finally：一轮被 GameStuckError 之类的致命异常中断时，
            # 如果跳过排期，`Scheduler.NextRun` 会停在已过期的旧值，
            # 调度器下一个空闲槽就立刻重跑（2026-09-22 实测：04:24 那轮 04:32 中断，
            # 04:34 又进岛，间隔只有 9.7 分钟）。
            if not finished:
                logger.warning('[岛屿计划] 本轮被中断，仍按原计划排期，避免立刻重跑')
            self._delay_next_run()

    # ==================== 调度决策 ====================

    def _build_task_list(self):
        """返回本轮清单（顺序即执行顺序）。

        启用与否由 `IslandPlan` 组的 16 个 `EnableXxx` 开关决定，顺序默认取
        `SUB_TASKS` 的声明顺序；`IslandPlan.TaskOrder` 可选，只用来调整顺序
        （未列出的已启用模块排在后面），留空即完全按内置顺序。

        Returns:
            list[str]: 岛屿原子任务名列表。
        """
        enabled = [name for name in self.SUB_TASKS if self._is_sub_task_enabled(name)]
        if not enabled:
            logger.warning('[岛屿计划] 全部子模块开关均已关闭，本轮不执行任何子模块')
            return []

        order = self._parse_task_order()
        if not order:
            return enabled
        listed = [name for name in order if name in enabled]
        return listed + [name for name in enabled if name not in listed]

    @staticmethod
    def _enable_arg(task_name):
        """岛屿原子任务名 → `IslandPlan` 组里的开关名。

        Args:
            task_name (str): 如 `IslandFarm`。

        Returns:
            str: 如 `EnableFarm`。
        """
        return 'Enable' + task_name[len('Island'):]

    def _is_sub_task_enabled(self, task_name):
        """读取子模块开关。

        配置里读不到时按「开启」处理，避免旧配置缺字段导致整轮不跑。

        Args:
            task_name (str): 岛屿原子任务名。

        Returns:
            bool: 是否启用。
        """
        return bool(getattr(self.config, f'IslandPlan_{self._enable_arg(task_name)}', True))

    def _parse_task_order(self):
        """解析可选的 `IslandPlan.TaskOrder`，返回自定义顺序。

        复用 `Filter` 天然获得「按文本顺序、去重、丢弃非法名」的行为；
        留空或全部非法时返回空列表，由调用方回退到内置顺序。

        Returns:
            list[str]: 岛屿原子任务名列表，可能为空。
        """
        text = getattr(self.config, 'IslandPlan_TaskOrder', '')
        if not isinstance(text, str) or not text.strip():
            return []
        objs = [name_to_function(name) for name in self.SUB_TASKS]
        f = Filter(regex=r'(.*)', attr=['command'])
        f.load(text)
        return [obj.command for obj in f.apply(objs) if isinstance(obj, Function)]

    def _is_sub_task_due(self, task_name):
        """判断子模块的 `Scheduler.NextRun` 是否已到期。

        只看到期时间、不看 `Scheduler.Enable`——原子任务的 Enable 在本轮开始时
        已被收敛为 False，是否执行完全由 `EnableXxx` 开关与到期时间决定。

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

    def _get_interval_hours(self):
        """读取运行间隔并收敛到 `[MIN, MAX]` 小时。

        `IslandPlan.IntervalHours` 是自由填写的输入框，配置层在落盘与保存时已会收敛
        （`clamp_island_plan_interval`），这里再兜一次，防止运行期被临时改写。

        Returns:
            int | float: 合法的小时数。
        """
        raw = getattr(self.config, 'IslandPlan_IntervalHours', ISLAND_PLAN_INTERVAL_DEFAULT)
        hours = clamp_island_plan_interval(raw)
        try:
            same = float(raw) == hours
        except (TypeError, ValueError):
            same = False
        if not same:
            logger.warning(
                f'[岛屿计划] 运行间隔「{raw}」不合法，本次按 {hours} 小时执行'
                f'（有效范围 {ISLAND_PLAN_INTERVAL_MIN}~{ISLAND_PLAN_INTERVAL_MAX} 小时）'
            )
        return hours

    def _next_island_run(self, interval_hours):
        """计算下一轮进岛时间。

        默认（`IslandPlan.RespectSubTaskTimes` 开启）取「固定间隔」与「最早到期的子模块
        NextRun」中较早者：子模块把下次时间指到 18:00（每日采集第二次）/ 次日 03:00
        （每日订单、每日任务、每日补给）这类**固定时刻**，如果只按固定间隔进岛，
        这些时刻会落在两轮之间——2026-09-21 实测：每日采集 02:03 把下次设成当天 18:00，
        而下一轮是 14:23（未到期跳过），18:00 那次采集就永远丢了。

        但只有「距现在超过 `EARLY_TRIGGER_MIN_MINUTES` 分钟」的 NextRun 才算数：
        餐馆家族 `task_delay(minute=0)` 的立刻回访、几十分钟后的重检都属于噪声，
        每轮结束都会出现，认了它们就等于把间隔一直压到下界（2026-09-22 实测见常量注释）。

        关掉开关则完全按固定间隔，不再理会子模块时刻。

        Args:
            interval_hours (float): 用户配置的运行间隔（小时），作为间隔上限。

        Returns:
            datetime: 下一轮进岛时间。
        """
        cap = current_time() + timedelta(hours=interval_hours)
        if not self._respect_sub_task_times():
            return cap

        threshold = current_time() + timedelta(minutes=self.EARLY_TRIGGER_MIN_MINUTES)
        earliest = None
        for name in self.SUB_TASKS:
            next_run = deep_get(
                self.config.data, keys=f'{name}.Scheduler.NextRun', default=None
            )
            if not isinstance(next_run, datetime) or next_run <= threshold:
                continue
            earliest = next_run if earliest is None else min(earliest, next_run)
        if earliest is None or earliest >= cap:
            return cap
        # 岛屿内容每天 03:00 刷新，到点后稍等几分钟再进岛，避免和刷新抢跑
        return earliest + timedelta(minutes=self.RESET_BUFFER_MINUTES)

    def _respect_sub_task_times(self):
        """是否允许子模块的到期时刻把下一轮提前。

        配置里读不到时按「尊重」处理，保持既有行为。

        Returns:
            bool: 是否尊重子模块时刻。
        """
        return bool(getattr(self.config, 'IslandPlan_RespectSubTaskTimes', True))

    def _delay_next_run(self):
        """把岛屿计划自身延迟到下一轮进岛时间。"""
        target = self._next_island_run(self._get_interval_hours())
        self.config.task_delay(target=target)
        logger.info(f'[岛屿计划] 下次进岛时间: {self.config.Scheduler_NextRun}')

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
                action='请结合下方堆栈与截图定位；若反复失败可在岛屿计划设置里关闭该模块的开关。',
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