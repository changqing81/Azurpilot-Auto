"""异常层次结构。

定义 AzurPilot 的所有自定义异常。异常按严重程度和可恢复性分为以下层次：

正常战役结束（可预期的流程终止）：
    - CampaignEnd: 战役正常结束（回到关卡选择页面）
    - OilExhausted: 石油耗尽，无法继续出击
    - OilMaxed: 石油已达上限，需要消耗

地图导航错误（可恢复，触发重试）：
    - MapDetectionError: 地图透视检测失败（网格识别错误）
    - MapWalkError: 舰队行走错误（目标不可达）
    - MapEnemyMoved: 敌人移动导致路径失效，需要重新规划
    - CampaignNameError: 关卡名称无法识别

游戏状态错误（触发模拟器重启）：
    - GameStuckError: 游戏卡死（1 分钟内无有效操作，战斗期间 5 分钟）
    - GameBugError: 游戏客户端 bug（重启通常可恢复）
    - GameTooManyClickError: 点击次数过多（15 次操作中同一按钮被点 ≥12 次）

连接/页面错误（触发重启或人工干预）：
    - GameNotRunningError: 游戏未运行
    - GamePageUnknownError: 无法识别当前页面
    - EmulatorNotRunningError: 模拟器未运行

开发者/脚本错误：
    - ScriptError: 脚本逻辑错误（通常是代码 bug）
    - ScriptEnd: 脚本正常结束（用于中断当前任务流程）

不可恢复错误（需要人工干预）：
    - RequestHumanTakeover: 请求人工接管（配置错误等严重问题）
    - MindCalculatorScanError: 船坞扫描识别或定位不确定，结束工具任务但不重启模拟器
    - AutoSearchSetError: 自动搜索设置失败
    - HardNotSatisfied: 困难模式前置条件不满足

并发保护（非错误，调用方应放弃本轮并等待下一轮）：
    - EmulatorOpBusy: 已有模拟器启停操作正在进行
"""


class CampaignEnd(Exception):
    """战役正常结束。

    当舰队回到关卡选择页面时抛出，由调度循环捕获并处理后续逻辑
    （如推进关卡、记录掉落等）。
    """
    pass


class OilExhausted(Exception):
    """石油耗尽。

    当战斗准备界面检测到石油不足时抛出，结束当前战役任务。
    """
    pass


class OilMaxed(Exception):
    """石油已达上限。

    当石油溢出时抛出，提示需要消耗石油以免浪费。
    """
    pass


class MapDetectionError(Exception):
    """地图透视检测失败。

    当无法从截图中正确解析地图网格信息时抛出。
    可能原因：信息栏遮挡、弹窗干扰、游戏画面异常。
    """
    pass


class MapWalkError(Exception):
    """舰队行走错误。

    当舰队无法到达目标格子时抛出，可能原因包括路径被阻断、步数不足等。
    """
    pass


class MapEnemyMoved(Exception):
    """敌人移动导致路径失效。

    在可移动敌人系统中，敌人回合移动后当前路径可能不再有效，
    需要重新规划路径。此异常用于中断当前行走并触发重新寻路。
    """
    pass


class CampaignNameError(Exception):
    """关卡名称无法识别。

    当配置中的关卡名称无法映射到有效的地图文件时抛出。
    """
    pass


class ScriptError(Exception):
    """脚本逻辑错误。

    通常是开发者的代码错误，但也可能是偶发的随机问题。
    触发模拟器重启尝试恢复。
    """
    pass


class ScriptEnd(Exception):
    """脚本正常结束。

    用于中断当前任务流程（如情绪不足需要延迟），
    不视为错误，不计入失败次数。
    """
    pass


class GameStuckError(Exception):
    """游戏卡死。

    1 分钟内无有效截图操作（战斗/启动期间为 5 分钟）时触发。
    通常通过重启模拟器恢复。
    """
    pass


class GameBugError(Exception):
    """碧蓝航线游戏客户端发生错误，AzurPilot 无法自行处理。

    通常重启游戏即可恢复。
    """
    pass


class GameTooManyClickError(Exception):
    """点击次数过多，疑似陷入循环。

    最近 15 次操作中，同一按钮被点击 ≥12 次，
    或两个按钮各被点击 ≥6 次时触发。
    """
    pass


class EmulatorNotRunningError(Exception):
    """模拟器未运行。

    当 ADB 连接失败且模拟器进程不存在时抛出。
    """
    pass


