"""webapi 应用工厂与挂载入口。

设计约束（docs/new-ui/README.md §3）：
- 不新建进程、不替换 gui.py 的 asgi_app；提供 mount_webapi(app) 由现有应用调用。
- 静态产物 frontend/dist 挂在 /new/，SPA 回退 index.html。
- /newapi/* 为 REST 端点命名空间，避免与老 /api/* 冲突。
"""
from __future__ import annotations

import os
import re

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from module.logger import logger
from module.webapi import data

# 前端静态产物目录：**按优先级取第一个存在 index.html 的**。
#
# 为什么是列表而不是单个路径（2026-11-19 Docker 用户「切新 UI 自动回退」事故）：
#   Docker 镜像里没有 frontend/dist —— .gitignore/.dockerignore 都不带它，
#   Dockerfile 也不构建前端。于是 UI=new 时 _has_dist=False，
#   代码按设计降级回老 UI，用户看到的就是「切了没反应」。
#   现在镜像内会预构建一份放到 /opt/alas-frontend/dist（挂载路径之外，
#   否则会被 docker-compose 的 '.:/app/AzurPilot' 整个盖掉）。
#
# 顺序含义：
#   1. 仓库内 frontend/dist —— 本地 pnpm build 的产物，开发/自建镜像优先用它
#   2. ALAS_FRONTEND_DIST 环境变量 —— 给自定义部署一个显式覆盖口子
#   3. /opt/alas-frontend/dist —— Docker 镜像预构建的兜底
#
# 老 UI 与本地的行为完全不变：第一项存在就用第一项。
_DIST_CANDIDATES = [
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
    ),
    os.environ.get("ALAS_FRONTEND_DIST", ""),
    "/opt/alas-frontend/dist",
]


def _resolve_frontend_dist() -> str:
    """返回第一个含有 index.html 的候选目录；都没有则返回第一项（原仓库路径）。

    返回不存在的路径是有意的：调用方用 os.path.isdir 判断即可沿用原来的
    降级逻辑（_has_dist=False → 老 UI 继续服务 /）。
    """
    for candidate in _DIST_CANDIDATES:
        if candidate and os.path.isfile(os.path.join(candidate, "index.html")):
            return candidate
    return _DIST_CANDIDATES[0]


_FRONTEND_DIST = _resolve_frontend_dist()

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


async def ui_theme_set(request: Request) -> JSONResponse:
    """切换界面主题：写 deploy.yaml + 立刻换主题样式层。

    ⚠️ 为什么不能用老 UI 的 /api/deploy/settings：那个端点带
    is_local_request 门禁（launcher.py:24，要求 client.host 与 Host 头
    都是 127.0.0.1/localhost）。远控经中继转发时 Host 是 remurl 域名、
    client 也不是本机 → 直接 403「部署设置只允许从本机 WebUI 读取/保存」。
    实测：手机端经 remurl 访问 /api/deploy/settings 返回 403，于是主题
    按钮点了完全没反应（长卿 2026-11-17：「切换主题也切不动」）。

    /newapi/* 本来就为远控设计（远控下所有页面都要能用），且只改 Theme
    这一个字段，不暴露整份 deploy.yaml，安全边界与老 UI 不同但不更低：
    能打开远控页面的人本来就能改配置（配置页走的也是 /newapi/config）。
    """
    import json as _json

    from module.webui.deploy_settings import THEME_OPTIONS

    try:
        body = _json.loads(await request.body() or b"{}")
    except Exception:  # noqa: BLE001
        return JSONResponse(
            {"success": False, "error": "请求体不是有效 JSON"}, status_code=400
        )
    # 键名兼容两种写法：前端 onClick({ Theme: ... }) 传的是大写（沿用
    # deploy.yaml 的字段名），早先只读小写 theme 导致 400「不支持的主题: 」，
    # 而前端 fetch 非 2xx 直接 throw、被 catch 静默吞掉 —— 用户看到的就是
    # 「点主题完全没反应」（长卿 2026-11-17 二次反馈）。两种都收。
    target = str(body.get("theme") or body.get("Theme") or "").strip()
    if target not in THEME_OPTIONS:
        return JSONResponse(
            {"success": False, "error": f"不支持的主题: {target}"}, status_code=400
        )

    def _apply() -> dict:
        from module.webui.setting import State

        try:
            deploy = State.deploy_config
            deploy.read()
            deploy.Theme = target
            # 运行时也切过去，页面重载后 /newapi/theme-css 才会返回新主题层
            State.theme = target
            return {"success": True, "theme": target}
        except Exception as e:  # noqa: BLE001
            logger.error(f"[webapi] 切换主题失败: {e}")
            return {"success": False, "error": str(e)}

    return JSONResponse(await run_in_threadpool(_apply))


