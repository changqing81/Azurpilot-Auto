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


# i18n 词条进程级缓存：zh-CN.json 约 3.5MB / 3 万条，
# 每次 get_overview 都读盘 + JSON 解析会吃掉 200ms+（实测轮询下更糟）。
# 按 (mtime_ns, size) 失效，改文案后自动重载。
_I18N_CACHE: dict = {}


def _load_i18n(scope: str) -> dict:
    """读取 config/i18n/zh-CN.json 并缓存（键 = mtime_ns + size）。"""
    import pathlib

    path = os.path.join(
        os.path.dirname(__file__), "..", "config", "i18n", "zh-CN.json"
    )
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
        cached = _I18N_CACHE.get(scope)
        if cached and cached[0] == key:
            return cached[1]
        data = read_file(path)
        _I18N_CACHE[scope] = (key, data)
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] i18n 读取失败: {e}")
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

    状态码沿用老 UI 约定：1=running / 2=inactive / 3=warning / 4=updating。
    """
    from module.config.utils import alas_instance

    items = []
    try:
        for inst in alas_instance():
            items.append({
                "name": inst,
                # 运行状态由 /newapi/instance/{name}/status 提供（P2 接 ProcessManager）；先 inactive 兜底
                "state": 2,
            })
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


def get_header() -> dict:
    """页眉数据：logo 图标 URL + 排头文案（Gui.Status.Prefix，i18n zh-CN 同源）。"""
    try:
        path = os.path.join(os.path.dirname(__file__), "..", "config", "i18n", "zh-CN.json")
        data = read_file(path)
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
        i18n_path = os.path.join(os.path.dirname(__file__), "..", "config", "i18n", "zh-CN.json")
        menu = read_file(menu_path)
        i18n = read_file(i18n_path)
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
    + zh-CN.json（Group.Arg.name/help/option 标签翻译）。display=hide 的项不输出。
    """
    import os as _os

    from module.config.config import AzurLaneConfig

    arg_dir = _os.path.join(_os.path.dirname(__file__), "..", "config", "argument")
    try:
        args = read_file(_os.path.join(arg_dir, "args.json"))
        i18n = read_file(_os.path.join(_os.path.dirname(__file__), "..", "config", "i18n", "zh-CN.json"))
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
    from module.config.utils import parse_value
    from module.config.deep import deep_get, deep_set

    if os.environ.get("DEMO") == "1":
        return {"valid": [], "invalid": list(modified.keys()), "error": "demo mode"}

    try:
        updater = ConfigUpdater()
        config = updater.read_file(name)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 配置读取失败({name}): {e}")
        return {"valid": [], "invalid": list(modified.keys()), "error": str(e)}

    valid, invalid = [], []
    for k, v in modified.items():
        arg_def = deep_get(updater.args, k, {}) or {}
        valuetype = arg_def.get("valuetype") if isinstance(arg_def, dict) else None
        widget_type = arg_def.get("type") if isinstance(arg_def, dict) else None
        options = arg_def.get("option") if isinstance(arg_def, dict) else None
        v = parse_value(v, {"valuetype": valuetype, "type": widget_type, "option": options})
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
    """重启 Alas 实例（老 UI 的「重启Alas」按钮）。"""
    from module.webui.process_manager import ProcessManager

    try:
        if not instance:
            return {"success": False, "error": "没有可用实例"}
        mgr = ProcessManager.get_manager(instance)
        mgr.stop()
        mgr.start()
        logger.info(f"[webapi] 已重启实例 {instance}")
        return {"success": True, "instance": instance}
    except Exception as e:  # noqa: BLE001
        logger.error(f"[webapi] 重启实例失败: {e}")
        return {"success": False, "error": str(e)}


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
    """图源列表（wallpapers/wallpaper_sources.json）。"""
    import json as _json

    path = _wallpapers_dir() / _SOURCES_FILE_NAME
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        return {"success": True, "sources": data if isinstance(data, list) else []}
    except FileNotFoundError:
        return {"success": True, "sources": []}
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
    """取老 UI 的统计渲染方法（未绑定），供适配器调用。"""
    from module.webui.app_stat_opsi import OpsiStatisticsMixin

    return getattr(OpsiStatisticsMixin, name)


