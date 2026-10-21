"""WebUI 统计页装配器。

改版要点（对应 statistics-v2 原型）：
    页头（标题 + 刷新）→ 下划线页签 → 六个面板
    大世界 / 委托 / 科研 / 资源 / 科技列表 / 心智计算器

实现方式刻意保持「零侵入」：各统计子视图（ap_chart / opsi_stats /
commission_income / research_stats / resource_delta / ship_exp_table）以及
舰队科技列表与心智计算器（ft_tech / ft_mind）的渲染代码一行未改，仍然往同名
scope 里输出。本模块只负责

    1. 在 statistics-content 里铺好页头、页签和一个空的面板容器；
    2. 用一段 JS 把已存在的 scope 元素搬进对应面板，并接上页签交互。

后续任何子视图的增删改都不需要关心分页结构。
"""

from datetime import date, datetime, timedelta
from html import escape as html_escape
from pathlib import Path

import module.webui.lang as lang
from module.logger import logger
from module.webui.app_dependencies import (
    close_popup,
    current_time,
    popup,
    put_button,
    put_buttons,
    put_html,
    pin_on_change,
    put_input,
    put_scope,
    run_js,
    t,
    toast,
    use_scope,
)
from module.webui.app_fleet_technology import BRIDGE_PIN, build_fleet_tech_bridge_script
from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked
from module.webui.fleet_tech_view import STAT_PANELS


# 面板顺序即页签顺序。元组为 (面板 id, 页签文案 i18n 键, 徽标 i18n 键或 None)
_STAT_TABS = (
    ("opsi", "Gui.Stat.TabOpsi", None),
    ("commission", "Gui.Stat.TabCommission", None),
    ("research", "Gui.Stat.TabResearch", None),
    ("resource", "Gui.Stat.TabResource", None),
    # 舰队科技列表 / 心智计算器：内容宿主 scope 分别为 ft_tech / ft_mind，
    # 由 FleetTechnologyMixin 渲染（见 app_fleet_technology）。
    *((panel, key, None) for panel, key in STAT_PANELS),
)

# 页头「数据源」状态：最新写入超过这么多天就提示未更新。
# 阈值放宽到 7 天，避免脚本停跑一两天就误报。
_STAT_SOURCE_STALE_DAYS = 7

# 页头「数据源」状态 -> i18n 键
_STAT_SOURCE_KEYS = {
    "ok": "Gui.Stat.SourceOk",
    "stale": "Gui.Stat.SourceStale",
    "missing": "Gui.Stat.SourceMissing",
}

# 各面板承载的 PyWebIO scope 名（顺序即卡片顺序）。
# research 面板由 ResearchStatisticsMixin 渲染进 research_stats scope。
_STAT_PANEL_SCOPES = {
    "opsi": ("ap_chart", "opsi_stats", "ship_exp_table"),
    "commission": ("commission_income",),
    "research": ("research_stats",),
    "resource": ("resource_delta",),
    # 面板内容整块搬入：ft_tech / ft_mind 由舰队科技 Mixin 渲染（内容不变）。
    # ft_toolbar（扫描范围 + 扫描/同步按钮）必须跟着搬：漏搬时 PyWebIO 找不到
    # 该 scope 会在 ROOT 下新建孤儿容器，工具栏就永久贴在页面最底部，与统计
    # 面板彻底分离（真机截图里那条割裂白条）。
    "fleet": ("ft_tech", "ft_toolbar"),
    "mind": ("ft_mind",),
}

# 每个面板包含哪些分区渲染入口（懒加载按面板粒度调度）。
# 顺序即同一面板内的渲染顺序；缺失的方法在会话里静默跳过。
_STAT_PANEL_RENDERERS = {
    "opsi": (
        "_render_ap_chart",
        "_render_opsi_stats",
        "_render_ship_exp",
    ),
    "commission": ("_render_commission_income",),
    "research": ("_render_research_stats",),
    "resource": ("_render_resource_delta",),
    "fleet": ("_ft_render_tech_panel",),
    "mind": ("_ft_render_mind_panel",),
}

# 页签点击 / 面板高度回传共用的隐藏 pin 名（裸 HTML 按钮没有回调通道）。
ST_BRIDGE_PIN = "st_bridge_pin"

def _has_webio_session() -> bool:
    """当前线程是否处在真实 WebIO 会话里（无会话时 pin 注册会永久阻塞）。

    ⚠️ 不能直接调 ``get_current_session()``：pywebio 的 ``get_session_implement()``
    在没有任何活动会话实现时会**自动拉起一个 Script Mode 本地服务器**并阻塞
    等待浏览器连接（``session/__init__.py:210-212``）。在单测 / 脚本模式下
    表现就是「进程挂住、CPU 不动、无任何输出」——2026-10-21 实测整个
    ``test_webui_statistics_page`` 因此卡死。

    这里只读 pywebio 的会话实现注册表：为空即说明没有会话，直接返回 False，
    绝不触发 Script Mode 回退。
    """
    try:
        from pywebio.session import _active_session_cls
    except Exception:
        return False
    if not _active_session_cls:
        return False
    # 有注册实现时逐个探测，任一能拿到当前会话即为真实会话
    for session_cls in list(_active_session_cls):
        try:
            session_cls.get_current_session()
            return True
        except Exception:
            continue
    return False
_STAT_SKELETON_HEIGHT = {
    "opsi": 900,
    "commission": 760,
    "research": 820,
    "resource": 900,
    "fleet": 1200,
    "mind": 900,
}

# 骨架占位的最小高度：实测高度/估计值都不可信时的兜底
_STAT_SKELETON_MIN_HEIGHT = 240

_DEFAULT_PANEL = "opsi"

# 舰队科技面板的 id：它的数据刷新任务最贵（每 5 秒重建一次配置对象），
# 只在面板真正被用户点开后才登记轮询任务。
_PANEL_FLEET = "fleet"