async def ui_labels(request: Request) -> JSONResponse:
    """GET /newapi/ui-labels —— 新 UI 固定文案（**无参数**）。

    为什么不用 /newapi/i18n?keys=…：远控中继转发会剥掉 query string，
    参数一丢就只剩空结果、前端只能回退中文（长卿 2026-11-17 截图：
    侧栏已是 Home，页面里「调度器/统计/运行中」还是中文）。
    无参接口不依赖 query，中继下也能完整拿到。
    """
    return JSONResponse(await run_in_threadpool(data.get_ui_labels))


async def ui_i18n(request: Request) -> JSONResponse:
    """GET /newapi/i18n?keys=Gui.Overview.Scheduler,Gui.Log.Fold

    新 UI 里硬编码的中文文案（总览页的调度器/统计/运行中/队列中/等待中/
    日志/自动滚动/截图预览/导出今日日志/折叠…）切语言后不会跟着变。
    这个端点把老 UI 的 t() 结果透出来，前端拿到后用译文渲染。
    """
    keys = request.query_params.get("keys") or ""
    return JSONResponse(await run_in_threadpool(data.get_i18n_batch, keys))


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


async def maintenance(request: Request) -> JSONResponse:
    """GET /newapi/maintenance/{name} — 维护等待横幅状态 + 已翻译文案。"""
    name = request.path_params.get("name", "")
    return JSONResponse(await run_in_threadpool(data.get_maintenance, name))


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