def get_opsi_panels_html(instance_name: str) -> dict:
    """返回「大世界」页的完整 HTML（净赚卡 + 12 指标卡 + 大世界收获）。

    直接调老 UI 的 _render_opsi_stats 系列构建函数，把它们的输出
    收集成字符串返回。这样新 UI 显示的内容与老 UI 完全一致。

    Returns: {success, html, error}
    """
    import io
    from contextlib import redirect_stdout

    try:
        ctx = _StatRenderCtx(instance_name)
        # 绑定老 UI 的方法到适配器。
        # 注意 staticmethod 不能再 __get__ 绑定，否则会多传一个 self
        # （实测报 "got multiple values for argument 'tone'"）。
        for _m in (
            "_build_opsi_month_cards",
            "_build_cl1_summary",
            "_opsi_fmt_num",
            "_opsi_card",
            "_meow_loot_labels",
            "_build_meow_rows",
            "_build_opsi_summary_html",
            "_split_unit_text",
            "_build_meow_cards_html",
            "_load_opsi_stats_dependencies",
        ):
            if hasattr(ctx, _m):
                continue
            _raw = _stat_render_method(_m)
            # 从类 __dict__ 取原始描述符，判断是否 staticmethod
            from module.webui.app_stat_opsi import OpsiStatisticsMixin

            _desc = OpsiStatisticsMixin.__dict__.get(_m)
            if isinstance(_desc, staticmethod):
                setattr(ctx, _m, _desc.__func__)
            else:
                setattr(ctx, _m, _raw.__get__(ctx, type(ctx)))

        payload = ctx._build_opsi_month_cards()
        if payload is None:
            return {"success": False, "html": "", "error": "数据源不可用"}
        net, cards, inst, cl1_db = payload
        summary_html = ctx._build_opsi_summary_html(net, cards)
        meow_rows = ctx._build_meow_rows(cl1_db, inst)
        meow_html = ctx._build_meow_cards_html(meow_rows)
        return {
            "success": True,
            "html": summary_html,
            "meow_html": meow_html,
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

        # 面板通用工具（各 Mixin 都有同名实现，绑定时覆盖不到时兜底）
        def _opsi_fmt_num(self, value, decimals=0):
            from module.webui.app_stat_opsi import OpsiStatisticsMixin

            return OpsiStatisticsMixin._opsi_fmt_num(value, decimals)  # type: ignore[attr-defined]
    ctx = _Ctx()
    # 镜像所有方法（含 private）与类常量
    for _name in dir(mixin):
        if _name.startswith("__"):
            continue
        if _name in ("_stat_month_pair", "_stat_month_is_current"):
            continue
        _desc = mixin.__dict__.get(_name)
        if _desc is None:
            # 继承来的方法
            try:
                _val = getattr(mixin, _name)
            except Exception:  # noqa: BLE001
                continue
            if callable(_val):
                setattr(ctx, _name, _val.__get__(ctx, type(ctx)))
            continue
        if isinstance(_desc, staticmethod):
            setattr(ctx, _name, _desc.__func__)
        elif isinstance(_desc, classmethod):
            continue
        else:
            try:
                setattr(ctx, _name, _desc.__get__(ctx, type(ctx)))
            except Exception:  # noqa: BLE001
                pass
    return ctx


def get_commission_panel_html(instance_name: str) -> dict:
    """委托面板（app_stat_commission.py）。返回 summary/table/recent 三段 HTML。"""
    try:
        ctx = _make_stat_ctx(
            instance_name,
            "module.webui.app_stat_commission",
            "CommissionIncomeStatisticsMixin",
        )
        data = ctx._load_commission_income_data()
        if data is None:
            return {"success": False, "error": "暂无委托数据"}
        summary, table, recent = ctx._build_commission_income_html(data)
        return {
            "success": True,
            "summary_html": summary,
            "table_html": table,
            "recent_html": recent,
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染委托统计失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def get_research_panel_html(instance_name: str) -> dict:
    """科研面板（app_stat_research.py）。"""
    try:
        ctx = _make_stat_ctx(
            instance_name,
            "module.webui.app_stat_research",
            "ResearchStatisticsMixin",
        )
        data = ctx._load_research_stats_data()
        table = ctx._build_research_table_html(data)
        return {"success": True, "table_html": table, "error": ""}
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染科研统计失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def get_delta_panel_html(instance_name: str) -> dict:
    """资源面板（app_stat_delta.py）。返回汇总/排行/图例三段 HTML。"""
    try:
        ctx = _make_stat_ctx(
            instance_name,
            "module.webui.app_stat_delta",
            "ResourceDeltaStatisticsMixin",
        )
        timeline, summary, ranking = ctx._load_delta_data()
        if not timeline and not summary and not ranking:
            return {"success": False, "error": "暂无资源变动数据"}
        stats_html = ctx._build_delta_stats_html(summary) if summary else ""
        legend_html = ctx._build_delta_legend_html() if hasattr(ctx, "_build_delta_legend_html") else ""
        summary_html = ctx._build_summary_html(summary) if summary else ""
        return {
            "success": True,
            "stats_html": stats_html,
            "legend_html": legend_html,
            "summary_html": summary_html,
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[webapi] 渲染资源统计失败: {e}")
        return {"success": False, "error": f"{type(e).__name__}: {e}"}
