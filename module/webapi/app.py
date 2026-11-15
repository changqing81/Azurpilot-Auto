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


async def task_config_save(request: Request) -> JSONResponse:
    import json as _json

    name = request.path_params.get("name", "")
    task = request.path_params.get("task", "")
    body = _json.loads(await request.body() or b"{}")
    modified = body.get("modified", {})
    return JSONResponse(data.save_task_config(name, task, modified))


async def task_config(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    task = request.path_params.get("task", "")
    return JSONResponse(data.get_task_config(name, task))


async def resource_delta(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(data.get_resource_delta(name))


async def theme_css(request: Request) -> JSONResponse:
    return JSONResponse(data.get_theme_styles())


async def oobe_state(request: Request) -> JSONResponse:
    return JSONResponse(data.get_oobe_state())


async def scheduler_start(request: Request) -> JSONResponse:
    import json as _json

    name = request.path_params.get("name", "")
    body = _json.loads(await request.body() or b"{}")
    return JSONResponse(data.start_scheduler(name, body.get("func")))


async def scheduler_stop(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(data.stop_scheduler(name))


async def wallpaper_candidates(request: Request) -> JSONResponse:
    return JSONResponse(data.get_wallpaper_candidates())


async def remote_status(request: Request) -> JSONResponse:
    return JSONResponse(data.get_remote_status())


async def remote_toggle(request: Request) -> JSONResponse:
    import json as _json

    body = _json.loads(await request.body() or b"{}")
    return JSONResponse(data.set_remote_enabled(bool(body.get("enabled"))))


async def update_info(request: Request) -> JSONResponse:
    return JSONResponse(data.get_update_info())


async def mind_calculator(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(data.get_mind_calculator(name))


async def fleet_tech(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(data.get_fleet_tech(name))


async def research_stats(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(data.get_research_stats(name))


async def opsi_summary(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(data.get_opsi_summary(name))


async def commission_income(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(data.get_commission_income(name))


async def menu(request: Request) -> JSONResponse:
    return JSONResponse(data.get_menu())


async def header(request: Request) -> JSONResponse:
    return JSONResponse(data.get_header())


async def log_tail(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    since = request.query_params.get("since")
    since_val = int(since) if since is not None and since.isdigit() else -1
    return JSONResponse(data.get_log_tail(name, since=since_val))



class SpaStaticFiles(StaticFiles):
    """SPA 静态服务：index.html no-cache（保证发版即生效），hash 资源 immutable 长缓存。"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        path = str(getattr(response, "path", ""))
        if path.endswith("index.html"):
            response.headers["Cache-Control"] = "no-cache"
        elif "/assets/" in path.replace("\\", "/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _frontend_files() -> StaticFiles | None:
    """frontend/dist 存在才挂；未构建时 /new/ 返回 404，不影响老 UI。"""
    if os.path.isdir(_FRONTEND_DIST):
        return SpaStaticFiles(directory=_FRONTEND_DIST, html=True, check_dir=True)
    logger.warning("[webapi] frontend/dist 不存在，/new/ 未挂载（先 pnpm build）")
    return None


def mount_webapi(app: Starlette, mount_root: bool = False) -> None:
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
        Route("/newapi/menu", menu),
        Route("/newapi/stat/{name}/resource_delta", resource_delta),
        Route("/newapi/stat/{name}/commission_income", commission_income),
        Route("/newapi/stat/{name}/opsi_summary", opsi_summary),
        Route("/newapi/stat/{name}/research_stats", research_stats),
        Route("/newapi/stat/{name}/fleet_tech", fleet_tech),
        Route("/newapi/stat/{name}/mind", mind_calculator),
        Route("/newapi/update/info", update_info),
        Route("/newapi/remote/status", remote_status),
        Route("/newapi/remote/toggle", remote_toggle, methods=["POST"]),
        Route("/newapi/wallpaper/candidates", wallpaper_candidates),
        Route("/newapi/scheduler/{name}/start", scheduler_start, methods=["POST"]),
        Route("/newapi/scheduler/{name}/stop", scheduler_stop, methods=["POST"]),
        Route("/newapi/oobe", oobe_state),
        Route("/newapi/theme-css", theme_css),
        Route("/newapi/config/{name}/task/{task}", task_config),
        Route("/newapi/config/{name}/task/{task}", task_config_save, methods=["POST"]),
    ]
    files = _frontend_files()
    if files is not None:
        # mount_root=True：新 UI 直接占据 /（老 UI 已由调用方关闭），否则沿用 /new 并存
        routes.append(Mount("/" if mount_root else "/new", app=files))
    for route in routes:
        try:
            app.routes.append(route)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[webapi] 挂载失败 {route}: {e}")
    logger.hr("[webapi] 新 UI API 已挂载")
    logger.attr("端点", [r.path for r in routes if hasattr(r, "path")])
