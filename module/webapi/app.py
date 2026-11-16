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
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.concurrency import run_in_threadpool
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
    return JSONResponse(await run_in_threadpool(data.get_version))


async def instances(request: Request) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(data.get_instances))


async def dashboard(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_dashboard, name))


async def overview(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_overview, name))


async def task_config_save(request: Request) -> JSONResponse:
    import json as _json

    name = request.path_params.get("name", "")
    task = request.path_params.get("task", "")
    body = _json.loads(await request.body() or b"{}")
    modified = body.get("modified", {})
    return JSONResponse(await run_in_threadpool(data.save_task_config, name, task, modified))


async def task_config(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    task = request.path_params.get("task", "")
    return JSONResponse(await run_in_threadpool(data.get_task_config, name, task))


async def resource_delta(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_resource_delta, name))


async def theme_css(request: Request) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(data.get_theme_styles))


async def bundled_css(request: Request) -> Response:
    """把老 UI 的多个 CSS 合并成一个响应，减少首屏 14 个渲染阻塞请求。

    只合并「基础层 + 9 个 alas 层」，**不含主题层**（主题层由 /newapi/theme-css
    另行动态注入，因为要跟随 deploy.yaml 的 Theme 变化）。
    合并顺序必须与 index.html 原来的 <link> 顺序一致，否则层叠优先级会变。
    """
    from module.webapi.data import get_bundled_css

    # 移动端判定与老 UI 同口径：User-Agent 里含 mobi/android/iphone 等。
    # 老 UI 用 session_info.user_agent.is_mobile；新 UI 无 PyWebIO 会话，
    # 这里按 UA 子串判断（与 user-agents 库的判定集一致）。
    ua = (request.headers.get("user-agent") or "").lower()
    is_mobile = any(
        token in ua
        for token in ("mobile", "android", "iphone", "ipod", "ipad", "windows phone", "blackberry")
    )
    # 主题也要传：老 UI 的 Bootstrap 主题层随主题切换（dark 用 bs-theme/dark.min.css）
    from module.webapi.data import get_theme_name

    theme = await run_in_threadpool(get_theme_name)
    body = await run_in_threadpool(get_bundled_css, is_mobile, theme)
    return Response(
        body,
        media_type="text/css",
        # 同一 URL 会因主题/移动端返回不同内容，必须按 Vary 缓存，否则
        # 切主题后浏览器可能仍用旧缓存（外观不变、误判为"切换无效"）。
        headers={
            "Cache-Control": "public, max-age=60",
            "Vary": "User-Agent",
        },
    )


async def instances_detail(request: Request) -> JSONResponse:
    """实例详情列表（管理页用：名称/状态/模块名 + 可用模板）。"""
    return JSONResponse(await run_in_threadpool(data.list_instances_detail))


def _parse_json_body(raw: bytes) -> tuple:
    """解析 POST body，返回 (dict, error)。非法 JSON 返回 400 而不是 500。

    之前直接 json.loads 会让畸形请求抛异常打穿到 500，
    排查时只看到 "Internal Server Error"，分不清是代码 bug 还是请求格式错。
    """
    import json as _json

    if not raw:
        return {}, ""
    try:
        parsed = _json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return {}, f"请求体不是合法 JSON：{e}"
    if not isinstance(parsed, dict):
        return {}, "请求体必须是 JSON 对象"
    return parsed, ""


async def instance_create(request: Request) -> JSONResponse:
    """创建实例（POST {name, copy_from}）。"""
    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    return JSONResponse(
        await run_in_threadpool(
            data.create_instance, body.get("name", ""), body.get("copy_from", "")
        )
    )


async def instance_delete(request: Request) -> JSONResponse:
    """删除实例（POST {name}）。"""
    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    return JSONResponse(
        await run_in_threadpool(data.delete_instance, body.get("name", ""))
    )


async def instance_import(request: Request) -> JSONResponse:
    """导入配置文件（multipart/form-data，字段名 file）。

    与老 UI app_manage.py:_import 同规则：文件名决定实例名与模块名，
    内容必须是 JSON 对象，写入走 config_updater（含配置迁移）。
    """
    try:
        form = await request.form()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"success": False, "error": f"表单解析失败：{e}"})
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"success": False, "error": "缺少文件字段 file"})
    try:
        content = await upload.read()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"success": False, "error": f"读取文件失败：{e}"})
    # 1MB 上限（老 UI file_upload 也是 max_size="1M"）
    if len(content) > 1024 * 1024:
        return JSONResponse({"success": False, "error": "文件超过 1MB 限制"})
    return JSONResponse(
        await run_in_threadpool(
            data.import_instance_config, getattr(upload, "filename", ""), content
        )
    )


