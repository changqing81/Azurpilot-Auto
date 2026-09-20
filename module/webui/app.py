"""AzurPilot WebUI 的兼容入口和 ASGI 应用工厂。

提供 WebUI 的主应用类，通过多个 Mixin 组合实现各功能页面：
仪表盘（Dashboard）、开发者菜单、开发者设置、开发者工具、
版本更新、活动工具等。同时提供 ASGI 应用创建和路由注册。

该模块是 WebUI 的顶层入口，被 gui.py 启动时引用。

设计上采用延迟加载策略：module-level 仅保留静态资源哈希计算，
所有重依赖（pywebio、mixin 等）延迟到 app() 被调用时才加载。
MCP 子应用进一步推迟到首次 /mcp 请求时加载。
"""

from hashlib import sha256
from pathlib import Path
from threading import Lock, Thread

from module.webui.app_dashboard import DashboardMixin
from module.webui.app_dependencies import (
    Dict,
    Frame,
    IS_ON_PHONE_CLOUD,
    List,
    PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE,
    ProcessManager,
    RESTRICTED_DEVICE_IDS,
    RESTRICTED_DEVICE_MESSAGE,
    RichLog,
    State,
    argparse,
    asgi_app,
    get_device_id,
    get_localstorage_values,
    info,
    lang,
    load_webui_styles,
    local,
    logger,
    login,
    popup,
    run_js,
    set_env,
    task_handler,
    time,
    updater,
    webconfig,
)
from module.webui.app_developer_menu import DeveloperMenuMixin
from module.webui.app_developer_settings import DeveloperSettingsMixin
from module.webui.app_developer_tools import DeveloperToolsMixin
from module.webui.app_developer_update import DeveloperUpdateMixin
from module.webui.app_event_tools import EventToolsMixin
from module.webui.app_fleet_management import FleetManagementMixin
from module.webui.app_helpers import (
    WEBUI_AUTO_PASSWORD_FILE,
    build_muted_notice,
    build_recommendation_box,
    build_simple_table,
    build_title_block,
    ensure_public_webui_password,
    generate_webui_password,
    is_demo_mode,
    is_public_webui_host,
    is_webui_password_set,
    read_webapp_template,
    timedelta_to_text,
)
from module.webui.app_home import HomeMixin
from module.webui.app_instances import InstanceMixin
from module.webui.app_lifecycle import clearup, startup
from module.webui.app_manage import app_manage
from module.webui.app_overview import OverviewMixin
from module.webui.app_shell import (
    AppShellMixin,
    normalize_webui_theme,
    pywebio_theme_for,
)
from module.webui.app_stat_action_point import ActionPointStatisticsMixin
from module.webui.app_stat_action_point_toolbar import ActionPointToolbarMixin
from module.webui.app_stat_commission import CommissionIncomeStatisticsMixin
from module.webui.app_stat_delta import ResourceDeltaStatisticsMixin
from module.webui.app_stat_opsi import OpsiStatisticsMixin
from module.webui.app_stat_opsi_export import OpsiExportMixin
from module.webui.app_stat_ship import ShipExperienceStatisticsMixin
from module.webui.app_statistics_page import StatisticsPageMixin
from module.webui.app_task_config import TaskConfigMixin
from module.webui.fastapi import (
    INITIAL_LOADING_STYLE_MARKER,
    WEBSOCKET_RECONNECT_TIMEOUT,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _LazyMCPApp:
    """延迟加载 mcp_server_sse 的 ASGI 包装器。

    MCP 子应用在 app() 构建时不导入，仅当首次收到 /mcp 请求时才加载。
    """

    def __init__(self):
        self._app = None
        self._lock = Lock()

    async def __call__(self, scope, receive, send):
        if self._app is None:
            with self._lock:
                if self._app is None:
                    from mcp_server_sse import app as mcp_app
                    self._app = mcp_app
        await self._app(scope, receive, send)


def _versioned_static_asset(relative_path: str) -> str:
    """返回带内容哈希的相对静态资源地址。"""
    digest = sha256((PROJECT_ROOT / relative_path).read_bytes()).hexdigest()[:12]
    return f"static/{relative_path}?v={digest}"


INITIAL_WEBUI_CSS = _versioned_static_asset("assets/gui/css/alas.css")
WEBUI_THEME_STYLE_NAMES = {
    "dark": ("dark-alas",),
    "advanced_material": ("advanced-material-alas",),
    "dark_advanced_material": (
        "advanced-material-alas",
        "dark-advanced-material-overrides-alas",
    ),
    # 透明主题：复用高级材质的布局规则，再叠加透明化覆盖层
    "transparent": (
        "advanced-material-alas",
        "transparent-alas",
    ),
}
INITIAL_LOADING_JS = """
(function () {
    var observer = null;
    function hasContent() {
        var root = document.getElementById("pywebio-scope-ROOT");
        var inputs = document.getElementById("input-cards");
        return (root && root.firstElementChild)
            || (inputs && inputs.firstElementChild)
            || document.querySelector(".modal");
    }
    function markReady() {
        if (!hasContent()) return;
        document.documentElement.classList.add("alas-initial-ready");
        if (observer) observer.disconnect();
    }
    observer = new MutationObserver(markReady);
    observer.observe(document.body, {childList: true, subtree: true});
    markReady();
})();
(function () {
    // —— 远控断线看门狗：只在"页面自身的重连循环已经死掉"时才整页刷新 ——
    //
    // 服务端已开启 PyWebIO 会话重连（fastapi.py::WEBSOCKET_RECONNECT_TIMEOUT）：
    // WS 断开后前端会自动用同一个 session id 重连，服务端保留会话并补发漏掉的
    // 消息，页面状态不丢、也不需要刷新。所以这里的职责从"断线就刷新"改成：
    //  1) 只要有连接在建/已连，或最近 ATTEMPT_MS 内有过新的建连尝试（说明 pywebio
    //     的重连循环还活着），就一直等、绝不刷新 —— 远控抖动时页面原地恢复；
    //  2) 只有下面两种情况才判定页面没救了、按退避刷新：a) 连续静默超过
    //     ATTEMPT_MS（既无连接也无建连尝试 → 页面假死）；b) 重连循环空转但
    //     连续 MAX_DOWN_MS 都没能真正连上（隧道彻底不可达，转圈也没意义）。
    //     刷新本身也有上限：单次故障最多 MAX_RELOADS 次快重试（1s/2s/4s），
    //     之后转 SLOW_RETRY_MS 慢重试并常驻提示条，不会无限刷新；
    //  3) 刷新计数只在"本次连接稳定存活满 STABLE_MS"后才清零（旧实现一收到 ws
    //     open 就清零，抖动链路上"几次就停手"的上限形同虚设）；
    //  4) 与 assets/gui/js/alas-utils.js 的 on_session_close 共用同一计数入口
    //     （window.__alasReconnect），避免两条刷新链各自计数互相叠加；
    //  5) 想彻底不刷新：控制台执行 window.reload = 0，或
    //     localStorage.setItem('alas_auto_reload', '0')（后者重启浏览器仍生效）。
    var NativeWebSocket = window.WebSocket;
    var KEY = 'alas_watchdog_reload';
    var OPEN_KEY = 'alas_watchdog_open_at';
    var POLL_MS = 5000;          // 复查间隔
    var ATTEMPT_MS = 45000;      // 连续静默超过该时长才认为重连循环已死
    var MAX_DOWN_MS = 300000;    // 持续连不上多久才兜底刷新一次（5 分钟）
    var STABLE_MS = 60000;       // 连续在线满 60 秒才算一次健康连接
    var WINDOW_MS = 600000;      // 距上次自动刷新超过 10 分钟视为新故障
    var MAX_RELOADS = 3;         // 单次故障内最多自动刷新次数
    var SLOW_RETRY_MS = 120000;  // 超出次数上限后的慢重试间隔
    var sockets = [];
    var lastAttemptTs = Date.now();   // 最近一次新建 WebSocket 的时间
    var downSince = 0;                // 本轮"连不上"的起点（真正连上时清零）
    var scheduled = false;       // 已安排刷新，避免多条刷新链重复计时
    var watching = false;        // 已在复查循环里

    // 本页的"首个 WS 连上时刻"记号，每次页面加载重新计时
    try { sessionStorage.setItem(OPEN_KEY, '0'); } catch (e) {}

    function backoffMs(tries) { return Math.min(15000, 1000 * Math.pow(2, tries)); }
    function autoReloadDisabled() {
        if (window.reload === 0) return true;
        try { return localStorage.getItem('alas_auto_reload') === '0'; } catch (e) { return false; }
    }
    function readOpenAt() {
        try { return parseInt(sessionStorage.getItem(OPEN_KEY) || '0', 10) || 0; } catch (e) { return 0; }
    }
    function readRecord() {
        var rec = null;
        try { rec = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch (e) { rec = null; }
        if (!rec || typeof rec !== 'object' || typeof rec.t !== 'number') rec = { t: 0, ts: 0 };
        if (Date.now() - (rec.ts || 0) > WINDOW_MS) rec.t = 0;   // 超出窗口期，按新故障重算
        return rec;
    }
    function writeRecord(rec) {
        try { sessionStorage.setItem(KEY, JSON.stringify(rec)); } catch (e) {}
    }
    function hideBanner() {
        var bar = document.getElementById('alas-offline-banner');
        if (bar && bar.parentNode) bar.parentNode.removeChild(bar);
    }
    function setBanner(text) {
        var bar = document.getElementById('alas-offline-banner');
        if (bar) {
            if (bar.firstChild) bar.firstChild.textContent = text;
            return;
        }
        var host = document.body || document.documentElement;
        if (!host) return;
        bar = document.createElement('div');
        bar.id = 'alas-offline-banner';
        bar.style.cssText = 'position:fixed;left:50%;bottom:18px;transform:translateX(-50%);'
            + 'z-index:2147483600;display:flex;align-items:center;gap:12px;padding:10px 16px;'
            + 'border-radius:8px;font-size:13px;line-height:1.4;background:rgba(32,34,37,.94);'
            + 'color:#f2f3f5;box-shadow:0 6px 24px rgba(0,0,0,.45)';
        var label = document.createElement('span');
        label.textContent = text;
        var button = document.createElement('button');
        button.textContent = '立即重载';
        button.style.cssText = 'padding:4px 12px;border-radius:6px;border:1px solid #8b89d8;'
            + 'background:transparent;color:#8b89d8;cursor:pointer;font-size:13px';
        button.onclick = function () {
            try { sessionStorage.removeItem(KEY); } catch (e) {}
            location.reload();
        };
        bar.appendChild(label);
        bar.appendChild(button);
        host.appendChild(bar);
    }
    function scheduleReload() {
        if (autoReloadDisabled()) {
            setBanner('与服务器的连接已断开，自动重载已关闭');
            return;
        }
        if (scheduled) return;
        scheduled = true;
        var rec = readRecord();
        if (rec.t >= MAX_RELOADS) {
            rec.ts = Date.now();
            writeRecord(rec);
            setBanner('重连失败，已切换为慢速重试');
            setTimeout(function () { location.reload(); }, SLOW_RETRY_MS);
            return;
        }
        var delay = backoffMs(rec.t);
        rec.t += 1; rec.ts = Date.now();
        writeRecord(rec);
        setBanner('重连失败，正在重新加载页面…');
        setTimeout(function () { location.reload(); }, delay);
    }
    function anyOpen() {
        return sockets.some(function (s) { return s.readyState === 1; });
    }
    function armWatch() {
        if (watching || scheduled) return;
        watching = true;
        setTimeout(watchConnection, POLL_MS);
    }
    function watchConnection() {
        watching = false;
        if (scheduled) return;
        if (anyOpen()) {                 // 已经连上：什么都不做
            downSince = 0;
            hideBanner();
            return;
        }
        if (!downSince) downSince = Date.now();
        if (Date.now() - lastAttemptTs >= ATTEMPT_MS) {
            scheduleReload();            // 连建连尝试都没有 → 页面假死
            return;
        }
        if (Date.now() - downSince >= MAX_DOWN_MS) {
            scheduleReload();            // 重连循环空转太久（隧道彻底不可达）
            return;
        }
        // 页面自己的重连循环还活着（pywebio 约每 5 秒重连一次）→ 继续等，不刷新
        setBanner('与服务器的连接中断，正在自动重连…');
        armWatch();
    }
    function markOpen() {
        downSince = 0;
        if (!readOpenAt()) {
            try { sessionStorage.setItem(OPEN_KEY, String(Date.now())); } catch (e) {}
        }
    }
    function WrappedWebSocket(url, protocols) {
        lastAttemptTs = Date.now();
        var ws = protocols === undefined
            ? new NativeWebSocket(url)
            : new NativeWebSocket(url, protocols);
        // 只保留活跃连接，重连次数多时不让数组无限增长
        sockets = sockets.filter(function (s) {
            return s.readyState === 0 || s.readyState === 1;
        });
        sockets.push(ws);
        ws.addEventListener('open', function () {
            markOpen();
            hideBanner();
        });
        ws.addEventListener('close', function () {
            // 本次连接稳定存活过 STABLE_MS → 上一次故障已结束，计数清零重来
            var openAt = readOpenAt();
            if (openAt && Date.now() - openAt >= STABLE_MS) {
                try { sessionStorage.removeItem(KEY); } catch (e) {}
            }
            armWatch();
        });
        return ws;
    }
    WrappedWebSocket.prototype = NativeWebSocket.prototype;
    WrappedWebSocket.CONNECTING = NativeWebSocket.CONNECTING;
    WrappedWebSocket.OPEN = NativeWebSocket.OPEN;
    WrappedWebSocket.CLOSING = NativeWebSocket.CLOSING;
    WrappedWebSocket.CLOSED = NativeWebSocket.CLOSED;
    window.WebSocket = WrappedWebSocket;
    // alas-utils.js 等独立脚本复用同一套退避/限次逻辑，避免多条刷新链各自计数
    window.__alasReconnect = { schedule: scheduleReload, banner: setBanner };
})();
"""


def _initial_style_names(theme: str) -> tuple[str, ...]:
    """返回首屏必须通过 HTML 预加载的样式名称。"""
    return (
        "alas",
        "entry-alas",
        *WEBUI_THEME_STYLE_NAMES.get(theme, ("light-alas",)),
    )


def _initial_loading_css(theme: str) -> str:
    """生成在 PyWebIO 首条可见输出前展示的轻量加载骨架。"""
    if theme in ("dark", "dark_advanced_material"):
        background = "#202225"
        foreground = "#f2f3f5"
        accent = "#8b89d8"
        track = "rgba(139, 137, 216, .22)"
    else:
        background = "#f4f5f7"
        foreground = "#34343d"
        accent = "#4e4c97"
        track = "rgba(78, 76, 151, .22)"
    return f"""
/* {INITIAL_LOADING_STYLE_MARKER} */
html:not(.alas-initial-ready) #pywebio-scope-ROOT:empty {{
    position: fixed;
    inset: 0;
    z-index: 2147483000;
    display: grid;
    place-items: center;
    min-height: 100vh;
    background: {background};
    color: {foreground};
}}
html:not(.alas-initial-ready) #pywebio-scope-ROOT:empty::before {{
    width: 34px;
    height: 34px;
    content: "";
    border: 3px solid {track};
    border-top-color: {accent};
    border-radius: 50%;
    animation: alas-initial-spin .72s linear infinite;
}}
html:not(.alas-initial-ready) #pywebio-scope-ROOT:empty::after {{
    position: absolute;
    top: calc(50% + 34px);
    content: "AzurPilot";
    font: 600 14px/1.5 system-ui, sans-serif;
    letter-spacing: .04em;
}}
@keyframes alas-initial-spin {{
    to {{ transform: rotate(360deg); }}
}}
@media (prefers-reduced-motion: reduce) {{
    html:not(.alas-initial-ready) #pywebio-scope-ROOT:empty::before {{
        animation-duration: 1.8s;
    }}
}}
"""


def _build_alas_gui_class():
    """延迟构建 AlasGUI 类，合并所有 Mixin。"""
    from module.webui.app_dashboard import DashboardMixin
    from module.webui.app_dependencies import Dict, Frame, List, RichLog
    from module.webui.app_developer_menu import DeveloperMenuMixin
    from module.webui.app_developer_settings import DeveloperSettingsMixin
    from module.webui.app_developer_tools import DeveloperToolsMixin
    from module.webui.app_developer_update import DeveloperUpdateMixin
    from module.webui.app_event_tools import EventToolsMixin
    from module.webui.app_fleet_management import FleetManagementMixin
    from module.webui.app_home import HomeMixin
    from module.webui.app_instances import InstanceMixin
    from module.webui.app_overview import OverviewMixin
    from module.webui.app_shell import AppShellMixin
    from module.webui.app_stat_action_point import ActionPointStatisticsMixin
    from module.webui.app_stat_action_point_toolbar import ActionPointToolbarMixin
    from module.webui.app_stat_commission import CommissionIncomeStatisticsMixin
    from module.webui.app_stat_delta import ResourceDeltaStatisticsMixin
    from module.webui.app_stat_opsi import OpsiStatisticsMixin
    from module.webui.app_stat_opsi_export import OpsiExportMixin
    from module.webui.app_stat_ship import ShipExperienceStatisticsMixin
    from module.webui.app_statistics_page import StatisticsPageMixin
    from module.webui.app_task_config import TaskConfigMixin

    class AlasGUI(
        AppShellMixin,
        StatisticsPageMixin,
        ActionPointStatisticsMixin,
        ActionPointToolbarMixin,
        OpsiStatisticsMixin,
        OpsiExportMixin,
        ShipExperienceStatisticsMixin,
        CommissionIncomeStatisticsMixin,
        ResourceDeltaStatisticsMixin,
        FleetManagementMixin,
        TaskConfigMixin,
        EventToolsMixin,
        OverviewMixin,
        DashboardMixin,
        DeveloperMenuMixin,
        DeveloperUpdateMixin,
        DeveloperSettingsMixin,
        DeveloperToolsMixin,
        InstanceMixin,
        HomeMixin,
        Frame,
    ):
        """组合各 WebUI 视图的会话控制器。"""

        ALAS_MENU: Dict[str, Dict[str, List[str]]]
        ALAS_ARGS: Dict[str, Dict[str, Dict[str, Dict[str, str]]]]
        theme = "default"
        _log = RichLog

    return AlasGUI


def debug() -> None:
    """初始化 WebUI 后进入交互式调试会话。"""
    from module.webui.app_lifecycle import startup

    startup()
    _build_alas_gui_class()().run()


def app():
    """创建供 Uvicorn 使用的 ASGI 应用工厂。

    采用延迟加载：所有重依赖（pywebio、mixin 等）在此函数调用时才加载。

    Returns:
        Starlette: 挂载 WebUI 页面和 MCP 子应用的 ASGI 应用。
    """
    import argparse
    import time
    from typing import List

    from module.webui.app_dependencies import (
        IS_ON_PHONE_CLOUD,
        PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE,
        ProcessManager,
        RESTRICTED_DEVICE_IDS,
        RESTRICTED_DEVICE_MESSAGE,
        State,
        asgi_app,
        get_device_id,
        get_localstorage_values,
        info,
        lang,
        load_webui_styles,
        local,
        logger,
        login,
        popup,
        run_js,
        set_env,
        updater,
        webconfig,
    )
    from module.webui.app_helpers import (
        ensure_public_webui_password,
        is_demo_mode,
        is_webui_password_set,
    )
    from module.webui.app_lifecycle import clearup, startup

    AlasGUI = _build_alas_gui_class()

    parser = argparse.ArgumentParser(description="Alas web service")
    parser.add_argument(
        "-k", "--key", type=str, help="Password of alas. No password by default"
    )
    parser.add_argument(
        "--cdn",
        action="store_true",
        help="Use jsdelivr cdn for pywebio static files (css, js). Self host cdn by default.",
    )
    parser.add_argument(
        "--run",
        nargs="+",
        type=str,
        help="Run alas by config names on startup",
    )
    args, _ = parser.parse_known_args()

    initial_theme = normalize_webui_theme(State.deploy_config.Theme)
    initial_pywebio_theme = pywebio_theme_for(initial_theme)
    AlasGUI.theme = initial_theme
    State.theme = initial_theme
    State.deploy_config.Theme = initial_theme
    initial_style_names = _initial_style_names(initial_theme)
    initial_css_files = (
        INITIAL_WEBUI_CSS,
        *(
            _versioned_static_asset(f"assets/gui/css/{name}.css")
            for name in initial_style_names[1:]
        ),
    )
    initial_loading_css = _initial_loading_css(initial_theme)
    lang.LANG = State.deploy_config.Language
    key = args.key if is_webui_password_set(args.key) else State.deploy_config.Password
    key, password_error = ensure_public_webui_password(key)
    cdn: str | bool = args.cdn if args.cdn else State.deploy_config.CDN
    runs: List[str] | None = None
    if args.run:
        runs = args.run
    elif State.deploy_config.Run:
        tmp = State.deploy_config.Run.split(",")
        runs = [item.strip(" ['\"]") for item in tmp if item]
    instances: List[str] | None = runs

    logger.hr("[WebUI] WebUI 配置")
    logger.attr("主题", State.deploy_config.Theme)
    logger.attr("语言", lang.LANG)
    logger.attr("密码", is_webui_password_set(key))
    logger.attr("CDN", cdn)
    logger.attr("云手机", IS_ON_PHONE_CLOUD)

    from deploy.atomic import atomic_failure_cleanup

    atomic_failure_cleanup("./config")
    static_mounts = {
        "/static/assets": str(PROJECT_ROOT / "assets"),
        "/static/doc": str(PROJECT_ROOT / "doc"),
    }

    def _block_restricted_device() -> bool:
        if is_demo_mode():
            return False
        if get_device_id() not in RESTRICTED_DEVICE_IDS:
            return False
        popup(
            "安全保护",
            RESTRICTED_DEVICE_MESSAGE,
            implicit_close=False,
            closable=False,
        )
        return True

    def _block_public_webui_password_error() -> bool:
        if is_demo_mode() or password_error is None:
            return False
        popup(
            "安全保护",
            PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE,
            implicit_close=False,
            closable=False,
        )
        return True

    def _run_gui(initial_page: str = "home") -> None:
        session_theme = normalize_webui_theme(State.deploy_config.Theme)
        if session_theme != initial_theme:
            # 应用运行期间切换主题时，当前 HTML 仍是启动时主题，需要兼容热切换。
            AlasGUI.set_theme(theme=session_theme)
        else:
            # 主题未变化，仅同步缓存；样式注入统一下方 load_webui_styles 完成。
            AlasGUI.theme = session_theme
            State.theme = session_theme
        set_env(title="AzurPilot", output_animation=False)
        # HTML <link> 预载仅用于首屏骨架着色，不作为最终保障：远控 P2P
        # 隧道下主文档能成功而 CSS 子请求可能静默失败（暗色/亮色高级材质
        # 均发生过整页裸奔）。此处总是经 WebSocket 全量注入主题样式——
        # WebSocket 是会话内已建立的可靠通道；预载成功时重复注入的规则
        # 级联结果相同，预载失败时由本次注入兜底，行为确定无竞态。
        load_webui_styles(
            theme=AlasGUI.theme,
            is_mobile=info.user_agent.is_mobile,
        )
        if _block_restricted_device() or _block_public_webui_password_error():
            return
        localstorage = None
        if is_webui_password_set(key):
            localstorage = get_localstorage_values(
                ("password", "clarity_notice_shown", "aside")
            )
        if is_webui_password_set(key) and not login(
            key, stored_password=localstorage.get("password")
        ):
            logger.warning(f"[WebUI] {info.user_ip} 登录失败")
            time.sleep(1.5)
            run_js("location.reload();")
            return
        gui = AlasGUI()
        local.gui = gui
        gui.run(initial_page=initial_page, localstorage=localstorage)

    @webconfig(
        theme=initial_pywebio_theme,
        css_file=initial_css_files,
        css_style=initial_loading_css,
        js_code=INITIAL_LOADING_JS,
    )
    def index() -> None:
        _run_gui()

    @webconfig(
        theme=initial_pywebio_theme,
        css_file=initial_css_files,
        css_style=initial_loading_css,
        js_code=INITIAL_LOADING_JS,
    )
    def manage() -> None:
        _run_gui(initial_page="manage")

    application = asgi_app(
        applications=[index, manage],
        cdn=cdn,
        static_mounts=static_mounts,
        debug=False,
        # 远控断线时不整页刷新：PyWebIO 保留会话，前端用同一 session id 原地重连;
        # 断开超过该秒数才是会话过期，届时才需要重新加载。
        reconnect_timeout=WEBSOCKET_RECONNECT_TIMEOUT,
        on_startup=[
            startup,
            # worker 拉起是逐个 spawn 新解释器的重操作，放后台线程执行，
            # WebUI 就绪不再被实例数量拖慢；页面可用后侧栏如实显示启动状态。
            lambda: Thread(
                target=ProcessManager.restart_processes,
                kwargs=dict(instances=instances, ev=updater.event),
                name="RestartProcesses",
                daemon=True,
            ).start(),
        ],
        on_shutdown=[clearup],
    )
    application.mount("/mcp", _LazyMCPApp())
    return application


# 兼容旧导入路径：updater.py 从 module.webui.app import clearup
# 注意：此处使用 lazy import 避免 module-level 触发 pywebio 加载
def __getattr__(name: str):
    if name == "clearup":
        from module.webui.app_lifecycle import clearup

        return clearup
    if name == "startup":
        from module.webui.app_lifecycle import startup

        return startup
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")