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

# **实际正在运行**的界面版本。由 mount_webapi(mount_root=...) 在启动时写入，
# 之后不再改变（切换界面版本必须重启，见 module/webui/app.py 的「新 UI 挂载」段）。
#
# 为什么不能直接读 deploy.yaml 回答「当前是什么 UI」：
# deploy.yaml 是**配置**，进程启动后它可能被别处改写（部署设置保存、模板补字段、
# 用户手改、启动器 reset）。配置和正在跑的进程完全可能是两回事——实测踩过：
# 进程明明是 UI=new（/ 上跑的是 React），/newapi/ui 却因 deploy.yaml 被改回
# legacy 而回答 legacy，前端的「切换后轮询直到变成目标值」于是瞬间误判成功、
# 直接跳转，用户看到的就是「点了没反应」。
# 所以运行时状态只认这里的值，配置值另开一个字段暴露，二者不一致就是「待重启」。
_RUNTIME_UI: dict = {"mounted": "legacy", "dist": False}

# Windows 注册表可能把 .js 关联成 text/plain 导致整页白屏（wess09 踩过的坑），
# StaticFiles 用 mimetypes 覆盖表兜底。
import mimetypes

for _ext, _mime in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css")):
    mimetypes.add_type(_mime, _ext)


def _sync_runtime_language(language: str) -> None:
    """把 WebUI 语言同步到 module.webui.lang.LANG（老 UI 的运行时语言源）。

    为什么需要：新 UI 以前切语言只写 deploy.yaml，而实际渲染用的
    lang.LANG 是**进程启动时设一次**（module/webui/app.py:543），之后再没人改。
    所以点「English / 日本語」只有配置文件变了，界面文字一字不动 ——
    这就是「切换语言是没有用的」的根因。

    老 UI 的 set_language 会同时做三件事（改 LANG、写 deploy.yaml、按需加载
    该语言的 JSON）。新 UI 的保存链路没有这个函数，这里补齐：
    改 LANG + 预加载该语言词条，保证后续 /newapi/* 立刻返回新语言。
    """
    try:
        from module.webui import lang as _lang
        from module.config.utils import LANGUAGES

        name = str(language or "").strip()
        if name not in LANGUAGES:
            return
        _lang.LANG = name
        # 预加载：首次请求该语言时不必再等 3.5MB JSON 解析
        _lang.load_language(name)
        if name != "en-US":
            _lang.load_language("en-US")
        # 语言变了，webapi 的 i18n 缓存必须整体作废：缓存键里带路径，
        # 换语言理论上不会误命中，但清掉最省心（切语言是低频操作）。
        try:
            from module.webapi import data as _data

            _data._I18N_CACHE.clear()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001 —— 同步失败不该让保存请求失败
        logger.warning(f"[webapi] 同步运行时语言失败 ({language}): {e}")


# 对外公开的名字：deploy_settings 保存语言时也会调用（下划线前缀的不宜跨模块引用）
sync_runtime_language = _sync_runtime_language


async def stat_data_state(request: Request) -> JSONResponse:
    """统计页本地数据源状态（数据截至时间 + ok/stale/missing）。"""
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_statistics_data_state, name))

async def stat_i18n(request: Request) -> JSONResponse:
    """统计页全部文案（老 UI i18n 原文，按当前语言）。前端零硬编码。"""
    return JSONResponse(await run_in_threadpool(data.get_stat_i18n))


async def fixed_menu(request: Request) -> JSONResponse:
    """固定菜单文案（主页 6 个入口 + 侧栏「主页/管理」），按当前语言翻译。"""
    return JSONResponse(await run_in_threadpool(data.get_fixed_menu))


async def ui_language(request: Request) -> JSONResponse:
    """当前界面语言（新 UI 用来决定按钮高亮）。"""
    return JSONResponse({"language": data._current_language()})


