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
