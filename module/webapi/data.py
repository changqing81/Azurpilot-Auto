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
    try:
        config = AzurLaneConfig(name, task=None)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 实例配置加载失败({name}): {e}")
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
    from module.config.config import AzurLaneConfig

    try:
        config = AzurLaneConfig(name, task=None)
        config.get_next_task()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 任务队列读取失败({name}): {e}")
        return {"running": [], "pending": [], "waiting": [], "alive": False}

    # i18n: 优先 zh-CN；键缺失时退回 command
    def _task_name(command: str) -> str:
        try:
            path = os.path.join(
                os.path.dirname(__file__), "..", "config", "i18n", "zh-CN.json"
            )
            data = read_file(path)  # read_file 内部已 JSON 解析，返回 dict
            return str(data.get("Task", {}).get(command, {}).get("name") or command)
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

def get_log_tail(name: str, limit: int = 200) -> dict:
    """日志尾部 HTML（复用老 UI RichLog 渲染链：ProcessManager.renderables → HTMLConsole）。

    与老 UI 同进程运行时（mount_webapi 挂进现有 asgi_app）renderables 直接可读；
    独立运行（无 WebUI 会话）时返回空。渲染缓存复用 RichLog._html_cache。
    """
    from module.webui.process_manager import ProcessManager

    pm = ProcessManager._processes.get(name)
    if pm is None:
        return {"chunks": [], "html": ""}
    renderables = list(pm.renderables)[-limit:]
    if not renderables:
        return {"chunks": [], "html": ""}
    try:
        from module.logger import HTMLConsole, Highlighter, WEB_THEME
        from module.webui.utils import (
            DARK_TERMINAL_THEME, LIGHT_TERMINAL_THEME, LOG_CODE_FORMAT, State,
        )

        theme_name = getattr(State, "theme", "light") or "light"
        terminal_theme = DARK_TERMINAL_THEME if theme_name in ("dark", "dark_advanced_material") else LIGHT_TERMINAL_THEME
        console = HTMLConsole(
            force_terminal=False, force_interactive=False, width=80,
            color_system="truecolor", markup=False, record=True, safe_box=False,
            highlighter=Highlighter(), theme=WEB_THEME,
        )
        with console.capture():
            for r in renderables:
                console.print(r)
        html = console.export_html(
            theme=terminal_theme, clear=True, code_format=LOG_CODE_FORMAT, inline_styles=True,
        )
        return {"chunks": [], "html": html}
    except Exception as e:  # noqa: BLE001 —— 渲染降级：退回纯文本
        logger.warning(f"[webapi] 日志渲染失败({name}): {e}")
        return {"chunks": [], "html": ""}

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
