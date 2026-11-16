"""新老 UI 热切换容器（长卿要求的「不用重启随便切」）。

背景
----
原来的实现是「启动时决定」：`Webui.UI = new` 时把老 UI 的 `/` 与 `/manage`
路由从 Starlette application 里**摘掉**，改由新 UI 静态文件接管。
改配置必须重启 GUI 才生效。

长卿明确要求：
> 「加个不用重启的热切换，并让 / 和 /new/ 同时可用，随便切来切去」

设计
----
不改路由表，改 `/` 这一个路径的**分流**：

    ┌─────────────────────────────────────────────┐
    │ HotSwitchApp（容器）                         │
    │  path == "/" 且 UI=new  → 返回新 UI index.html│
    │  其余一切                → 透传给原 PyWebIO app│
    └─────────────────────────────────────────────┘

为什么用容器 ASGI app 而不是中间件：
- 老 UI 的 `/` 要处理 **WebSocket 升级**（PyWebIO 全程走 WS），
  中间件只能改 http 响应、没法把 ws 交给下游。
- 容器方案里 `self.inner(scope, receive, send)` 原样透传，
  WS / lifespan / http 全都按原逻辑走，零副作用。

为什么读配置要用 mtime 缓存：
- `State.deploy_config.read()` 会读磁盘 + 解析 YAML，
  每个请求都读一遍太浪费（首屏有 30+ 请求）。
- 按文件 mtime 失效：改完 deploy.yaml 立即生效，不需要重启也不需要定时轮询。

范围
----
- `/`     → 跟随配置（legacy = PyWebIO / new = 新 UI）
- `/new/` → 永远新 UI（对照与回退入口）
- 其余    → 原样透传

注意 PyWebIO 的路由表里**只有** `/`（一条 Route + 一条 WebSocketRoute，
见 pywebio/platform/fastapi.py:_webio_routes）。它用 `?app=<name>` 查询参数
区分多个 application —— 所以老 UI 的「管理」页**没有** `/manage` 这个 URL，
它是侧栏按钮走 WS 内部回调（`ui_manage`）进入的。
这里不要试图接管 `/manage`：那个路径从来不存在。
"""
from __future__ import annotations

import os
from typing import Any

from starlette.responses import FileResponse, HTMLResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from module.logger import logger

# 配置缓存：键 = deploy.yaml 的 (mtime_ns, size)
_UI_MODE_CACHE: dict[str, Any] = {"key": None, "mode": "legacy"}


def _deploy_config_path() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "deploy.yaml")
    )


def current_ui_mode() -> str:
    """读 `Webui.UI`，按 deploy.yaml 的 mtime 缓存。

    与老 UI 的读取口径一致（State.deploy_config.UI），
    但**不依赖 State 是否已初始化** —— 首次请求可能早于 startup 回调。

    Returns:
        "new" 或 "legacy"（非法值一律按 legacy 处理，保证能进老 UI 自救）。
    """
    path = _deploy_config_path()
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return "legacy"

    if _UI_MODE_CACHE["key"] == key:
        return _UI_MODE_CACHE["mode"]

    mode = "legacy"
    try:
        # 优先用已初始化的 State（它已把 deploy.yaml 解析好）
        from module.webui.setting import State

        raw = getattr(State, "deploy_config", None)
        value = getattr(raw, "UI", None) if raw is not None else None
        if value:
            mode = str(value).strip().lower()
        else:
            # State 还没初始化：直接读 YAML（轻量，只在文件变化时发生）
            import yaml

            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            value = (data.get("Deploy") or {}).get("Webui", {}).get("UI")
            if value:
                mode = str(value).strip().lower()
    except Exception as e:  # noqa: BLE001 —— 读配置失败不能让整站 500
        logger.warning(f"[HotSwitch] 读取 Webui.UI 失败，按 legacy 处理: {e}")
        mode = "legacy"

    if mode not in ("new", "legacy"):
        mode = "legacy"
    _UI_MODE_CACHE["key"] = key
    _UI_MODE_CACHE["mode"] = mode
    return mode


class HotSwitchApp:
    """把 `/` 按配置分流到新/老 UI，其余路径透传给原应用。"""

    def __init__(self, inner: ASGIApp, frontend_dist: str) -> None:
        self.inner = inner
        self.dist = frontend_dist
        self.index_file = os.path.join(frontend_dist, "index.html")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # websocket / lifespan 一律透传（PyWebIO 的会话全靠 ws）
            await self.inner(scope, receive, send)
            return

        path = scope.get("path") or "/"
        # 只接管根路径。注意不能用 path.rstrip("/") == "" 之类的写法，
        # 子路径（/new/xxx、/api/xxx）必须原样交给下游，否则新 UI 的
        # SPA fallback 与老 UI 的接口都会被这层吃掉。
        if path == "/" and current_ui_mode() == "new":
            response = self._serve_spa_index()
            await response(scope, receive, send)
            return

        await self.inner(scope, receive, send)

    def _serve_spa_index(self) -> Response:
        """返回新 UI 的 index.html（no-cache，保证切主题/切 UI 立即生效）。

        **必须重写资源路径**：vite 配置 base='./'，dist/index.html 里是
        './assets/index-xxx.js' 这种相对路径 ——
          - 走 /new/ 时，相对路径基于 /new/ 解析 → /new/assets/... ✓
          - 走 /（UI=new 时由本类接管）时，基于 / 解析 → /assets/... ✗ 404

        实测症状：UI=new 重启后页面卡在「正在加载 AzurPilot…」，
        骨架不消失、React 完全不挂载，且控制台无任何 JS 报错
        （脚本根本没下载到，所以连报错都没有）。这个坑只在
        「UI=new + 访问 /」时才出现，走 /new/ 一直是好的，很容易漏测。

        修法：把 ./assets/ 前缀改成 /new/assets/。
        其它绝对路径（/static/...、/pywebio_static/...）原样保留。
        """
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                html = f.read()
            html = html.replace('"./assets/', '"/new/assets/').replace(
                "'./assets/", "'/new/assets/"
            )
            return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
        except FileNotFoundError:
            logger.error("[HotSwitch] frontend/dist/index.html 不存在，回退老 UI")
            return HTMLResponse("<h1>新 UI 未构建</h1>", status_code=500)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[HotSwitch] 读取 index.html 失败: {e}")
            return HTMLResponse("<h1>新 UI 读取失败</h1>", status_code=500)