class RecoveryFailed(Exception):
    """本轮模拟器恢复没有成功（超时、停不下来、起不来、预算耗尽）。

    这是设备侧的问题，不是脚本的错：调用方应退避后继续重试，
    不能因此结束调度进程。用户主动关闭恢复开关属于另一类终止条件，
    仍由 RecoveryStopped 表达。
    """
    pass


class NativeIpcTimeout(EmulatorNotRunningError):
    """模拟器原生 IPC 在一分钟预算内没有返回。

    继承 EmulatorNotRunningError：设备已经无法确认和控制，应按"模拟器失去
    响应"处理——由恢复协调器重启整台实例，而不是结束脚本进程。
    Python 无法安全中断卡在 DLL 里的线程，该 IPC 实例被永久封禁，不再复用。
    """
    pass


class GameNotRunningError(Exception):
    """游戏未运行。

    当截图为黑屏或游戏进程已退出时抛出。
    """
    pass


class GamePageUnknownError(Exception):
    """无法识别当前游戏页面。

    当页面检测逻辑无法匹配任何已知页面时抛出。
    """
    pass


class RequestHumanTakeover(Exception):
    """请求人工接管。

    AzurPilot 无法处理此类错误，可能是由于配置错误导致。
    需要用户手动检查并修正问题。
    """
    pass


class MindCalculatorScanError(RequestHumanTakeover):
    """船坞识别或精确定位无法确认；结束工具任务并保留旧清单，不重启模拟器。"""


class AutoSearchSetError(Exception):
    """自动搜索设置失败。

    当无法正确设置自动搜索选项时抛出（如舰队准备界面异常）。
    """
    pass


class HardNotSatisfied(RequestHumanTakeover):
    """困难模式前置条件不满足。

    继承自 RequestHumanTakeover，当困难关卡的前置条件未满足时抛出。
    """
    pass


class EmulatorOpBusy(Exception):
    """已有模拟器启停操作正在进行，本次操作被跳过。

    这不是错误，而是并发保护（见 PlatformWindows 的启停互斥锁）：
    emulator_start / emulator_stop 会真实地关闭并重启模拟器，两个操作
    并发时会互相踩踏——一个线程刚发出启动命令，另一个线程随即 shutdown，
    模拟器在冷启动完成前被反复打断，表现为"窗口一直卡在加载、起不来"。

    调用方收到此异常时应放弃本轮操作，等待下一轮调度，而不是立即重试。
    """
    pass


# 异常对象上标记"当前正在执行的子任务名"的属性名。
# 聚合调度（大世界智能调度/岛屿计划等）以子任务身份代跑逻辑时，上下文切换器
# 的 finally 会先于异常处理把 config.task 恢复成父任务名，导致顶层错误推送
# 报不出真正出错的子任务。因此代跑上下文在异常向外冒泡前，把子任务名直接
# 写在异常对象上带出去，顶层据此还原真实的任务上下文。
TASK_MARK_ATTRIBUTE = '_azurpilot_task'


def mark_task_on_exception(exc: BaseException, task_name: str) -> None:
    """在异常对象上标记当前正在执行的子任务名（用于错误推送定位）。

    聚合调度代跑子任务出错时，异常会带着此标记一路冒泡到 alas.py 的
    顶层异常捕获，推送前可通过 get_task_from_exception() 读出子任务名。

    标记动作本身绝不抛异常：task_name 非字符串时静默跳过，
    避免给原异常处理链路引入二次故障。
    """
    try:
        if isinstance(task_name, str) and task_name:
            setattr(exc, TASK_MARK_ATTRIBUTE, task_name)
    except Exception:
        # 异常对象理论上都支持 setattr；万一遇到特殊实现（如 frozen dataclass
        # 包装的异常），放弃标记，不影响原有异常传播
        pass


def get_task_from_exception(exc: BaseException) -> str | None:
    """读取异常对象上标记的子任务名；未标记或读取失败时返回 None。

    供 alas.py 组装错误推送内容时调用：优先取异常身上携带的子任务名
    （代跑场景的真实出错任务），取不到再退回调度器传入的任务名。
    """
    try:
        value = getattr(exc, TASK_MARK_ATTRIBUTE, None)
        if isinstance(value, str) and value:
            return value
    except Exception:
        pass
    return None