def _task_of(request) -> str:
    """取 task 参数（query 优先，其次路径段；"_" 视为空）。

    远控中继转发会剥掉 query string，所以参数还要能从路径段取到。
    """
    v = (
        request.query_params.get("task")
        or request.path_params.get("task")
        or ""
    ).strip()
    return "" if v == "_" else v


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
    # panel 优先取 query；远控中继会剥掉 query，所以再取路径段（/panels_html/{panel}）。
    # 见 Route 注册处的注释。
    panel = (
        request.query_params.get("panel")
        or request.path_params.get("panel")
        or "opsi"
    ).strip().lower()
    # month/task 也可能被中继剥掉 query，回退读路径段（见 Route 注册处注释）。
    # 前端在路径里用 "_" 占位空值（空路径段会被折叠掉），这里还原成空串。
    month = (
        request.query_params.get("month")
        or request.path_params.get("month")
        or ""
    ).strip()
    if month == "_":
        month = ""
    # 其余参数：query 优先，其次路径段（远控中继剥 query，参数走 /_p/ 段）。
    # 见 Route 注册处与 docs 注释。
    _pp = request.path_params

    # ⭐ 通用参数段解包（/panels_html/{panel}/_p/{k=v&k=v}）。
    # 远控中继会把 query string 整段剥掉，各面板参数又互不相同、还会继续增加，
    # 「每个参数加一段路由」不可持续。这里把参数整体编码进一个路径段，
    # 解包成 dict 后与 query / 单段路径参数一起作为三级回退。
    _p_params: dict = {}
    _raw_p = _pp.get("params") or ""
    if _raw_p and _raw_p != "_":
        from urllib.parse import parse_qsl as _parse_qsl

        try:
            for _k, _v in _parse_qsl(_raw_p, keep_blank_values=True):
                # 空值约定：前端用 "_" 占位，这里还原成空串
                _p_params[_k] = "" if _v == "_" else _v
        except Exception:  # noqa: BLE001 —— 解包失败按无参数处理
            _p_params = {}

    def _arg(key: str, default: str = "") -> str:
        # 优先级：query（直连）> 单段路径（老路由）> 通用参数段（远控保底）
        v = request.query_params.get(key)
        if v is None:
            v = _pp.get(key)
        if v is None:
            v = _p_params.get(key)
        v = (v or "").strip()
        return "" if v == "_" else v

    period = _arg("period")
    scope = _arg("scope")
    try:
        series = int(_arg("series") or 0)
    except (TypeError, ValueError):
        series = 0
    try:
        year = int(_arg("year") or 0)
    except (TypeError, ValueError):
        year = 0
    try:
        month_no = int(_arg("yearmonth") or 0)
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
            fn,
            name,
            month,
            _task_of(request),
        ))
    if panel == "research":
        return JSONResponse(
            await run_in_threadpool(fn, name, period, scope, series)
        )
    if panel == "fleet":
        # 科技列表有完整的筛选/搜索/排序/分页状态（老 UI 是实例属性驱动）
        #
        # ⚠️ 必须全部走 _arg()，不能直接读 request.query_params：
        # 远控中继会把 query string 整段剥掉，只有 _arg() 才有
        # 「query → 单段路径 → _p 段」三级回退。
        # 这里曾经直接读 query_params，导致 filters/q/page 等参数在远控下
        # 全部取默认值 —— 表现为「点驱逐、点阵营毫无反应，count 恒为 904」
        # （长卿 2026-11-19 远控实测）。
        return JSONResponse(
            await run_in_threadpool(
                fn, name,
                _arg("filters"),
                _arg("q"),
                _arg("scope") or "all",
                _arg("stage") or "all",
                _arg("sort") or "code",
                _arg("dir") or "1",
                int(_arg("page") or 0),
                _arg("pending") in ("1", "true"),
                _arg("edit") in ("1", "true"),
                _arg("bonuses") in ("1", "true"),
                _arg("extra") in ("1", "true"),
            )
        )
    if panel == "mind":
        # 心智计算器：清单表分页 + 批量编辑开关（老 UI 是实例属性 _mc_page/_mc_batch）
        # 同样必须走 _arg()：远控下 query 被剥，分页参数会丢 →「下一页点不动」。
        return JSONResponse(
            await run_in_threadpool(
                fn, name,
                int(_arg("page") or 0),
                _arg("edit") in ("1", "true"),
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

    参数同时支持 query 与路径段（远控中继剥 query）：
      /ap_chart/{view}/{month}
    """
    name = request.path_params.get("name", "")
    _pp = request.path_params

    def _a(key: str) -> str:
        v = request.query_params.get(key)
        if v is None:
            v = _pp.get(key)
        v = (v or "").strip()
        return "" if v == "_" else v

    # 兼容：path 段里没定义时返回空（老路由仍然走 query）
    view = _a("view")
    month = _a("month")
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
        # ⚠️ 必须串行，且 opsi 放第一个 —— 这是实测出来的，不要"优化"成并行。
        #
        # 进统计页时前端会**同时**发 warm 和当前页签的 panels_html（默认页签
        # 就是 opsi）。两者都要渲染 opsi，靠 _STAT_HTML_INFLIGHT 合并成一次计算，
        # 前台等这一次算完就能返回。
        #
        # 实测（前台 opsi 耗时 / warm 总耗时，三次取样）：
        #   串行     110ms 287ms 270ms  /  735ms 777ms 707ms
        #   并行(4)  313ms 561ms 803ms  /  603ms 693ms 961ms
        # 并行看起来 warm 快了点，但前台被另外 6 个面板抢 GIL + 磁盘 IO，
        # 反而慢 2~3 倍 —— 用户感知的是前台那块，所以串行才是对的。
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


async def background_upload_status(request: Request) -> JSONResponse:
    """GET /newapi/background/upload-status?upload_id=xxx —— 已收到哪些分片。

    断点续传用：客户端中断后重新上传时先问这里，只补缺失的分片，
    已传的部分不白费（长卿 2026-11-18：「服务器中断了不白搭？」）。
    """
    upload_id = (request.query_params.get("upload_id") or "").strip()
    if not upload_id:
        return JSONResponse({"success": False, "error": "缺少 upload_id"}, status_code=400)
    return JSONResponse(
        await run_in_threadpool(data.upload_chunk_received, upload_id)
    )


async def background_upload_chunk(request: Request) -> JSONResponse:
    """POST /newapi/background/upload-chunk —— 分片上传（绕开中继 1MB 限制）。

    remurl 等远控中继的 nginx 对 request body 限制约 1MB（实测 1000KB 通过、
    1024KB 返回 413 HTML），手机照片/视频整文件上传必然失败。前端按 512KB
    切片逐片发，这里收到最后一片后合并落盘。
    """
    import json as _json

    body, err = _parse_json_body(await request.body())
    if err:
        return JSONResponse({"success": False, "error": err}, status_code=400)
    return JSONResponse(
        await run_in_threadpool(
            data.upload_custom_background_chunk,
            str(body.get("upload_id", "")),
            int(body.get("index", 0)),
            int(body.get("total", 1)),
            str(body.get("filename", "")),
            str(body.get("data", "")),
        )
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

    # ⚠️ 切到 new 之前必须确认前端产物存在，否则写进配置也是白搭：
    # 重启后 module/webui/app.py 里 _mount_root = (UI==new and _has_dist) 为 False，
    # 静默降级回老 UI，用户看到的就是「点了切换、重启了、还是老 UI」，无任何提示。
    # 2026-11-19 实测：Windows 启动器用户与 Docker 用户都踩这个坑
    # （dist 曾长期不入库，git/CND 分发拿不到构建产物）。
    if target == "new" and not os.path.isdir(_FRONTEND_DIST):
        return JSONResponse(
            {
                "success": False,
                "error": (
                    "前端产物缺失，无法切换到新 UI。"
                    f"（{_FRONTEND_DIST} 不存在）"
                    " 请联系维护者更新，或自行在 frontend 目录执行 pnpm build。"
                ),
                "dist": False,
            },
            status_code=400,
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


async def event_calculator(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    force = request.query_params.get("refresh") == "1"
    return JSONResponse(
        await run_in_threadpool(data.get_event_calculator, name, force)
    )


async def event_calculator_refresh(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    return JSONResponse(await run_in_threadpool(data.get_event_calculator, name, True))


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



class B64InstanceNameMiddleware:
    """路径段里的实例名 base64url 解码（远控中继拒绝百分号编码 CJK）。

    铁证（2026-11-19 长卿实测）：
      直连  /newapi/config/%E5%B0%8F%E5%8F%B7/task/General → 200
      中继  /{peer}/newapi/config/%E5%B0%8F%E5%8F%B7/task/General →
            400 Invalid HTTP request received（隧道层拒绝编码多字节路径段）
      中继  /{peer}/newapi/config/alas/task/General → 200

    所有非 ASCII 实例名（中文等）在远控下全部打不开配置页 ——
    alas 这种 ASCII 名独活。

    修法：前端把 {name} 段编码成 base64url 前缀 b64_（纯 ASCII，中继放行）；
    本中间件在 ASGI 层把 b64_xxx 还原成中文实例名，handler 无感知。
    """

    _B64_NAME_RE = re.compile(r"(?<=/)(b64_[A-Za-z0-9_-]+)(?=/)")

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path") or "/"
            if "b64_" in path:
                import base64 as _b64

                def _decode(m: "re.Match[str]") -> str:
                    try:
                        raw = _b64.urlsafe_b64decode(m.group(1)[4:] + "==")
                        return raw.decode("utf-8")
                    except Exception:
                        return m.group(1)

                new_path = self._B64_NAME_RE.sub(_decode, path)
                if new_path != path:
                    scope["path"] = new_path
                    # raw_path 也要同步（uvicorn 用 raw_path 重新解析 query）
                    if "raw_path" in scope:
                        scope["raw_path"] = new_path.encode("utf-8") + (
                            scope["raw_path"].split(b"?")[1:] and b"?" + scope["raw_path"].split(b"?", 1)[1] or b""
                        )
        return await self.app(scope, receive, send)

class SpaStaticFiles(StaticFiles):
    """SPA 静态服务：index.html no-cache（并按请求路径重写资源前缀），
    hash 资源 immutable 长缓存。

    ## 为什么要在服务端重写 index.html 的资源前缀

    长卿 2026-11-17：「https://remurl.a1.nanoda.work/89982203008c27b2?app=index
    他这个完全就是加载不出来……还有其他人的远控也是这样子的，你不能做我
    一个人专门的优化，你得做全部的」。

    远控中继（remurl.a1.nanoda.work 等）把每个客户端挂在 /{peer_id} 下，
    且**只认带 peer 前缀的路径**。实测：
      /89982203008c27b2?app=index          → 200 页面
      /89982203008c27b2/assets/index-xxx.js → 200 真实 JS
      /assets/index-xxx.js                  → 502
      /newapi/ui                            → 502

    而入口页 URL 是 /{peer_id}?app=index（末尾没有斜杠），浏览器把最后一段
    当文件名，于是相对路径 ./assets/x.js 会解析成 /assets/x.js —— 依然 502。
    所以**相对路径的 vite base 也救不了**，必须让 HTML 里的资源链接显式带上
    /{peer_id}/ 前缀。而 peer_id 只有请求进来时才知道，只能在服务端渲染时改写。

    做法：返回 index.html 时，若请求路径带一个「像中继 peer_id」的首段
    （16 位十六进制串，或 /p2p/<id> 形态），就把 HTML 里所有 /assets/、/newapi/、
    /static/、/pywebio_static/、/api/ 引用前缀成 /{peer_id}/…。
    前端 api/client.ts 与 main.tsx 的 fetch 劫持负责页面跑起来之后的请求，
    两者配合覆盖中继的通用形态（不针对某个用户）。

    ## 兜底：剥前缀寻径 + SPA 回退
    直连场景下若请求意外带了前缀，逐段剥掉重试（最多 4 段）；都失败则回退
    index.html 交给前端路由。
    """

    #: 需要加前缀的同源引用前缀（vite 产物 + 手工 link + API 前缀）
    _PREFIXABLE = ("/assets/", "/newapi/", "/static/", "/pywebio_static/", "/api/")

    @staticmethod
    def _is_behind_relay(header_pairs) -> bool:
        """请求是否来自远控中继（而非浏览器直连）。

        中继转发时**不提供任何能还原 peer_id 的信息**（实测 echo 端点只有 CDN
        与 UA 头，路径前缀已被剥掉），所以只能判定「是不是经中继而来」。判定后
        返回自举页，由浏览器自己从 location.pathname 读出前缀 —— 浏览器是唯一
        知道前缀的一方。

        特征（实测 remurl.a1.nanoda.work / TencentEdgeOne）：
          cdn-loop: TencentEdgeOne; loops=N
          eo-connecting-ip / eo-log-uuid
          cf-connecting-ip（Cloudflare）
        """
        names = set()
        for item in header_pairs or []:
            try:
                names.add(item[0].decode("latin-1").lower())
            except Exception:  # noqa: BLE001
                continue
        return bool(
            names
            & {
                "cdn-loop",
                "eo-connecting-ip",
                "eo-log-uuid",
                "cf-connecting-ip",
            }
        )

    @staticmethod
    def _relay_prefix(path: str, referer: str = "") -> str:
        """从请求路径推断中继前缀，没有则返回空串。

        只认两种形态，避免把真实业务路径误当前缀：

          - /{hex16}   （remurl 一类，16 位十六进制）
          - /p2p/{id}  （内置 WebRTC 隧道）
        """
        # 优先从 Referer 里取中继前缀。
        #
        # ⚠️ 实测（2026-11-17）：中继转发到后端时**会剥掉 /{peer} 前缀**，
        # 后端收到的 path 是 "/"（echo 端点验证：/{peer}/newapi/relay-echo
        # 到达后端时变成 /newapi/relay-echo），所以光看 path 永远拿不到 peer_id。
        # 但 Referer 是透传的，且带着完整原始路径：
        #   referer = "https://remurl.a1.nanoda.work/89982203008c27b2?app=index"
        # 从中能还原 /{peer} 前缀。这样对所有中继用户都生效（不是个案特调）。
        if referer:
            try:
                from urllib.parse import urlsplit as _urlsplit

                _segs = [p for p in _urlsplit(referer).path.replace("\\", "/").split("/") if p]
                if _segs:
                    if _segs[0] == "p2p" and len(_segs) >= 2:
                        return "/p2p/" + _segs[1]
                    if (
                        len(_segs[0]) >= 8
                        and all(c in "0123456789abcdefABCDEF" for c in _segs[0])
                    ):
                        return "/" + _segs[0]
            except Exception:  # noqa: BLE001
                pass

        segs = [p for p in path.replace("\\", "/").split("/") if p]
        if not segs:
            return ""
        if segs[0] == "p2p" and len(segs) >= 2:
            return "/p2p/" + segs[1]
        if (
            len(segs) == 1
            and len(segs[0]) >= 8
            and all(c in "0123456789abcdefABCDEF" for c in segs[0])
        ):
            return "/" + segs[0]
        return ""

    def _inject_prefix(self, html: str, prefix: str) -> str:
        """给 HTML 里的同源引用加上中继前缀。

        只处理 HTML 里**静态写出**的引用。运行时动态请求（fetch/EventSource/
        WebSocket）由前端 api/client.ts + main.tsx 的劫持补前缀。
        """
        if not prefix:
            return html
        out = html
        for frag in self._PREFIXABLE:
            out = out.replace('"' + frag, '"' + prefix + frag)
            out = out.replace("'" + frag, "'" + prefix + frag)
            rel = "./" + frag.lstrip("/")
            out = out.replace('"' + rel, '"' + prefix + frag)
            out = out.replace("'" + rel, "'" + prefix + frag)
        return out

    def _serve_index_for_relay(self, request):
        """远控中继场景的入口页：302 到带尾斜杠的 /{peer}/，让相对路径天然正确。

        为什么不能靠服务端写死前缀：中继转发到后端时既剥掉 /{peer} 前缀、
        也不提供任何能还原它的请求头（诊断端点实测只有 CDN 与 UA 头）。
        后端无从得知 peer_id，唯一知道的是浏览器（location.pathname）。

        ## 为什么用重定向而不是 <base>（长卿 2026-11-17 23:17「又改什么了
        我怎么打不开了」实测复现）

        <base> 方案下，Chrome 的**预加载扫描器**会在文档流里遇到 <base> 之前，
        就按**站点根**预先解析 ./assets/index-xxx.js、./static/… 并发请求：
          /assets/index-xxx.js   → 502（中继只认带 /{peer} 前缀的路径）
          /static/…/*.css        → 404
        真正的带前缀请求随后才发。实测一次加载里有 11 个这类失败请求；更糟的是
        预载的 502 响应会**占据同名资源的缓存位**，运气差时主 JS 拿到 502、
        React 永不挂载 —— 就是「打不开」的白屏。

        改用 302：入口 /{peer}?app=index → /{peer}/?app=index。文档 URL 自带
        尾斜杠后，./assets/… 天然解析成 /{peer}/assets/…，从第一字节起就
        没有「按站点根解析」的阶段，一个失败请求都不会产生。

        结构契约：返回**原版** index.html（结构与 id 原样保留），只把
        /static/…、/pywebio_static/… 的绝对引用改成相对 ./…。
        main.tsx 靠 #alas-layout-css / #alas-bs-theme 切移动端与主题，
        自拼 HTML 会把这些 id 和 CSS 顺序丢掉（已踩过：样式全失效）。
        """
        from starlette.responses import RedirectResponse

        path = request.url.path
        from starlette.responses import HTMLResponse

        try:
            raw = open(os.path.join(_FRONTEND_DIST, "index.html"), encoding="utf-8").read()
        except OSError as e:
            logger.warning(f"[webapi] 读取 index.html 失败: {e}")
            raise HTTPException(status_code=500, detail="index.html unreadable")

        for frag in ("/static/", "/pywebio_static/"):
            raw = raw.replace('"' + frag, '".' + frag)
            raw = raw.replace("'" + frag, "'." + frag)

        # 浏览器是唯一知道完整入口 URL 的一方（中继剥掉了 path 前缀，
        # 后端看到的 path 通常是 "/"，无法服务端 302）。所以在任何资源
        # 请求发出之前，用同步脚本把地址归一到 /{peer}/：
        #   - 尾斜杠让 ./assets/… 天然解析到 /{peer}/assets/…
        #   - 在 <head> 最前面同步执行 → 预加载扫描器拿到的已是归一后的 URL，
        #     不会出现 <base> 方案那种「按站点根预载 → 502/404 占缓存位」的问题
        boot = (
            "<script>(function(){var p=location.pathname;"
            + "if(p.charAt(p.length-1)==='/')return;"
            + "if(p.lastIndexOf('/',0)===0&&p.split('/').filter(Boolean).length===0)return;"
            + "var segs=p.split('/').filter(Boolean);"
            + "var RESERVED=['new','assets','newapi','static','pywebio_static','api'];"
            + "if(!segs.length||RESERVED.indexOf(segs[0])>=0)return;"
            + "var ok=(segs[0]==='p2p'&&segs[1])||/^[0-9a-f]{8,64}$/i.test(segs[0]);"
            + "if(!ok)return;"
            + "location.replace(p+'/'+location.search);"
            + "})();</script>"
        )
        marker = "<head>"
        raw = raw.replace(marker, marker + boot, 1) if marker in raw else boot + raw
        return HTMLResponse(raw, headers={"Cache-Control": "no-cache"})

    async def get_response(self, path: str, scope):
        # 从 ASGI scope 里拿到**原始请求路径**（含中继前缀）
        raw_path = str(scope.get("path") or "/")
        # Referer 里带中继前缀（中继转发会剥 path 但透传 Referer），见 _relay_prefix 注释
        referer = ""
        for k, v in (scope.get("headers") or []):
            if k.lower() == b"referer":
                referer = v.decode("latin-1")
                break
        prefix = self._relay_prefix(raw_path, referer)
        behind_relay = self._is_behind_relay(scope.get("headers") or [])

        # 中继场景且拿不到明确前缀：302 跳到带尾斜杠的 /{peer}/，让相对路径
        # 天然解析正确（中继不提供 peer_id，后端无从得知，见 _serve_index_for_relay）。
        # 只对「页面入口」生效，资源请求仍走正常路径（浏览器补完前缀后自然命中）。
        if behind_relay and not prefix and not raw_path.startswith(
            ("/assets/", "/static/", "/pywebio_static/", "/newapi/", "/api/")
        ):
            # get_response 的签名只有 (path, scope)，没有 request；
            # _serve_index_for_relay 需要 url.path / url.query 来做 302，
            # 从 scope 重建一个最小兼容对象（/ 命中此分支时 scope['path']='/'，
            # 此前引用未定义的 request 直接 NameError → 500，
            # 长卿 2026-11-17 23:17「我怎么打不开了」就是这个）。
            from starlette.requests import Request as _Req

            return self._serve_index_for_relay(_Req(scope))

        # ⚠️ 关键：index.html 必须**自己读文件并构造 HTMLResponse**，
        # 不能指望改 FileResponse —— 它没有 .body 属性（流式发送），
        # 第一版就是踩了这个坑：判定 body is None 直接 return，前缀从未注入。
        if prefix:
            try:
                resp = await super().get_response(path, scope)
            except HTTPException as e:
                if e.status_code != 404:
                    raise
                resp = None
            # 命中 index.html（含 html=True 对无扩展名路径的目录回退）就重写
            if resp is not None and self._is_index(resp):
                return self._serve_index_with_prefix(prefix)
            if resp is not None:
                return resp

        try:
            resp = await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            parts = [p for p in path.replace("\\", "/").split("/") if p]
            for cut in range(1, min(5, len(parts))):
                candidate = "/".join(parts[cut:])
                if not candidate:
                    return self._serve_index_with_prefix(prefix)
                try:
                    hit = await super().get_response(candidate, scope)
                except HTTPException:
                    continue
                if self._is_index(hit):
                    return self._serve_index_with_prefix(prefix)
                return hit
            return self._serve_index_with_prefix(prefix)
        if self._is_index(resp):
            return self._serve_index_with_prefix(prefix)
        return resp

    @staticmethod
    def _is_index(resp) -> bool:
        """响应是否指向 index.html（FileResponse 的 path 以 index.html 结尾）。"""
        try:
            p = str(getattr(resp, "path", "") or "")
            return p.replace("\\", "/").endswith("index.html")
        except Exception:  # noqa: BLE001
            return False

    def _serve_index_with_prefix(self, prefix: str):
        """读 disc 上的 index.html，注入中继前缀后返回 HTMLResponse。

        前缀存进 <base href>（若有）与所有同源引用；body 与 Content-Length
        由 HTMLResponse 自己算，不会出现头部不一致导致浏览器挂起。
        """
        from starlette.responses import HTMLResponse

        html_path = os.path.join(_FRONTEND_DIST, "index.html")
        try:
            html = open(html_path, encoding="utf-8").read()
        except OSError as e:
            logger.warning(f"[webapi] 读取 index.html 失败: {e}")
            raise HTTPException(status_code=500, detail="index.html unreadable")
        if prefix:
            html = self._inject_prefix(html, prefix)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        resp_path = str(getattr(response, "path", ""))
        if resp_path.endswith("index.html"):
            response.headers["Cache-Control"] = "no-cache"
        elif "/assets/" in resp_path.replace("\\", "/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class RelayPrefixMiddleware:
    """剥掉远控中继前缀，让带前缀的请求命中真实路由。

    远控中继（remurl.a1.nanoda.work 等）把每个客户端挂在 /{peer_id} 下。
    三件事分开处理：

      1. **入口 HTML**：由 SpaStaticFiles 注入 /{peer_id}/ 前缀（见那里注释）
      2. **页面内的 XHR/SSE/WS**：前端加请求前缀（api/client.ts + main.tsx）
      3. **本中间件**：中继转发到后端时，有的中继会去掉前缀、有的不会。没去掉时
         后端要能自己剥掉再匹配路由，否则 /{peer}/newapi/ui 会落进 StaticFiles
         的 SPA 回退、返回一整页 HTML（前端 fetch 解析 JSON 直接失败）。

    在 ASGI 层把 scope["path"] 换成剥掉前缀后的路径，路由按正常方式匹配。
    """

    #: 中继前缀形态：/{hex16} 或 /p2p/{id}
    _RELAY_RE = re.compile(r"^/([0-9a-fA-F]{8,64}|p2p/[^/]+)(?=/)")

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path") or "/"
            m = self._RELAY_RE.match(path)
            if m:
                rest = path[m.end():] or "/"
                # 只剥后端路由/共享静态前缀；SPA 自己的 /{peer}/assets/ 交给
                # StaticFiles 的顺序回退与 index 注入处理。
                if rest.startswith(("/newapi/", "/api/", "/pywebio_static/", "/static/")):
                    scope = dict(scope)
                    scope["path"] = rest
                    # 不能改 root_path：Starlette 的 Mount 用它拼子路径，改写后
                    # /static/... 会拼成 /{peer}/static/... 再匹配失败（实测 404）。
        await self.app(scope, receive, send)


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
        Route("/newapi/i18n", ui_i18n),
        # 无参数版本：远控中继剥 query，这个才靠得住
        Route("/newapi/ui-labels", ui_labels),
        Route("/newapi/language", ui_language_set, methods=["POST"]),
        # 远控可用（无 is_local_request 门禁），见 ui_theme_set 注释
        Route("/newapi/theme", ui_theme_set, methods=["POST"]),
        Route("/newapi/version", version),
        Route("/newapi/instances", instances),
        Route("/newapi/dashboard/{name}", dashboard),
        Route("/newapi/overview/{name}", overview),
        # 维护等待横幅（老 UI 概览页那条「服务器维护中 · cn_android-12 · …」）。
        # 数据与老 UI 同源：实例写 log/<name>.maintenance.json，这里读并翻译文案。
        Route("/newapi/maintenance/{name}", maintenance),
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
        Route("/newapi/event-calculator/{name}", event_calculator),
        Route("/newapi/event-calculator/{name}/refresh", event_calculator_refresh, methods=["POST"]),
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
        # ⚠️ 同时注册「query 版」与「path 版」两条路由：
        # 远控中继（remurl 等）转发时会**剥掉 query string**（实测：经中继请求
        # ?panel=ship_exp 到达后端时 panel 为空 → 默认走 opsi，于是「舰船经验」位置
        # 渲染出净赚体力卡、页面上出现两份净赚体力）。
        # path 版把 panel 放进路径段，中继不剥路径，所以远控下也能正确分发。
        Route("/newapi/stat/{name}/panels_html", stat_panels_html),
        Route("/newapi/stat/{name}/panels_html/{panel}", stat_panels_html),
        # ⭐ 通用「全参数路径」通道（必须注册在 {month} 路由**之前**）。
        #
        # 为什么单独开一条：远控中继会把 query string 整段剥掉，而各面板的参数
        # 集互不相同且会继续增加（科研 series、科技 filters/q/page/edit…、
        # 资源 period）。每加一个参数就加一段路由不可持续。
        # 这里把参数整体编码进**一个**路径段（k=v&k=v），中继不剥路径。
        #
        # ⚠️ 顺序敏感：Starlette 按注册顺序匹配，
        #   "/{panel}/{month}/{task}" 会把 "_p/xxx" 里的 "_p" 当成 {month} 吃掉，
        #   导致参数永远解析不到（实测：series 恒为 9、period 恒为 month）。
        #   所以这条必须排在 {month} 之前。
        Route(
            "/newapi/stat/{name}/panels_html/{panel}/_p/{params:path}",
            stat_panels_html,
        ),
        # path 版：panel / month / task 都放进路径段。远控中继会剥掉 query
        # string（实测 ?panel=ship_exp 到达后端时为空），但不动路径。
        Route(
            "/newapi/stat/{name}/panels_html/{panel}/{month}/{task}",
            stat_panels_html,
        ),
        # 全参数路径版：panel/month/task/period/scope/year/yearmonth 全在路径里。
        # 远控中继剥 query，多参数面板（科研/委托/科技/资源）靠这条保住状态。
        Route(
            "/newapi/stat/{name}/panels_html/{panel}/{month}/{task}/{period}"
            "/{scope}/{year}/{yearmonth}",
            stat_panels_html,
        ),
        # 第 8 段 series：科研的期数。
        # 中继会剥掉 query string，series 只放在 ?series= 里时远控下永远丢失
        # →「第9期/第5期」点了没反应（长卿 2026-11-19 报的按钮点不动之一）。
        Route(
            "/newapi/stat/{name}/panels_html/{panel}/{month}/{task}/{period}"
            "/{scope}/{year}/{yearmonth}/{series}",
            stat_panels_html,
        ),

        Route("/newapi/stat/{name}/panels_warm", stat_panels_warm),
        Route("/newapi/stat/{name}/months", stat_months),
        Route("/newapi/stat/{name}/ap_chart", stat_ap_chart),
        # 体力图参数的路径版：远控中继剥 query，view（折线/按日/按月）
        # 与 month 会全丢 → 图表切不动（长卿 2026-11-19）。
        Route("/newapi/stat/{name}/ap_chart/{view}/{month}", stat_ap_chart),
        Route("/newapi/background", background_setting),
        Route("/newapi/background/mode", background_mode, methods=["POST"]),
        Route("/newapi/background/media", background_media, methods=["POST"]),
        Route("/newapi/background/upload", background_upload, methods=["POST"]),
        # 分片上传：远控中继 body 上限 ~1MB，整文件上传必然 413
        Route("/newapi/background/upload-chunk", background_upload_chunk, methods=["POST"]),
        Route("/newapi/background/upload-status", background_upload_status),
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