async def stat_panels_html(request: Request) -> JSONResponse:
    """GET /newapi/stat/{name}/panels_html?panel=<name>

    返回老 UI 同一套渲染器产出的 HTML（结构/样式/数字逐字节一致），
    前端直接 innerHTML 挂载，避免在 React 里重写几百行指标逻辑。

    支持的 panel：
      opsi       → 净赚卡 + 12 指标卡 + 大世界收获
      commission → 委托收入（汇总/明细表/最近记录）
      research   → 科研掉落表
      delta      → 资源变动（汇总/排行/图例）
    """
    name = request.path_params.get("name", "")
    panel = (request.query_params.get("panel") or "opsi").strip().lower()
    month = (request.query_params.get("month") or "").strip()
    handlers = {
        "opsi": data.get_opsi_panels_html,
        "commission": data.get_commission_panel_html,
        "research": data.get_research_panel_html,
        "delta": data.get_delta_panel_html,
        "ship_exp": data.get_ship_exp_panel_html,
        "fleet": data.get_fleet_tech_panel_html,
        "mind": data.get_mind_panel_html,
    }
    fn = handlers.get(panel)
    if fn is None:
        return JSONResponse(
            {"success": False, "error": f"不支持的 panel: {panel}（支持 {', '.join(handlers)}）"}
        )
    # 目前只有 opsi 支持月份切换（净赚卡/12 指标卡/大世界收获都按月）
    if panel == "opsi":
        return JSONResponse(await run_in_threadpool(fn, name, month))
    return JSONResponse(await run_in_threadpool(fn, name))


async def stat_months(request: Request) -> JSONResponse:
    """GET /newapi/stat/{name}/months — 统计页可选月份（新→旧）。"""
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_stat_available_months, name))


async def stat_panels_warm(request: Request) -> JSONResponse:
    """GET /newapi/stat/{name}/panels_warm — 后台预热统计面板缓存。

    首次渲染 opsi 面板要 1.3s（_build_meow_rows 对侵蚀等级 1~6 各查一次
    SQLite）。用户点进统计页时才开始算，就会看到明显卡顿。
    这个端点让前端**进页面时立刻并发预热**，用户点页签时缓存已就绪。
    返回已预热的 panel 列表（不返回 HTML，省带宽）。
    """
    name = request.path_params.get("name", "")

    def _warm() -> dict:
        done = []
        for panel, fn in (
            ("opsi", data.get_opsi_panels_html),
            ("commission", data.get_commission_panel_html),
            ("research", data.get_research_panel_html),
            ("delta", data.get_delta_panel_html),
            ("ship_exp", data.get_ship_exp_panel_html),
            ("fleet", data.get_fleet_tech_panel_html),
            ("mind", data.get_mind_panel_html),
        ):
            try:
                r = fn(name)
                if isinstance(r, dict) and r.get("success"):
                    done.append(panel)
            except Exception:  # noqa: BLE001 —— 预热失败不影响正常请求
                pass
        return {"success": True, "warmed": done}

    return JSONResponse(await run_in_threadpool(_warm))


async def background_setting(request: Request) -> JSONResponse:
    """GET /newapi/background — 当前背景设置（mode/media/是否有自定义图）。"""
    return JSONResponse(await run_in_threadpool(data.get_background_setting))


async def background_mode(request: Request) -> JSONResponse:
    """POST /newapi/background/mode {mode: random|custom}。"""
    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    return JSONResponse(
        await run_in_threadpool(data.set_background_mode, str(body.get("mode", "")))
    )


async def background_media(request: Request) -> JSONResponse:
    """POST /newapi/background/media {media: image|video|auto}。"""
    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    return JSONResponse(
        await run_in_threadpool(data.set_media_preference, str(body.get("media", "")))
    )


async def background_upload(request: Request) -> JSONResponse:
    """POST /newapi/background/upload（multipart，字段 file）。"""
    try:
        form = await request.form()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"success": False, "error": f"表单解析失败：{e}"})
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"success": False, "error": "缺少文件字段 file"})
    try:
        content = await upload.read()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"success": False, "error": f"读取文件失败：{e}"})
    return JSONResponse(
        await run_in_threadpool(
            data.upload_custom_background, getattr(upload, "filename", ""), content
        )
    )


async def background_custom_info(request: Request) -> JSONResponse:
    """GET /newapi/background/custom — 自定义背景的访问信息。"""
    return JSONResponse(await run_in_threadpool(data.get_custom_background_info))


