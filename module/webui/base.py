"""
Web界面基础框架。

提供 Base 和 Frame 两个核心类。Base 管理页面生命周期和后台任务调度，
Frame 实现侧边栏、菜单导航和内容区域的切换逻辑。
"""

import functools
import json
import threading
import time

from pywebio.output import clear, put_html, put_scope, put_text, use_scope
from pywebio.session import defer_call, info, run_js

from module.webui.lang import t
from module.webui.utils import Icon, WebIOTaskHandler, set_localstorage


class _RenderLock:
    """会话级渲染锁：可重入 + 用户交互优先 + 后台任务防饿死。

    为什么不用 threading.RLock：RLock 无优先级，总览页的周期刷新任务
    （仪表盘全量重建历史上平均 3.4s、峰值 10.3s）持锁期间，用户点击
    「统计」「任务设置」等回调只能排队干等，且还会被下一轮周期任务
    反复插队——表现为「总览加载中点哪里都没反应」。

    规则：
    - 用户回调（按钮点击等会话线程）注册为交互等待者，等锁期间
      所有后台任务让位，保证用户是下一个拿锁的；
    - 后台任务（TaskHandler 线程）拿锁前若发现交互等待者就让位轮询，
      超过让位耐心后硬闯一次，防止持续的用户操作饿死周期刷新。
    """

    # 后台任务在有用户等待时的让位耐心（秒）：超过后硬闯防饿死。
    # 用户回调都是亚秒级，3s 预算内几乎必然已拿到锁。
    BACKGROUND_PATIENCE = 3.0
    # 让位轮询间隔（秒）：通过 Condition.wait 间接响应 release 唤醒，
    # 轮询只是兜底。
    _YIELD_POLL = 0.05

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._owner = None  # 持锁线程 ident
        self._depth = 0
        self._interactive_waiters = 0

    def has_interactive_waiters(self) -> bool:
        """是否有用户回调正在等锁（供后台长渲染做分段中断）。"""
        with self._cond:
            return self._interactive_waiters > 0

    def acquire(self, is_background: bool = False) -> None:
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._depth += 1
                return
            if is_background:
                deadline = time.monotonic() + self.BACKGROUND_PATIENCE
                while True:
                    if self._owner is None and self._interactive_waiters == 0:
                        break
                    if time.monotonic() >= deadline:
                        # 硬闯防饿死：插一次队，跑完本轮立即释放
                        break
                    self._cond.wait(self._YIELD_POLL)
            else:
                self._interactive_waiters += 1
                try:
                    while self._owner is not None:
                        self._cond.wait(self._YIELD_POLL)
                finally:
                    self._interactive_waiters -= 1
            self._owner = me
            self._depth = 1

    def release(self) -> None:
        with self._cond:
            if self._owner != threading.get_ident():
                raise RuntimeError("render lock released by non-owner thread")
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._cond.notify_all()


def _current_is_background_task(gui) -> bool:
    """当前线程是否正在执行 TaskHandler 注册的后台任务。

    TaskHandler.loop 执行任务期间会把任务写进自己的 thread-local，
    用户按钮回调线程里它恒为 None——据此区分调用者身份。
    """
    handler = getattr(gui, "task_handler", None)
    if handler is None:
        return False
    return handler._task is not None


def render_locked(func):
    """序列化页面渲染。

    PyWebIO 的线程会话为每个按钮回调新建独立线程，后台 TaskHandler
    任务也在独立线程运行。页面渲染均为"清空 scope → 逐条输出"两阶段，
    若两个线程并发渲染同一区域，清空与输出指令会交错，导致：
    控件重复、布局错位、同名 Pin widget 被标记失效，以及
    `use_scope` 对已消失 scope 自动在 ROOT 下创建孤儿容器
    （离开总览页后 10s 刷新任务仍渲染一次即永久残留）。

    使用会话级可重入锁 `self.render_lock` 串行化所有渲染入口；
    远控 P2P 高延迟会大幅拉长竞态窗口，本锁在服务端消除交错。

    锁是交互优先的（见 `_RenderLock`）：用户回调 vs 后台任务按调用
    线程身份自动区分，无需在装饰器上人工标注。
    """

    @functools.wraps(func)
    def inner(self, *args, **kwargs):
        # 生产类均继承 Base 拥有 render_lock；测试替身等无锁对象直接调用。
        lock = getattr(self, "render_lock", None)
        if lock is None:
            return func(self, *args, **kwargs)
        lock.acquire(is_background=_current_is_background_task(self))
        try:
            return func(self, *args, **kwargs)
        finally:
            lock.release()

    # functools.wraps 默认把 __wrapped__ 指向紧内层的包装函数（如 use_scope
    # 的 wrapper）；tests 通过 `__wrapped__` 绕过全部装饰器直达原实现。
    # 沿用被装饰者已有的 __wrapped__ 链，避免装饰叠加后测试误触会话依赖。
    if getattr(func, "__wrapped__", None) is not None:
        inner.__wrapped__ = func.__wrapped__
    return inner


