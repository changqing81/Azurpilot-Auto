"""webapi 应用工厂与挂载入口。

设计约束（docs/new-ui/README.md §3）：
- 不新建进程、不替换 gui.py 的 asgi_app；提供 mount_webapi(app) 由现有应用调用。
- 静态产物 frontend/dist 挂在 /new/，SPA 回退 index.html。
- /newapi/* 为 REST 端点命名空间，避免与老 /api/* 冲突。
"""
from __future__ import annotations

import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from module.logger import logger
from module.webapi import data

_FRONTEND_DIST = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
)

# Windows 注册表可能把 .js 关联成 text/plain 导致整页白屏（wess09 踩过的坑），
# StaticFiles 用 mimetypes 覆盖表兜底。
import mimetypes

for _ext, _mime in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css")):
    mimetypes.add_type(_mime, _ext)


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def version(request: Request) -> JSONResponse:
    return JSONResponse(data.get_version())


async def instances(request: Request) -> JSONResponse:
    return JSONResponse(data.get_instances())


async def dashboard(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(data.get_dashboard(name))


async def overview(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(data.get_overview(name))


async def header(request: Request) -> JSONResponse:
    return JSONResponse(data.get_header())


async def log_tail(request: Request) -> JSONResponse:
    from starlette.responses import PlainTextResponse

    name = request.path_params.get("name", "")
    result = data.get_log_tail(name)
    # html 片段直接回（含 rich 内联样式 span），前端 innerHTML 拼进 <code>
    return PlainTextResponse(result.get("html") or "", media_type="text/html; charset=utf-8")


def _frontend_files() -> StaticFiles | None:
    """frontend/dist 存在才挂；未构建时 /new/ 返回 404，不影响老 UI。"""
    if os.path.isdir(_FRONTEND_DIST):
        return StaticFiles(directory=_FRONTEND_DIST, html=True, check_dir=True)
    logger.warning("[webapi] frontend/dist 不存在，/new/ 未挂载（先 pnpm build）")
    return None


def mount_webapi(app: Starlette) -> None:
    """把 webapi 路由挂进现有 Starlette 应用。

    顺序敏感：必须在老 UI 主路由之后追加（老 UI 的 / 优先），
    Mount('/new') 放最后做 SPA 回退。
    """
    routes = [
        Route("/newapi/healthz", healthz),
        Route("/newapi/version", version),
        Route("/newapi/instances", instances),
        Route("/newapi/dashboard/{name}", dashboard),
        Route("/newapi/overview/{name}", overview),
        Route("/newapi/log/{name}/tail", log_tail),
        Route("/newapi/header", header),
    ]
    files = _frontend_files()
    if files is not None:
        routes.append(Mount("/new", app=files))
    for route in routes:
        try:
            app.routes.append(route)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[webapi] 挂载失败 {route}: {e}")
    logger.hr("[webapi] 新 UI API 已挂载")
    logger.attr("端点", [r.path for r in routes if hasattr(r, "path")])