async def background_custom_file(request: Request) -> Response:
    """GET /api/wallpapers/custom — 直接返回自定义背景文件（支持 Range，视频可拖动）。"""
    from starlette.responses import FileResponse

    info = await run_in_threadpool(data.get_custom_background_info)
    if not info.get("exists"):
        return JSONResponse({"success": False, "error": "没有自定义背景"}, status_code=404)
    path = data._wallpapers_dir() / "custom" / info["filename"]
    return FileResponse(path, headers={"Cache-Control": "no-store"})


async def wallpaper_sources(request: Request) -> JSONResponse:
    """GET /newapi/background/sources — 图源列表。"""
    return JSONResponse(await run_in_threadpool(data.get_wallpaper_sources))


async def wallpaper_sources_save(request: Request) -> JSONResponse:
    """POST /newapi/background/sources {sources: [...]}。"""
    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    return JSONResponse(
        await run_in_threadpool(data.set_wallpaper_sources, body.get("sources", []))
    )


async def wallpaper_sources_reset(request: Request) -> JSONResponse:
    """POST /newapi/background/sources/reset — 恢复默认图源。"""
    return JSONResponse(await run_in_threadpool(data.reset_wallpaper_sources))


async def dev_action(request: Request) -> JSONResponse:
    """开发者工具的调试动作（对应老 UI app_developer_tools.py:dev_utils）。

    老 UI 这些按钮全是 PyWebIO 会话内回调，新 UI 没 WS 通道。
    这里统一成一个 POST 端点，用 action 字段分派 ——
    相比给每个动作单独开路由，这样前端只需一张动作表。
    """
    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    action = str(body.get("action", "")).strip()
    instance = str(body.get("instance", "")).strip()
    mode = str(body.get("mode", "")).strip()

    handlers = {
        "raise_exception": lambda: data.dev_raise_exception(),
        "preview_update": lambda: data.dev_preview_update_notice(),
        "mock_icon": lambda: data.dev_mock_icon_state(
            instance, int(body.get("state", 1)), int(body.get("seconds", 10))
        ),
        "clear_icon": lambda: data.dev_clear_icon_state(instance),
        "cross_month": lambda: data.dev_cross_month_rehearsal(instance, mode),
        "cancel_cross_month": lambda: data.dev_cancel_cross_month_rehearsal(instance),
        "restart_alas": lambda: data.dev_restart_alas(instance),
    }
    fn = handlers.get(action)
    if fn is None:
        return JSONResponse(
            {"success": False, "error": f"未知动作: {action}"}, status_code=400
        )
    try:
        return JSONResponse(await run_in_threadpool(fn))
    except Exception as e:  # noqa: BLE001 —— raise_exception 就是故意抛的
        return JSONResponse({"success": False, "error": f"{type(e).__name__}: {e}"})


async def announcement(request: Request) -> JSONResponse:
    """公告内容（/newapi/announcement?force=1 强制刷新）。"""
    force = request.query_params.get("force") in ("1", "true", "yes")
    return JSONResponse(await run_in_threadpool(data.get_announcement, force))


async def update_log(request: Request) -> JSONResponse:
    """更新日志（changelog.json，与更新器页同源）。"""
    return JSONResponse(await run_in_threadpool(data.get_update_log))


async def switch_ui(request: Request) -> JSONResponse:
    """热切换界面版本（legacy ↔ new），**不需要重启**。

    写入 deploy.yaml 后，由 `module/webui/hot_switch.py` 在**下一次请求**时
    按 mtime 感知并生效 —— 用户刷新页面即可看到新界面。
    """
    import json as _json

    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    target = str(body.get("ui", "")).strip().lower()
    if target not in ("legacy", "new"):
        return JSONResponse(
            {"success": False, "error": "ui 必须是 legacy 或 new"}, status_code=400
        )

    def _write() -> dict:
        from module.webui.setting import State
        from module.logger import logger as _lg

        try:
            deploy = State.deploy_config
            deploy.read()
            deploy.UI = target
            deploy.write()
            _lg.info(f"[WebUI] 界面版本已热切换为 {target}（刷新页面生效）")
            return {"success": True, "ui": target}
        except Exception as e:  # noqa: BLE001
            _lg.error(f"[WebUI] 热切换失败: {e}")
            return {"success": False, "error": str(e)}

    return JSONResponse(await run_in_threadpool(_write))


async def current_ui(request: Request) -> JSONResponse:
    """当前界面版本（供前端显示切换按钮状态）。"""
    from module.webui.hot_switch import current_ui_mode

    return JSONResponse({"ui": await run_in_threadpool(current_ui_mode)})