async def ui_language_set(request: Request) -> JSONResponse:
    """切换界面语言：写 deploy.yaml + 同步运行时 LANG。

    与老 UI app_home.py:set_language 的行为对齐：改完立刻生效，
    前端重新拉 /newapi/* 就是新语言（不需要重启进程）。
    """
    import json as _json

    from module.config.utils import LANGUAGES

    try:
        body = _json.loads(await request.body() or b"{}")
    except Exception:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "error": "请求体不是有效 JSON"}, status_code=400
        )
    target = str(body.get("language", "")).strip()
    if target not in LANGUAGES:
        return JSONResponse(
            {"success": False, "error": f"不支持的语言: {target}"}, status_code=400
        )

    def _apply() -> dict:
        from module.webui.setting import State

        try:
            deploy = State.deploy_config
            deploy.read()
            deploy.Language = target
            _sync_runtime_language(target)
            return {"success": True, "language": target}
        except Exception as e:  # noqa: BLE001
            logger.error(f"[webapi] 切换语言失败: {e}")
            return {"success": False, "error": str(e)}

    return JSONResponse(await run_in_threadpool(_apply))


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
      delta      → 资源变动（粒度切换 + 时间轴图 + 汇总表 + 消耗排行榜）
    """
    name = request.path_params.get("name", "")
    panel = (request.query_params.get("panel") or "opsi").strip().lower()
    month = (request.query_params.get("month") or "").strip()
    period = (request.query_params.get("period") or "").strip()
    scope = (request.query_params.get("scope") or "").strip()
    try:
        series = int(request.query_params.get("series") or 0)
    except (TypeError, ValueError):
        series = 0
    try:
        year = int(request.query_params.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    try:
        month_no = int(request.query_params.get("month") or 0)
    except (TypeError, ValueError):
        month_no = 0
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
    # 各面板的可变参数不同（都是老 UI 的实例属性，见各自 Mixin）：
    #   opsi       → month（查看月份）
    #   research   → period（month/days30/days90/all）+ scope（series/consumable）
    #   commission → period（month/week）
    if panel == "opsi":
        # task = 大世界收获的任务筛选后缀（老 UI _meow_task_filter 的 REST 等价物）
        return JSONResponse(await run_in_threadpool(
            fn, name, month, request.query_params.get("task") or ""
        ))
    if panel == "research":
        return JSONResponse(
            await run_in_threadpool(fn, name, period, scope, series)
        )
    if panel == "fleet":
        # 科技列表有完整的筛选/搜索/排序/分页状态（老 UI 是实例属性驱动）
        return JSONResponse(
            await run_in_threadpool(
                fn, name,
                request.query_params.get("filters") or "",
                request.query_params.get("q") or "",
                request.query_params.get("scope") or "all",
                request.query_params.get("stage") or "all",
                request.query_params.get("sort") or "code",
                request.query_params.get("dir") or "1",
                int(request.query_params.get("page") or 0),
                request.query_params.get("pending") in ("1", "true"),
                request.query_params.get("edit") in ("1", "true"),
                request.query_params.get("bonuses") in ("1", "true"),
                request.query_params.get("extra") in ("1", "true"),
            )
        )
    if panel == "mind":
        # 心智计算器：清单表分页 + 批量编辑开关（老 UI 是实例属性 _mc_page/_mc_batch）
        return JSONResponse(
            await run_in_threadpool(
                fn, name,
                int(request.query_params.get("page") or 0),
                request.query_params.get("edit") in ("1", "true"),
            )
        )
    if panel == "delta":
        # 资源面板有多档粒度（日/周/月/总），老 UI 用 _delta_period 驱动
        return JSONResponse(await run_in_threadpool(fn, name, period))
    if panel == "commission":
        # 委托面板有 4 个可变参数：周期 + 查看年月（老 UI 的 ◀ ▶ 翻页）。
        # 只传 period 会让年月永远是当月 —— 实测翻页无效（year/month 回传仍是当月）。
        return JSONResponse(
            await run_in_threadpool(fn, name, period, year, month_no)
        )
    return JSONResponse(await run_in_threadpool(fn, name))


async def stat_ap_chart(request: Request) -> JSONResponse:
    """GET /newapi/stat/{name}/ap_chart?view=&month=

    体力变化图：返回老 UI 的 ap_chart_panel.html + ap_chart.js 完整产物。
    前端挂载后浏览器执图表脚本 —— 就是老 UI 那张图，不是重画的。
    """
    name = request.path_params.get("name", "")
    view = (request.query_params.get("view") or "").strip()
    month = (request.query_params.get("month") or "").strip()
    return JSONResponse(
        await run_in_threadpool(data.get_ap_chart_html, name, view, month)
    )


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
        # 推送测试（老 UI app_developer_tools.py:427-451 的三个按钮）
        "notify_test": lambda: data.dev_notify_test(
            instance, str(body.get("kind", ""))
        ),
        # 心智计算器：触发船坞扫描（老 UI _mc_scan_start）
        "mind_scan": lambda: data.dev_mind_scan(
            instance,
            int(body.get("min_level", 95)),
            int(body.get("max_level", 125)),
        ),
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
    """切换界面版本（legacy ↔ new）：只写 deploy.yaml，**重启 GUI 后生效**。

    这里**不做任何热切换**——启动时按配置决定挂哪一套 UI，所以写盘即返回，
    前端负责提示重启并轮询 /newapi/ui 的 restart_required 变化。
    见 module/webui/app.py 的「新 UI 挂载」段）。这是长卿 2026-11-16 的要求：
    正式版老 UI 不需要在后台常驻，切换 + 重启比热切换更简单可靠。
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
            _lg.info(f"[WebUI] 界面版本已切换为 {target}（重启 WebUI 后生效）")
            return {"success": True, "ui": target}
        except Exception as e:  # noqa: BLE001
            _lg.error(f"[WebUI] 界面版本切换失败: {e}")
            return {"success": False, "error": str(e)}

    return JSONResponse(await run_in_threadpool(_write))