class StatisticsPageMixin(WebUIMixinBase):
    """惰性装配并复用统计子视图。"""

    @render_locked
    def alas_set_stat(self) -> None:
        """显示统计页，已装配的内容会直接复用。"""
        self.init_menu(name="Stat")
        self.set_title(t("Gui.Overview.Stat"))
        if not hasattr(self, "_ap_chart_view"):
            self._ap_chart_view = "line"
        # 查看月份：None = 本月。体力图、侵蚀一卡片与大世界收获共用（见本文件
        # 「查看月份」一节），资源消耗 / 舰船经验 / 委托收益各有自己的口径。
        if not hasattr(self, "_stat_view_month"):
            self._stat_view_month = None
        if not hasattr(self, "_commission_income_period"):
            self._commission_income_period = "month"
        if not hasattr(self, "_commission_income_year") or not hasattr(
            self, "_commission_income_month"
        ):
            now = datetime.now()
            self._commission_income_year = now.year
            self._commission_income_month = now.month
        if not hasattr(self, "_research_stats_scope"):
            self._research_stats_scope = "series"
        if not hasattr(self, "_research_stats_period"):
            self._research_stats_period = "days90"
        if not hasattr(self, "_research_stats_series"):
            self._research_stats_series = 0

        cache_key = self._get_statistics_cache_key()
        cached_key = getattr(self, "_statistics_cache_key", None)

        if cached_key != cache_key:
            self._mount_statistics_page(cache_key)
        else:
            self._refresh_statistics_if_changed()
            # 面板常驻：切页会随 pending_delete 清掉两个扫描开关的轮询任务，
            # 每次进入补画一次工具栏（登记按任务名幂等，不会叠加）。
            # 懒加载：舰队面板没加载过就不画工具栏——否则会为一个用户根本
            # 没点开的页签白做一次配置读取（load_config 很贵）。
            if _PANEL_FLEET in getattr(self, "_st_loaded_panels", set()):
                self._ft_render_scan_controls()
            # 上次离开统计页时分区可能还没填充完（填充任务被切页一并清掉）：
            # 这里补排一次，避免分区永远是空壳。
            if not getattr(self, "_statistics_sections_rendered", False):
                self._queue_statistics_fill()

        # 原先的 5 个任务会在进页后立即各重绘一次。现在仅轮询本地数据源
        # 版本并提示存在新数据，不再打断用户正在查看的图表状态。
        self.task_handler.add(self._refresh_statistics_if_changed, 15, True, group="slow")
        # 科技记录 / 心智清单的自动刷新只在「舰队面板已加载」时才登记：
        # 该任务每 5 秒调一次 load_config()（完整读 JSON + 合并模板 + 写盘），
        # 用户没点开舰队页签时纯属空转，是 2026-10-21 界面卡死的直接成因之一。
        # 面板被点开时由 _render_statistics_panel 负责补登记。
        if _PANEL_FLEET in getattr(self, "_st_loaded_panels", set()):
            self.task_handler.add(self._ft_data_refresh, 5, True)

    def _mount_statistics_page(self, cache_key) -> None:
        """为当前实例首次创建统计内容。"""
        with self._page_lock:
            if getattr(self, "page", None) != "Stat":
                return
            if getattr(self, "_statistics_cache_key", None) is not None:
                self.cleanup_client_resources(
                    "__apChartCleanups",
                    "__resourceDeltaChartCleanups",
                )

            with use_scope("statistics-content", clear=True):
                put_html(self._build_statistics_head_html())
                put_html(self._build_statistics_tabs_html())
                # 空容器：面板由 _layout_statistics_panels 注入的 JS 现场创建。
                # 不能用「开标签 / 关标签分两次 put_html」建嵌套——输出会经浏览器
                # 解析，不闭合标签被自动补全后嵌套结构就没了。
                put_html('<div class="st-panels" id="statistics-panels"></div>')

                put_scope(
                    "statistics-toolbar",
                    [
                        put_button(
                            t("Gui.Stat.Refresh"),
                            onclick=self._refresh_statistics_page,
                            color="off",
                        )
                    ],
                ).style(
                    "display:flex;justify-content:flex-end;margin-bottom:.5rem;"
                )
                # 懒加载：面板容器仍然一次性建好，但内容只在用户点开页签时填充。
                # scope 必须先于面板搬移存在，否则 PyWebIO 会在 ROOT 下新建孤儿
                # 容器（历史事故：内容渲染到菜单列 / 页面底部多一条白条）。
                put_scope("ap_chart", [])
                put_scope("opsi_stats", [])
                put_scope("commission_income", [])
                put_scope("research_stats", [])
                put_scope("resource_delta", [])
                put_scope("ship_exp_table", [])
                # 舰队科技列表 / 心智计算器的内容宿主（整块搬进各自面板），
                # ft_toolbar 是扫描工具栏，必须一起搬——漏搬会让它留在页面
                # 最底部、与面板分离（真机截图里那条割裂的白条就是它）。
                put_scope("ft_tech", [])
                put_scope("ft_mind", [])
                put_scope("ft_toolbar", [])
                # 两个隐藏点击桥：ft_bridge 转发表格排序/编辑，st_bridge 转发
                # 页签点击与面板高度回传（裸 HTML 按钮没有回调通道）。
                put_scope("ft_bridge", [put_input(name=BRIDGE_PIN, value="")])
                put_scope("st_bridge", [put_input(name=ST_BRIDGE_PIN, value="")])

            self._statistics_cache_key = cache_key
            self._ft_bind_callbacks()
            # 每次挂载都必须重新绑定：切页会清空 statistics-content 并重建
            # st_bridge 输入框，上一份 pin 绑定指向的是已被销毁的旧节点，
            # 不重绑就会「点了页签没反应」（2026-10-21 真机：只有默认页签能出内容）。
            self._st_callbacks_bound = False
            self._st_bind_callbacks()
            run_js(build_fleet_tech_bridge_script(BRIDGE_PIN))
            # 懒加载状态：已加载面板集合 + 各面板上次实测高度（方案 C 用）
            self._st_loaded_panels = {_DEFAULT_PANEL}
            heights = getattr(self, "_st_panel_heights", None)
            if not isinstance(heights, dict):
                self._st_panel_heights = {}
            # 先分页归位再渲染：体力图表的 canvas 在绘制时读取容器宽度，
            # 若此时还不在激活面板里会量到 0 宽。
            self._layout_statistics_panels()
            # 各分区改后台首跑（同总览仪表盘）：同步渲染整页实测 3.6 秒，
            # 骨架（页头/页签/空面板）落地后再填充，进场观感从秒级变即时。
            self._statistics_source_signature = (
                self._get_statistics_source_signature()
            )
            self._statistics_refresh_pending = False
            # 分区尚未填充完成：填充任务真正渲染成功后才置 True；任务若在排队
            # 阶段就被切页清掉，这里会保持 False（alas_set_stat 据此补排一次）。
            self._statistics_sections_rendered = False
            self._queue_statistics_fill()

    def _queue_statistics_fill(self, force: bool = False) -> None:
        """把「已加载面板」的渲染排进后台首跑；重复入队用标志位去重。

        必须传**生成器对象**而不是生成器函数：``TaskHandler.add`` 对普通
        可调用对象会套 ``get_generator`` 包装（每次 tick 只 yield 出新建的
        生成器、从不驱动它），任务将永远不执行——2026-10-17 真机现场：
        分区从未加载，第二次进统计页在补画扫描开关时炸 ``_ft_report``。
        传生成器对象走 ``isinstance(func, Generator)`` 分支，按 delay 驱动。

        填充任务为一次性（跑完自调 delay 静默），随切页 pending_delete 清理；
        执行前校验仍在统计页，避免向已清空的 scope 写孤儿容器（同仪表盘守卫）。

        Args:
            force: True 表示「用户点了刷新」，已加载面板重读数据再画；
                False 表示首次挂载，只画一次即可。
        """
        if getattr(self, "_statistics_fill_queued", False):
            # 已在排队时又点了刷新：把 force 记下来，让排队中的那次重读数据
            if force:
                self._statistics_fill_force = True
            return
        self._statistics_fill_queued = True
        self._statistics_fill_force = bool(force)
        self.task_handler.add(
            self._fill_statistics_sections(), 0.05, True, group="slow"
        )

    def _fill_statistics_sections(self):
        yield
        self._statistics_fill_queued = False
        # 取走本次是否强制重读，然后复位（下一轮按普通渲染处理）
        force = bool(getattr(self, "_statistics_fill_force", False))
        self._statistics_fill_force = False
        if getattr(self, "page", None) != "Stat":
            return
        # 必须经 _render_statistics_sections_locked 进入：它保证「先渲染锁、再页面锁」
        # 的锁序与用户回调一致。此处此前是先拿 _page_lock、再调 @render_locked 的分区
        # 渲染（_page_lock → render_lock），与用户点「总览/主页」时的
        # render_lock → _page_lock 互逆，两边互等即死锁——真机表现为整个界面
        # 点哪里都没反应、会话销毁时调度线程还停不下来。
        self._render_statistics_sections_locked(force=force)
        # 填充一次即安静：把自身节奏调长，避免每秒空转（任务随切页被清理）。
        task = getattr(self.task_handler, "_task", None)
        if task is not None:
            task.delay = 3600
        while True:
            yield
    @render_locked
    def _render_statistics_sections_locked(self, force: bool = False) -> None:
        """统计页分区的唯一渲染入口（后台填充与手动刷新共用）。

        锁序约定：**先 render_lock、再 _page_lock**。用户回调（``alas_set_stat``
        等 ``@render_locked`` 方法）天然是这个顺序，后台填充也走这里，就不会
        再出现「后台持 _page_lock 等 render_lock、用户持 render_lock 等
        _page_lock」的 ABBA 死锁。

        拿到页面锁后再校验一次是否仍在统计页：填充任务是后台异步执行的，
        排队期间用户可能已经切走，向已清空的 scope 输出会在 ROOT 下留下
        孤儿容器（历史上的「统计内容渲染到别处」事故）。
        """
        with self._page_lock:
            if getattr(self, "page", None) != "Stat":
                return
            if getattr(self, "_statistics_cache_key", None) is None:
                return
            self._render_statistics_sections(force=force)
            # 分区已完整落位：alas_set_stat 的「补排一次」判断依赖这个标记
            self._statistics_sections_rendered = True

    def _reset_statistics_page_state(self) -> None:
        """离开统计页时清理统计页的会话状态（由 ``Frame.init_menu`` 调用）。

        切页会通过 pending_delete 把统计页注册的后台任务清掉，但标志位是会话级
        的、不会跟着任务一起消失：``_statistics_fill_queued`` 残留会让下次进入
        统计页误判「已经排过队」，分区永远不再填充（舰队工具栏因缺
        ``_ft_report`` 抛异常，整页打不开）；``_statistics_refresh_pending``
        残留则会让页头「有新数据」提示与刷新按钮状态错位。
        """
        self._statistics_fill_queued = False
        self._statistics_refresh_pending = False

    @render_locked
    def _refresh_statistics_page(self) -> None:
        """刷新统计页——只刷新「已加载」的面板，不把没看过的面板一起画出来。

        懒加载改造前这里会把 8 个分区全重绘一遍（同步版实测 0.9 秒、后台版
        也要把舰队科技这类重面板重算一次），用户点刷新会明显卡顿。现在只重绘
        用户真正看过的面板：没点开过的页签保持骨架，等用户点开时再首次渲染。
        """
        with self._page_lock:
            if getattr(self, "page", None) != "Stat":
                return
            if getattr(self, "_statistics_cache_key", None) is None:
                return

            self._statistics_source_signature = (
                self._get_statistics_source_signature()
            )
            self._set_statistics_refresh_pending(False)
        # 刷新同样走后台填充：点击立即返回，各分区在任务里重绘
        # （同步版整页实测 0.9 秒，按钮期间页面无响应）。
        # 填充任务内部按 _st_loaded_panels 过滤，这里不用再判断。
        self._queue_statistics_fill(force=True)

    def _refresh_statistics_if_changed(self) -> None:
        """检测当前实例的本地统计数据变化并更新刷新提示。"""
        if getattr(self, "page", None) != "Stat":
            return
        if (
            getattr(self, "_statistics_cache_key", None)
            != self._get_statistics_cache_key()
        ):
            return

        source_signature = self._get_statistics_source_signature()
        self._set_statistics_refresh_pending(
            source_signature
            != getattr(self, "_statistics_source_signature", None)
        )

    def _set_statistics_refresh_pending(self, pending: bool) -> None:
        """只更新刷新提示，不替换用户正在查看的统计 DOM。"""
        if pending == getattr(self, "_statistics_refresh_pending", False):
            return
        self._statistics_refresh_pending = pending
        run_js(
            """
            (function () {
                var toolbar = document.getElementById(
                    "pywebio-scope-statistics-toolbar"
                );
                var button = toolbar && toolbar.querySelector("button");
                if (!button) return;
                button.classList.toggle("statistics-refresh-pending", pending);
                button.title = pending ? refreshHint : "";
                button.setAttribute(
                    "aria-label",
                    pending ? refreshHint : button.textContent.trim()
                );
            })();
            """,
            pending=pending,
            refreshHint=t("Gui.Stat.NewDataAvailable"),
        )

    # ------------------------------------------------------------------
    # 页头 / 页签：静态骨架
    # ------------------------------------------------------------------
    def _statistics_instance_name(self) -> str:
        """当前统计页对应的实例名。"""
        return getattr(self, "alas_name", None) or "default"

    def _get_statistics_data_state(self):
        """探测本地统计库状态。

        Returns:
            (最新写入时间或 None, 状态字符串)
            状态：'ok'（7 天内有写入）/ 'stale'（更早）/ 'missing'（一个数据源都没有）
        """
        project_root = Path(__file__).resolve().parents[2]
        instance_name = self._statistics_instance_name()
        candidates = (
            project_root / "config" / "cl1_data.db",
            project_root / "config" / "azurstats_local.db",
            project_root / "config" / "resource_delta.db",
            project_root / "log" / "cl1" / instance_name / "ship_exp_data.json",
        )
        newest = None
        for base in candidates:
            # WAL 里的写入也要算进来，否则数据已更新但 .db 的 mtime 还是旧的
            for path in (base, Path(str(base) + "-wal")):
                try:
                    stamp = path.stat().st_mtime
                except OSError:
                    continue
                if newest is None or stamp > newest:
                    newest = stamp
        if newest is None:
            return None, "missing"
        latest = datetime.fromtimestamp(newest)
        if datetime.now() - latest > timedelta(days=_STAT_SOURCE_STALE_DAYS):
            return latest, "stale"
        return latest, "ok"

    def _statistics_meta_text(self, latest) -> str:
        """页头第二行：数据截至时间 + 实例名。

        注意：lang.t() 内部已经会对翻译串做 .format(*args, **kwargs)，
        所以占位符参数必须传给 t() 本身，不能再在外面套一次 .format()
        （那样 t() 会先以无参 format 解析 {time} 并抛 KeyError）。
        """
        template = "Gui.Stat.PageMeta"
        try:
            return t(
                template,
                time=latest.strftime("%Y-%m-%d %H:%M") if latest else "--",
                instance=self._statistics_instance_name(),
            )
        except (KeyError, IndexError, ValueError):
            # 翻译串被改坏了（占位符缺失或写错）时不让整页挂掉
            return t(template)

    def _build_statistics_head_html(self) -> str:
        """页头：标题 + 数据时间/实例 + 数据源状态 + 右侧操作位。

        右侧的刷新按钮由 _layout_statistics_panels 注入的 JS 搬进
        #statistics-head-actions，所以这里先放状态徽标，按钮会排在它后面。
        """
        latest, state = self._get_statistics_data_state()
        meta = self._statistics_meta_text(latest)
        chip = (
            f'<span class="st-source is-{state}">'
            f'<i class="st-source-dot"></i>'
            f'{html_escape(t(_STAT_SOURCE_KEYS.get(state, "Gui.Stat.SourceMissing")))}'
            "</span>"
        )
        return (
            '<div class="st-page">'
            '<div class="st-head">'
            "<div>"
            f"<h1>{html_escape(t('Gui.Stat.PageTitle'))}</h1>"
            f'<div class="st-head-meta">{html_escape(meta)}</div>'
            "</div>"
            '<div class="st-head-actions" id="statistics-head-actions">'
            f"{chip}"
            "</div>"
            "</div>"
        )

    def _build_statistics_tabs_html(self) -> str:
        """下划线式页签。交互由 _layout_statistics_panels 注入的 JS 接管。"""
        buttons = []
        for panel_id, label_key, badge_key in _STAT_TABS:
            selected = panel_id == _DEFAULT_PANEL
            badge = (
                f'<span class="st-tab-badge">{html_escape(t(badge_key))}</span>'
                if badge_key
                else ""
            )
            buttons.append(
                '<button class="st-tab" type="button" role="tab"'
                f' data-panel="{panel_id}"'
                f' id="statistics-tab-{panel_id}"'
                f' aria-controls="st-panel-{panel_id}"'
                f' aria-selected="{"true" if selected else "false"}"'
                f' tabindex="{"0" if selected else "-1"}">'
                f"{html_escape(t(label_key))}{badge}</button>"
            )
        return (
            '<div class="st-tabs" id="statistics-tabs" role="tablist">'
            + "".join(buttons)
            + '<span class="st-tab-indicator" id="statistics-tab-indicator"></span>'
            "</div>"
        )

    # ------------------------------------------------------------------
    # 分页归位
    # ------------------------------------------------------------------
    def _layout_statistics_panels(self) -> None:
        """建面板、搬 scope、插骨架、接页签交互（懒加载版）。

        与旧版的区别：
        1. 未加载面板先插一个「骨架」占位，高度取上次实测值（方案 C），
           首次访问用估计值，避免内容到达时页面高度突变（割裂感）；
        2. 页签点击除了切 CSS 类，还经隐藏 pin 通知服务端按需渲染该面板，
           裸 HTML 按钮没有回调通道，只能走 pin 桥；
        3. 已加载的面板切回来只切类名，不重新渲染。
        """
        run_js(
            """
            (function () {
                var PANEL_ORDER = panelOrder;
                var PANEL_SCOPES = panelScopes;
                var DEFAULT_PANEL = defaultPanel;
                var SKELETON_HEIGHT = skeletonHeight;
                var LOADED = loadedPanels;
                var STORAGE_KEY = "__alasStatPanel";

                /* 当前页签记在 localStorage（不是 window）：远控频繁断线重连会整页
                   重载，window 上的状态全丢，用户每次都被拽回默认页签。 */
                function readStored() {
                    try { return window.localStorage.getItem(STORAGE_KEY); }
                    catch (e) { return null; }
                }
                function writeStored(value) {
                    try { window.localStorage.setItem(STORAGE_KEY, value); }
                    catch (e) {}
                }

                var host = document.getElementById("statistics-panels");
                var tabsBox = document.getElementById("statistics-tabs");
                if (!host || !tabsBox) return;

                if (!window.__alasStatLoaded) window.__alasStatLoaded = {};
                for (var k = 0; k < LOADED.length; k++) {
                    window.__alasStatLoaded[LOADED[k]] = true;
                }

                /* 1) 建面板（重新挂载时 host 是新的，dataset 随之重置，天然幂等） */
                if (!host.dataset.built) {
                    for (var i = 0; i < PANEL_ORDER.length; i++) {
                        var panel = document.createElement("div");
                        panel.className = "st-panel";
                        panel.id = "st-panel-" + PANEL_ORDER[i];
                        panel.setAttribute("role", "tabpanel");
                        panel.setAttribute(
                            "aria-labelledby",
                            "statistics-tab-" + PANEL_ORDER[i]
                        );
                        host.appendChild(panel);
                    }
                    host.dataset.built = "1";
                }

                /* 2) 把已存在的 scope 搬进对应面板 */
                for (var p = 0; p < PANEL_ORDER.length; p++) {
                    var id = PANEL_ORDER[p];
                    var target = document.getElementById("st-panel-" + id);
                    if (!target) continue;
                    var names = PANEL_SCOPES[id] || [];
                    for (var n = 0; n < names.length; n++) {
                        var el = document.getElementById(
                            "pywebio-scope-" + names[n]
                        );
                        if (el && el.parentNode !== target) target.appendChild(el);
                    }
                }

                /* 3) 刷新按钮搬进页头右侧 */
                var toolbar = document.getElementById(
                    "pywebio-scope-statistics-toolbar"
                );
                var actions = document.getElementById("statistics-head-actions");
                if (toolbar && actions && toolbar.parentNode !== actions) {
                    actions.appendChild(toolbar);
                }

                /* 4) 未加载面板插骨架：固定高度占位，避免内容到达时高度跳变。
                      骨架是纯 CSS 灰条，不含任何「加载中」文字。 */
                function ensureSkeleton(panelId) {
                    var panel = document.getElementById("st-panel-" + panelId);
                    if (!panel) return;
                    if (window.__alasStatLoaded[panelId]) return;
                    if (document.getElementById("st-skeleton-" + panelId)) return;
                    var height = SKELETON_HEIGHT[panelId] || 400;
                    var sk = document.createElement("div");
                    sk.id = "st-skeleton-" + panelId;
                    sk.className = "st-skeleton";
                    sk.style.height = height + "px";
                    var lines = "";
                    for (var i = 0; i < 6; i++) {
                        lines += '<div class="st-skeleton-line" style="width:'
                            + (88 - i * 11) + '%"></div>';
                    }
                    sk.innerHTML = '<div class="st-skeleton-title"></div>' + lines;
                    panel.appendChild(sk);
                }

                /* 5) 页签交互 */
                var tabs = tabsBox.querySelectorAll(".st-tab");
                var indicator = document.getElementById("statistics-tab-indicator");
                var resizeQueued = false;

                function moveIndicator(tab) {
                    if (!indicator || !tab) return;
                    indicator.style.width = tab.offsetWidth + "px";
                    indicator.style.transform =
                        "translateX(" + tab.offsetLeft + "px)";
                }

                /* 通知服务端：该页签需要渲染（经隐藏 pin，裸按钮没有回调） */
                var retryCount = {};
                function requestPanel(panelId) {
                    var bridge = document.querySelector(
                        'input[name="st_bridge_pin"]'
                    );
                    if (!bridge) return;
                    bridge.value = "panel|" + panelId;
                    bridge.dispatchEvent(new Event("input", {bubbles: true}));
                    bridge.dispatchEvent(new Event("change", {bubbles: true}));
                    /* 远控 P2P 链路会丢消息：等不到内容就再要一次（最多 3 次）。
                       没有这层兜底，用户会看到「点了页签永远是骨架」。 */
                    retryCount[panelId] = (retryCount[panelId] || 0) + 1;
                    if (retryCount[panelId] <= 3) {
                        window.setTimeout(function () {
                            if (window.__alasStatLoaded[panelId]) return;
                            requestPanel(panelId);
                        }, 2500 * retryCount[panelId]);
                    }
                }

                function activate(id, remember) {
                    if (PANEL_ORDER.indexOf(id) === -1) id = DEFAULT_PANEL;
                    /* 先量页签指示条再切面板：面板类名切换会让新面板首次参与
                       布局，此时 moveIndicator 里读 offsetWidth 会同步强制完成
                       整页布局（实测科技列表 / 心智计算器这类大面板每次冷切换
                       阻塞主线程约 0.5 秒）。页签几何只与页签自身有关，提前算，
                       面板的布局与绘制交给浏览器异步完成。 */
                    var target = null;
                    for (var s = 0; s < tabs.length; s++) {
                        if (tabs[s].getAttribute("data-panel") === id) {
                            target = tabs[s];
                        }
                    }
                    moveIndicator(target);
                    for (var i = 0; i < PANEL_ORDER.length; i++) {
                        var panel = document.getElementById(
                            "st-panel-" + PANEL_ORDER[i]
                        );
                        if (panel) {
                            panel.classList.toggle(
                                "is-active",
                                PANEL_ORDER[i] === id
                            );
                        }
                    }
                    for (var t = 0; t < tabs.length; t++) {
                        var on = tabs[t].getAttribute("data-panel") === id;
                        tabs[t].setAttribute("aria-selected", on ? "true" : "false");
                        tabs[t].setAttribute("tabindex", on ? "0" : "-1");
                    }
                    /* 懒加载：未加载的面板先铺骨架，再请服务端渲染；
                       已加载的直接切类名即可（不重复渲染）。 */
                    ensureSkeleton(id);
                    if (!window.__alasStatLoaded[id]) requestPanel(id);
                    if (remember !== false) writeStored(id);
                    /* 收尾放在下一帧：页面高度变化会增减滚动条、挪动页签位置，
                       届时校正一次指示条；图表也要按新宽度重绘一次。放在 rAF
                       里避免在点击处理中同步强制整页布局。 */
                    if (!resizeQueued) {
                        resizeQueued = true;
                        var settle = function () {
                            resizeQueued = false;
                            var selected = tabsBox.querySelector(
                                '.st-tab[aria-selected="true"]'
                            );
                            moveIndicator(selected);
                            window.dispatchEvent(new Event("resize"));
                        };
                        if (window.requestAnimationFrame) {
                            window.requestAnimationFrame(settle);
                        } else {
                            settle();
                        }
                    }
                }

                if (!tabsBox.dataset.bound) {
                    tabsBox.dataset.bound = "1";
                    tabsBox.addEventListener("click", function (event) {
                        var tab = event.target.closest(".st-tab");
                        if (tab) activate(tab.getAttribute("data-panel"));
                    });
                    tabsBox.addEventListener("keydown", function (event) {
                        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
                            return;
                        }
                        var list = [];
                        for (var i = 0; i < tabs.length; i++) list.push(tabs[i]);
                        var index = -1;
                        for (var j = 0; j < list.length; j++) {
                            if (list[j].getAttribute("aria-selected") === "true") {
                                index = j;
                            }
                        }
                        var step = event.key === "ArrowRight" ? 1 : -1;
                        var next = list[(index + step + list.length) % list.length];
                        if (next) {
                            next.focus();
                            activate(next.getAttribute("data-panel"));
                        }
                        event.preventDefault();
                    });
                }

                /* 首屏只激活默认页签：只有它会被渲染，其余等用户点开 */
                activate(readStored() || DEFAULT_PANEL, false);

                /* 远控下切回前台时补一次：离开期间丢掉的渲染请求在这里补发，
                   避免用户回来只看到骨架。全局只绑一次，回调内按当前 DOM 取页签。 */
                if (!window.__alasStatReaskBound) {
                    window.__alasStatReaskBound = true;
                    var reaskActive = function () {
                        var box = document.getElementById("statistics-tabs");
                        if (!box) return;
                        var on = box.querySelector(
                            '.st-tab[aria-selected="true"]'
                        );
                        if (!on) return;
                        var pid = on.getAttribute("data-panel");
                        if (pid && !window.__alasStatLoaded[pid]) requestPanel(pid);
                    };
                    document.addEventListener("visibilitychange", function () {
                        if (!document.hidden) reaskActive();
                    });
                    window.addEventListener("focus", reaskActive);
                }
                /* 页面每次重新挂载都会生成新的 tabsBox：监听器若每次都绑定，
                   会不断累积并持有已销毁的 DOM（频繁切页即内存泄漏 + 重复重绘）。
                   这里全局只绑一次，回调内按 id 重新取当前挂载的页签。 */
                if (!window.__alasStatResizeBound) {
                    window.__alasStatResizeBound = true;
                    window.addEventListener("resize", function () {
                        var box = document.getElementById("statistics-tabs");
                        if (!box) return;
                        var active = box.querySelector(
                            '.st-tab[aria-selected="true"]'
                        );
                        var indicator = document.getElementById(
                            "statistics-tab-indicator"
                        );
                        if (!active || !indicator) return;
                        indicator.style.width = active.offsetWidth + "px";
                        indicator.style.transform =
                            "translateX(" + active.offsetLeft + "px)";
                    });
                }
            })();
            """,
            panelOrder=list(_STAT_PANEL_SCOPES.keys()),
            panelScopes={
                key: list(value) for key, value in _STAT_PANEL_SCOPES.items()
            },
            defaultPanel=_DEFAULT_PANEL,
            # 骨架高度：上次实测值优先，没有实测用估计值（方案 C）
            skeletonHeight=self._stat_skeleton_heights(),
            # 已加载面板集合：重新挂载时让前端知道哪些面板不用再铺骨架
            loadedPanels=sorted(getattr(self, "_st_loaded_panels", None) or ()),
        )
    def _render_statistics_sections(self, force: bool = False) -> None:
        """渲染「已加载」的统计分区（懒加载：没点开过的页签一律不渲染）。

        改造前这里一次画完全部 8 个分区（含舰队科技 / 心智计算器这类重面板），
        实测整页同步渲染 3.6 秒，期间用户点任何按钮都没响应。改成按需加载后，
        首屏只画默认页签（大世界），其余面板等用户点开页签时才由
        ``_render_statistics_panel`` 补渲染。

        force=True 时先重读数据（挂载与点「刷新」时用）。「刷新」只刷新已加载的
        面板，不会把没看过的面板一起画出来。
        """
        loaded = getattr(self, "_st_loaded_panels", None)
        if not loaded:
            loaded = {_DEFAULT_PANEL}
        for panel_id in _STAT_PANEL_SCOPES:
            if panel_id not in loaded:
                continue
            self._render_statistics_panel_sections(panel_id, force=force)

    def _render_statistics_panel_sections(self, panel_id: str, force: bool = False) -> None:
        """渲染单个面板包含的全部分区（不负责清理骨架，由调用方处理）。

        分区与面板的对应关系见 ``_STAT_PANEL_RENDERERS``；方法名在模块里可能
        不存在（精简组合 / 测试替身），缺失的静默跳过，不影响其余分区。
        """
        for method_name in _STAT_PANEL_RENDERERS.get(panel_id, ()):
            method = getattr(self, method_name, None)
            if method is None:
                continue
            # 舰队两个面板的渲染入口吃 force 参数，其余分区不带参数
            if method_name in ("_ft_render_tech_panel", "_ft_render_mind_panel"):
                method(force=force)
            else:
                method()

    @render_locked
    def _render_statistics_panel(self, panel_id: str) -> None:
        """按需渲染一个面板（用户第一次点开该页签时由桥回调触发）。

        幂等：已加载过的面板直接返回，重复点击不会重复渲染（避免同一个面板
        被画两遍导致内容翻倍——PyWebIO 每个回调新起线程，连点即并发）。
        渲染完成后调 ``_mark_stat_panel_loaded`` 撤掉骨架并把实测高度记下来，
        下次进页面用真实高度占位，切换时页面不会跳。
        """
        if panel_id not in _STAT_PANEL_SCOPES:
            return
        if getattr(self, "page", None) != "Stat":
            return
        loaded = getattr(self, "_st_loaded_panels", None)
        if loaded is None:
            loaded = self._st_loaded_panels = set()
        if panel_id in loaded:
            return
        with self._page_lock:
            # 拿锁后再校验一次：排队期间用户可能已经切走页面
            if getattr(self, "page", None) != "Stat":
                return
            if getattr(self, "_statistics_cache_key", None) is None:
                return
            if panel_id in loaded:
                return
            loaded.add(panel_id)
            self._render_statistics_panel_sections(panel_id, force=True)
        # 舰队面板首次加载后补登记它的数据刷新任务（alas_set_stat 里只在
        # 已加载时才登记，避免用户没点开就每 5 秒空转读配置）。
        if panel_id == _PANEL_FLEET:
            self._st_ensure_fleet_refresh_task()
        self._mark_stat_panel_loaded(panel_id)

    def _st_ensure_fleet_refresh_task(self) -> None:
        """登记舰队面板的数据刷新轮询任务（按任务名幂等，不叠加）。

        ``_ft_data_refresh`` 每 5 秒调一次 ``load_config()``（完整读 JSON +
        合并模板 + 写盘）。用户没点开舰队页签时它纯属空转，是界面卡死的
        直接成因之一，所以改成「面板加载后才登记」。
        """
        handler = getattr(self, "task_handler", None)
        if handler is None:
            return
        try:
            if handler.get_task("_ft_data_refresh") is not None:
                return
        except Exception:
            pass
        handler.add(self._ft_data_refresh, 5, True)
    def _st_bind_callbacks(self) -> None:
        """注册统计页点击桥的回调；页面重入不重复绑定。

        ``pin_on_change`` 对同一控件名重复注册会覆盖旧回调，但这里仍然用
        ``_st_callbacks_bound`` 去重：重入时重复注册会让服务端多一份监听，
        连点时同一次点击被处理两次（渲染翻倍的老坑）。

        必须确认当前线程有活动的 WebIO 会话：``pin_on_change`` 在没有会话时
        会**永久阻塞**（等待 input 事件源注册），单测 / 脚本模式下直接挂死。
        这与 ``use_scope`` 在无会话时拉起本地服务器是同一类陷阱。
        """
        if getattr(self, "_st_callbacks_bound", False):
            return
        if not _has_webio_session():
            # 无会话（单测 / 脚本模式）时跳过，避免 pywebio 回退到 Script Mode
            # 而阻塞；真实会话下若出现这行，说明桥接会失效、页签点不开。
            logger.debug("[Statistics] 无 WebIO 会话，跳过页签桥绑定")
            return
        pin_on_change(
            name=ST_BRIDGE_PIN,
            onchange=self._st_on_bridge,
            clear=True,
            serial_mode=True,
        )
        self._st_callbacks_bound = True

    def _stat_skeleton_heights(self) -> dict:
        """返回各面板的骨架高度（px）：上次实测值优先，否则用估计值。

        方案 C 的核心：骨架按「这个面板上次画出来有多高」占位，内容替换时
        页面高度几乎不变，就不会出现「先矮后高」的跳变（割裂感）。实测高度
        由 ``_mark_stat_panel_loaded`` 通过点击桥回传，存在会话里。
        """
        measured = getattr(self, "_st_panel_heights", None)
        if not isinstance(measured, dict):
            measured = {}
        heights = {}
        for panel_id, estimate in _STAT_SKELETON_HEIGHT.items():
            value = measured.get(panel_id)
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 0
            if value <= 0:
                value = estimate
            heights[panel_id] = max(_STAT_SKELETON_MIN_HEIGHT, value)
        return heights

    def _mark_stat_panel_loaded(self, panel_id: str) -> None:
        """撤掉该面板的占位骨架，并把面板实测高度回传给服务端记住。

        高度回传用于「方案 C」：下次再进统计页时骨架按上次真实高度占位，
        内容替换时页面不会出现「从矮突然变高」的跳变。
        """
        run_js(
            """
            (function () {
                var panel = document.getElementById("st-panel-" + panelId);
                if (!panel) return;
                var skeleton = document.getElementById("st-skeleton-" + panelId);
                if (skeleton) skeleton.parentNode.removeChild(skeleton);
                panel.dataset.loaded = "1";
                if (window.__alasStatLoaded) window.__alasStatLoaded[panelId] = true;
                // 高度回传：服务端记下来，下次做骨架高度用（不触发再次渲染）
                var bridge = document.querySelector(
                    'input[name="st_bridge_pin"]'
                );
                if (!bridge) return;
                bridge.value = "height|" + panelId + "|" + Math.round(panel.offsetHeight);
                bridge.dispatchEvent(new Event("input", {bubbles: true}));
                bridge.dispatchEvent(new Event("change", {bubbles: true}));
            })();
            """,
            panelId=panel_id,
        )

    @render_locked
    def _st_on_bridge(self, value) -> None:
        """统计页点击桥回调：接收页签点击（panel|id）与高度回传（height|id|px）。

        PyWebIO 的 ``put_html`` 里输出的裸 ``<button>`` 没有回调通道，页签点击
        只能靠隐藏 input 转发到服务端（与舰队科技点击桥同一套做法）。
        高度回传走同一根 pin，用前缀区分，避免多绑一个控件。
        """
        text = "" if value is None else str(value).strip()
        if not text:
            return
        parts = text.split("|")
        if parts[0] == "height" and len(parts) >= 3:
            try:
                heights = getattr(self, "_st_panel_heights", None)
                if heights is None:
                    heights = self._st_panel_heights = {}
                heights[parts[1]] = max(80, int(parts[2]))
            except (TypeError, ValueError):
                pass
            # 高度回传只记数，绝不能再触发渲染（否则就是无限循环）
            return
        if parts[0] == "panel" and len(parts) >= 2:
            self._render_statistics_panel(parts[1])

    # ------------------------------------------------------------------
    # 查看月份：体力图 / 侵蚀一卡片 / 大世界收获 三处共用
    # ------------------------------------------------------------------
    def _stat_selected_month(self):
        """用户显式选中的月份 ``(year, month)``；``None`` 表示本月。"""
        value = getattr(self, "_stat_view_month", None)
        if isinstance(value, (tuple, list)) and len(value) == 2:
            try:
                year, month = int(value[0]), int(value[1])
            except (TypeError, ValueError):
                return None
            if 1 <= month <= 12:
                return year, month
        return None

    def _stat_month_pair(self):
        """当前查看月份 ``(year, month)``；未选中时取当前月。

        用 ``current_time()`` 而不是 ``datetime.now()``：与三块面板取数时的
        时间口径保持一致（用户可以配置时间源）。
        """
        selected = self._stat_selected_month()
        if selected is not None:
            return selected
        now = current_time()
        return now.year, now.month

    def _stat_month_key(self):
        """当前查看月份键，形如 ``"2026-09"``。"""
        year, month = self._stat_month_pair()
        return f"{year:04d}-{month:02d}"

    def _stat_month_is_current(self):
        """当前查看的是否是本月。"""
        now = current_time()
        return self._stat_month_pair() == (now.year, now.month)

    def _stat_available_months(self):
        """可选月份（新→旧）：cl1 库 ∪ 大世界掉落库 ∪ 当前月。

        两个库的覆盖范围不同（体力图与侵蚀卡片在 ``cl1_data.db``，大世界收获
        另有 ``azurstats_local.db``），取并集后任一库有记录即可选。单个数据源
        读取失败只记日志并跳过，不影响另一侧 —— 用户至少还能回到本月。
        """
        from module.statistics.azurstats import AzurStats
        from module.statistics.opsi_month import get_available_months

        instance_name = self._statistics_instance_name()
        months = set()
        sources = (
            ("cl1_data.db", lambda: get_available_months(instance_name)),
            ("azurstats_local.db", AzurStats.get_opsi_drop_available_months),
        )
        for label, loader in sources:
            try:
                months |= {(int(year), int(month)) for year, month in loader()}
            except Exception:
                logger.warning(
                    f"[Statistics] 读取 {label} 的可用月份失败", exc_info=True
                )
        now = current_time()
        months.add((now.year, now.month))
        return sorted(months, reverse=True)

    @render_locked
    def _open_stat_month_picker(self) -> None:
        """弹出月份选择器（本月 + 有记录的历史月份）。

        按钮文案沿用大世界收获那套 ``Gui.Stat.MeowLoot*`` 键（本月 / 选择查看
        月份 / 暂无历史月份），避免维护两套等价键。
        """
        selected = self._stat_selected_month()
        now = current_time()
        buttons = [
            {
                "label": t(
                    "Gui.Stat.MeowLootCurrentMonthOption",
                    month=f"{now.year:04d}-{now.month:02d}",
                ),
                "value": None,
                "color": "primary" if selected is None else "secondary",
            }
        ]
        buttons += [
            {
                "label": f"{year:04d}-{month:02d}",
                "value": (year, month),
                "color": "primary" if selected == (year, month) else "secondary",
            }
            for year, month in self._stat_available_months()
            if (year, month) != (now.year, now.month)
        ]
        if len(buttons) == 1:
            toast(t("Gui.Stat.MeowLootNoHistoryMonth"))
            return

        with popup(t("Gui.Stat.MeowLootPickMonthTitle")):
            put_buttons(buttons, onclick=self._set_stat_month)

    @render_locked
    def _set_stat_month(self, value) -> None:
        """切换查看月份并重绘受影响的区块（``None`` = 回到本月）。"""
        close_popup()
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            value = None
        self._stat_view_month = value
        self._render_stat_month_sections()

    @render_locked
    def _render_stat_month_sections(self) -> None:
        """只重绘跟随查看月份的区块。

        资源消耗（最近 7 天）、舰船经验（当前练级周期）与委托收益（自带选月）
        与这个月份无关，不必跟着重建 —— 少发一屏输出，切月才不会发卡。
        """
        with self._page_lock:
            if getattr(self, "page", None) != "Stat":
                return
            if getattr(self, "_statistics_cache_key", None) is None:
                return
            self._render_ap_chart()
            self._render_opsi_month_view()

    def _get_statistics_cache_key(self):
        """返回会影响统计页文案与数据归属的键。"""
        return getattr(self, "alas_name", None), lang.LANG

    def _get_statistics_source_signature(self):
        """以廉价的文件版本检查代替重复解析和绘图。"""
        project_root = Path(__file__).resolve().parents[2]
        instance_name = getattr(self, "alas_name", None) or "default"
        paths = (
            project_root / "config" / "cl1_data.db",
            project_root / "config" / "cl1_data.db-wal",
            project_root / "config" / "azurstats_local.db",
            project_root / "config" / "azurstats_local.db-wal",
            project_root / "config" / "resource_delta.db",
            project_root / "config" / "resource_delta.db-wal",
            project_root / "log" / "cl1" / instance_name / "ship_exp_data.json",
        )
        return date.today().isoformat(), tuple(
            self._get_statistics_file_version(path) for path in paths
        )

    @staticmethod
    def _get_statistics_file_version(path: Path):
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size