async def restart_webui(request: Request) -> JSONResponse:
    """重启 WebUI（让 deploy.yaml 改动生效）。链路与老 UI 重启按钮完全一致。"""
    return JSONResponse(await run_in_threadpool(data.restart_webui))


async def oobe_state(request: Request) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(data.get_oobe_state))


async def scheduler_start(request: Request) -> JSONResponse:
    import json as _json

    name = request.path_params.get("name", "")
    body = _json.loads(await request.body() or b"{}")
    return JSONResponse(await run_in_threadpool(data.start_scheduler, name, body.get("func")))


async def scheduler_stop(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.stop_scheduler, name))


async def wallpaper_candidates(request: Request) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(data.get_wallpaper_candidates))


async def remote_status(request: Request) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(data.get_remote_status))


async def remote_toggle(request: Request) -> JSONResponse:
    import json as _json

    body = _json.loads(await request.body() or b"{}")
    return JSONResponse(await run_in_threadpool(data.set_remote_enabled, bool(body.get("enabled"))))


async def update_info(request: Request) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(data.get_update_info))


async def mind_calculator(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_mind_calculator, name))


async def fleet_tech(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_fleet_tech, name))


async def research_stats(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_research_stats, name))


async def opsi_summary(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_opsi_summary, name))


async def commission_income(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_commission_income, name))


async def menu(request: Request) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(data.get_menu))


async def header(request: Request) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(data.get_header))


async def log_tail(request: Request) -> JSONResponse:
    """日志增量：since=游标，width=浏览器量出的渲染宽度（老 UI RichLog.get_width 同口径）。"""
    name = request.path_params.get("name", "")
    since = request.query_params.get("since")
    since_val = int(since) if since is not None and since.lstrip("-").isdigit() else -1
    width_raw = request.query_params.get("width")
    width_val = int(width_raw) if width_raw is not None and width_raw.isdigit() else 80
    return JSONResponse(await run_in_threadpool(data.get_log_tail, name, since_val, 200, width_val))



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
        Route("/newapi/restart", restart_webui, methods=["POST"]),
        Route("/newapi/ui", current_ui),
        Route("/newapi/ui/switch", switch_ui, methods=["POST"]),
        Route("/newapi/bundle.css", bundled_css),
        Route("/newapi/instances/detail", instances_detail),
        Route("/newapi/instance/import", instance_import, methods=["POST"]),
        Route("/newapi/instance/create", instance_create, methods=["POST"]),
        Route("/newapi/instance/delete", instance_delete, methods=["POST"]),
        Route("/newapi/stat/{name}/panels_html", stat_panels_html),
        Route("/newapi/stat/{name}/panels_warm", stat_panels_warm),
        Route("/newapi/stat/{name}/months", stat_months),
        Route("/newapi/background", background_setting),
        Route("/newapi/background/mode", background_mode, methods=["POST"]),
        Route("/newapi/background/media", background_media, methods=["POST"]),
        Route("/newapi/background/upload", background_upload, methods=["POST"]),
        Route("/newapi/background/custom", background_custom_info),
        Route("/api/wallpapers/custom", background_custom_file),
        Route("/newapi/background/sources", wallpaper_sources),
        Route("/newapi/background/sources/save", wallpaper_sources_save, methods=["POST"]),
        Route("/newapi/background/sources/reset", wallpaper_sources_reset, methods=["POST"]),
        Route("/newapi/dev/action", dev_action, methods=["POST"]),
        Route("/newapi/announcement", announcement),
        Route("/newapi/update-log", update_log),
        Route("/newapi/config/{name}/task/{task}", task_config),
        Route("/newapi/config/{name}/task/{task}", task_config_save, methods=["POST"]),
    ]
    files = _frontend_files()
    if files is not None:
        # mount_root=True：新 UI 直接占据 /（老 UI 已由调用方关闭）。
        # 但 /new/ 也必须保留：vite 的 base 是 /new/，index.html 里引用的
        # 资源路径（/new/assets/index-xxx.js）与 SPA 的 fetch 前缀都写死了 /new/，
        # 只挂 / 会让这些资源 404、页面白屏。两个前缀指向同一份 StaticFiles。
        routes.append(Mount("/new", app=files))
        if mount_root:
            routes.append(Mount("/", app=files))
    for route in routes:
        try:
            app.routes.append(route)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[webapi] 挂载失败 {route}: {e}")
    logger.hr("[webapi] 新 UI API 已挂载")
    logger.attr("端点", [r.path for r in routes if hasattr(r, "path")])
