"""主页数据源：把现有业务函数包装成可序列化 dict。

数据归属（勿在此处重复实现）：
- 实例列表/状态 → module/webui/worker_registry.py + process_manager.py（经 State）
- 任务队列   → alas 配置实例的 get_next_task()（app_dashboard.py 同源）
- 仪表盘     → module/log_res/log_res.py LogRes + config dashboard.yaml 资源清单
- 版本       → module/webui/update_log.py / deploy config
"""
from __future__ import annotations

import json
import os
from typing import Any

from module.config.utils import read_file
from module.logger import logger

_DASHBOARD_MANIFEST_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config", "argument", "dashboard.yaml")
)


def _load_yaml(path: str) -> dict:
    """读 YAML；失败返回空表，避免主页因单个数据源挂掉而整页 500。"""
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 —— 数据源降级，主页骨架必须始终可渲染
        logger.warning(f"[webapi] 读取 {path} 失败: {e}")
        return {}


# AzurLaneConfig 实例缓存：构造一次要读 args.json(1.6MB)+用户配置+绑定任务，
# 实测 ~150ms。总览页 15s 轮询、仪表盘也各要一次，每次重建纯浪费。
# 按 (name, 配置文件 mtime) 失效——用户改配置后自动重建。
_CONFIG_CACHE: dict = {}


def _get_config(name: str):
    """取（缓存的）AzurLaneConfig 实例。失败返回 None。"""
    from module.config.config import AzurLaneConfig
    from module.config.utils import filepath_config

    try:
        path = filepath_config(name)
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except Exception:  # noqa: BLE001
        key = None
    cached = _CONFIG_CACHE.get(name)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        config = AzurLaneConfig(name, task=None)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 实例配置加载失败({name}): {e}")
        return None
    _CONFIG_CACHE[name] = (key, config)
    return config


# i18n 词条进程级缓存：单语言 JSON 约 3.5MB / 3 万条，
# 每次 get_overview 都读盘 + JSON 解析会吃掉 200ms+（实测轮询下更糟）。
# 按 (path, mtime_ns, size) 失效，改文案后自动重载。
_I18N_CACHE: dict = {}

# 语言回退链：日语翻译缺键时用英文兜底（与 lang.load_language 的策略一致）。
_I18N_FALLBACK = {"ja-JP": "en-US"}

# 默认语言：仅在拿不到运行时语言时兜底。
_DEFAULT_LANG = "zh-CN"


def _current_language() -> str:
    """返回 WebUI 当前生效的界面语言。

    为什么需要这个函数：新 UI 的所有文案都由 /newapi/* 从 i18n JSON 里取出，
    而这里原先**全部硬编码 zh-CN.json** —— 用户点「English / 日本語」写进了
    deploy.yaml 却没有任何效果（实测确认过的 bug）。现在统一从这里取语言：

    1. 优先 module.webui.lang.LANG（老 UI 的 set_language 会实时更新它，
       新 UI 的保存动作也会同步它，见 webapi/app.py:_sync_runtime_language）
    2. 退回 deploy.yaml 的 Webui.Language（进程刚启动、lang 还没初始化时）
    3. 再退回 zh-CN
    """
    try:
        from module.webui import lang as _lang

        current = str(getattr(_lang, "LANG", "") or "").strip()
        if current.endswith(".json"):
            current = current[:-5]
        if current:
            return current
    except Exception:  # noqa: BLE001 —— 老 UI 模块不在线也必须有结果
        pass
    try:
        from module.webui.setting import State

        value = str(getattr(State.deploy_config, "Language", "") or "").strip()
        if value:
            return value
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_LANG


def _i18n_path(language: str) -> str:
    """i18n JSON 路径。语言非法时回退默认语言，不让上层拿到不存在的路径。"""
    from module.config.utils import LANGUAGES

    lang_name = language if language in LANGUAGES else _DEFAULT_LANG
    return os.path.join(
        os.path.dirname(__file__), "..", "config", "i18n", f"{lang_name}.json"
    )


def _load_i18n(scope: str, language: str | None = None) -> dict:
    """读取当前界面语言的 i18n JSON 并缓存（键 = path + mtime_ns + size）。

    Args:
        scope: 缓存命名空间（沿用原调用方传入的字符串，仅用于区分用途）。
        language: 显式语言；省略时取 :func:`_current_language`。
    """
    lang_name = language or _current_language()
    path = _i18n_path(lang_name)
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
        cached = _I18N_CACHE.get(scope)
        if cached and cached[0] == key:
            return cached[1]
        data = read_file(path)
        # 日语缺键回退英文（ja-JP.json 里缺失的键值是键路径本身）
        fallback = _I18N_FALLBACK.get(lang_name)
        if fallback:
            base = read_file(_i18n_path(fallback))
            from module.config.deep import deep_iter, deep_set, deep_get

            for p, v in deep_iter(base, depth=3):
                if deep_get(data, keys=p) in (None, ".".join(p)):
                    deep_set(data, keys=p, value=v)
        _I18N_CACHE[scope] = (key, data)
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] i18n 读取失败 ({path}): {e}")
        return {}