class Base:
    """WebUI 应用的基础类，管理生命周期和任务调度。"""

    def __init__(self) -> None:
        self.alive = True
        # 页面渲染串行化锁（可重入；用户回调优先于后台刷新任务）
        self.render_lock = _RenderLock()
        # 窗口是否可见（切换页面时置为 False 阻止旧页面的任务继续执行）
        self.visible = True
        # 是否为移动端设备
        self.is_mobile = info.user_agent.is_mobile
        # 任务处理器，用于管理后台异步任务
        self.task_handler = WebIOTaskHandler()
        defer_call(self.stop)

    def stop(self) -> None:
        self.alive = False
        self.task_handler.stop()

    def render_should_yield(self) -> bool:
        """后台长渲染的分段中断检查点。

        返回 True 表示有用户回调（切页/点击）正在等渲染锁：长循环里的
        渲染任务应立即放弃本轮剩余输出、释放锁让用户先走，下一轮周期
        再补渲染。已写出的中间状态由调用方保证可重跑（中断时不提交
        缓存快照、不置 first_display 等一次性标志）。
        """
        lock = getattr(self, "render_lock", None)
        return lock.has_interactive_waiters() if lock is not None else False


class Frame(Base):
    """WebUI 页面框架，管理侧边栏、菜单和内容区域的切换与导航。"""

    # 当前页面名（localStorage）：整页重载后据此回到离开时的页面
    LAST_PAGE_KEY = "alas_last_page"

    def __init__(self) -> None:
        super().__init__()
        self.page = "Home"
        self._page_lock = threading.Lock()

    @staticmethod
    def cleanup_client_resources(*registry_names: str) -> None:
        """调用前端资源清理器，释放已替换视图持有的事件回调。"""
        if not registry_names:
            return

        run_js(
            f"""
            (function (keys) {{
                keys.forEach(function (key) {{
                    var cleanups = window[key];
                    if (!cleanups) return;
                    Object.keys(cleanups).forEach(function (id) {{
                        if (typeof cleanups[id] === 'function') cleanups[id]();
                    }});
                }});
            }})({json.dumps(registry_names)});
            """
        )

    def init_aside(self, expand_menu: bool = True, name: str = None) -> None:
        """
        侧边栏按钮点击时的初始化回调。

        展开菜单并高亮指定按钮。菜单由目标页面准备完成后替换。
        合并 WebSocket 调用以减少网络往返。

        Args:
            expand_menu: 是否展开菜单。
            name: 需要高亮的按钮名称（标签）。
        """
        self.visible = True
        self.task_handler.remove_pending_task()
        js_parts = []
        if expand_menu:
            js_parts.append(
                "$('.container-menu-collapsed').removeClass('container-menu-collapsed');\n"
                "$('#pywebio-scope-content').addClass('container-content-collapsed');\n"
            )
        if name:
            js_parts.append(
                "$('button.btn-aside').removeClass('btn-aside-active');\n"
                "$('div[style*=\"--aside-" + name + "--\"]>button').addClass('btn-aside-active');\n"
            )
            set_localstorage("aside", name)
        # 主页右下角"纯背景模式"圆点仅在主页(aside=Home)显示
        js_parts.append(
            "(function () {\n"
            "  if (window.alasWallpaperToggle) {\n"
            "    window.alasWallpaperToggle(" + json.dumps(name == "Home") + ");\n"
            "  }\n"
            "})();\n"
        )
        if js_parts:
            run_js("".join(js_parts))

    def init_menu(self, collapse_menu: bool = True, name: str = None, *, skip_clear: bool = False) -> None:
        """
        菜单按钮点击时的初始化回调。

        清空内容区域，折叠菜单，并高亮指定按钮。
        将多次 WebSocket 往返合并为尽可能少的调用。

        Args:
            collapse_menu: 是否折叠菜单。
            name: 需要高亮的按钮名称（标签）。
            skip_clear: 如果调用方已通过 @use_scope("content", clear=True) 清空，
                可设 True 跳过重复 clear，减少一次 WebSocket 往返。
        """
        self.visible = True
        self.task_handler.remove_pending_task()
        with self._page_lock:
            self.page = name
            js_parts = []

            js_parts.append(
                "(function () {\n"
                "  var keys = " + json.dumps(["__apChartCleanups", "__resourceDeltaChartCleanups"]) + ";\n"
                "  keys.forEach(function (key) {\n"
                "    var cleanups = window[key];\n"
                "    if (!cleanups) return;\n"
                "    Object.keys(cleanups).forEach(function (id) {\n"
                "      if (typeof cleanups[id] === 'function') cleanups[id]();\n"
                "    });\n"
                "  });\n"
                "})();\n"
            )

            if collapse_menu:
                js_parts.append(
                    "$('#pywebio-scope-menu').addClass('container-menu-collapsed');\n"
                    "$('.container-content-collapsed').removeClass('container-content-collapsed');\n"
                )

            if name:
                js_parts.append(
                    "$('button.btn-menu').removeClass('btn-menu-active');\n"
                    "$('div[style*=\"--menu-" + name + "--\"]>button').addClass('btn-menu-active');\n"
                )

            # 骨架屏：点击菜单的下一帧先画出占位，**真实内容落地后自动撤掉**。
            # 三个历史坑（都踩过，别再改回去）：
            #  1) 早期实现直接 `content.innerHTML = 骨架`：pywebio 自己的 scope
            #     容器被一并抹掉，之后往这些 scope 输出时客户端只会在 ROOT 下
            #     新建孤儿容器 —— 页面内容渲染到别处（总览的「调度器/统计界面」
            #     整块跑进左侧菜单列，看着像二级菜单）。
            #  2) 骨架写进 DOM 却没人清理：只有配置页自己补了一次 clear("content")，
            #     其余页面（总览/设置/工具/更新/管理）骨架永久残留、压在内容上方。
            #  3) 观察目标必须每次重新绑定：内容区元素可能被重建。
            # 现在只 append 一个 #alas-page-skeleton 占位节点（不动别人的 DOM），
            # 用 MutationObserver 盯内容区子节点：出现非骨架节点即撤掉骨架。
            js_parts.append(
                "(function () {\n"
                "  var content = document.getElementById('pywebio-scope-content');\n"
                "  if (!content) return;\n"
                "  var stale = document.getElementById('alas-page-skeleton');\n"
                "  if (stale) stale.remove();\n"
                "  if (!" + ("true" if name and name != "HomePage" else "false") + ") return;\n"
                "  var sk = document.createElement('div');\n"
                "  sk.id = 'alas-page-skeleton';\n"
                "  var lines = '';\n"
                "  for (var i = 0; i < 6; i++) {\n"
                "    lines += '<div class=\"alas-skeleton-line\" style=\"width:' +\n"
                "      (88 - i * 11) + '%\"></div>';\n"
                "  }\n"
                "  sk.innerHTML = '<div class=\"alas-skeleton\">'\n"
                "    + '<div class=\"alas-skeleton-title\"></div>' + lines + '</div>';\n"
                "  content.appendChild(sk);\n"
                "  if (!window.__alasSkeletonObserver) {\n"
                "    window.__alasSkeletonObserver = new MutationObserver(function () {\n"
                "      var c = document.getElementById('pywebio-scope-content');\n"
                "      if (!c) return;\n"
                "      var s = document.getElementById('alas-page-skeleton');\n"
                "      var real = false;\n"
                "      for (var i = 0; i < c.children.length; i++) {\n"
                "        if (c.children[i] !== s) { real = true; break; }\n"
                "      }\n"
                "      if (real && s) s.remove();\n"
                "    });\n"
                "  }\n"
                "  if (window.__alasSkeletonTarget !== content) {\n"
                "    window.__alasSkeletonObserver.disconnect();\n"
                "    window.__alasSkeletonObserver.observe(content, {childList: true});\n"
                "    window.__alasSkeletonTarget = content;\n"
                "  }\n"
                "})();\n"
            )

            # 主页右下角"纯背景模式"圆点仅在主页(menu=HomePage)显示
            js_parts.append(
                "(function () {\n"
                "  if (window.alasWallpaperToggle) {\n"
                "    window.alasWallpaperToggle(" + json.dumps(name == "HomePage") + ");\n"
                "  }\n"
                "})();\n"
            )

            # 记住当前页面：远控抖动触发兜底刷新（整页重载）后回到离开时那一页，
            # 而不是一律回到总览。搭在同一批 JS 里发送，不额外增加往返。
            if name:
                js_parts.append(
                    "(function () {\n"
                    "  try { localStorage.setItem("
                    + json.dumps(self.LAST_PAGE_KEY)
                    + ", "
                    + json.dumps(name)
                    + "); } catch (e) {}\n"
                    "})();\n"
                )

            if js_parts:
                run_js("".join(js_parts))

            # clear("content") 也是阻塞的 WebSocket 调用。默认仍执行以兼容旧调用方；
            # @use_scope("content", clear=True) 已清空的调用方可传 skip_clear=True 跳过。
            if not skip_clear:
                clear("content")

        self.set_statistics_content_visible(name == "Stat")

    def page_renderer(self, name: str):
        """把页面名映射回渲染函数；不认识的名字返回 None（调用方自行回退）。

        用途：远控断线走到兜底刷新时会整页重载、新开一个会话，此时若一律渲染
        总览，用户就会被从他正看的页面「刷」走。``init_menu`` 在每次导航时把
        页面名写进 localStorage，新会话据此恢复。

        任务页只在它确实出现在任务菜单里时才恢复（并且按菜单的 page 类型走
        对应的入口：``tool`` 页走 ``alas_daemon_overview``），避免把非页面条目
        当成任务配置页渲染。
        """
        if not name:
            return None
        named = {
            "Overview": self.alas_overview,
            "Stat": self.alas_set_stat,
            "HomePage": self.show_home,
            "Develop": self.dev_set_menu,
            "Setting": self.dev_setting,
            "Utils": self.dev_utils,
            "Update": self.dev_update,
            "Remote": self.dev_remote,
            "FleetScan": self.fleet_scan_page,
            "FleetInfo": self.fleet_info_page,
        }
        if name in named:
            return named[name]
        for task_data in (getattr(self, "ALAS_MENU", None) or {}).values():
            if not isinstance(task_data, dict):
                continue
            if name not in (task_data.get("tasks") or []):
                continue
            if task_data.get("page") == "tool":
                return lambda task=name: self.alas_daemon_overview(task)
            return lambda task=name: self.alas_set_group(task)
        return None

    @staticmethod
    def set_statistics_content_visible(visible: bool) -> None:
        """在普通内容区与可复用的统计内容区之间切换。"""
        run_js(
            """
            (function () {
                var content = document.getElementById("pywebio-scope-content");
                var statistics = document.getElementById(
                    "pywebio-scope-statistics-content"
                );
                if (content) content.style.display = visible ? "none" : "";
                if (statistics) statistics.style.display = visible ? "" : "none";
            })();
            """,
            visible=visible,
        )

    @staticmethod
    @use_scope("ROOT", clear=True)
    def _show() -> None:
        # 页眉排头恒定渲染，结构为「头像 + 排头 + 加载圈 + 状态文字」：
        #   zh-CN / zh-MIAO / zh-TW：排头是整句「待到山花烂漫时，她在丛中笑」，
        #       状态文字为空，加载圈紧跟在排头之后；
        #   en-US / ja-JP：排头是 AzurPilot，加载圈后还有状态文字。
        put_scope(
            "header",
            [
                put_html(Icon.ALAS).style("--header-icon--"),
                put_text(t("Gui.Status.Prefix")).style("--header-text--"),
                put_scope("header_status"),
                put_scope("header_title"),
            ],
        )
        put_scope(
            "contents",
            [
                put_scope("aside"),
                put_scope("menu"),
                put_scope("content"),
                put_scope("statistics-content").style("display: none;"),
            ],
        )

    @staticmethod
    @use_scope("header_title", clear=True)
    def set_title(text=""):
        put_text(text)

    @staticmethod
    def collapse_menu() -> None:
        run_js(
            """
            $("#pywebio-scope-menu").addClass("container-menu-collapsed");
            $(".container-content-collapsed").removeClass("container-content-collapsed");
        """
        )

    @staticmethod
    def expand_menu() -> None:
        run_js(
            """
            $(".container-menu-collapsed").removeClass("container-menu-collapsed");
            $("#pywebio-scope-content, #pywebio-scope-statistics-content")
                .addClass("container-content-collapsed");
        """
        )

    @staticmethod
    def active_button(position, value) -> None:
        run_js(
            f"""
            $("button.btn-{position}").removeClass("btn-{position}-active");
            $("div[style*='--{position}-{value}--']>button").addClass("btn-{position}-active");
        """
        )

    @staticmethod
    def pin_set_invalid_mark(keys) -> None:
        if isinstance(keys, str):
            keys = [keys]
        keys = ["_".join(key.split(".")) for key in keys]
        js = "".join(
            [
                f"""$(".form-control[name='{key}']").addClass('is-invalid');"""
                for key in keys
            ]
        )
        if js:
            run_js(js)

    @staticmethod
    def pin_remove_invalid_mark(keys) -> None:
        if isinstance(keys, str):
            keys = [keys]
        keys = ["_".join(key.split(".")) for key in keys]
        js = "".join(
            [
                f"""$(".form-control[name='{key}']").removeClass('is-invalid');"""
                for key in keys
            ]
        )
        if js:
            run_js(js)
