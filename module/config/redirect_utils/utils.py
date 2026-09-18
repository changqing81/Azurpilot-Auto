"""配置重定向工具函数集。

提供配置版本升级时的值转换函数。
当配置 schema 发生变更时（如选项名称修改、值格式调整），
这些函数负责将旧格式的配置值转换为新格式。

重定向函数在 ConfigUpdater.config_redirect() 中被调用，
确保用户升级后无需手动修改配置文件。

常见的重定向场景：
- 选项名称变更（如 'auto' → 'default'）
- 值格式调整（如布尔值 → 枚举值）
- 服务器名称规范化
"""

from module.config.server import to_server


def upload_redirect(value):
    """
    redirect attr about upload.
    """
    if isinstance(value, list):
        if not value[0] and not value[1]:
            return 'do_not'
        elif value[0] and not value[1]:
            return 'save'
        elif not value[0] and value[1]:
            return 'upload'
        else:
            return 'save_and_upload'
    else:
        if not value:
            return 'do_not'
        else:
            return 'save'


def api_redirect(value):
    """
    redirect attr about api.
    """
    if value == 'auto':
        return 'default'
    elif to_server(value) == 'cn':
        return 'cn_gz_reverse_proxy'
    else:
        return 'default'


def dossier_redirect(value):
    """
    OpsiDossierBeacon -> AttackMode
    """
    if value:
        return 'current_dossier'
    else:
        return 'current'


def enhance_favourite_redirect(value):
    """
    EnhanceFavourite -> ShipToEnhance
    """
    if value:
        return 'all'
    else:
        return 'favourite'


def enhance_check_redirect(value):
    """
    CheckPerCategory should be at least 5
    """
    if isinstance(value, int):
        if value < 5:
            return 5
    return value


def emotion_mode_redirect(value):
    """
    CalculateEmotion + IgnoreLowEmotionWarn -> Emotion.Mode
    """
    calculate, ignore = value
    if calculate:
        if ignore:
            return 'calculate_ignore'
        else:
            return 'calculate'
    else:
        if ignore:
            return 'ignore'
        else:
            # Invalid, fallback to calculate
            return 'calculate'


def change_ship_redirect(value):
    """
    FlagshipChange + FlagshipEquipChange -> ChangeFlagship
    """
    ship, equip = value
    if not ship:
        return 'disabled'
    elif equip:
        return 'ship_equip'
    else:
        return 'ship'


def api_redirect2(value):
    """
    remove shanghai proxy, use guangzhou
    """
    if value == 'cn_sh_reverse_proxy':
        return 'cn_gz_reverse_proxy'
    else:
        return value


def coalition_to_frostfall(value):
    """
    将通用难度名转换为霜落活动的内部关卡编号。
    """
    if value == 'easy':
        return 'tc1'
    elif value == 'normal':
        return 'tc2'
    elif value == 'hard':
        return 'tc3'
    else:
        return value


def coalition_to_little_academy(value):
    """
    将旧联动活动的 TC 关卡编号转换为通用难度名。
    """
    normalized = str(value).lower().replace('-', '')
    if normalized == 'tc1':
        return 'easy'
    elif normalized == 'tc2':
        return 'normal'
    elif normalized == 'tc3':
        return 'hard'
    else:
        return value


def execute_fixed_patrol_scan_redirect(value):
    """
    OpsiHazard1Leveling.ExecuteFixedPatrolScan 旧布尔 → 等级枚举。

    旧版本该配置是布尔开关（开启=强制移动）。升级为 0/1/2 等级后，
    需把旧布尔显式转成数字，避免 GUI 显示（Python 的 True==1 会误配到选项 1）
    与运行时行为不一致：
    - True  → 2（分级恢复，保留最高可用档）
    - False → 0（关闭）
    仅 int 值直接透传。
    """
    if isinstance(value, bool):
        return 2 if value else 0
    return value


# 岛屿计划子模块的短名（`IslandPlan` 组里的开关名 = `Enable` + 短名，
# 对应的原子任务名 = `Island` + 短名）。
# 顺序必须与 module/island/island_scheduling.py 的 SUB_TASKS 保持一致，
# tests/test_island_scheduling.py 有断言守住这一点。
ISLAND_PLAN_SUB_TASKS = [
    'AirDrop',
    'DailyGather',
    'CargoPreparation',
    'DailyOrder',
    'DailyInteract',
    'Farm',
    'Rancher',
    'MineForest',
    'PearlSell',
    'Manufacture',
    'Business',
    'Restaurant',
    'Teahouse',
    'Grill',
    'JuuEatery',
    'JuuCoffee',
]


def island_plan_task_priority_redirect(value):
    """
    IslandPlan.TaskPriority 文本框 → 16 个 EnableXxx 开关。

    旧版本用一个 `>` 分隔的文本框同时表达「启用清单 + 执行顺序」。为了让普通用户
    看得懂，改成 16 个独立开关后，这里把旧清单里出现过的模块翻译成「开」，其余关，
    用户原先的自定义（比如特意删掉某个模块）不会丢失。顺序不再由旧文本决定。
    """
    text = value if isinstance(value, str) else ''
    return tuple(f'Island{name}' in text for name in ISLAND_PLAN_SUB_TASKS)


# 岛屿计划运行间隔（小时）的合法范围与默认值。
# island_scheduling 里的 MIN/MAX/DEFAULT_INTERVAL_HOURS 指向这三个常量，
# 单测断言两边一致（config 层不能 import module.island，会循环依赖）。
ISLAND_PLAN_INTERVAL_MIN = 1
ISLAND_PLAN_INTERVAL_MAX = 24
ISLAND_PLAN_INTERVAL_DEFAULT = 12


def clamp_island_plan_interval(value, default=ISLAND_PLAN_INTERVAL_DEFAULT):
    """
    把 IslandPlan.IntervalHours 收敛到 1~24 小时。

    界面上的运行间隔是自由填写的输入框，配置系统没有声明式的上下限校验
    （`validate` 只支持 datetime），用户可能填 99、0、-3 或非数字。这里统一收敛，
    保证落盘和运行时用的都是合法值：非数字 / NaN 回退默认值，越界取边界。

    Returns:
        int | float: 整数小时返回 int，小数小时原样返回（如 6.5）。
    """
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return default
    if hours != hours:  # NaN
        return default
    if hours < ISLAND_PLAN_INTERVAL_MIN:
        return ISLAND_PLAN_INTERVAL_MIN
    if hours > ISLAND_PLAN_INTERVAL_MAX:
        return ISLAND_PLAN_INTERVAL_MAX
    return int(hours) if hours.is_integer() else hours