async def current_ui(request: Request) -> JSONResponse:
    """当前界面版本。

    返回三个字段，语义刻意分开（前端据此判断"该不该轮询重启"）：

    - ui：**进程实际在跑**的界面（启动时定下，重启才会变）。
    - configured：deploy.yaml 里写的值（用户意图）。
    - restart_required：两者不一致，表示"已切换但还没重启"。

    为什么必须分开：切换流程是「写配置 → 重启 → 轮询直到新版本起来」。
    如果 ui 直接读 deploy.yaml，那么写配置的那一刻它就立刻变成目标值，
    轮询第一次就能"成功"并跳转，于是页面在服务重启前就刷新了——用户看到
    的是旧界面（甚至连接被拒），判定为「点了没反应」。踩过这个坑。
    """

    def _read() -> dict:
        from module.webui.setting import State

        mounted = _RUNTIME_UI.get("mounted", "legacy")
        if mounted not in ("legacy", "new"):
            mounted = "legacy"
        configured = mounted
        try:
            State.deploy_config.read()
            raw = str(getattr(State.deploy_config, "UI", None) or "").strip().lower()
            if raw in ("legacy", "new"):
                configured = raw
        except Exception:  # noqa: BLE001 —— 读不到配置时按运行时值回答
            pass
        return {
            "ui": mounted,
            "configured": configured,
            "restart_required": configured != mounted,
            "dist": bool(_RUNTIME_UI.get("dist")),
        }

    return JSONResponse(await run_in_threadpool(_read))


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


async def mind_ships_save(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求体不是有效 JSON"}, status_code=400)
    ships = body.get("ships", [])
    rev = str(body.get("expect_revision", "") or "")
    return JSONResponse(await run_in_threadpool(data.save_mind_ships, name, ships, rev))


async def mind_ships_export(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.export_mind_ships, name))


async def mind_ships_import(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    return JSONResponse(await run_in_threadpool(data.import_mind_rows, name, body.get("rows", [])))


async def fleet_scan(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    return JSONResponse(await run_in_threadpool(
        data.fleet_scan_start, name,
        int(body.get("min_level", 95)), int(body.get("max_level", 125)),
    ))


async def fleet_sync(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.fleet_sync_mind, name))

async def fleet_tech_batch(request: Request) -> JSONResponse:
    name = request.path_params.get("name", "")
    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    return JSONResponse(await run_in_threadpool(
        data.batch_fleet_tech,
        name,
        body.get("gids", []),
        body.get("changes", {}),
        str(body.get("expected_revision", "") or ""),
        str(body.get("action", "save") or "save"),
    ))


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
        Route("/newapi/language", ui_language),
        Route("/newapi/language", ui_language_set, methods=["POST"]),
        Route("/newapi/version", version),
        Route("/newapi/instances", instances),
        Route("/newapi/dashboard/{name}", dashboard),
        Route("/newapi/overview/{name}", overview),
        Route("/newapi/log/{name}/tail", log_tail),
        Route("/newapi/header", header),
        Route("/newapi/menu/fixed", fixed_menu),
        Route("/newapi/stat/i18n", stat_i18n),
        Route("/newapi/menu", menu),
        Route("/newapi/stat/{name}/state", stat_data_state),
    Route("/newapi/stat/{name}/resource_delta", resource_delta),
        Route("/newapi/stat/{name}/commission_income", commission_income),
        Route("/newapi/stat/{name}/opsi_summary", opsi_summary),
        Route("/newapi/stat/{name}/research_stats", research_stats),
        Route("/newapi/stat/{name}/fleet_tech", fleet_tech),
        Route("/newapi/stat/{name}/mind", mind_calculator),
    Route("/newapi/mind/{name}/ships", mind_ships_save, methods=["POST"]),
    Route("/newapi/mind/{name}/export", mind_ships_export),
    Route("/newapi/mind/{name}/import", mind_ships_import, methods=["POST"]),
    Route("/newapi/fleet/{name}/scan", fleet_scan, methods=["POST"]),
    Route("/newapi/fleet/{name}/sync", fleet_sync, methods=["POST"]),
    Route("/newapi/fleet/{name}/batch", fleet_tech_batch, methods=["POST"]),
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
        Route("/newapi/stat/{name}/ap_chart", stat_ap_chart),
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
    # 记录**实际挂载**结果，供 /newapi/ui 回答「现在跑的是哪套」
    # （只认这里的值，不认 deploy.yaml —— 理由见 _RUNTIME_UI 的注释）。
    _RUNTIME_UI["dist"] = files is not None
    _RUNTIME_UI["mounted"] = "new" if (mount_root and files is not None) else "legacy"
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