def get_version() -> dict:
    """版本信息（页脚 --version CSS 变量的数据源）。

    与 app_overview.py:416 同源：本地 git commit 短哈希，取不到用 Unknown。
    """
    import subprocess

    try:
        commit = subprocess.run(
            ["git", "log", "-1", "--format=%h"],
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return {"version": commit or "Unknown"}
    except Exception:  # noqa: BLE001 —— 打包发行版可能没有 .git
        return {"version": "Unknown"}


def get_instances() -> dict:
    """实例名列表 + 每个实例的运行状态（等价老 UI set_aside_status 的数据面）。

    状态码沿用老 UI 约定（app_shell.py:273-293 refresh_aside_instances）：
        1 = running（绿色旋转圈）
        2 = inactive（纯播放键）
        3 = warning（播放键 + 红角标，异常停止）
        4 = updating（播放键 + 蓝角标，更新中）

    **必须真读 ProcessManager.get_manager(name).state**。
    这里原来把 state 硬编码成 2（注释写「先 inactive 兜底」，后来忘了接），
    导致侧栏实例图标永远是空闲态的播放键 —— 长卿：
    「这里运行的小圆圈记得需要加回去 以及其他各个状态」。
    前端 Shell.tsx:instanceIcon 早已按 1/3/4/其他 分四种图标渲染，
    缺的从来只是后端这个值。
    """
    from module.config.utils import alas_instance
    from module.webui.process_manager import ProcessManager

    items = []
    try:
        for inst in alas_instance():
            state = 2
            try:
                state = int(ProcessManager.get_manager(inst).state)
            except Exception as e:  # noqa: BLE001 —— 单个实例读不到不影响整列表
                logger.debug(f"[webapi] 读取实例状态失败({inst}): {e}")
            items.append({"name": inst, "state": state})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 实例列表获取失败: {e}")
    return {"instances": items}


def get_dashboard(name: str) -> dict:
    """仪表盘数据（与 app_dashboard.py:479-547 渲染口径逐字段对齐）。

    数据来源 LogRes.group()：Value/Limit/Total/Color(^十六进制)/Record。
    输出 {items:[{name,value,limit,total,color,record}]}，前端算相对时间。
    """
    from module.config.config import AzurLaneConfig
    from module.log_res.log_res import LogRes

    manifest = _load_yaml(_DASHBOARD_MANIFEST_PATH).get("Dashboard") or []
    config = _get_config(name)
    if config is None:
        return {"items": []}
    log_res = LogRes(config)
    items: list[dict[str, Any]] = []
    for item in manifest:
        group = log_res.group(item)
        if not group:
            continue
        limit = str(group.get("Limit", ""))
        total = str(group.get("Total", ""))
        if not limit and not total and item == "Pt":
            # Pt 上限取活动 PT 配置（app_dashboard.py:490-499 同口径）
            from module.config.deep import deep_get

            pt_limit = deep_get(config.data, "EventGeneral.EventGeneral.PtLimit")
            limit = str(pt_limit) if pt_limit else ""
            if limit == "0":
                limit = ""
        items.append({
            "name": item,
            "value": str(group.get("Value")),
            "limit": limit,
            "total": total,
            "color": str(group.get("Color", "")).replace("^", "#"),
            "record": str(group.get("Record") or ""),
        })
    return {"items": items}


def get_overview(name: str) -> dict:
    """总览页任务队列（等价 alas_update_overview_task 的数据面，app_dashboard.py:196-213 同口径）。

    返回 running/pending/waiting 三组 [{command, name, next_run}] + alive（调度器是否在跑）。
    任务名翻译直接在服务端取好（i18n zh-CN Task.<command>.name），前端零翻译逻辑。
    """
    config = _get_config(name)
    if config is None:
        return {"running": [], "pending": [], "waiting": [], "alive": False}
    try:
        config.get_next_task()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 任务队列读取失败({name}): {e}")
        return {"running": [], "pending": [], "waiting": [], "alive": False}

    # i18n: 优先 zh-CN；键缺失时退回 command。
    # 走进程级缓存——35 个任务逐个读盘解析 3.5MB JSON 是这里最大的单项开销。
    i18n = _load_i18n("task_name")

    def _task_name(command: str) -> str:
        try:
            return str(i18n.get("Task", {}).get(command, {}).get("name") or command)
        except Exception:  # noqa: BLE001
            return command

    alive = False
    try:
        from module.webui.process_manager import ProcessManager

        pm = ProcessManager._processes.get(name)
        alive = bool(pm.alive) if pm is not None else False
    except Exception:  # noqa: BLE001 —— 进程管理器未初始化（无 WebUI 会话）时视为空闲
        pass

    def _rows(funcs) -> list:
        out = []
        for func in funcs:
            out.append({
                "command": func.command,
                "name": _task_name(func.command),
                "next_run": str(func.next_run),
            })
        return out

    pending = list(config.pending_task)
    running = pending[:1] if alive else []
    rest = pending[1:] if alive else pending
    return {
        "running": _rows(running),
        "pending": _rows(rest),
        "waiting": _rows(config.waiting_task),
        "alive": alive,
    }

def get_log_tail(name: str, since: int = -1, limit: int = 200, width: int = 80) -> dict:
    """日志增量拉取（老 UI put_log 游标语义：环形缓冲 + 增量 HTML）。

    与老 UI 同源的三件事必须完全一致，否则换行位置/颜色/内容都会偏：
    1. 渲染宽度：老 UI RichLog.get_width() 用 canvas 量浏览器实际宽度
       （容器宽-16)/font-size*16/单字宽，钳在 80..128。前端量好后通过
       ?width= 传进来；缺省 80 与后端 console 默认一致。
    2. 终端主题：按 State.theme 选 DARK/LIGHT_TERMINAL_THEME。
    3. 片段缓存：复用 RichLog._html_cache，键是 (width, id(renderable))。

    since=上次返回的 cursor（renderables 长度）；环形回绕时 full=true 要求前端重建。
    """
    from module.webui.process_manager import ProcessManager

    # 宽度与老 UI get_width() 的钳制区间一致（module/webui/widgets.py:get_width）
    width = 128 if width > 128 else 80 if width < 80 else int(width)

    pm = ProcessManager._processes.get(name)
    if pm is None:
        return {"html": "", "cursor": 0, "full": True}
    items = pm.renderables
    total = len(items)
    if since is None or since < 0 or since > total:
        since = max(0, total - limit)
        full = True
    else:
        full = False
    # 环形回绕：列表被裁剪过（长度变小），游标失真 → 重建
    if total < since:
        since = 0
        full = True
    new_items = items[since:]
    cursor = total
    if not new_items:
        return {"html": "", "cursor": cursor, "full": False}
    try:
        from module.logger import HTMLConsole, Highlighter, WEB_THEME
        from module.webui.utils import (
            DARK_TERMINAL_THEME, LIGHT_TERMINAL_THEME, LOG_CODE_FORMAT, State,
        )
        from module.webui.widgets import RichLog

        theme_name = getattr(State, "theme", "light") or "light"
        terminal_theme = DARK_TERMINAL_THEME if theme_name in ("dark", "dark_advanced_material") else LIGHT_TERMINAL_THEME
        console = HTMLConsole(
            force_terminal=False, force_interactive=False, width=width,
            color_system="truecolor", markup=False, record=True, safe_box=False,
            highlighter=Highlighter(), theme=WEB_THEME,
        )
        # 复用老 UI RichLog._html_cache（对象同一性缓存）：相同 renderable 不重复渲染
        parts = []
        missed = []
        with RichLog._html_cache_lock:
            cache = RichLog._html_cache
            for r in new_items:
                key = (width, id(r))
                entry = cache.get(key)
                if entry is not None and entry[0] is r:
                    parts.append(entry[1])
                    cache.move_to_end(key)
                else:
                    missed.append((key, r))
        for key, r in missed:
            with console.capture():
                console.print(r)
            html = console.export_html(
                theme=terminal_theme, clear=True, code_format=LOG_CODE_FORMAT, inline_styles=True,
            )
            parts.append(html)
            with RichLog._html_cache_lock:
                cache[key] = (r, html)
                cache.move_to_end(key)
                while len(cache) > 2048:
                    cache.popitem(last=False)
        return {"html": "".join(parts), "cursor": cursor, "full": full}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 日志渲染失败({name}): {e}")
        return {"html": "", "cursor": cursor, "full": full}


# 统计页的全部用户可见文案（老 UI 源码里逐个 t() 键的字面值）。
# 前端不再硬编码任何中文 —— 长 卿：「文案一定要是老UI的文案，不要乱改」。
# 键名 = 老 UI i18n 键去掉 Gui.Stat./Gui.FleetTechnology. 前缀，便于对照。
_STAT_I18N_KEYS = {
    # 页头
    "PageTitle": "Gui.Stat.PageTitle",
    "Refresh": "Gui.Stat.Refresh",
    "PageMeta": "Gui.Stat.PageMeta",
    "SourceOk": "Gui.Stat.SourceOk",
    "SourceStale": "Gui.Stat.SourceStale",
    "SourceMissing": "Gui.Stat.SourceMissing",
    # 页签（fleet/mind 两个键在 FleetTechnology 组）
    "TabOpsi": "Gui.Stat.TabOpsi",
    "TabCommission": "Gui.Stat.TabCommission",
    "TabResearch": "Gui.Stat.TabResearch",
    "TabResource": "Gui.Stat.TabResource",
    "TabFleet": "Gui.FleetTechnology.TabTech",
    "TabMind": "Gui.FleetTechnology.TabMind",
    # 体力图工具栏
    "ApChartTitle": "Gui.Stat.ApChartTitle",
    "ViewLineButton": "Gui.Stat.ViewLineButton",
    "ViewDayButton": "Gui.Stat.ViewDayButton",
    "ViewMonthButton": "Gui.Stat.ViewMonthButton",
    # 科研
    "ResearchTitle": "Gui.Stat.ResearchTitle",
    "ResearchMetricTotal": "Gui.Stat.ResearchMetricTotal",
    "ResearchMetricToday": "Gui.Stat.ResearchMetricToday",
    "ResearchMetricMonth": "Gui.Stat.ResearchMetricMonth",
    "ResearchMetricRecords": "Gui.Stat.ResearchMetricRecords",
    "ResearchScopeSeries": "Gui.Stat.ResearchScopeSeries",
    "ResearchScopeConsumable": "Gui.Stat.ResearchScopeConsumable",
    "ResearchPeriodMonth": "Gui.Stat.ResearchPeriodMonth",
    "ResearchPeriodDays30": "Gui.Stat.ResearchPeriodDays30",
    "ResearchPeriodDays90": "Gui.Stat.ResearchPeriodDays90",
    "ResearchPeriodAll": "Gui.Stat.ResearchPeriodAll",
    "ResearchEmptyTitle": "Gui.Stat.ResearchEmptyTitle",
    # 委托
    "CommissionIncomeTitle": "Gui.Stat.CommissionIncomeTitle",
    "CommissionIncomeDay": "Gui.Stat.CommissionIncomeDay",
    "CommissionIncomeWeek": "Gui.Stat.CommissionIncomeWeek",
    "CommissionIncomeMonth": "Gui.Stat.CommissionIncomeMonth",
    "CommissionIncomeMonthPicker": "Gui.Stat.CommissionIncomeMonthPicker",
    "CommissionIncomeNoData": "Gui.Stat.CommissionIncomeNoData",
    # 资源
    "DeltaPeriodDay": "Gui.Stat.DeltaPeriodDay",
    "DeltaPeriodWeek": "Gui.Stat.DeltaPeriodWeek",
    "DeltaPeriodMonth": "Gui.Stat.DeltaPeriodMonth",
    "DeltaPeriodTotal": "Gui.Stat.DeltaPeriodTotal",
    "DeltaChartTitle": "Gui.Stat.DeltaChartTitle",
    "DeltaSummaryTitle": "Gui.Stat.DeltaSummaryTitle",
    "DeltaIncrease": "Gui.Stat.DeltaIncrease",
    "DeltaDecrease": "Gui.Stat.DeltaDecrease",
    "DeltaNet": "Gui.Stat.DeltaNet",
    "DeltaResource": "Gui.Stat.DeltaResource",
    "DeltaTimelineHint": "Gui.Stat.DeltaTimelineHint",
    "ConsumptionRankingTitle": "Gui.Stat.ConsumptionRankingTitle",
    "NoDeltaData": "Gui.Stat.NoDeltaData",
    # 大世界
    "OpsiDropTitle": "Gui.Stat.OpsiDropTitle",
    "OpsiDataCollectionTitle": "Gui.Stat.OpsiDataCollectionTitle",
    "NoApData": "Gui.Stat.NoApData",
    # 科技列表
    "FtCategory": "Gui.FleetTechnology.Category",
    "FtRarity": "Gui.FleetTechnology.Rarity",
    "FtNation": "Gui.FleetTechnology.Nation",
    "FtBonusType": "Gui.FleetTechnology.BonusType",
    "FtAttr": "Gui.FleetTechnology.Attr",
    "FtStatus": "Gui.FleetTechnology.Status",
    "FtFunctions": "Gui.FleetTechnology.Functions",
    "FtEdit": "Gui.FleetTechnology.Edit",
    "FtBonuses": "Gui.FleetTechnology.Bonuses",
    "FtExtra": "Gui.FleetTechnology.Extra",
    "FtPendingOnly": "Gui.FleetTechnology.PendingOnly",
    "FtScanRange": "Gui.FleetTechnology.ScanRange",
    "FtSearch": "Gui.FleetTechnology.Search",
    "FtScope": "Gui.FleetTechnology.Scope",
    "FtStage": "Gui.FleetTechnology.Stage",
    "FtAll": "Gui.FleetTechnology.All",
    "FtUnlock": "Gui.FleetTechnology.Unlock",
    "FtTech": "Gui.FleetTechnology.Tech",
    "FtNone": "Gui.FleetTechnology.None",
    "FtEmpty": "Gui.FleetTechnology.Empty",
    # 分页（老 UI app_fleet_technology.py:508-509 用 Prev/Next 键）
    "FtPrevPage": "Gui.FleetTechnology.Prev",
    "FtNextPage": "Gui.FleetTechnology.Next",
    "FtRowCount": "Gui.FleetTechnology.RowCount",
    "FtAttribution": "Gui.FleetTechnology.Attribution",
    "ResearchSeriesOption": "Gui.Stat.ResearchSeriesOption",
    "MeowLootBackToCurrent": "Gui.Stat.MeowLootBackToCurrent",
    # 心智计算器（Gui.MindCalculator 组，老 UI 按钮原文）
    "McBatchEdit": "Gui.MindCalculator.BatchEdit",
    "McBatchExit": "Gui.MindCalculator.BatchExit",
    "McPrevious": "Gui.MindCalculator.Previous",
    "McNext": "Gui.MindCalculator.Next",
    "McExportJson": "Gui.MindCalculator.ExportJson",
    "McExportCsv": "Gui.MindCalculator.ExportCsv",
    "McEdit": "Gui.MindCalculator.Edit",
    "McDelete": "Gui.MindCalculator.Delete",
    "McAdd": "Gui.MindCalculator.Add",
    "McImport": "Gui.MindCalculator.Import",
    "McSaved": "Gui.MindCalculator.Saved",
    "McSaveConflict": "Gui.MindCalculator.SaveConflict",
    "McRowCount": "Gui.MindCalculator.RowCount",
    "McMinLevel": "Gui.MindCalculator.MinLevel",
    "McMaxLevel": "Gui.MindCalculator.MaxLevel",
    "McScan": "Gui.MindCalculator.Scan",
    "McScanning": "Gui.MindCalculator.Scanning",
    "McScanStarted": "Gui.MindCalculator.ScanStarted",
    "McScanHint": "Gui.MindCalculator.ScanHint",
    "FtBatchTitle": "Gui.FleetTechnology.BatchTitle",
    "FtBatchAll": "Gui.FleetTechnology.BatchAll",
    "FtBatchHint": "Gui.FleetTechnology.BatchHint",
    "FtOwned": "Gui.FleetTechnology.Owned",
    "FtStar": "Gui.FleetTechnology.Star",
    "FtLevel": "Gui.FleetTechnology.Level",
    "FtKeep": "Gui.FleetTechnology.Keep",
    "FtYes": "Gui.FleetTechnology.Yes",
    "FtNo": "Gui.FleetTechnology.No",
    "FtUnknown": "Gui.FleetTechnology.Unknown",
    "FtSetLevel": "Gui.FleetTechnology.SetLevel",
    "FtPresetOwned": "Gui.FleetTechnology.PresetOwned",
    "FtPresetStar": "Gui.FleetTechnology.PresetStar",
    "FtPreset120": "Gui.FleetTechnology.Preset120",
    "FtRestore": "Gui.FleetTechnology.Restore",
    "FtCancel": "Gui.FleetTechnology.Cancel",
    "FtSave": "Gui.FleetTechnology.Save",
    "FtSelected": "Gui.FleetTechnology.Selected",
    "FtBatchEdit": "Gui.FleetTechnology.BatchEdit",
    "FtSelectionHint": "Gui.FleetTechnology.SelectionHint",
    "FtScan": "Gui.FleetTechnology.Scan",
    "FtScanning": "Gui.FleetManagement.Scanning",
    "FtScanStarted": "Gui.FleetManagement.ScanStarted",
    "FtSync": "Gui.FleetTechnology.Sync",
    "FtSyncDone": "Gui.FleetTechnology.SyncDone",
    "FtHint": "Gui.FleetTechnology.Hint",
    # 心智计算器（与科技列表同组）
    "FtTabMind": "Gui.FleetTechnology.TabMind",
}


def get_stat_i18n() -> dict:
    """统计页全部文案，按当前界面语言返回（键 = 上表的短名）。

    语言必须先 load_language：_t 只查已加载的 dic_lang，
    没加载的语言查出来是键名本身（实测踩过）。en-US 是回退底座，
    老 UI 的 load_language 也会顺带加载它。
    """
    from module.webui import lang as _lang_mod

    lang = _current_language()
    try:
        _lang_mod.load_language(lang)
    except Exception:  # noqa: BLE001
        pass
    out = {}
    for short, full in _STAT_I18N_KEYS.items():
        value = _lang_mod._t(full, lang)
        # 拿不到键时回退 zh-CN（_t 未命中会返回键名本身）
        if value == full:
            value = _lang_mod._t(full, "zh-CN")
        out[short] = value
    return out


# 固定菜单项的 i18n 键（老 UI app_developer_menu.py + base.py 里的 t() 调用）
# 注意：这些是**硬编码在前端的 HOME_MENU 里**的文案，切换语言时必须由后端
# 按当前语言返回，否则侧栏/主页菜单永远是中文（实测过：配置页已经变成
# "For example, 7-2..." 英文了，侧栏还是「主页/更新器/远程控制」）。
_FIXED_MENU_KEYS = (
    ("HomePage", "Gui.MenuDevelop.HomePage"),
    ("Update", "Gui.MenuDevelop.Update"),
    ("Remote", "Gui.MenuDevelop.Remote"),
    ("Setting", "Gui.MenuDevelop.Setting"),
    ("Announcement", "Gui.MenuDevelop.Announcement"),
    ("Utils", "Gui.MenuDevelop.Utils"),
)


def get_fixed_menu() -> dict:
    """固定菜单项（主页的 6 个入口）+ 侧栏文字，按当前界面语言翻译。

    对应老 UI 的：
        app_developer_menu.py:22-62  put_button(label=t("Gui.MenuDevelop.X"))
        base.py:init_aside          「主页」「管理」与实例名
    """
    from module.webui.lang import t as _t

    items = [{"key": key, "name": _t(i18n_key)} for key, i18n_key in _FIXED_MENU_KEYS]
    # 「管理」在 gui.yaml 里没有独立键（老 UI 的侧栏管理按钮用的是
    # Gui.MenuDevelop.Manage，部分语言回退成原键名），取不到就用中文兜底。
    manage = _t("Gui.MenuDevelop.Manage")
    if manage == "Gui.MenuDevelop.Manage":
        manage = "管理"
    search = _t("Gui.MenuAlas.Search")
    if search == "Gui.MenuAlas.Search":
        search = "搜索配置"
    return {
        "items": items,
        # 侧栏两个固定按钮
        "aside_home": _t("Gui.MenuDevelop.HomePage"),
        "aside_manage": manage,
        "search_placeholder": search,
    }


def get_header() -> dict:
    """页眉数据：logo 图标 URL + 排头文案（Gui.Status.Prefix，i18n zh-CN 同源）。"""
    try:
        data = _load_i18n("header")
        prefix = str(data.get("Gui", {}).get("Status", {}).get("Prefix") or "AzurPilot")
    except Exception:  # noqa: BLE001
        prefix = "AzurPilot"
    return {
        "icon": "/static/assets/spa/spa-icon-192x192.png",
        "prefix": prefix,
        "title": "AzurPilot",
    }

def get_menu() -> dict:
    """导航菜单树（menu.json 全量 + i18n 名称，等价老 UI task_config_menu_items 数据面）。

    返回 {groups:[{key,name,page,tasks:[{key,name}]}]}。组名 i18n 在 Menu.<key>.name。
    """
    try:
        menu_path = os.path.join(os.path.dirname(__file__), "..", "config", "argument", "menu.json")
        menu = read_file(menu_path)
        i18n = _load_i18n("menu")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 菜单读取失败: {e}")
        return {"groups": []}

    def _name(key: str) -> str:
        return str(i18n.get("Menu", {}).get(key, {}).get("name") or key)

    groups = []
    for key, body in menu.items():
        tasks = []
        for task in body.get("tasks", []):
            tasks.append({
                "key": task,
                "name": str(i18n.get("Task", {}).get(task, {}).get("name") or task),
            })
        groups.append({
            "key": key,
            "name": _name(key),
            "page": body.get("page", ""),
            "tasks": tasks,
        })
    return {"groups": groups}

def get_task_config(name: str, task: str) -> dict:
    """配置页 schema+值（等价 alas_set_group 的数据面）。

    合并三源：args.json（type/value/option/display）+ config/{name}.json（用户当前值）
    + 当前界面语言的 i18n JSON（Group.Arg.name/help/option 标签翻译）。
    display=hide 的项不输出。
    """
    import os as _os

    from module.config.config import AzurLaneConfig

    arg_dir = _os.path.join(_os.path.dirname(__file__), "..", "config", "argument")
    try:
        args = read_file(_os.path.join(arg_dir, "args.json"))
        i18n = _load_i18n("task_config")
        task_def = args.get(task)
        if task_def is None:
            return {"task": task, "groups": [], "error": f"未知任务 {task}"}
        config = AzurLaneConfig(name, task=None)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 任务配置读取失败({name}/{task}): {e}")
        return {"task": task, "groups": [], "error": str(e)}

    groups = []
    for group_name, group_body in task_def.items():
        if group_name.startswith("_") or group_name in ("Scheduler",):
            continue
        group_i18n = i18n.get(group_name, {})
        rows = []
        for arg_name, arg in group_body.items():
            if arg_name.startswith("_"):
                continue
            display = arg.get("display", "show")
            if display == "hide":
                continue
            arg_i18n = group_i18n.get(arg_name, {})
            # 当前值优先用户配置，缺省用 args.json 默认
            from module.config.deep import deep_get

            current = deep_get(config.data, f"{task}.{group_name}.{arg_name}")
            if current is None:
                current = arg.get("value")
            if isinstance(current, (list, tuple)):
                current = [str(v) for v in current]
            elif current is not None and not isinstance(current, (str, int, float, bool)):
                from datetime import datetime as _dt

                current = str(current)  # datetime 等类型转字符串，与老 UI 显示一致
            item: dict[str, Any] = {
                "name": arg_name,
                "title": str(arg_i18n.get("name") or arg_name),
                "help": str(arg_i18n.get("help") or ""),
                "type": arg.get("type", "input"),
                "value": current,
                "option": [
                    {"label": str(arg_i18n.get(opt) or opt), "value": opt}
                    for opt in (arg.get("option") or [])
                ],
            }
            rows.append(item)
        if rows:
            groups.append({
                "group": group_name,
                "title": str(group_i18n.get("_info", {}).get("name") or group_name),
                "args": rows,
            })
    return {"task": task, "groups": groups}

def save_task_config(name: str, task: str, modified: dict) -> dict:
    """保存配置修改（等价 _save_config 的核心链路：类型解析+校验+联动回调+写盘）。

    modified: {"Group.Arg": value, ...}（路径不含 task 前缀，与老 UI pin 命名一致）
    返回 {valid: [...], invalid: [...]}。
    """
    import re as _re

    from module.config.config_updater import ConfigUpdater
    from module.config.deep import deep_get, deep_set
    from module.webui.utils import parse_pin_value

    if os.environ.get("DEMO") == "1":
        return {"valid": [], "invalid": list(modified.keys()), "error": "demo mode"}

    try:
        updater = ConfigUpdater()
        config = updater.read_file(name)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 配置读取失败({name}): {e}")
        return {"valid": [], "invalid": list(modified.keys()), "error": str(e)}

    valid, invalid = [], []
    # 老 UI app_task_config.py:805 同款：modified.copy() —— 键补全/回写会
    # 往 modified 塞新键（两段→三段、save_callback 联动值），直接迭代原字典
    # 会 RuntimeError: dictionary changed size during iteration。
    for k, v in modified.copy().items():
        # 键契约：老 UI _save_config 用三段全路径 Task.Group.Arg
        # （app_task_config.py:806 deep_get(self.ALAS_ARGS, k)，args.json 是三层）。
        # 新 UI 前端早期发两段 Group.Arg —— 在 group 与 task 不同名的任务上
        # （如 General.Log.LogKeepCount）deep_set(config, 'Log.LogKeepCount', v)
        # 会创建错误的顶层键，config_update 重建时丢弃未知顶层键，
        # 保存"成功"但值永远不变（2026-11-17 实测）。两段键自动补全成三段。
        if k.count(".") == 1:
            k = task + "." + k
        arg_def = deep_get(updater.args, k, {}) or {}
        valuetype = arg_def.get("valuetype") if isinstance(arg_def, dict) else None
        widget_type = arg_def.get("type") if isinstance(arg_def, dict) else None
        options = arg_def.get("option") if isinstance(arg_def, dict) else None
        # ⚠️ 必须用老 UI 同一个 parse_pin_value（utils.py:849），不是
        # config/utils.py 的 parse_value。后者 282-284 行
        #   if 'option' in data: value not in data['option']
        # 在 option 为 None 时（args.json 的 checkbox 参数就是这样生成的）
        # 直接 TypeError → 整个保存 500 —— 长卿截图「无法保存配置」的根因。
        # parse_pin_value 与老 UI 保存链路（app_task_config.py:814）逐字对齐，
        # checkbox []/[True]、multiselect、str 保留语义全部一致。
        v = parse_pin_value(v, valuetype, widget_type, options)
        validate = deep_get(updater.args, k + ".validate")
        if not len(str(v)):
            default = deep_get(updater.args, k + ".value")
            modified[k] = default
            deep_set(config, k, default)
            valid.append(k)
        elif not validate or _re.fullmatch(validate, v):
            deep_set(config, k, v)
            modified[k] = v
            valid.append(k)
            for set_key, set_value in updater.save_callback(k, v):
                deep_set(config, set_key, set_value)
                valid.append(set_key)
        else:
            invalid.append(k)
    try:
        updater.write_file(name, config)
        logger.info(f"[webapi] 配置已保存({name}/{task}): {len(valid)} 项")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 配置写盘失败({name}): {e}")
        return {"valid": valid, "invalid": invalid, "error": str(e)}
    return {"valid": valid, "invalid": invalid}

def get_statistics_data_state(name: str = "") -> dict:
    """本地统计库状态（app_statistics_page.py:_get_statistics_data_state 同口径）。

    Returns: {latest: "%Y-%m-%d %H:%M" 或 "", state: ok/stale/missing, instance}
    候选数据源：cl1_data.db / azurstats_local.db / resource_delta.db /
    ship_exp_data.json（含 -wal 文件，WAL 里的写入也算）。
    """
    from datetime import datetime, timedelta
    from pathlib import Path

    from module.config.utils import alas_instance

    if name:
        instance = name
    else:
        all_names = alas_instance()
        instance = all_names[0] if all_names else "alas"
    root = Path(__file__).resolve().parents[2]
    candidates = (
        root / "config" / "cl1_data.db",
        root / "config" / "azurstats_local.db",
        root / "config" / "resource_delta.db",
        root / "log" / "cl1" / instance / "ship_exp_data.json",
    )
    newest = None
    for base in candidates:
        for path in (base, Path(str(base) + "-wal")):
            try:
                stamp = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or stamp > newest:
                newest = stamp
    if newest is None:
        return {"latest": "", "state": "missing", "instance": instance}
    latest = datetime.fromtimestamp(newest)
    stale_days = 7
    state = "stale" if datetime.now() - latest > timedelta(days=stale_days) else "ok"
    return {
        "latest": latest.strftime("%Y-%m-%d %H:%M"),
        "state": state,
        "instance": instance,
    }


def get_resource_delta(name: str, days: int = 7) -> dict:
    """资源变动面板数据（app_stat_delta.py:_load_delta_data 同口径）。"""
    from datetime import datetime, timedelta

    try:
        from module.statistics.resource_delta_stats import (
            get_consumption_ranking,
            get_delta_summary,
            get_task_delta_timeline,
        )

        start = (datetime.now() - timedelta(days=days)).isoformat()
        timeline = get_task_delta_timeline(name, start_ts=start)
        summary = get_delta_summary(name, start_ts=start)
        ranking = get_consumption_ranking(name, start_ts=start)
        return {"timeline": timeline, "summary": summary, "ranking": ranking}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 资源变动数据失败({name}): {e}")
        return {"timeline": [], "summary": {}, "ranking": []}

def get_commission_income(name: str) -> dict:
    """委托收益面板（app_stat_commission.py:_load_commission_income_data 简化口径：本月）。"""
    from datetime import datetime

    try:
        from module.statistics.commission_income_stats import get_commission_income_summary

        now = datetime.now()
        summary = get_commission_income_summary(name, period="month", year=now.year, month=now.month)
        return {"period": f"{now.year}-{now.month:02d}", "summary": summary}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 委托收益数据失败({name}): {e}")
        return {"period": "", "summary": {}}

def get_research_stats(name: str, scope: str = "series", series: int = 0, days: int = 90) -> dict:
    """科研统计面板（research_stats.collect 直接口径：默认 90 天/最新期）。"""
    try:
        from module.statistics import research_stats

        return research_stats.collect(name, days=days, series=series, scope=scope)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 科研统计数据失败({name}): {e}")
        return {"error": str(e)}

def get_opsi_summary(name: str) -> dict:
    """大世界统计面板（app_stat_opsi.py:_build_opsi_month_cards 数据面：当月 summary）。"""
    from datetime import datetime

    try:
        from module.statistics.opsi_month import get_opsi_stats

        now = datetime.now()
        summary = get_opsi_stats(instance_name=name).summary(year=now.year, month=now.month)
        return {"month": summary.get("month"), "summary": summary}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 大世界统计失败({name}): {e}")
        return {"month": "", "summary": {}}

def get_fleet_tech(name: str) -> dict:
    """科技列表面板（app_fleet_technology.py:_ft_load 同链路：catalog+report+combine_rows）。"""
    from module.config.config import AzurLaneConfig
    from module.config.deep import deep_get
    from module.runtime import fleet_tech as tech
    from module.webui import fleet_tech_view as view

    min_level_path = "FleetTechnology.FleetTechnology.MinLevel"
    max_level_path = "FleetTechnology.FleetTechnology.MaxLevel"
    try:
        config = AzurLaneConfig(name, task=None)
        data = config.read_file(name)
        record = tech.read_record(data)
        report = dict(
            instance=name,
            min_level=deep_get(data, min_level_path, 95) or 95,
            max_level=deep_get(data, max_level_path, 125) or 125,
            **tech.report(record),
        )
        catalog = tech.public_catalog()
        rows = view.combine_rows(catalog, report)
        # JSON 序列化兜底：未知类型转字符串
        def _safe(v):
            if isinstance(v, (str, int, float, bool)) or v is None:
                return v
            if isinstance(v, (list, tuple)):
                return [_safe(x) for x in v]
            if isinstance(v, dict):
                return {str(k): _safe(x) for k, x in v.items()}
            return str(v)
        return {"rows": [_safe(r) for r in rows], "revision": str(report.get("revision", "")), "total": len(rows)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 科技列表失败({name}): {e}")
        return {"rows": [], "error": str(e), "total": 0}

def get_mind_calculator(name: str) -> dict:
    """心智计算器面板（app_mind_calculator_tab.py:_mc_load 同链路：清单+实时计算）。"""
    from module.config.config import AzurLaneConfig
    from module.config.deep import deep_get
    from module.runtime import mind_calculator as mind_calc

    try:
        config = AzurLaneConfig(name, task=None)
        data = config.read_file(name)
        stored = deep_get(data, mind_calc.RESULT_PATH, {})
        stored = stored if isinstance(stored, dict) else {}
        ships = stored.get("ships", [])
        if not isinstance(ships, list):
            ships = []
        result = mind_calc.calculate(ships)
        def _safe(v):
            if isinstance(v, (str, int, float, bool)) or v is None:
                return v
            if isinstance(v, (list, tuple)):
                return [_safe(x) for x in v]
            if isinstance(v, dict):
                return {str(k): _safe(x) for k, x in v.items()}
            return str(v)
        return {
            "ships": [_safe(s) for s in ships],
            "result": _safe(result),
            "updated_at": str(stored.get("updated_at", "")),
            "total": len(ships),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 心智计算器失败({name}): {e}")
        return {"ships": [], "error": str(e), "total": 0}

def save_mind_ships(name: str, ships: list, expect_revision: str = "") -> dict:
    """心智清单写接口（老 UI _mc_save 同链路：事务 + revision 乐观锁 + 实时计算）。

    ships 每项 {name, level, status}（status: counted / pending / excluded）。
    expect_revision 为空时跳过乐观锁（首次保存）。
    返回 {ships, result, updated_at, total} 或 {error}。
    """
    from datetime import datetime as _dt

    from module.config.config import AzurLaneConfig
    from module.config.deep import deep_get, deep_set
    from module.config.utils import get_config_mod
    from module.runtime import mind_calculator as mind_calc
    from module.webui.app_dependencies import State

    try:
        config = AzurLaneConfig(name, task=None)
        data = config.read_file(name)
        stored = deep_get(data, mind_calc.RESULT_PATH, {})
        stored = stored if isinstance(stored, dict) else {}
        current = stored.get("ships", []) if isinstance(stored, dict) else []
        if expect_revision and mind_calc.revision(current) != expect_revision:
            return {"error": "清单已被其他操作修改，请刷新后重试"}
        # 行数据保留原字段（name/level/rarity/base_rarity/excluded/review/source
        # 是存储契约，calculate 从 excluded/review 派生四态；归一化成
        # {name, level, status} 会把清单写坏 —— 实测第一版就犯了）。只做最小
        # 清洗：必须有 name；level 转 int；excluded/review 转 bool。
        clean = []
        for s in ships or []:
            if not isinstance(s, dict) or not s.get("name"):
                continue
            item = dict(s)
            item["name"] = str(item["name"])
            try:
                item["level"] = int(item.get("level") or 0)
            except (TypeError, ValueError):
                item["level"] = 0
            item["excluded"] = bool(item.get("excluded", False))
            item["review"] = bool(item.get("review", False))
            item.setdefault("source", "")
            item.setdefault("rarity", "")
            item.setdefault("base_rarity", "")
            clean.append(item)
        updated_at = _dt.now().isoformat(timespec="seconds")
        deep_set(data, mind_calc.RESULT_PATH, dict(ships=clean, updated_at=updated_at))
        State.config_updater.write_file(name, data, get_config_mod(name))
        return {
            "ships": clean,
            "result": mind_calc.calculate(clean),
            "updated_at": updated_at,
            "total": len(clean),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 心智清单保存失败({name}): {e}")
        return {"error": str(e)}


def export_mind_ships(name: str) -> dict:
    """导出心智清单（老 UI _mc_export 同口径：含计算列的 JSON）。"""
    from module.config.config import AzurLaneConfig
    from module.config.deep import deep_get
    from module.runtime import mind_calculator as mind_calc

    try:
        config = AzurLaneConfig(name, task=None)
        data = config.read_file(name)
        stored = deep_get(data, mind_calc.RESULT_PATH, {})
        stored = stored if isinstance(stored, dict) else {}
        ships = stored.get("ships", []) if isinstance(stored, dict) else []
        result = mind_calc.calculate(ships)
        by_name = {s["name"]: s for s in result.get("ships", [])}
        rows = []
        for s in ships:
            r = by_name.get(s.get("name"), {})
            rows.append({
                "name": s.get("name"),
                "level": s.get("level"),
                "status": r.get("status"),
                "mind": r.get("mind"),
                "gold": r.get("gold"),
            })
        return {
            "updated_at": str(stored.get("updated_at", "")),
            "rows": rows,
            "total": len(rows),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 心智清单导出失败({name}): {e}")
        return {"error": str(e)}


def batch_fleet_tech(name: str, gids: list, changes: dict, expected_revision: str, action: str = "save") -> dict:
    """科技列表批量编辑（老 UI app_fleet_tech_batch.py:_ft_batch_save 同链路）。

    gids: 目标舰船 group_type 列表（'selected' 已由前端解析成具体列表）。
    changes: collect_batch_fields 产出的显式字段（owned/full_star/lv120/level），
            'restore' 语义 = {"restore": True}。
    expected_revision: 乐观锁，与打开弹窗时的 revision 对比。
    """
    from module.config.deep import deep_set
    from module.config.transaction import config_transaction
    from module.config.utils import filepath_config, get_config_mod
    from module.webui.app_dependencies import State, load_config
    from module.runtime import fleet_tech as tech

    if action not in ("save", "restore"):
        return {"error": "未知批量操作: " + str(action)}
    if not gids:
        return {"error": "没有目标舰船"}
    try:
        # 表单校验（collect_batch_fields 同款规则，REST 直接收已解析字段）
        fields = {}
        if action == "restore":
            fields = {"restore": True}
        else:
            for f in ("owned", "full_star", "lv120"):
                if f in changes:
                    v = changes[f]
                    if v not in (True, False, None):
                        return {"error": f"{f} 取值非法（true/false/unknown）"}
                    fields[f] = v
            if "level" in changes:
                lv = changes["level"]
                if lv is not None:
                    try:
                        lv = int(lv)
                    except (TypeError, ValueError):
                        return {"error": "等级必须是 1-125 的整数"}
                    if not 1 <= lv <= 125:
                        return {"error": "等级必须是 1-125 的整数"}
                fields["level"] = lv
        if not fields:
            return {"error": "没有要修改的字段"}
        with config_transaction(filepath_config(name)):
            cfg = load_config(name).read_file(name)
            record = tech.read_record(cfg)
            if tech.revision(record) != expected_revision:
                return {"error": "清单已被其他操作更新，请刷新后重试"}
            change_list = [dict(group_type=gid, **fields) for gid in gids]
            updated = tech.apply_changes(record, change_list)
            deep_set(cfg, tech.RECORD_PATH, updated)
            State.config_updater.write_file(name, cfg, get_config_mod(name))
        return {"success": True, "count": len(change_list)}
    except tech.FleetTechChangeError as exc:
        return {"error": str(exc)}
    except (ValueError, TimeoutError, OSError) as exc:
        return {"error": str(exc)}


def dev_mind_scan(instance: str, min_level: int, max_level: int) -> dict:
    """触发心智单元船坞扫描（老 UI app_mind_calculator_tab.py:_mc_scan_start）。

    校验等级范围 → 写 FleetTechScan/MindCalculatorScan 扫描范围配置 →
    worker 存活时先停（后台收尾）再以 MindCalculatorScan 任务启动。
    """
    import time as _time

    from module.config.deep import deep_set
    from module.config.utils import get_config_mod
    from module.webui.app_dependencies import State

    if not (1 <= min_level <= max_level <= 125):
        return {"success": False, "error": "等级范围必须满足 1 ≤ 最低等级 ≤ 最高等级 ≤ 125"}
    try:
        updater = State.config_updater
        cfg = updater.read_file(instance)
        deep_set(cfg, "FleetTechScan.FleetTech.MinLevel", int(min_level))
        deep_set(cfg, "FleetTechScan.FleetTech.MaxLevel", int(max_level))
        updater.write_file(instance, cfg, get_config_mod(instance))
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}

    # ProcessManager.start 在 worker 存活时是 no-op（既不切换任务也不报错），
    # 扫描必须走「先停（后台等收尾）再以扫描任务启动」两段 —— 老 UI 同款。
    from module.webui.process_manager import ProcessManager

    mgr = ProcessManager.get_manager(instance)
    if mgr.alive:
        def action():
            mgr.stop_by_user()
            deadline = _time.monotonic() + 240
            while mgr.alive and _time.monotonic() < deadline:
                _time.sleep(2)
            mgr.start("MindCalculatorScan")
        try:
            from module.webui.instance_action import spawn_instance_action
            spawn_instance_action(mgr, action, "start")
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}
    else:
        mgr.start("MindCalculatorScan")
    return {"success": True, "task": "MindCalculatorScan"}


def import_mind_rows(name: str, rows: list) -> dict:
    """导入舰船清单（老 UI _mc_import_merge 同语义：同名合并、取最高等级）。

    rows: [{name, level}...]（前端已解析 JSON/CSV）。
    合并进现有清单后保存，返回新的清单与计算结果。
    """
    from module.runtime import mind_calculator as mind_calc

    try:
        cur = get_mind_calculator(name)
        ships = [] if cur.get("error") else cur.get("ships", [])
        incoming = []
        for r in rows or []:
            if not isinstance(r, dict) or not r.get("name"):
                continue
            try:
                level = int(r.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            incoming.append({"name": str(r["name"]).strip(), "level": level})
        if not incoming:
            return {"error": "没有可导入的数据行"}
        by_key = {}
        for s in ships:
            by_key[mind_calc.normalize_name(s.get("name", ""))] = s
        added, updated = 0, 0
        for r in incoming:
            key = mind_calc.normalize_name(r["name"])
            if key in by_key:
                existing = by_key[key]
                if (r["level"] or 0) > (existing.get("level") or 0):
                    existing["level"] = r["level"]
                    updated += 1
            else:
                new_item = dict(r)
                info = mind_calc.find_ship(r["name"])
                if info:
                    new_item["rarity"] = info["rarity"]
                    new_item["base_rarity"] = info["base_rarity"]
                new_item.setdefault("excluded", False)
                new_item.setdefault("review", True)
                new_item.setdefault("source", "手动导入")
                ships.append(new_item)
                by_key[key] = new_item
                added += 1
        saved = save_mind_ships(name, ships)
        if saved.get("error"):
            return saved
        return {"success": True, "added": added, "updated": updated, "total": len(ships),
                "ships": saved.get("ships", []), "result": saved.get("result", {})}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 心智清单导入失败({name}): {e}")
        return {"error": str(e)}


def fleet_scan_start(name: str, min_level: int, max_level: int) -> dict:
    """启动科技扫描（app_fleet_technology.py:_ft_scan_start 同链路）。

    校验等级范围 → 写扫描范围配置 → ProcessManager 两段语义（worker 存活
    先停后台收尾，再以 FleetTechScan 启动）。
    """
    import time as _time

    from module.config.deep import deep_set
    from module.config.utils import get_config_mod
    from module.webui.app_dependencies import State

    if not (1 <= min_level <= max_level <= 125):
        return {"error": "等级范围必须满足 1 ≤ 最低等级 ≤ 最高等级 ≤ 125"}
    try:
        updater = State.config_updater
        cfg = updater.read_file(name)
        deep_set(cfg, "FleetTechScan.FleetTech.MinLevel", int(min_level))
        deep_set(cfg, "FleetTechScan.FleetTech.MaxLevel", int(max_level))
        updater.write_file(name, cfg, get_config_mod(name))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}

    from module.webui.process_manager import ProcessManager

    mgr = ProcessManager.get_manager(name)
    if mgr.alive:
        def action():
            mgr.stop_by_user()
            deadline = _time.monotonic() + 240
            while mgr.alive and _time.monotonic() < deadline:
                _time.sleep(2)
            mgr.start("FleetTechScan")

        try:
            from module.webui.instance_action import spawn_instance_action
            spawn_instance_action(mgr, action, "start")
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
    else:
        mgr.start("FleetTechScan")
    return {"success": True, "task": "FleetTechScan"}


def fleet_sync_mind(name: str) -> dict:
    """把心智扫描结果累计进科技记录（app_fleet_technology.py:_ft_on_sync）。

    只增不减、可重复点击：tech.merge_mind 内部按「取更好值」合并。
    """
    from module.config.deep import deep_get, deep_set
    from module.config.transaction import config_transaction
    from module.config.utils import filepath_config, get_config_mod
    from module.runtime import fleet_tech as tech
    from module.webui.app_dependencies import State, load_config

    try:
        with config_transaction(filepath_config(name)):
            cfg = load_config(name).read_file(name)
            record = tech.read_record(cfg)
            result = deep_get(cfg, "MindCalculatorScan.MindCalculator.Result", {})
            ships = result.get("ships", []) if isinstance(result, dict) else []
            if not ships:
                return {"error": "没有心智扫描清单可同步（先跑一次心智扫描）", "empty": True}
            merged = tech.merge_mind(record, ships)
            changed = merged != record
            if changed:
                deep_set(cfg, tech.RECORD_PATH, merged)
                State.config_updater.write_file(name, cfg, get_config_mod(name))
        return {"success": True, "changed": changed}
    except (ValueError, TimeoutError, OSError) as exc:
        return {"error": str(exc)}


def get_update_info() -> dict:
    """更新器数据（app_developer_update.py:update_table 同口径：本地 commit + 上游 20 条历史）。"""
    import subprocess

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _git(args: list, timeout: int = 15):
        try:
            return subprocess.run(
                ["git", *args], cwd=repo, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            ).stdout.strip()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[webapi] git 调用失败 {args[:2]}: {e}")
            return ""

    local = _git(["log", "-1", "--format=%h|%an|%ci|%s"])
    # 上游分支：deploy.yaml Branch（默认 dev）
    branch = "dev"
    try:
        import yaml as _yaml

        deploy = _yaml.safe_load(open(os.path.join(repo, "config", "deploy.yaml"), encoding="utf-8"))
        branch = str(deploy.get("Deploy", {}).get("Git", {}).get("Branch") or "dev")
    except Exception:  # noqa: BLE001
        pass
    _git(["fetch", "origin", branch, "--quiet"], timeout=60)
    upstream_raw = _git(["log", f"origin/{branch}", "-20", "--format=%h|%an|%ci|%s"])
    history = []
    for line in (upstream_raw or "").split("\n"):
        parts = line.split("|", 3)
        if len(parts) == 4:
            history.append({"sha": parts[0], "author": parts[1], "time": parts[2], "message": parts[3]})
    local_parts = (local or "").split("|", 3)
    return {
        "branch": branch,
        "local": {"sha": local_parts[0] if len(local_parts) == 4 else "", "author": local_parts[1] if len(local_parts) == 4 else "", "time": local_parts[2] if len(local_parts) == 4 else "", "message": local_parts[3] if len(local_parts) == 4 else ""},
        "upstream": history,
    }

def get_remote_status() -> dict:
    """远程控制页状态（app_developer_tools.py:dev_remote 的数据面）。"""
    from module.webui.app_dependencies import RemoteAccess, State

    enabled = bool(State.deploy_config.EnableRemoteAccess)
    state = RemoteAccess.get_state()
    status_map = {
        "direct_p2p": "P2P 直连",
        "turn_relay": "TURN 中继",
        "ssh_forward": "SSH 隧道",
        "waiting_peer": "等待对端",
        "signaling": "信令中",
        "starting": "启动中",
        "dependency_missing": "SSH 隧道",
        "failed": "启动失败",
    }
    entry = RemoteAccess.get_entry_point()
    error = RemoteAccess.get_error()
    return {
        "enabled": enabled,
        "state": int(state) if isinstance(state, int) else state,
        "running": state in (1, 2),
        "connection_state": RemoteAccess.get_connection_state(),
        "connection_text": status_map.get(RemoteAccess.get_connection_state(), RemoteAccess.get_connection_state()),
        "entry_point": entry or "",
        "error": error or "",
        "is_alive": bool(RemoteAccess.is_alive()),
    }

def set_remote_enabled(enabled: bool) -> dict:
    """远控开关（写 deploy.yaml EnableRemoteAccess；服务启停沿用老 UI Switch 链路）。"""
    from module.webui.app_dependencies import RemoteAccess, State
    from module.config.deep import deep_set

    try:
        State.deploy_config.EnableRemoteAccess = enabled
        deep_set(State.deploy_config.data, "EnableRemoteAccess", enabled)
        State.deploy_config.update()
        if enabled:
            from module.webui.remote_access import start_remote_access_service

            start_remote_access_service()
        else:
            RemoteAccess.kill_ssh_process()
        return {"success": True, "enabled": enabled}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 远控开关失败: {e}")
        return {"success": False, "error": str(e)}

def get_wallpaper_candidates() -> dict:
    """壁纸候选直链（app_home.py:init_wallpaper 数据面：并发拉启用图源取直链）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from module.webui.app_home import HomeMixin

    mixin = HomeMixin.__new__(HomeMixin)  # 不走 __init__（会话态），只用静态方法
    candidates = []
    try:
        fetchers = [
            ("lolicon", mixin._fetch_lolicon_wallpaper),
            ("imgapi", mixin._fetch_imgapi_wallpaper),
        ]
        # 自定义直链源（yeelight/mukyu 等内置 custom）
        try:
            for source in mixin._load_sources():
                if source.get("type") == "custom" or source.get("direct"):
                    fetchers.append((source.get("key", "custom"), lambda s=source: mixin._fetch_custom_source(s)))
        except Exception:  # noqa: BLE001
            pass
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(func): key for key, func in fetchers}
            for fut in as_completed(futures):
                try:
                    url = fut.result()
                    if url:
                        is_video = bool(__import__("re").search(r"\.(mp4|webm|mov)(\?|$)", url, __import__("re").IGNORECASE))
                        candidates.append({ "source": futures[fut], "url": url, "video": is_video })
                except Exception:  # noqa: BLE001
                    continue
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 壁纸候选拉取失败: {e}")
    return {"candidates": candidates}

def get_oobe_state() -> dict:
    """OOBE 引导状态（module/config/utils.py:is_oobe_needed 同口径）。"""
    from module.config.utils import is_oobe_needed

    try:
        return {"oobe_needed": bool(is_oobe_needed())}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] OOBE 状态检查失败: {e}")
        return {"oobe_needed": False}

def start_scheduler(name: str, func: str | None = None) -> dict:
    """调度器启动（总览页"启动"按钮）。func=None 用配置默认入口。"""
    from module.webui.process_manager import ProcessManager
    from module.webui.app_dependencies import State

    try:
        pm = ProcessManager._processes.get(name)
        if pm is None:
            pm = ProcessManager(config_name=name)
            ProcessManager._processes[name] = pm
        if pm.alive:
            return {"success": False, "error": "调度器已在运行"}
        pm.start(func)
        return {"success": True, "alive": bool(pm.alive)}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 调度器启动失败({name}): {e}")
        return {"success": False, "error": str(e)}


def stop_scheduler(name: str) -> dict:
    """调度器停止（总览页"停止"按钮）。"""
    from module.webui.process_manager import ProcessManager

    try:
        pm = ProcessManager._processes.get(name)
        if pm is None:
            return {"success": True, "alive": False}
        stopped = pm.stop()
        return {"success": bool(stopped), "alive": bool(pm.alive)}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 调度器停止失败({name}): {e}")
        return {"success": False, "error": str(e)}

def get_theme_styles() -> dict:
    """当前主题的 CSS <link> 清单（utils.py:webui_style_names 同口径：基础层+主题层）。"""
    from module.webui.utils import webui_style_names
    from module.webui.app_dependencies import State

    try:
        theme = getattr(State, "theme", None) or State.deploy_config.Theme or "default"
    except Exception:  # noqa: BLE001
        theme = "default"
    try:
        names = webui_style_names(theme, False)
    except Exception:  # noqa: BLE001
        names = ["alas", "alas-pc", "light-alas"]
    links = [f"/static/assets/gui/css/{name}.css" for name in names]
    return {"theme": theme, "links": links}


def restart_webui() -> dict:
    """请求重启 WebUI 进程（让 deploy.yaml 的改动生效）。

    复用老 UI 的 request_webui_restart()（module/webui/app_developer_tools.py:60）：
    它会先把当前运行的实例名写进 config/reloadalas，再置 restart_event 通知父进程
    重启并恢复实例——与老 UI「重启」按钮完全相同的一条链路。

    返回 {success, message}。前端拿到 success 后应提示用户"正在重启，稍后刷新"。
    """
    try:
        from module.webui.app_developer_tools import request_webui_restart

        ok = bool(request_webui_restart())
        if ok:
            return {"success": True, "message": "正在重启 WebUI"}
        return {"success": False, "message": "重启请求被拒绝（可能有更新事务在进行）"}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] WebUI 重启请求失败: {e}")
        return {"success": False, "message": str(e)}


# 首屏 CSS 打包：老 UI 需要 12 个 CSS 文件（3 个 PyWebIO 基础层 + 9 个 alas 层）。
# 12 个 <link> 全是渲染阻塞资源，浏览器要全部下完才画第一帧——这是新 UI 首屏
# 比老 UI 慢的主要剩余项（每个 ~20-150ms 往返，串行叠加）。
# 合并成一个响应后只剩 1 个往返。
_BUNDLE_CSS_FILES_PC = (
    # 顺序**必须与老 UI 实际加载顺序逐项一致**（实测 22267 的 document.styleSheets）。
    # 顺序错了会出真问题：bs-theme 必须排在 markdown.min.css **之后**，
    # 因为深色 Bootstrap 里 body{color:#d3d3d3!important} 要覆盖
    # markdown.min.css 的 .markdown-body{color:#24292e}；反过来的话
    # 深色主题下 .markdown-body 仍把文字定成深色 →「深底深字」看不清。
    #
    # 老 UI 实测顺序：
    #   markdown.min.css → codemirror → toastify → bs-theme → bootstrap-select
    #   → app.css → alas/entry-alas/…（9 层，WS 注入）→ 主题层
    ("/pywebio_static/css/markdown.min.css", None),
    ("/pywebio_static/css/codemirror.min.css", None),
    ("/pywebio_static/css/toastify.min.css", None),
    # bs-theme 由 get_bundled_css(theme=) 动态替换成 default/dark
    ("/pywebio_static/css/bs-theme/default.min.css", None),
    ("/pywebio_static/css/bootstrap-select.min.css", None),
    ("/pywebio_static/css/app.css", None),
    ("/static/assets/gui/css/alas.css", "alas"),
    ("/static/assets/gui/css/alas-pc.css", "alas-pc"),
    ("/static/assets/gui/css/entry-alas.css", "entry-alas"),
    ("/static/assets/gui/css/statistics-alas.css", "statistics-alas"),
    ("/static/assets/gui/css/stat-delta-alas.css", "stat-delta-alas"),
    ("/static/assets/gui/css/meowfficer-score-alas.css", "meowfficer-score-alas"),
    ("/static/assets/gui/css/meow-loot-alas.css", "meow-loot-alas"),
    ("/static/assets/gui/css/fleet-tech-alas.css", "fleet-tech-alas"),
    ("/static/assets/gui/css/fleet-management-alas.css", "fleet-management-alas"),
)

# 移动端：与老 UI 的 utils.py:webui_style_names 完全一致——把 alas-pc 换成 alas-mobile。
# alas-mobile.css 里是「菜单折叠开关 / 日志小字号 / 总览改上下两行 / 仪表盘 6rem 网格 /
# 触控目标 44px」等整组窄屏规则，缺了它手机上排版会挤成一团。
_BUNDLE_CSS_FILES_MOBILE = tuple(
    (url, "alas-mobile" if name == "alas-pc" else name)
    for url, name in _BUNDLE_CSS_FILES_PC
)
_BUNDLE_CSS_FILES = _BUNDLE_CSS_FILES_PC

_BUNDLE_CSS_CACHE: dict = {}


def _read_css_file(url_path: str, alas_name: str | None) -> str:
    """读单个 CSS 文件（带 mtime 缓存）。缺失返回空串，不中断整包。"""
    import pathlib

    if alas_name is not None:
        path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "assets", "gui", "css", f"{alas_name}.css")
        )
    else:
        # PyWebIO 静态目录：找 pywebio 包内路径
        try:
            import pywebio

            # PyWebIO 的静态资源挂载点是 /pywebio_static -> pywebio/html/
            rel = url_path.replace("/pywebio_static/", "")
            path = os.path.abspath(os.path.join(os.path.dirname(pywebio.__file__), "html", rel))
        except Exception:  # noqa: BLE001
            return ""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
        cached = _BUNDLE_CSS_CACHE.get(path)
        if cached and cached[0] == key:
            return cached[1]
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        _BUNDLE_CSS_CACHE[path] = (key, content)
        return content
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] CSS 读取失败 {path}: {e}")
        return ""


def get_theme_name() -> str:
    """当前主题名。

    优先级改成 **deploy.yaml 的 Theme 优先**：
    原先是 State.theme 优先，而 State.theme 只在启动与老 UI set_theme() 时更新，
    新 UI 通过 /api/deploy/settings 改主题时它不会变 —— 实测点了没反应，
    /newapi/theme-css 一直返回旧主题。deploy_config.Theme 是实时读盘的，
    以它为准才能保证「保存即生效」。State.theme 仅作兜底。
    """
    try:
        from module.webui.app_dependencies import State

        State.deploy_config.read()
        theme = str(getattr(State.deploy_config, "Theme", None) or "").strip()
        if not theme:
            theme = str(getattr(State, "theme", None) or "").strip()
        return theme or "default"
    except Exception:  # noqa: BLE001
        return "default"


def _pywebio_bs_theme(theme: str) -> str:
    """与老 UI 的 app_shell.py:pywebio_theme_for 完全一致：只有 dark 用深色 Bootstrap。"""
    return "dark" if (theme or "").strip().lower() == "dark" else "default"


def get_bundled_css(is_mobile: bool = False, theme: str = "default") -> str:
    """合并首屏所需的全部 CSS。保持原顺序，缺失文件跳过。

    两处必须与老 UI 逐项对齐，否则会出现「深底深字」这类错配色：

    1. Bootstrap 主题层：老 UI 按主题加载 bs-theme/dark.min.css 或
       bs-theme/default.min.css（app_shell.py:pywebio_theme_for）。
       这一层提供 .markdown-body { color:#c9d1d9; background:#0d1117 } 等
       基础文字色 —— 固定用 default 会导致深色主题下文字是深黑、看不清。
    2. 侧栏分支：is_mobile 时 alas-pc → alas-mobile（utils.py:webui_style_names）。

    Args:
        is_mobile: True 时把 alas-pc.css 换成 alas-mobile.css。
        theme: 当前主题名，决定用哪套 Bootstrap 主题层。
    """
    files = list(_BUNDLE_CSS_FILES_MOBILE if is_mobile else _BUNDLE_CSS_FILES_PC)
    bs = _pywebio_bs_theme(theme)
    files = [
        (f"/pywebio_static/css/bs-theme/{bs}.min.css", None)
        if url.startswith("/pywebio_static/css/bs-theme/")
        else (url, name)
        for url, name in files
    ]
    parts = []
    for url_path, alas_name in files:
        content = _read_css_file(url_path, alas_name)
        if content:
            # 注释里用真实读到的文件名（移动端 alas-pc → alas-mobile，
            # 若照抄 url_path 会写成 alas-pc.css，排查时极容易误判）
            label = f"{alas_name}.css" if alas_name else url_path
            parts.append(f"/* ==== {label} ==== */\n{content}")
    return "\n".join(parts)


# 公告缓存：公告源在外网（jsdelivr/gitcode/raw.githubusercontent），
# 三个源都失败时很慢（本机实测单个超时 1s、三源串行）。缓存 5 分钟，
# 避免每次打开公告页都重新拉一遍。
_ANNOUNCEMENT_CACHE: dict = {}
_ANNOUNCEMENT_TTL = 300.0


def get_announcement(force: bool = False) -> dict:
    """公告内容（与 app_home.py:1750-1841 的 announcement_checker 同源）。

    数据来自 ApiClient.get_announcement()（module/base/api_client.py:58，
    jsdelivr → GitHub 三源回退）。配图经 localize_images 落本地后返回本地 URL，
    避免浏览器直连 jsdelivr（手机/远控基本不通）。

    返回 {title, content, id, url, error}。拉不到时 error 非空、其余为空。
    """
    import time as _time

    now = _time.time()
    cached = _ANNOUNCEMENT_CACHE.get("data")
    if not force and cached and now - cached[0] < _ANNOUNCEMENT_TTL:
        return cached[1]

    result: dict = {"title": "", "content": "", "id": None, "url": "", "error": ""}
    try:
        from module.base.api_client import ApiClient

        data = ApiClient.get_announcement(timeout=10)
        if not data:
            result["error"] = "公告拉取失败（三个源都不可达）"
        else:
            content = data.get("content") or ""
            # 配图本地化：失败不影响文字公告
            if content:
                try:
                    from module.webui.announcement_images import localize_images

                    content = localize_images(content)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[webapi] 公告配图本地化失败: {e}")
            result = {
                "title": data.get("title", "") or "",
                "content": content,
                "id": data.get("announcementId"),
                "url": data.get("url", "") or "",
                "error": "",
            }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 公告获取失败: {e}")
        result["error"] = str(e)

    if not result["error"]:
        _ANNOUNCEMENT_CACHE["data"] = (now, result)
    return result


def get_update_log() -> dict:
    """更新日志（与 app_developer_update.py:230-270 同源）。

    数据在独立仓库 changqing81/announcement-changelog 的 changelog.json，
    走 ApiClient.get_changelog()（与公告同一套分发），在**更新前**就能读到这一版改了什么。
    返回 {entries: [{id,date,title,content,sha}], html, error}。
    html 直接复用 module/webui/update_log.py:render_entries_html 的服务端渲染，
    保证与新老 UI 显示完全一致（含图片安全性过滤）。
    """
    try:
        from module.base.api_client import ApiClient
        from module.webui.update_log import normalize_entries, render_entries_html

        data = ApiClient.get_changelog(timeout=5)
        entries = normalize_entries(data)
        return {
            "entries": entries,
            "html": render_entries_html(entries, ""),
            "error": "",
        }
    except Exception as e:  # noqa: BLE001 —— 更新日志是辅助信息，失败不阻塞页面
        logger.warning(f"[webapi] 更新日志读取失败: {e}")
        return {"entries": [], "html": "", "error": str(e)}


# ─────────────────────── 实例管理（补老 UI 的 /manage 页缺口） ───────────────────────
# 老 UI 的管理页（module/webui/app_manage.py）全走 PyWebIO WS 回调，没有 REST 等价物。
# 关掉老 UI（UI=new）后侧栏「管理」按钮会 404，所以这里补一组最小可用端点：
# 列表 / 创建 / 删除。导入导出等重逻辑暂不迁移（见 docs/new-ui/feature-coverage.md）。


def list_instances_detail() -> dict:
    """实例列表 + 每个实例的状态与模块名（管理页用）。

    状态码与老 UI 一致：1=running / 2=inactive / 3=warning / 4=updating。
    """
    from module.config.utils import alas_instance, get_config_mod
    from module.webui.process_manager import ProcessManager

    items = []
    try:
        names = alas_instance()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 实例列表失败: {e}")
        return {"instances": [], "templates": []}

    for name in names:
        state = 2
        try:
            state = ProcessManager.get_manager(name).state
        except Exception:  # noqa: BLE001 —— 单个实例状态读不到不影响整页
            pass
        try:
            mod_name = get_config_mod(name)
        except Exception:  # noqa: BLE001
            mod_name = "alas"
        items.append({"name": name, "state": state, "mod": mod_name})

    templates = []
    try:
        from module.config.utils import alas_template

        templates = list(alas_template())
    except Exception:  # noqa: BLE001
        pass
    return {"instances": items, "templates": templates}


def create_instance(name: str, copy_from: str) -> dict:
    """从模板/现有实例复制出一个新实例（对应 app_manage.py:376-392）。

    Returns: {success, error}
    """
    from module.config.utils import alas_instance
    from module.webui.app_dependencies import State

    name = (name or "").strip()
    # 校验规则与老 UI validate_name（app_manage.py:136-145）逐条一致
    if not name:
        return {"success": False, "error": "名称不能为空"}
    try:
        existing = alas_instance()
    except Exception:  # noqa: BLE001
        existing = []
    if name in existing:
        return {"success": False, "error": "实例名已存在"}
    if set(name) & set('.\\/:*?"\'<>|'):
        return {"success": False, "error": "名称含非法字符（禁止 . \\ / : * ? \" \' < > |）"}
    if name.lower().startswith("template"):
        return {"success": False, "error": "名称不能以 template 开头"}

    try:
        updater = State.config_updater
        # 与老 UI 相同：读源实例配置 → 以新名字写盘
        config = updater.read_file(copy_from)
        updater.write_file(name, config)
        logger.info(f"[webapi] 已创建实例 {name}（复制自 {copy_from}）")
        return {"success": True, "error": ""}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 创建实例失败({name}): {e}")
        return {"success": False, "error": str(e)}


def delete_instance(name: str) -> dict:
    """删除实例配置文件（对应 app_manage.py:266-328）。

    两道保护与老 UI 完全一致：不能删最后一个实例、不能删运行中的实例。
    """
    from module.config.utils import alas_instance, filepath_config, get_config_mod
    from module.webui.process_manager import ProcessManager

    try:
        if len(alas_instance()) <= 1:
            return {"success": False, "error": "不能删除最后一个实例"}
    except Exception:  # noqa: BLE001
        pass
    try:
        if ProcessManager.is_running(name):
            return {"success": False, "error": f"实例 {name} 正在运行，请先停止"}
    except Exception:  # noqa: BLE001
        pass

    try:
        mod_name = get_config_mod(name)
        os.remove(filepath_config(name, mod_name))
        ProcessManager.remove_manager(name)
        logger.info(f"[webapi] 已删除实例 {name}")
        return {"success": True, "error": ""}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 删除实例失败({name}): {e}")
        return {"success": False, "error": str(e)}


def import_instance_config(filename: str, content: bytes) -> dict:
    """导入配置文件（对应 app_manage.py:429-484 的 _import）。

    文件名决定实例名与模块名，规则与老 UI 完全一致：
    - `alas.json`              → 实例 alas，模块 alas
    - `alas2.alas.json`        → 实例 alas2，模块 alas（三段式）
    - 其余 `xxx.json`          → 实例 xxx，模块 alas
    内容必须是 JSON 对象；写入走 State.config_updater.write_file（会做配置迁移）。

    Returns: {success, error, name, mod}
    """
    import json as _json

    from module.webui.app_dependencies import State

    try:
        # 1) 解析文件名 → (实例名, 模块名)，与老 UI 同一套规则
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        parts = stem.split(".")
        if len(parts) == 1:
            config_name, mod_name = parts[0], "alas"
        else:
            config_name, mod_name = parts[0], parts[1]

        if not config_name:
            return {"success": False, "error": "文件名无法解析出实例名", "name": "", "mod": ""}

        # 2) 实例名白名单校验（挡住路径穿越与非法字符）
        from module.config.utils import alas_instance

        if set(config_name) & set('.\\/:*?"\'<>|'):
            return {"success": False, "error": "实例名含非法字符", "name": "", "mod": ""}
        if config_name.lower().startswith("template"):
            return {"success": False, "error": "实例名不能以 template 开头", "name": "", "mod": ""}

        # 3) 内容必须是 JSON 对象
        try:
            config = _json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, _json.JSONDecodeError) as e:
            return {"success": False, "error": f"文件不是合法 JSON：{e}", "name": "", "mod": ""}
        if not isinstance(config, dict):
            return {"success": False, "error": "配置文件根节点必须是对象", "name": "", "mod": ""}

        # 4) 写盘（覆盖同名实例，与老 UI 的 OverrideWarning 提示一致）
        State.config_updater.write_file(config_name, config, mod_name)
        logger.info(f"[webapi] 已导入配置 {config_name}（模块 {mod_name}）")
        return {"success": True, "error": "", "name": config_name, "mod": mod_name}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 导入配置失败({filename}): {e}")
        return {"success": False, "error": str(e), "name": "", "mod": ""}


# ─────────────────── 开发者工具（补老 UI dev_utils 的 REST 缺口）───────────────────
# 老 UI 的这些调试动作全在 PyWebIO 回调里（app_developer_tools.py:dev_utils），
# 新 UI 没有 WS 通道。这里把它们逐个搬成 REST —— 逻辑一行不改，只换调用方式。
# 这些是开发调试用，非终端用户高频路径，但仍要能跑，不能只显示"请切回老 UI"。


def dev_raise_exception() -> dict:
    """抛出异常事件（老 UI 的 raise_exception）。

    直接在服务端抛，让 PyWebIO/Starlette 的错误处理走一遍，
    用于验证异常捕获与错误页渲染。
    """
    raise RuntimeError("开发者工具：故意抛出的测试异常（新 UI 触发）")


def dev_preview_update_notice() -> dict:
    """预览更新提示（_preview_update_notice）。"""
    from module.webui.app_dependencies import State

    # 老 UI 的做法：走 updater 的预览通道。这里复用同一入口。
    try:
        updater = State.deploy_config  # 仅用于确认 State 已初始化
        del updater
        from module.webui import update_notice

        fn = getattr(update_notice, "preview", None)
        if callable(fn):
            fn()
            return {"success": True, "note": "已触发更新提示预览"}
        return {"success": False, "error": "当前版本没有 update_notice.preview 入口"}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 预览更新提示失败: {e}")
        return {"success": False, "error": str(e)}


def dev_mock_icon_state(instance: str, state: int, seconds: int = 10) -> dict:
    """模拟侧栏实例图标状态（_mock_icon_state）。

    state: 1=运行中 / 3=异常 / 4=更新中
    """
    from module.webui.process_manager import ProcessManager

    try:
        if not instance:
            return {"success": False, "error": "没有可用实例"}
        ProcessManager.get_manager(instance).set_state_override(state, duration=seconds)
        logger.info(f"[webapi] 已模拟 {instance} 图标状态 {state}（{seconds}s）")
        return {"success": True, "instance": instance, "state": state}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 模拟图标状态失败: {e}")
        return {"success": False, "error": str(e)}


def dev_clear_icon_state(instance: str) -> dict:
    """清除图标模拟状态（_clear_mock_icon_state）。"""
    from module.webui.process_manager import ProcessManager

    try:
        if not instance:
            return {"success": False, "error": "没有可用实例"}
        ProcessManager.get_manager(instance).clear_state_override()
        return {"success": True, "instance": instance}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def dev_cross_month_rehearsal(instance: str, mode: str) -> dict:
    """跨月预演（_cross_month_rehearsal）。

    mode: "cleanup" 仅清理 / "full" 全流程
    向目标实例写入 RehearsalDebug 并立即触发调度。
    """
    from module.config.deep import deep_set
    from module.config.time_source import now as current_time
    from module.webui.app_dependencies import State

    try:
        if not instance:
            return {"success": False, "error": "没有可用实例"}
        if mode not in ("cleanup", "full"):
            return {"success": False, "error": "mode 必须是 cleanup 或 full"}
        data = State.config_updater.read_file(instance)
        deep_set(data, "OpsiCrossMonth.OpsiCrossMonth.RehearsalDebug", mode)
        deep_set(data, "OpsiCrossMonth.Scheduler.Enable", True)
        deep_set(
            data,
            "OpsiCrossMonth.Scheduler.NextRun",
            current_time().strftime("%Y-%m-%d %H:%M:%S"),
        )
        State.config_updater.write_file(instance, data)
        logger.info(f"[webapi] 已向 {instance} 下发跨月预演（{mode}）")
        return {"success": True, "instance": instance, "mode": mode}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 跨月预演失败: {e}")
        return {"success": False, "error": str(e)}


def dev_cancel_cross_month_rehearsal(instance: str) -> dict:
    """取消待执行的跨月预演（_cross_month_rehearsal_cancel）。"""
    from datetime import timedelta

    from module.config.deep import deep_get, deep_set
    from module.config.utils import get_os_next_reset
    from module.webui.app_dependencies import State

    try:
        if not instance:
            return {"success": False, "error": "没有可用实例"}
        data = State.config_updater.read_file(instance)
        mode = deep_get(data, "OpsiCrossMonth.OpsiCrossMonth.RehearsalDebug", "off")
        if mode not in ("cleanup", "full"):
            return {"success": False, "error": "当前没有待执行的跨月预演请求"}
        deep_set(data, "OpsiCrossMonth.OpsiCrossMonth.RehearsalDebug", "off")
        next_run = get_os_next_reset() - timedelta(minutes=10)
        deep_set(
            data,
            "OpsiCrossMonth.Scheduler.NextRun",
            next_run.strftime("%Y-%m-%d %H:%M:%S"),
        )
        State.config_updater.write_file(instance, data)
        return {"success": True, "instance": instance}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def dev_restart_alas(instance: str) -> dict:
    """重启 Alas 实例（老 UI 的「重启Alas」按钮）。

    老 UI（app_developer_tools.py:193-201 _restart_alas）的链路是
    mgr.stop() + mgr.start(None)。**start() 必须传 func=None**——
    之前漏了这个参数，实测报
    ProcessManager.start() missing 1 required positional argument: 'func'。
    """
    from module.webui.process_manager import ProcessManager

    try:
        if not instance:
            return {"success": False, "error": "没有可用实例"}
        mgr = ProcessManager.get_manager(instance)
        mgr.stop()
        mgr.start(None)
        logger.info(f"[webapi] 已重启实例 {instance}")
        return {"success": True, "instance": instance}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 重启实例失败: {e}")
        return {"success": False, "error": str(e)}


def dev_notify_test(instance: str, kind: str) -> dict:
    """推送测试（老 UI app_developer_tools.py:385-425 的三个按钮）。

    kind:
        update       → notify_webui(update=True)   测试更新推送
        announcement → notify_webui(updata=False)  测试公告推送
        error        → handle_notify(Error_OnePushConfig) 测试错误推送

    注意老 UI 的拼写就是 `updata`（公告那条的参数名），notify_webui
    的签名如此，不是错别字。
    """
    try:
        if not instance:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance = all_instances[0] if all_instances else None
        if not instance:
            return {"success": False, "error": "没有可用实例"}

        if kind == "update":
            from module.notify.notify import notify_webui

            notify_webui(
                instance=instance,
                title="发现更新喵！",
                content="测试更新推送逻辑，启动器应显示专用标题。",
                update=True,
            )
            return {"success": True, "instance": instance, "kind": kind,
                    "note": "更新推送已发送（启动器应显示专用标题）"}
        if kind == "announcement":
            from module.notify.notify import notify_webui

            notify_webui(
                instance=instance,
                title="新公告喵！",
                content="测试公告推送逻辑，启动器应显示专用标题。",
                updata=False,
            )
            return {"success": True, "instance": instance, "kind": kind,
                    "note": "公告推送已发送（启动器应显示专用标题）"}
        if kind == "error":
            from module.config.config import AzurLaneConfig
            from module.notify import handle_notify

            config = AzurLaneConfig(instance)
            success = handle_notify(
                config.Error_OnePushConfig,
                title=f"AzurPilot <{instance}> 崩溃",
                content=f"<{instance}> 开发者错误推送测试",
            )
            if not success:
                return {"success": False, "error": "onepush 发送失败（未配置推送渠道时属正常）",
                        "instance": instance, "kind": kind}
            return {"success": True, "instance": instance, "kind": kind,
                    "note": "错误推送已发送"}
        return {"success": False, "error": f"未知推送测试类型: {kind}"}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 推送测试失败: {e}")
        return {"success": False, "error": str(e)}


def dev_preview_update_notice_v2() -> dict:
    """预览更新提示的**数据部分**（老 UI app_developer_menu.py:69 的弹窗内容）。

    老 UI 在浏览器里注入 #alas-update-notice DOM。新 UI 没有这条通道，
    但前端有自己的公告/提示渲染（alasShowAnnouncement），所以这里只返回
    弹窗内容，由前端调用本地渲染函数显示。
    """
    return {
        "success": True,
        "title": "有可用更新！",
        "eyebrow": "发现新版本",
        "content": "建议及时更新，以获得更稳定的脚本运行体验。",
    }


# ─────────────────── 背景/壁纸管理（补老 UI 主页 4 个按钮的 REST 缺口）───────────────────
# 老 UI 这 4 个动作都在 app_home.py 的 PyWebIO 回调里（input_group 弹窗），
# 新 UI 没有 WS 通道。这里把存储逻辑原样搬过来（路径/字段名/默认值全部一致），
# 只换调用方式 —— 与老 UI 共享同一份 wallpapers/background_setting.json，
# 所以两边切换后设置不会丢。

_WALLPAPER_DIR_NAME = "wallpapers"
_BG_SETTING_FILE = "background_setting.json"
_SOURCES_FILE_NAME = "wallpaper_sources.json"


def _wallpapers_dir():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / _WALLPAPER_DIR_NAME


def _read_bg_setting() -> dict:
    """读 wallpapers/background_setting.json（与老 UI 同一份）。"""
    import json as _json

    path = _wallpapers_dir() / _BG_SETTING_FILE
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write_bg_setting(update: dict) -> None:
    """合并写入背景设置，保留其它字段（同 app_home.py:_write_background_setting）。"""
    import json as _json

    path = _wallpapers_dir() / _BG_SETTING_FILE
    data = _read_bg_setting()
    data.update(update)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(data), encoding="utf-8")


def get_background_setting() -> dict:
    """当前背景设置（mode / media）+ 自定义背景是否存在。"""
    setting = _read_bg_setting()
    custom_dir = _wallpapers_dir() / "custom"
    has_custom = False
    if custom_dir.is_dir():
        has_custom = any(custom_dir.iterdir())
    return {
        "mode": setting.get("mode", "random"),
        "media": setting.get("media", "auto"),
        "has_custom": has_custom,
    }


def set_background_mode(mode: str) -> dict:
    """切换随机/自定义背景模式（对应 _switch_to_random_background）。"""
    if mode not in ("random", "custom"):
        return {"success": False, "error": "mode 必须是 random 或 custom"}
    try:
        _write_bg_setting({"mode": mode})
        return {"success": True, "mode": mode}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 切换背景模式失败: {e}")
        return {"success": False, "error": str(e)}


def set_media_preference(media: str) -> dict:
    """设置随机背景的媒体类型偏好（对应 _set_media_preference）。

    media: image / video / auto
    """
    if media not in ("image", "video", "auto"):
        return {"success": False, "error": "media 必须是 image/video/auto"}
    try:
        _write_bg_setting({"media": media})
        return {"success": True, "media": media}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def upload_custom_background(filename: str, content: bytes) -> dict:
    """上传自定义背景（对应 _upload_custom_background 的落盘部分）。

    只做「保存文件 + 标记为自定义模式」，不做老 UI 的内存压缩
    （那是为了塞进 data URI，新 UI 走 HTTP 路由 /api/wallpapers/custom 取文件，
    不需要 data URI，所以跳过压缩反而保真）。
    """
    import os as _os

    try:
        if not content:
            return {"success": False, "error": "文件为空"}
        # 20MB 上限（老 UI 没限制但内联 data URI 实际上限更低；
        # 走 HTTP 路由后可以放宽）
        if len(content) > 20 * 1024 * 1024:
            return {"success": False, "error": "文件超过 20MB 限制"}
        ext = _os.path.splitext(filename or "")[1].lower()
        allowed = {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
            ".mp4", ".m4v", ".webm", ".mov",
        }
        if ext not in allowed:
            return {"success": False, "error": f"不支持的文件类型: {ext or '(无扩展名)'}"}

        custom_dir = _wallpapers_dir() / "custom"
        custom_dir.mkdir(parents=True, exist_ok=True)
        # 清掉旧的自定义背景（老 UI 同一时刻只保留一个）
        for old in custom_dir.iterdir():
            try:
                old.unlink()
            except OSError:
                pass
        target = custom_dir / ("custom_bg" + ext)
        target.write_bytes(content)
        _write_bg_setting({"mode": "custom"})
        logger.info(f"[webapi] 已保存自定义背景 {target.name}（{len(content)} 字节）")
        return {"success": True, "filename": target.name, "mode": "custom"}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 上传自定义背景失败: {e}")
        return {"success": False, "error": str(e)}


def get_custom_background_info() -> dict:
    """自定义背景的访问信息（新 UI 用 <img>/<video> 直接取 /api/wallpapers/custom）。"""
    import os as _os

    custom_dir = _wallpapers_dir() / "custom"
    if not custom_dir.is_dir():
        return {"exists": False}
    files = [f for f in custom_dir.iterdir() if f.is_file()]
    if not files:
        return {"exists": False}
    f = files[0]
    ext = _os.path.splitext(f.name)[1].lower()
    is_video = ext in (".mp4", ".m4v", ".webm", ".mov")
    return {
        "exists": True,
        "url": "/api/wallpapers/custom",
        "is_video": is_video,
        "filename": f.name,
        "bytes": f.stat().st_size,
    }


def get_wallpaper_sources() -> dict:
    """图源列表（wallpapers/wallpaper_sources.json；文件不存在时回落内置默认源，
    与老 UI _wallpaper_source_list 的「内置默认 + 用户自定义」语义一致）。"""
    import json as _json

    path = _wallpapers_dir() / _SOURCES_FILE_NAME
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        return {"success": True, "sources": data if isinstance(data, list) else []}
    except FileNotFoundError:
        from module.webui.app_home import _BUILTIN_SOURCES
        return {"success": True, "sources": _BUILTIN_SOURCES, "builtin_only": True}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e), "sources": []}


def set_wallpaper_sources(sources) -> dict:
    """保存图源列表（对应 _manage_wallpaper_sources 的落盘部分）。"""
    import json as _json

    try:
        if not isinstance(sources, list):
            return {"success": False, "error": "sources 必须是数组"}
        path = _wallpapers_dir() / _SOURCES_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(sources, ensure_ascii=False), encoding="utf-8")
        return {"success": True, "count": len(sources)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def reset_wallpaper_sources() -> dict:
    """恢复默认图源（删除自定义文件，回落到内置默认）。"""
    try:
        path = _wallpapers_dir() / _SOURCES_FILE_NAME
        if path.exists():
            path.unlink()
        return {"success": True}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


# ─────────────────── 统计页面板缓存 ───────────────────
# 为什么需要缓存（实测数据）：
#   opsi 面板单次渲染 1202ms、research 518ms、commission 461ms、delta 200ms，
#   且**连续 5 次耗时完全一样**（无任何缓存效果）。
#   分段计时定位：_build_meow_rows 669ms（对侵蚀等级 1~6 各查一次 SQLite），
#   _build_opsi_month_cards 182ms。
#
# 老 UI 的做法：app_statistics_page.py 用 _statistics_cache_key 判断数据是否
# 变化，没变化就不重建。这里用同样的思路，但缓存键取**数据源 mtime + 月份**：
#   - config/cl1_data.db 的 mtime（战斗/掉落数据都写在这里）
#   - 当前年月（切月份要重算）
#   - 实例名
# 数据没变就直接返回上次的 HTML；变了才重算。
# 这样「进页面看统计」这种高频操作只有首次慢，之后都是内存命中。

_STAT_HTML_CACHE: dict = {}
_STAT_HTML_CACHE_LOCK = None


def _stat_cache_key(instance_name: str, panel: str, month: str = "") -> tuple:
    """统计面板的缓存键：数据源 mtime + 年月 + 实例 + 面板名 + 查看月份。

    month 是 "YYYY-MM" 形式的查看月份（空 = 本月），用于统计页的月份切换：
    切换后必须重算，所以必须进缓存键。
    """
    import os as _os

    from module.config.time_source import now as _now

    t = _now()
    db_path = _wallpapers_dir().parent / "config" / "cl1_data.db"
    try:
        db_mtime = _os.stat(db_path).st_mtime_ns
    except OSError:
        db_mtime = 0
    return (instance_name, panel, db_mtime, t.year, t.month, month)


def _cached_stat_html(instance_name: str, panel: str, builder, month: str = ""):
    """带缓存的统计面板渲染。builder() 返回 dict，缓存其成功结果。

    month: "YYYY-MM" 查看月份（空 = 本月），参与缓存键。
    """
    global _STAT_HTML_CACHE_LOCK
    if _STAT_HTML_CACHE_LOCK is None:
        import threading

        _STAT_HTML_CACHE_LOCK = threading.Lock()

    key = _stat_cache_key(instance_name, panel, month)
    with _STAT_HTML_CACHE_LOCK:
        hit = _STAT_HTML_CACHE.get(key)
    if hit is not None:
        return hit

    result = builder()
    if isinstance(result, dict) and result.get("success"):
        # 只缓存成功结果；失败下次重试（数据可能刚写入）
        with _STAT_HTML_CACHE_LOCK:
            # 只保留最近的 12 个键（3 面板 × 4 个月份量级），避免无限增长
            if len(_STAT_HTML_CACHE) > 12:
                _STAT_HTML_CACHE.clear()
            _STAT_HTML_CACHE[key] = result
    return result


# ─────────────────── 统计页 HTML 复用（老 UI 模板直接渲染）───────────────────
# 为什么复用老 UI 的渲染器而不是在 React 里重写：
#   老 UI 的统计页由 11 个 HTML 模板（webapp/*.html）拼装，指标口径、
#   千分位格式、卡片语义色、折叠块结构全部固化在 Python 侧
#   （app_stat_opsi.py:_build_opsi_summary_html 等几百行）。
#   在 React 里重写等价于把那几百行翻译一遍，且必然出现「数字对不上」。
#   正确做法：后端调老 UI 的同一套构建函数产出 HTML 字符串，
#   前端 dangerouslySetInnerHTML 挂上 —— 结构/样式/数字逐字节一致。
#
# 这里用一个轻量的「假 self」适配器：老 UI 的构建函数都是 Mixin 方法，
# 只依赖 self 的少数属性（alas_name）与几个内部工具函数。


class _StatRenderCtx:
    """供老 UI 统计渲染函数使用的最小上下文。

    app_stat_opsi.py 的方法用到 self.alas_name、self._stat_month_pair()、
    若干自身方法，以及**类级常量**（MEOW_BADGE_COLORS 等）。
    直接实例化 mixin 会拉起整个 AlasGUI（需要 PyWebIO 会话），
    所以用适配器把方法和常量逐个转过去。
    """

    # 类级常量（从 OpsiStatisticsMixin 镜像过来，见 _mirror_class_attrs）
    MEOW_BADGE_COLORS = {3: "#378ADD", 5: "#E24B4A"}
    MEOW_COLLAPSE_TPL = "fold_block.html"

    def __init__(self, instance_name: str):
        self.alas_name = instance_name
        self._stat_year, self._stat_month = _current_stat_month()
        self._mirror_class_attrs()

    def _mirror_class_attrs(self) -> None:
        """把 mixin 上的大写类常量（MEOW_* 等）镜像到本实例。"""
        from module.webui.app_stat_opsi import OpsiStatisticsMixin

        for _name in dir(OpsiStatisticsMixin):
            if not _name.isupper():
                continue
            if hasattr(type(self), _name):
                continue
            try:
                setattr(self, _name, getattr(OpsiStatisticsMixin, _name))
            except Exception:  # noqa: BLE001
                pass

    def _stat_selected_month(self):
        """用户显式选中的月份 (year, month)；None 表示本月。

        老 UI app_statistics_page.py:1102 同款。REST 下「选中月份」由构造时
        传入的 _stat_year/_stat_month 表达（默认即当前月），所以这里只在
        与当前月不同时返回该月份 —— 各面板据此决定 persist 等行为。
        """
        from module.config.time_source import now as _now

        now = _now()
        if (self._stat_year, self._stat_month) == (now.year, now.month):
            return None
        return self._stat_year, self._stat_month

    def _stat_month_pair(self):
        return self._stat_year, self._stat_month

    def _stat_month_is_current(self) -> bool:
        """当前查看的月份是否是本月（老 UI app_statistics_page.py:1131 同款）。"""
        from module.config.time_source import now as _now

        t = _now()
        return (self._stat_year, self._stat_month) == (t.year, t.month)


def _current_stat_month():
    """当前统计月份（与老 UI app_statistics_page 的默认月份一致：当月）。"""
    from module.config.time_source import now as _now

    t = _now()
    return t.year, t.month


def _stat_render_method(name: str):
    """取老 UI 的统计渲染方法（未绑定），供适配器调用。

    「大世界收获」的四块分散在两个 mixin 里：
      OpsiStatisticsMixin —— _build_meow_rows / _build_meow_cards_html
                            / _build_opsi_summary_html / _opsi_card 等
      OpsiExportMixin     —— _build_meow_header_html / _build_meow_strip_html
                            / _build_opsi_drop_table_html / _load_meow_loot_view
                            / _load_opsi_drop_view 等
    只查第一个会 AttributeError（实测：_build_meow_header_html 找不到）。
    """
    from module.webui.app_stat_opsi import OpsiStatisticsMixin
    from module.webui.app_stat_opsi_export import OpsiExportMixin

    for mixin in (OpsiStatisticsMixin, OpsiExportMixin):
        if hasattr(mixin, name):
            return getattr(mixin, name)
    raise AttributeError(f"统计渲染方法不存在: {name}")


def get_opsi_panels_html(instance_name: str, month: str = "") -> dict:
    """大世界面板。month = "YYYY-MM"（空 = 本月）。"""
    return _cached_stat_html(
        instance_name, "opsi",
        lambda: _get_opsi_panels_html_impl(instance_name, month),
        month,
    )


def _get_opsi_panels_html_impl(instance_name: str, month: str = "") -> dict:
    """返回「大世界」页的完整 HTML（净赚卡 + 12 指标卡 + 大世界收获）。

    直接调老 UI 的 _render_opsi_stats 系列构建函数，把它们的输出
    收集成字符串返回。这样新 UI 显示的内容与老 UI 完全一致。

    Returns: {success, html, error}
    """
    import io
    from contextlib import redirect_stdout

    try:
        ctx = _StatRenderCtx(instance_name)
        # 月份切换：把 "YYYY-MM" 覆盖到适配器
        if month:
            try:
                _y, _m = month.split("-")
                ctx._stat_year, ctx._stat_month = int(_y), int(_m)
            except (ValueError, AttributeError):
                pass
        # 绑定老 UI 的方法到适配器。
        # 注意 staticmethod 不能再 __get__ 绑定，否则会多传一个 self
        # （实测报 "got multiple values for argument 'tone'"）。
        # 把所有统计渲染方法镜像到适配器上。
        # 原来手写白名单，漏一个就 AttributeError（已经踩了两次：
        # _build_meow_header_html、_meow_loot_icon_html）。两个 mixin 的
        # 方法都镜像，白名单维护交给 hasattr 判断。
        from module.webui.app_stat_opsi import OpsiStatisticsMixin
        from module.webui.app_stat_opsi_export import OpsiExportMixin

        for _src in (OpsiStatisticsMixin, OpsiExportMixin):
            for _m in dir(_src):
                if _m.startswith("__"):
                    continue
                if hasattr(ctx, _m) and not _m.startswith("_build"):
                    continue
                _desc = _src.__dict__.get(_m)
                if _desc is None:
                    continue
                try:
                    if isinstance(_desc, staticmethod):
                        setattr(ctx, _m, _desc.__func__)
                    elif callable(_desc):
                        setattr(ctx, _m, _desc.__get__(ctx, type(ctx)))
                except Exception:  # noqa: BLE001
                    pass
        payload = ctx._build_opsi_month_cards()
        if payload is None:
            return {"success": False, "html": "", "error": "数据源不可用"}
        net, cards, inst, cl1_db = payload
        summary_html = ctx._build_opsi_summary_html(net, cards)
        meow_rows = ctx._build_meow_rows(cl1_db, inst)

        # 「大世界收获」在老 UI 里是四块（app_stat_opsi_export.py
        # _render_meowofficer_farming）：页头 + 合计条 strip + 左栏任务筛选
        # + 右栏明细标签/侵蚀卡/掉落明细表。之前只接了侵蚀卡，长卿看到
        # 「大世界收获」只剩两张卡（2026-11-17：侵蚀5 其他东西呢？深渊开荒、
        # 每日统计、金菜、深渊隐蔽呢？）。这里补齐可纯 HTML 化的三块。
        meow_html = ctx._build_meow_cards_html(meow_rows)
        meow_header_html = ""
        meow_strip_html = ""
        meow_drop_html = ""
        try:
            _view = ctx._load_meow_loot_view()
            _drop = ctx._load_opsi_drop_view()
            meow_header_html = ctx._build_meow_header_html()
            meow_strip_html = ctx._build_meow_strip_html(_view, _drop)
            meow_drop_html = ctx._build_opsi_drop_table_html(_drop)
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"[webapi] 大世界收获明细渲染失败: {_e}")
        return {
            "success": True,
            "html": summary_html,
            "meow_html": meow_html,
            "meow_header_html": meow_header_html,
            "meow_strip_html": meow_strip_html,
            "meow_drop_html": meow_drop_html,
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染大世界统计失败: {e}")
        return {"success": False, "html": "", "meow_html": "", "error": f"{type(e).__name__}: {e}"}


def _make_stat_ctx(instance_name: str, mixin_module: str, mixin_class: str):
    """构造统计渲染适配器，自动镜像 mixin 的方法与类常量。

    与 _StatRenderCtx 同思路：老 UI 的统计渲染函数都是 Mixin 方法，
    只依赖 self.alas_name、_stat_month_pair 等少数属性，
    用适配器即可在没有 PyWebIO 会话的情况下直接调用。
    """
    import importlib

    module = importlib.import_module(mixin_module)
    mixin = getattr(module, mixin_class)

    class _Ctx:
        MEOW_BADGE_COLORS = {3: "#378ADD", 5: "#E24B4A"}
        MEOW_COLLAPSE_TPL = "fold_block.html"

        def __init__(self):
            self.alas_name = instance_name
            self._stat_year, self._stat_month = _current_stat_month()
            # 老 UI 在 app_statistics_page.py:192-193 懒初始化的实例属性，
            # 各统计面板会读它（委托面板的周期：month / week）。
            self._commission_income_period = "month"

        def _stat_month_pair(self):
            return self._stat_year, self._stat_month

        def _stat_month_is_current(self) -> bool:
            from module.config.time_source import now as _now

            t = _now()
            return (self._stat_year, self._stat_month) == (t.year, t.month)

        def _stat_month_key(self) -> str:
            """当前查看月份键 "YYYY-MM"（老 UI app_statistics_page.py:1126）。"""
            return f"{self._stat_year:04d}-{self._stat_month:02d}"

        def _open_stat_month_picker(self) -> None:
            """月份选择器：REST 场景下由前端下拉框承担，这里给空实现。

            老 UI 这个方法是弹窗（PyWebIO popup），REST 化后由前端 UI 处理。
            留着空实现是为了让复用过来的渲染函数不因缺属性报错。
            """

        def _stat_selected_month(self):
            """显式选中的月份；None 表示本月（老 UI 同款）。"""
            return (self._stat_year, self._stat_month)

        # 面板通用工具（各 Mixin 都有同名实现，绑定时覆盖不到时兜底）
        def _opsi_fmt_num(self, value, decimals=0):
            from module.webui.app_stat_opsi import OpsiStatisticsMixin

            return OpsiStatisticsMixin._opsi_fmt_num(value, decimals)  # type: ignore[attr-defined]
    ctx = _Ctx()
    # 镜像**所有**大写类常量到实例（AP_CHART_VIEWS / AP_LINE_VIEWS /
    # AP_SERIES_COLORS 等是类属性，复用过来的方法通过 self.XXX 访问，
    # 不镜像就会 AttributeError）。
    for _name in dir(mixin):
        if not _name.isupper():
            continue
        try:
            setattr(ctx, _name, getattr(mixin, _name))
        except Exception:  # noqa: BLE001
            pass
    # 镜像所有方法（含 private）。
    # ⚠️ 必须沿 MRO 找**原始描述符**：staticmethod 若只做 getattr(mixin, name)
    # 拿到的是已解包的函数，再用 __get__ 绑定会多传一个 self
    # （实测报 "got multiple values for argument"）。
    def _find_descriptor(cls, name):
        for _base in cls.__mro__:
            if name in _base.__dict__:
                return _base.__dict__[name]
        return None

    for _name in dir(mixin):
        if _name.startswith("__"):
            continue
        if _name in ("_stat_month_pair", "_stat_month_is_current"):
            continue
        _desc = _find_descriptor(mixin, _name)
        if _desc is None:
            continue
        if isinstance(_desc, staticmethod):
            # staticmethod 同时挂到实例和类上：
            # 有些复用方法内部写的是 type(self)._format_ap_metric(...)，
            # 只挂实例会 AttributeError（实测报 type object '_Ctx' has no attribute）。
            setattr(ctx, _name, _desc.__func__)
            try:
                setattr(type(ctx), _name, _desc.__func__)
            except Exception:  # noqa: BLE001
                pass
        elif isinstance(_desc, classmethod):
            # classmethod 也要绑定：__get__(ctx, type(ctx)) 后调用时会传 cls=_Ctx，
            # 方法内部若用 cls.XXX 访问类常量也能正常工作。
            # 之前直接 continue 跳过了，导致 _build_ap_resource_row /
            # _build_ap_legend_item 缺失 → AttributeError。
            try:
                setattr(ctx, _name, _desc.__get__(ctx, type(ctx)))
            except Exception:  # noqa: BLE001
                pass
        elif isinstance(_desc, property):
            continue
        else:
            try:
                setattr(ctx, _name, _desc.__get__(ctx, type(ctx)))
            except Exception:  # noqa: BLE001
                pass
    return ctx


def get_commission_panel_html(
    instance_name: str, period: str = "", year: int = 0, month_no: int = 0
) -> dict:
    """委托面板。

    period: day / week / month（对应老 UI _commission_income_period，
            老 UI 三个选项「每日 / 每周 / 每月」，历史月份下 day/week 禁用）
    year/month_no: 查看年月（0 = 当月，对应老 UI 的 ◀ ▶ 翻页）
    """
    return _cached_stat_html(
        instance_name, f"commission:{period}:{year}:{month_no}",
        lambda: _get_commission_panel_html_impl(instance_name, period, year, month_no),
        f"{period}:{year}:{month_no}",
    )


def _get_commission_panel_html_impl(
    instance_name: str, period: str = "", year: int = 0, month_no: int = 0
) -> dict:
    """委托面板（app_stat_commission.py）。

    返回 running/summary/table/gem/recent 五段 HTML + 可选年月。
    """
    try:
        ctx = _make_stat_ctx(
            instance_name,
            "module.webui.app_stat_commission",
            "CommissionIncomeStatisticsMixin",
        )
        # 周期：老 UI 三个值 day / week / month（默认 month）
        if period in ("day", "week", "month"):
            ctx._commission_income_period = period
        # 查看年月（老 UI _commission_income_year/_month，默认当月）
        if year and month_no:
            ctx._commission_income_year = int(year)
            ctx._commission_income_month = int(month_no)
        else:
            from module.config.time_source import now as _now

            _t = _now()
            ctx._commission_income_year = _t.year
            ctx._commission_income_month = _t.month
        data = ctx._load_commission_income_data()
        if data is None:
            return {"success": False, "error": "暂无委托数据"}
        summary, table, recent = ctx._build_commission_income_html(data)
        # 进行中的委托（含收益预估）—— 老 UI 在 summary 之上渲染，
        # 由 app_stat_running.py:_build_running_commission_html 产出。
        # 我第一版完全漏了这个模块（长卿：「我委托和科研的正在进行中的呢」）。
        running = ""
        try:
            # ⚠️ 必须用 RunningProjectsMixin 自己的适配器：
            # _build_running_commission_html 依赖 _load_running_snapshot，
            # 那个方法在 RunningProjectsMixin 上，不在委托 Mixin 上。
            # 直接传 ctx（委托的）会报 '_Ctx' object has no attribute
            # '_load_running_snapshot' —— 实测 running_html 恒为 0 字节。
            run_ctx = _make_stat_ctx(
                instance_name, "module.webui.app_stat_running", "RunningProjectsMixin"
            )
            running = run_ctx._build_running_commission_html()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[webapi] 渲染进行中委托失败: {e}")
        # 钻石委托区块（老 UI app_stat_commission_gem.py:_build_commission_gem_html）
        gem = ""
        try:
            from module.webui.app_stat_commission_gem import (
                CommissionGemStatisticsMixin,
            )

            gem = CommissionGemStatisticsMixin._build_commission_gem_html(ctx, data)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[webapi] 渲染钻石委托失败: {e}")
        return {
            "success": True,
            "running_html": running,
            "summary_html": summary,
            "table_html": table,
            "gem_html": gem,
            "recent_html": recent,
            "period": getattr(ctx, "_commission_income_period", "month"),
            "year": getattr(ctx, "_commission_income_year", 0),
            "month": getattr(ctx, "_commission_income_month", 0),
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染委托统计失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def get_research_panel_html(
    instance_name: str, period: str = "", scope: str = "", series: int = 0
) -> dict:
    """科研面板。period: month/days30/days90/all；scope: series/consumable。"""
    return _cached_stat_html(
        instance_name, f"research:{period}:{scope}:{series}",
        lambda: _get_research_panel_html_impl(instance_name, period, scope, series),
        period or scope,
    )


def _get_research_panel_html_impl(
    instance_name: str, period: str = "", scope: str = "", series: int = 0
) -> dict:
    """科研面板（app_stat_research.py）。"""
    try:
        ctx = _make_stat_ctx(
            instance_name,
            "module.webui.app_stat_research",
            "ResearchStatisticsMixin",
        )
        # 周期/口径覆盖：老 UI 用 _research_stats_period 等实例属性驱动渲染，
        # 这里在调用构建函数前覆盖到适配器上（非法值不覆盖，由 mixin 兜底）。
        if period in ("month", "days30", "days90", "all"):
            ctx._research_stats_period = period
        if scope in ("series", "consumable"):
            ctx._research_stats_scope = scope
        if series:
            ctx._research_stats_series = int(series)
        # 进行中的科研队列 —— 老 UI 贴在标题下方（_render_research_stats 里
        # put_html(running_html, scope="research_stats")），比历史掉落更即时。
        running = ""
        try:
            run_ctx = _make_stat_ctx(
                instance_name, "module.webui.app_stat_running", "RunningProjectsMixin"
            )
            running = run_ctx._build_running_research_html()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[webapi] 渲染进行中科研失败: {e}")
        data = ctx._load_research_stats_data()
        # 指标卡（总掉落/今日/本月/记录次数）—— 老 UI app_stat_research.py:178-193
        # 用 build_metric_grid 生成 .metric-grid/.metric，**在控件之前**。
        # 之前只返 table_html，整块指标卡丢了，科研页只剩按钮 + 表格。
        metric = ""
        if data is not None:
            try:
                from module.webui.app_helpers import build_metric_grid
                from module.webui.lang import t as _t

                metric = build_metric_grid(
                    [
                        _t("Gui.Stat.ResearchMetricTotal"),
                        _t("Gui.Stat.ResearchMetricToday"),
                        _t("Gui.Stat.ResearchMetricMonth"),
                        _t("Gui.Stat.ResearchMetricRecords"),
                    ],
                    [
                        f"{int(data.get('total', 0)):,}",
                        f"{int(data.get('today', 0)):,}",
                        f"{int(data.get('month', 0)):,}",
                        f"{int(data.get('records', 0)):,}",
                    ],
                )
            except Exception as e:  # noqa: BLE001 —— 指标卡失败不能拖垮整页
                logger.warning(f"[webapi] 渲染科研指标卡失败: {e}")
        table = ctx._build_research_table_html(data)
        # 可选期数（老 UI _series_options(data)，仅在「期数」口径下出现）
        series_opts = []
        try:
            from module.webui.app_stat_research import _series_options

            series_opts = [str(x) for x in (_series_options(data) or [])]
        except Exception:  # noqa: BLE001
            pass
        return {
            "success": True,
            "running_html": running,
            "metric_html": metric,
            "table_html": table,
            "scope": data.get("scope", "series") if isinstance(data, dict) else "series",
            "series": data.get("series") if isinstance(data, dict) else None,
            "series_options": series_opts,
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染科研统计失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def get_delta_panel_html(instance_name: str, period: str = "") -> dict:
    """带缓存入口（缓存键见 _stat_cache_key）。period: day/week/month/total。"""
    return _cached_stat_html(
        instance_name,
        f"delta:{period}",
        lambda: _get_delta_panel_html_impl(instance_name, period),
        period,
    )


def _get_delta_panel_html_impl(instance_name: str, period: str = "") -> dict:
    """资源面板（app_stat_delta.py:_render_resource_delta 的等价物）。

    老 UI 这一页有**四块**，我原来只做了第三块（汇总表）和汇总条，
    时间轴图和消耗排行榜根本不存在 —— 长卿：「资源这块完全是半成品做都没做」。

        ① _render_delta_toolbar   粒度切换 日/周/月/总   ← 原来没有
        ② _render_delta_timeline  任务时间轴（echarts）  ← 原来没有（含 stats/legend）
        ③ _build_summary_html     增减汇总表
        ④ _build_ranking_html     消耗排行榜            ← 原来没有

    时间轴图不是普通 HTML，是「HTML 模板 + 一段填好数据的 JS」
    （webapp/resource_delta_timeline.{html,js}，与大世界体力图同一套路），
    所以这里返回 timeline_html / timeline_js 两个字段，由前端挂载后执行脚本。
    """
    try:
        from module.webui.app_stat_delta import (
            PERIOD_DAYS,
            PERIOD_KEYS,
            _TIMELINE_TOP_N,
        )
        from module.webui.app_helpers import read_webapp_template
        from module.webui.lang import t as _t

        ctx = _make_stat_ctx(
            instance_name,
            "module.webui.app_stat_delta",
            "ResourceDeltaStatisticsMixin",
        )
        # 粒度覆盖：老 UI 用 _delta_period 实例属性驱动 _load_delta_data
        if period in PERIOD_KEYS:
            ctx._delta_period = period
        current_period = str(getattr(ctx, "_delta_period", "day"))
        timeline, summary, ranking = ctx._load_delta_data()
        if not timeline and not summary and not ranking:
            return {"success": False, "error": "暂无资源变动数据"}

        # ① 粒度切换（老 UI _render_delta_toolbar：日/周/月/总，primary vs off）
        period_labels = {
            "day": _t("Gui.Stat.DeltaPeriodDay"),
            "week": _t("Gui.Stat.DeltaPeriodWeek"),
            "month": _t("Gui.Stat.DeltaPeriodMonth"),
            "total": _t("Gui.Stat.DeltaPeriodTotal"),
        }

        # ② 时间轴：HTML 模板 + JS（数据全部内联进 JS）
        timeline_html = ""
        timeline_js = ""
        if timeline:
            try:
                chart_id = "rdt_webapi"
                timeline_html = read_webapp_template(
                    "resource_delta_timeline.html"
                ).format(
                    chart_id=chart_id,
                    title=_t("Gui.Stat.DeltaChartTitle"),
                    stats_html=ctx._build_delta_stats_html(summary) if summary else "",
                    legend_html=ctx._build_delta_legend_html(),
                )
                tasks = [ctx._build_timeline_task(item) for item in timeline]
                # t() 会无条件 .format()，含 {n} 的翻译必须把 n 传回字面 "{n}"
                timeline_js = (
                    read_webapp_template("resource_delta_timeline.js")
                    .replace("__TASKS__", json.dumps(tasks, ensure_ascii=False))
                    .replace("__CHART_ID__", chart_id)
                    .replace("__TXT_GAIN__", _t("Gui.Stat.DeltaIncrease"))
                    .replace("__TXT_LOSS__", _t("Gui.Stat.DeltaDecrease"))
                    .replace("__TXT_TIMES__", _t("Gui.Stat.DeltaTaskTimes", n="{n}"))
                    .replace("__TXT_MORE__", _t("Gui.Stat.DeltaMore", n="{n}"))
                )
            except Exception as e:  # noqa: BLE001 —— 图失败不能拖垮整页
                logger.warning(f"[webapi] 渲染资源时间轴失败: {e}")

        stats_html = ctx._build_delta_stats_html(summary) if summary else ""
        legend_html = (
            ctx._build_delta_legend_html()
            if hasattr(ctx, "_build_delta_legend_html")
            else ""
        )
        # ③ 汇总表 + ④ 排行榜：老 UI 用 resource_delta_rank.html 一次包住两块
        summary_html = ""
        try:
            summary_html = read_webapp_template("resource_delta_rank.html").format(
                summary_title=_t("Gui.Stat.DeltaSummaryTitle"),
                summary_html=ctx._build_summary_html(summary) if summary else "",
                ranking_title=_t("Gui.Stat.ConsumptionRankingTitle"),
                ranking_html=ctx._build_ranking_html(ranking),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[webapi] 渲染资源汇总/排行失败: {e}")
            summary_html = ctx._build_summary_html(summary) if summary else ""

        return {
            "success": True,
            "period": current_period,
            "period_options": [
                {"value": k, "label": period_labels[k]} for k in PERIOD_KEYS
            ],
            "timeline_html": timeline_html,
            "timeline_js": timeline_js,
            "stats_html": stats_html,
            "legend_html": legend_html,
            "summary_html": summary_html,
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染资源统计失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def get_ship_exp_panel_html(instance_name: str) -> dict:
    """科技列表（舰船经验）面板 —— 对应 app_stat_ship.py:_render_ship_exp。

    老 UI 这个函数用 use_scope + put_html 输出，但真正的内容全部由
    build_fold_block / build_chip_row / build_simple_table 三个**返回字符串**
    的辅助函数组装。所以这里复刻它的数据准备部分，直接拼出 HTML，
    不经过 PyWebIO 的输出通道。

    Returns: {success, html, error}
    """
    return _cached_stat_html(
        instance_name, "ship_exp", lambda: _get_ship_exp_panel_html_impl(instance_name)
    )


def _get_ship_exp_panel_html_impl(instance_name: str) -> dict:
    try:
        # t() 在 module.webui.lang（老 UI 的翻译入口），不在 module.config.utils
        from module.webui.lang import t
        from module.statistics.opsi_month import get_opsi_stats
        from module.statistics.ship_exp_stats import get_ship_exp_stats
        from module.webui.app_helpers import (
            build_chip_row,
            build_fold_block,
            build_muted_notice,
            build_simple_table,
        )

        name = instance_name or None
        if not name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            name = all_instances[0] if all_instances else None

        stats = get_ship_exp_stats(instance_name=name)
        if not stats.data or not stats.data.get("ships"):
            return {
                "success": True,
                "html": build_muted_notice(t("Gui.Stat.NoShipExpData")),
                "error": "",
            }

        ships_data = stats.data.get("ships", [])
        target_level = stats.data.get("target_level", 125)
        last_check_time = stats.data.get("last_check_time", "-")
        current_battles = (
            get_opsi_stats(instance_name=name).summary().get("total_battles", 0)
        )
        progress_list = [
            stats.calculate_progress(ship, target_level, current_battles)
            for ship in ships_data
        ]
        try:
            today = stats.get_today_stats() or {}
        except Exception:  # noqa: BLE001
            today = {}
        today_battles = int(today.get("battle_count", 0) or 0)
        today_exp = int(today.get("total_exp_gained", 0) or 0)
        today_run = int((today.get("total_run_time", 0) or 0) // 60)

        def fmt(v):
            if isinstance(v, bool):
                return str(v)
            try:
                return f"{int(v):,}"
            except (TypeError, ValueError):
                return str(v)

        second_unit = t("Gui.Stat.SecondUnit")
        avg_battle = stats.get_average_battle_time()
        avg_round = stats.get_average_round_time()
        exp_per_hour = stats.get_exp_per_hour()
        chips = [
            (t("Gui.Stat.AvgBattleTimeHeader"),
             f"{avg_battle:.1f}{second_unit}" if avg_battle > 0 else "-"),
            (t("Gui.Stat.ShipExpAvgRound"),
             f"{avg_round:.1f}{second_unit}" if avg_round > 0 else "-"),
            (t("Gui.Stat.ExpEfficiencyHeader"),
             f"{fmt(exp_per_hour)}/{t('Gui.Stat.HourUnit')}" if exp_per_hour > 0 else "-"),
            (t("Gui.Stat.ShipExpToday"),
             t("Gui.Stat.ShipExpTodayValue", battles=fmt(today_battles),
               exp=fmt(today_exp), run=fmt(today_run))),
            (t("Gui.Stat.ShipExpLastCheck"), str(last_check_time)),
        ]
        exp_labels = [
            t("Gui.Stat.ShipSlot"),
            t("Gui.Stat.Level"),
            t("Gui.Stat.CurrentExpThisLevel"),
            t("Gui.Stat.TotalExp"),
            t("Gui.Stat.TargetExpRequired"),
            t("Gui.Stat.ExpToTarget"),
            t("Gui.Stat.SortiesNeeded"),
            t("Gui.Stat.EstimatedTime"),
        ]

        def fmt_duration(seconds):
            """秒数 → 「X小时Y分钟」；单位走 i18n。

            老 UI app_stat_ship.py:152-167 _format_duration 同构：
            用 Gui.Stat.HourUnit / MinuteUnit（**不是** HourShort/MinuteShort ——
            这两个键在 i18n 里不存在，t() 会原样返回键名，页面上就出现
            「≈ 31Gui.Stat.HourShort20Gui.Stat.MinuteShort」这种乱码）。
            hours=0 时只显示分钟，与老 UI 一致。
            """
            if not seconds or seconds <= 0:
                return "-"
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            if hours > 0:
                return f"{hours}{t('Gui.Stat.HourUnit')}{minutes}{t('Gui.Stat.MinuteUnit')}"
            return f"{minutes}{t('Gui.Stat.MinuteUnit')}"

        exp_rows = []
        for progress in progress_list:
            done = progress["exp_needed"] <= 0
            exp_rows.append([
                t("Gui.Stat.ShipExpSlotValue", slot=progress["position"]),
                fmt(progress["level"]),
                "-" if done else fmt(progress["current_exp"]),
                fmt(progress["total_exp"]),
                "-" if done else fmt(progress["target_exp"]),
                fmt(progress["exp_needed"]),
                t("Gui.Stat.ShipExpDone") if done else f"≈ {fmt(progress['battles_needed'])}",
                "-" if done else f"≈ {fmt_duration(progress['time_seconds'])}",
            ])

        digest = " · ".join([
            t("Gui.Stat.FoldShipDigest", n=len(exp_rows)),
            t("Gui.Stat.ShipExpTargetShort", level=target_level),
            t("Gui.Stat.ShipExpTodayShort", n=fmt(today_battles)),
        ])
        html = build_fold_block(
            t("Gui.Stat.ShipExpProgressTitle"),
            build_chip_row(chips)
            + build_simple_table(exp_labels, exp_rows, numeric_from=1),
            digest=digest,
        )
        return {"success": True, "html": html, "error": ""}
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染科技列表失败: {e}")
        return {"success": False, "html": "", "error": f"{type(e).__name__}: {e}"}


def get_fleet_tech_panel_html(
    instance_name: str,
    filters: str = "",
    query: str = "",
    scope: str = "all",
    stage: str = "all",
    sort_key: str = "code",
    sort_dir: int = 1,
    page: int = 0,
    pending: bool = False,
    edit: bool = False,
    bonuses: bool = False,
    extra: bool = False,
) -> dict:
    """科技列表面板（舰队科技）—— 对应 app_fleet_technology.py:_ft_render_tech_tab。

    长卿：「说了多少遍了这个得改啊 这TM是什么？老UI抄的是这个你就这么敷衍我？」

    老 UI 的科技列表有**七块**，我原来只挂了 summary/bonus/table 三段静态 HTML，
    筛选栏 / 扫描行 / 搜索行 / 排序 / 分页 / 编辑模式全都没有 —— 所以新 UI 只剩
    一堆纯文字，连「哪些按钮、怎么筛」都没有。

    老 UI 真实结构（实测 22480 legacy，见 oldui_fleettech.html，18 个 scope）：
        ft_summary         汇总（最高科技点/四大阵营/Lv.120 进度/资料版本）
        ft_filters         六组筛选 button group
          ft_filter_type / rarity / nation / bonusType / attr / status
          ft_filter_functions  记录编辑模式 / 总属性加成 / 显示额外筛选 / 只看满星未确认
        ft_stage           加成阶段下拉（仅「显示额外筛选」开启时出现）
        ft_toolbar         扫描范围 min–max + 扫描/同步按钮
        ft_search          搜索框 + 科技条目范围下拉 + 计数
        ft_bonuses         总属性加成视图（仅开关打开时）
        ft_table           表格 + 分页 + 页脚

    这些渲染函数全是**返回字符串的纯函数**（fleet_tech_view.py），所以这里
    直接复用，把每一块拼成 HTML 返回，前端按顺序渲染。
    """
    return _cached_stat_html(
        instance_name,
        f"fleet_tech:{filters}:{query}:{scope}:{stage}:{sort_key}:{sort_dir}:{page}:{int(pending)}:{int(edit)}:{int(bonuses)}:{int(extra)}",
        lambda: _get_fleet_tech_panel_html_impl(
            instance_name, filters, query, scope, stage,
            sort_key, sort_dir, page, pending, edit, bonuses, extra,
        ),
        filters or query or scope,
    )


def _get_fleet_tech_panel_html_impl(
    instance_name: str,
    filters: str = "",
    query: str = "",
    scope: str = "all",
    stage: str = "all",
    sort_key: str = "code",
    sort_dir: int = 1,
    page: int = 0,
    pending: bool = False,
    edit: bool = False,
    bonuses: bool = False,
    extra: bool = False,
) -> dict:
    try:
        from module.runtime import fleet_tech as tech
        from module.webui import fleet_tech_view as view
        from module.webui.app_dependencies import deep_get, load_config
        from module.webui.lang import t as _t

        name = instance_name or None
        if not name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            name = all_instances[0] if all_instances else None

        _MIN_LEVEL_PATH = "FleetTechScan.FleetTech.MinLevel"
        _MAX_LEVEL_PATH = "FleetTechScan.FleetTech.MaxLevel"
        cfg = load_config(name).read_file(name)
        record = tech.read_record(cfg)
        report = dict(
            instance=name,
            min_level=deep_get(cfg, _MIN_LEVEL_PATH, 95) or 95,
            max_level=deep_get(cfg, _MAX_LEVEL_PATH, 125) or 125,
            **tech.report(record),
        )
        catalog = tech.public_catalog()
        rows = view.combine_rows(catalog, report)

        # 筛选状态解析："type|3,type|18" → {'type': ['3','18']}
        filter_map: dict = {}
        for token in (filters or "").split(","):
            token = token.strip()
            if not token or "|" not in token:
                continue
            group, _, value = token.partition("|")
            filter_map.setdefault(group, []).append(value)

        # ① 汇总
        summary = view.summary_html(catalog, report, rows)

        # ② 六组筛选 + 功能开关（老 UI _ft_render_filter_group / _ft_render_functions）
        filter_groups = []
        for key in view.FILTER_GROUPS:
            options = view.filter_options(key, rows)
            selected = set(filter_map.get(key, []))
            buttons = []
            for value, label in options:
                # 第一项是「全部」（value 为空）；未选任何值时它也高亮
                active = (value in selected) or (not value and not selected)
                buttons.append({
                    "value": f"{key}|{value}",
                    "label": label,
                    "active": active,
                })
            filter_groups.append({
                "key": key,
                "title": _t(f"Gui.FleetTechnology.{_FILTER_TITLE_KEYS[key]}"),
                "buttons": buttons,
            })
        # 功能开关（与筛选同级）
        toggle_defs = [
            ("edit", "Edit", edit),
            ("bonuses", "Bonuses", bonuses),
            ("extra", "Extra", extra),
            ("pending", "PendingOnly", pending),
        ]
        function_buttons = [
            {"value": key, "label": _t(f"Gui.FleetTechnology.{label_key}"), "active": bool(state)}
            for key, label_key, state in toggle_defs
        ]

        # ③ 表格 + 分页（老 UI _ft_render_table）
        #
        # 默认排序必须是 ('code', 1)：老 UI app_fleet_technology.py:188-189 就是
        # 这个值。用 ('total','desc') 会让 _CompareKey 在混类型值上抛
        # "not supported between str and int"（实测），因为 total 里既有数字
        # 也有 '—' 这类占位串。
        direction = 1
        if isinstance(sort_dir, str):
            direction = -1 if sort_dir.lower() in ("desc", "-1", "down") else 1
        else:
            direction = int(sort_dir)
        arranged = view.arrange_rows(
            rows, filter_map, query, scope, stage, sort_key, direction, pending
        )
        shown, cur_page, pages = view.page_slice(arranged, page)
        table = view.table_html(shown, sort_key, direction, edit)
        # 编辑模式：批量工具栏（老 UI app_fleet_tech_batch.py:86-96
        # _ft_batch_toolbar —— 表格上方显示"本页已选 0 / 批量修改已选舰船 /
        # 修改全部筛选结果"）。之前 edit=1 只回表格，工具栏没输出，
        # 勾选框孤零零的没有批量入口。
        if edit:
            from html import escape as _hesc
            from module.webui.lang import t as _t

            batch = (
                '<div class="ft-batch-toolbar">'
                f'<span>{_hesc(_t("Gui.FleetTechnology.Selected"))} '
                f'<b data-ft-selected-count>0</b></span>'
                f'<button type="button" class="ft-edit-btn" data-ft-batch="selected" disabled>'
                f'{_hesc(_t("Gui.FleetTechnology.BatchEdit"))}</button>'
                f'<button type="button" class="ft-edit-btn" data-ft-batch="all" '
                f'{"disabled" if not arranged else ""}>'
                f'{_hesc(_t("Gui.FleetTechnology.BatchAll", count=len(arranged)))}</button>'
                f'<small>{_hesc(_t("Gui.FleetTechnology.SelectionHint"))}</small></div>'
            )
            table = batch + table

        # ④ 加成阶段（仅「显示额外筛选」开启时）
        stage_html = ""
        if extra:
            opts = [
                ("all", _t("Gui.FleetTechnology.All")),
                ("get", _t("Gui.FleetTechnology.Unlock")),
                ("level", "Lv.120"),
            ]
            inner = "".join(
                f'<option value="{v}"{" selected" if v == stage else ""}>{lbl}</option>'
                for v, lbl in opts
            )
            stage_html = (
                f'<select name="ft_stage" class="form-control" style="width:auto;">{inner}</select>'
            )

        # ⑤ 总属性加成视图（仅开关打开时）
        bonuses_html = ""
        if bonuses:
            bonuses_html = (
                f'<h2>{_t("Gui.FleetTechnology.Bonuses")}</h2>'
                f'<div class="ft-bonus-grid">{view.bonus_grid_html(report)}</div>'
            )

        return {
            "success": True,
            "summary_html": summary,
            "filter_groups": filter_groups,
            "function_buttons": function_buttons,
            "stage_html": stage_html,
            "table_html": table,
            "bonus_html": bonuses_html,
            "count": len(arranged),
            "total": len(rows),
            "page": cur_page,
            "pages": pages,
            # 扫描范围/搜索/范围选择用到的当前值，供前端回填
            "min_level": report["min_level"],
            "max_level": report["max_level"],
            "query": query,
            "scope": scope,
            "stage": stage,
            "sort_key": sort_key,
            "sort_dir": direction,
            "pending": pending,
            "edit": edit,
            "bonuses": bonuses,
            "extra": extra,
            "revision": tech.revision(record),
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染科技列表失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


# 筛选组标题的 i18n 键（与老 UI FILTER_TITLES 一致）
_FILTER_TITLE_KEYS = {
    "type": "Category",
    "rarity": "Rarity",
    "nation": "Nation",
    "bonusType": "BonusType",
    "attr": "Attr",
    "status": "Status",
}


def get_mind_panel_html(instance_name: str, page: int = 0, edit: bool = False) -> dict:
    """心智计算器面板 —— 对应 app_mind_calculator_tab.py:_ft_render_mind_tab。

    内容：说明条 + 四张统计卡 + 稀有度汇总表 + 船舶清单表（分页）。
    数据来自 MindCalculatorScan.MindCalculator.Result（配置 stored），
    计算用 module.runtime.mind_calculator.calculate 实时进行。
    page/edit 驱动清单表分页与批量编辑模式；编辑保存后前端整块刷新。

    Returns: {success, intro_html, stats_html, summary_html, list_html, total, error}
    """
    return _cached_stat_html(
        instance_name, "mind:" + str(page) + ":" + str(edit),
        lambda: _get_mind_panel_html_impl(instance_name, page, edit)
    )


def _get_mind_panel_html_impl(instance_name: str, page: int = 0, edit: bool = False) -> dict:
    try:
        from module.runtime import mind_calculator as mind_calc
        from module.webui import mind_calculator_view as mind_view
        from module.webui.app_dependencies import deep_get, load_config

        name = instance_name or None
        if not name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            name = all_instances[0] if all_instances else None

        # RESULT_PATH 是 app_mind_calculator_tab.py 的模块常量
        _RESULT_PATH = "MindCalculatorScan.MindCalculator.Result"
        data = load_config(name).read_file(name)
        stored = deep_get(data, _RESULT_PATH, {})
        stored = stored if isinstance(stored, dict) else {}
        ships = stored.get("ships", [])
        if not isinstance(ships, list):
            ships = []
        updated_at = stored.get("updated_at")

        # calculate_rows(ships) 自己会调 calculate 并返回 (result, rows) ——
        # 只收一个参数（我一开始按两参调用，报 takes 1 positional argument but 2 were given）。
        result, rows = mind_view.calculate_rows(ships)
        if result is None:
            result = {}
        shown, cur_page, pages = mind_view.page_slice(rows, page)

        return {
            "success": True,
            "intro_html": mind_view.intro_html(result, updated_at),
            "stats_html": mind_view.stats_html(result),
            "summary_html": mind_view.summary_table_html(result),
            "list_html": mind_view.ships_table_html(shown, result, edit),
            "total": len(rows),
            "pages": pages,
            "page": cur_page,
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染心智计算器失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def get_stat_available_months(instance_name: str) -> dict:
    """统计页可选月份（新→旧）。

    与老 UI app_statistics_page.py:_stat_available_months 同口径：
    cl1_data.db ∪ azurstats_local.db ∪ 当前月，任一库有记录即可选。
    """
    try:
        from module.config.time_source import now as _now
        from module.statistics.azurstats import AzurStats
        from module.statistics.opsi_month import get_available_months

        months = set()
        for label, loader in (
            ("cl1_data.db", lambda: get_available_months(instance_name)),
            ("azurstats_local.db", AzurStats.get_opsi_drop_available_months),
        ):
            try:
                months |= {(int(y), int(m)) for y, m in loader()}
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[webapi] 读取 {label} 可用月份失败: {e}")
        now = _now()
        months.add((now.year, now.month))
        ordered = sorted(months, reverse=True)
        return {
            "success": True,
            "current": f"{now.year:04d}-{now.month:02d}",
            "months": [f"{y:04d}-{m:02d}" for y, m in ordered],
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 读取可用月份失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}", "months": []}


def get_ap_chart_html(instance_name: str, view: str = "", month: str = "") -> dict:
    """体力变化图 —— 完全复用老 UI 的 ap_chart.js（41KB Canvas 2D 手绘图表）。

    为什么不在 React 里重画（长卿 2026-11-16）：
        「你那个 K 线图是屎啊，老 UI 做的那么漂亮，你新 UI 做的跟屎一样，
          只有数字没有什么渲染」

    老 UI 的图表不是 echarts，而是 webapp/ap_chart.js —— 936 行 Canvas 2D
    手绘实现，支持：K 线/折线双模式、体力/紫币/黄币/资产/海里数五条序列、
    可点击图例、自定义 tooltip、hover 十字线、detail 缩放。

    我第一版用 echarts 拼了个 30 行的折线，粗糙到没法看。正确做法是
    **直接复用这两个模板**（ap_chart_panel.html + ap_chart.js）：
    后端调老 UI 的 _render_ap_chart_content 同一套数据装配逻辑，
    产出 HTML + <script>，前端 innerHTML 挂载后浏览器自动执行图表脚本。

    Returns: {success, html, error}
    """
    return _cached_stat_html(
        instance_name, f"ap_chart:{view}:{month}",
        lambda: _get_ap_chart_html_impl(instance_name, view, month),
        f"{view}:{month}",
    )


def _get_ap_chart_html_impl(instance_name: str, view: str = "", month: str = "") -> dict:
    """体力变化图：完整复用老 UI 的 ap_chart_panel.html + ap_chart.js。

    模板占位符与替换逻辑**逐项照抄** app_stat_action_point.py:_render_ap_chart_content
    （那里是 PyWebIO 的 put_html/run_js 输出，这边只是把它们拼成一个字符串）。
    """
    try:
        import json as _json

        from module.webui.app_stat_action_point import ActionPointStatisticsMixin
        from module.webui.app_helpers import read_webapp_template
        from module.webui.lang import t as _t

        ctx = _make_stat_ctx(
            instance_name,
            "module.webui.app_stat_action_point",
            "ActionPointStatisticsMixin",
        )
        if month:
            try:
                _y, _m = month.split("-")
                ctx._stat_year, ctx._stat_month = int(_y), int(_m)
            except (ValueError, AttributeError):
                pass
        if view:
            ctx._ap_chart_view = view
        elif not hasattr(ctx, "_ap_chart_view"):
            ctx._ap_chart_view = "line"

        raw_points, timeline, coins_timeline, asset_timeline = (
            ctx._load_ap_chart_dataset(reuse=False)
        )
        if not raw_points or not timeline:
            return {"success": True, "html": "", "error": "暂无体力数据"}

        chart_data = ctx._build_ap_chart_series(raw_points)
        if chart_data is None:
            return {"success": True, "html": "", "error": "数据不足以聚合 K 线"}

        auxiliary = ctx._build_ap_chart_auxiliary_data(
            timeline=timeline,
            coins_timeline=coins_timeline,
            asset_timeline=asset_timeline,
            chart_points=chart_data["chart_points"],
            current_view=chart_data["current_view"],
        )

        chart_id = "ap_cv_nc_" + str(instance_name).replace(".", "_")
        current_view = chart_data["current_view"]
        line_views = getattr(ActionPointStatisticsMixin, "AP_LINE_VIEWS", ())
        detail_controls_display = (
            "display:flex;" if current_view in line_views else "display:none;"
        )

        # 资源明细折叠块（黄币/紫币/资产图例）—— 仅在分时视图出现
        aux_fold = ""
        if current_view in line_views:
            _legend_parts = [
                ctx._build_ap_legend_item(
                    0,
                    ActionPointStatisticsMixin.AP_SERIES_COLORS["ap"],
                    _t("Gui.Stat.SeriesActionPoint"),
                ),
                auxiliary.get("coins_legend_html", ""),
            ]
            aux_fold = (
                '<details class="ap-aux-fold">'
                "<summary>"
                '<span class="ap-aux-title">' + str(_t("Gui.Stat.AuxFoldTitle")) + "</span>"
                '<span class="ap-aux-digest">' + str(auxiliary.get("aux_digest", "")) + "</span>"
                "</summary>"
                '<div class="ap-aux-body">'
                + str(auxiliary.get("coins_stats_html", ""))
                + '<div id="' + chart_id + '_legend" style="display:flex; flex-wrap:wrap;'
                ' gap:12px; margin-top:10px; font-size:12px; color:#888;">'
                + "".join(_legend_parts)
                + "</div></div></details>"
            )

        html_tpl = read_webapp_template("ap_chart_panel.html")
        html = html_tpl.format(
            chart_id=chart_id,
            ap_cur=chart_data["ap_cur"],
            change_color=chart_data["change_color"],
            change_sign=chart_data["change_sign"],
            ap_change=chart_data["ap_change"],
            ap_max=chart_data["ap_max"],
            ap_min=chart_data["ap_min"],
            ap_avg=chart_data["ap_avg"],
            data_points_text=chart_data["data_points_text"],
            detail_controls_display=detail_controls_display,
            aux_fold_html=aux_fold,
            kpi_current_ap=_t("Gui.Stat.KpiCurrentAp"),
            kpi_change=_t("Gui.Stat.KpiChange"),
            kpi_range_max=_t("Gui.Stat.KpiRangeMax"),
            kpi_range_min=_t("Gui.Stat.KpiRangeMin"),
            kpi_average=_t("Gui.Stat.KpiAverage"),
        )

        js = read_webapp_template("ap_chart.js")
        replacements = {
            "__CHART_TYPE__": "line" if chart_data["is_detail_mode"] else current_view,
            "__LABELS__": _json.dumps(chart_data["labels"], ensure_ascii=False),
            "__OPENS__": _json.dumps(chart_data["opens"]),
            "__HIGHS__": _json.dumps(chart_data["highs"]),
            "__LOWS__": _json.dumps(chart_data["lows"]),
            "__CLOSES__": _json.dumps(chart_data["closes"]),
            "__COUNTS__": _json.dumps(chart_data["counts"]),
            "__AP__": _json.dumps(chart_data["ap_list"]),
            "__AP_TS__": _json.dumps(chart_data["ap_ts"]),
            "__AVG__": str(chart_data["ap_avg"]),
            "__CHART_ID__": chart_id,
            "__IS_DETAIL_MODE__": "true" if chart_data["is_detail_mode"] else "false",
            "__SOURCES__": _json.dumps(
                chart_data["detail_sources"] if chart_data["is_detail_mode"] else []
            ),
            "__YELLOW_COINS__": _json.dumps(auxiliary["yellow_coins_list"]),
            "__PURPLE_COINS__": _json.dumps(auxiliary["purple_coins_list"]),
            "__COINS_SOURCES__": _json.dumps(auxiliary["coins_sources_list"]),
            "__ASSET__": _json.dumps(auxiliary["asset_list"]),
            "__ASSET_TS__": _json.dumps(auxiliary["asset_ts_list"]),
            "__DISTANCE__": _json.dumps(auxiliary["distance_list"]),
            "__SHOW_COINS__": "true" if auxiliary["show_coins"] else "false",
        }
        for _k, _v in replacements.items():
            js = js.replace(_k, _v)

        return {
            "success": True,
            "html": html,
            "js": js,
            "chart_id": chart_id,
            "view": current_view,
            "view_title": chart_data.get("view_title", ""),
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染体力图失败: {e}")
        return {"success": False, "html": "", "js": "", "error": f"{type(e).__name__}: {e}"}


def _t_gui(key: str, fallback: str) -> str:
    """取 i18n 文案（失败返回兜底）。"""
    try:
        from module.webui.lang import t

        return t(key)
    except Exception:  # noqa: BLE001
        return fallback
