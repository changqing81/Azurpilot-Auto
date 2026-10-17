"""WebUI 统计页装配器。

改版要点（对应 statistics-v2 原型）：
    页头（标题 + 刷新）→ 下划线页签 → 六个面板
    大世界 / 委托 / 科研（预留） / 资源 / 科技列表 / 心智计算器

实现方式刻意保持「零侵入」：各统计子视图（ap_chart / opsi_stats /
commission_income / resource_delta / ship_exp_table）以及舰队科技列表与
心智计算器（ft_tech / ft_mind）的渲染代码一行未改，仍然往同名 scope 里输出。
本模块只负责

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
    ("research", "Gui.Stat.TabResearch", "Gui.Stat.TabResearchBadge"),
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
# research 面板没有数据 scope，由本模块直接注入占位 HTML。
_STAT_PANEL_SCOPES = {
    "opsi": ("ap_chart", "opsi_stats", "ship_exp_table"),
    "commission": ("commission_income",),
    "research": (),
    "resource": ("resource_delta",),
    # 面板内容整块搬入：ft_tech / ft_mind 由舰队科技 Mixin 渲染（内容不变）。
    "fleet": ("ft_tech",),
    "mind": ("ft_mind",),
}

# 科研占位块在 DOM 里的 id，会被搬进 research 面板
_RESEARCH_SLOT_ID = "statistics-research-slot"

_DEFAULT_PANEL = "opsi"

# 科研占位的三个槽位：(名称 i18n 键, 数据契约说明 i18n 键)
_RESEARCH_SLOTS = (
    ("Gui.Stat.ResearchSlotProgress", "Gui.Stat.ResearchSlotProgressDesc"),
    ("Gui.Stat.ResearchSlotQueue", "Gui.Stat.ResearchSlotQueueDesc"),
    ("Gui.Stat.ResearchSlotCost", "Gui.Stat.ResearchSlotCostDesc"),
)


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

        cache_key = self._get_statistics_cache_key()
        cached_key = getattr(self, "_statistics_cache_key", None)

        if cached_key != cache_key:
            self._mount_statistics_page(cache_key)
        else:
            self._refresh_statistics_if_changed()
            # 面板常驻：切页会随 pending_delete 清掉两个扫描开关的轮询任务，
            # 每次进入补画一次工具栏（登记按任务名幂等，不会叠加）。
            self._ft_render_scan_controls()

        # 原先的 5 个任务会在进页后立即各重绘一次。现在仅轮询本地数据源
        # 版本并提示存在新数据，不再打断用户正在查看的图表状态。
        self.task_handler.add(self._refresh_statistics_if_changed, 15, True, group="slow")
        # 科技记录 / 心智清单被扫描任务外部写入时自动刷新两个面板（方法内
        # 自带「不在统计页就不重绘」的守卫）。
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
                put_scope("ap_chart", [])
                put_scope("opsi_stats", [])
                put_scope("commission_income", [])
                put_scope("resource_delta", [])
                put_scope("ship_exp_table", [])
                put_html(self._build_research_placeholder_html())
                # 舰队科技列表 / 心智计算器的内容宿主（整块搬进各自面板），
                # 外加点击桥的隐藏 pin（ft_bridge 由舰队皮肤隐藏）。
                put_scope("ft_tech", [])
                put_scope("ft_mind", [])
                put_scope("ft_bridge", [put_input(name=BRIDGE_PIN, value="")])

            self._statistics_cache_key = cache_key
            self._ft_bind_callbacks()
            run_js(build_fleet_tech_bridge_script(BRIDGE_PIN))
            # 先分页归位再渲染：体力图表的 canvas 在绘制时读取容器宽度，
            # 若此时还不在激活面板里会量到 0 宽。
            self._layout_statistics_panels()
            # 各分区改后台首跑（同总览仪表盘）：同步渲染整页实测 3.6 秒，
            # 骨架（页头/页签/空面板）落地后再填充，进场观感从秒级变即时。
            self._statistics_source_signature = (
                self._get_statistics_source_signature()
            )
            self._statistics_refresh_pending = False
            self._queue_statistics_fill()

    def _queue_statistics_fill(self) -> None:
        """把各分区渲染排进后台首跑；重复入队用标志位去重。

        必须传**生成器对象**而不是生成器函数：``TaskHandler.add`` 对普通
        可调用对象会套 ``get_generator`` 包装（每次 tick 只 yield 出新建的
        生成器、从不驱动它），任务将永远不执行——2026-10-17 真机现场：
        分区从未加载，第二次进统计页在补画扫描开关时炸 ``_ft_report``。
        传生成器对象走 ``isinstance(func, Generator)`` 分支，按 delay 驱动。

        填充任务为一次性（跑完自调 delay 静默），随切页 pending_delete 清理；
        执行前校验仍在统计页，避免向已清空的 scope 写孤儿容器（同仪表盘守卫）。
        """
        if getattr(self, "_statistics_fill_queued", False):
            return
        self._statistics_fill_queued = True
        self.task_handler.add(self._fill_statistics_sections(), 0.05, True, group="slow")

    def _fill_statistics_sections(self):
        yield
        self._statistics_fill_queued = False
        if getattr(self, "page", None) != "Stat":
            return
        with self._page_lock:
            if getattr(self, "_statistics_cache_key", None) is None:
                return
            self._render_statistics_sections(force=True)
        # 填充一次即安静：把自身节奏调长，避免每秒空转（任务随切页被清理）。
        task = getattr(self.task_handler, "_task", None)
        if task is not None:
            task.delay = 3600
        while True:
            yield

    @render_locked
    def _refresh_statistics_page(self) -> None:
        """刷新已挂载的全部统计模块。"""
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
            self._queue_statistics_fill()

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
    # 页头 / 页签 / 科研占位：静态骨架
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

    def _build_research_placeholder_html(self) -> str:
        """科研页的结构性占位：不是一句「暂无数据」，而是划好边界的槽位。"""
        slots = "".join(
            '<div class="st-ph-slot">'
            f'<div class="st-ph-slot-name">{html_escape(t(name_key))}</div>'
            f'<div class="st-ph-slot-desc">{html_escape(t(desc_key))}</div>'
            f'<span class="st-ph-slot-state">'
            f'{html_escape(t("Gui.Stat.ResearchSlotPending"))}</span>'
            "</div>"
            for name_key, desc_key in _RESEARCH_SLOTS
        )
        icon = (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
            ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M9 3h6M10 3v5.5L4.5 18A2 2 0 006.2 21h11.6a2 2 0 001.7-3'
            'L14 8.5V3"/></svg>'
        )
        return (
            f'<div id="{_RESEARCH_SLOT_ID}">'
            '<div class="st-ph">'
            f'<div class="st-ph-icon">{icon}</div>'
            '<div class="st-ph-title">'
            f"{html_escape(t('Gui.Stat.ResearchTitle'))}"
            f'<span class="st-ph-badge">'
            f'{html_escape(t("Gui.Stat.TabResearchBadge"))}</span>'
            "</div>"
            f'<div class="st-ph-desc">{html_escape(t("Gui.Stat.ResearchDesc"))}</div>'
            f'<div class="st-ph-slots">{slots}</div>'
            "</div>"
            '<div class="st-empty" style="margin-top:14px">'
            f"<b>{html_escape(t('Gui.Stat.ResearchEmptyTitle'))}</b><br>"
            f"{html_escape(t('Gui.Stat.ResearchEmptyDesc'))}"
            "</div>"
            "</div>"
        )

    # ------------------------------------------------------------------
    # 分页归位
    # ------------------------------------------------------------------
    def _layout_statistics_panels(self) -> None:
        """建面板、搬 scope、接页签交互。"""
        run_js(
            """
            (function () {
                var PANEL_ORDER = panelOrder;
                var PANEL_SCOPES = panelScopes;
                var RESEARCH_SLOT = researchSlotId;
                var DEFAULT_PANEL = defaultPanel;
                var STORAGE_KEY = "__alasStatPanel";

                var host = document.getElementById("statistics-panels");
                var tabsBox = document.getElementById("statistics-tabs");
                if (!host || !tabsBox) return;

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
                var slot = document.getElementById(RESEARCH_SLOT);
                var researchPanel = document.getElementById("st-panel-research");
                if (slot && researchPanel && slot.parentNode !== researchPanel) {
                    researchPanel.appendChild(slot);
                }

                /* 3) 刷新按钮搬进页头右侧 */
                var toolbar = document.getElementById(
                    "pywebio-scope-statistics-toolbar"
                );
                var actions = document.getElementById("statistics-head-actions");
                if (toolbar && actions && toolbar.parentNode !== actions) {
                    actions.appendChild(toolbar);
                }

                /* 4) 页签交互 */
                var tabs = tabsBox.querySelectorAll(".st-tab");
                var indicator = document.getElementById("statistics-tab-indicator");
                var resizeQueued = false;

                function moveIndicator(tab) {
                    if (!indicator || !tab) return;
                    indicator.style.width = tab.offsetWidth + "px";
                    indicator.style.transform =
                        "translateX(" + tab.offsetLeft + "px)";
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
                    if (remember !== false) window[STORAGE_KEY] = id;
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

                activate(window[STORAGE_KEY] || DEFAULT_PANEL, false);
                window.addEventListener("resize", function () {
                    var active = tabsBox.querySelector(
                        '.st-tab[aria-selected="true"]'
                    );
                    moveIndicator(active);
                });
            })();
            """,
            panelOrder=list(_STAT_PANEL_SCOPES.keys()),
            panelScopes={
                key: list(value) for key, value in _STAT_PANEL_SCOPES.items()
            },
            researchSlotId=_RESEARCH_SLOT_ID,
            defaultPanel=_DEFAULT_PANEL,
        )

    def _render_statistics_sections(self, force: bool = False) -> None:
        """统一刷新各统计子视图（舰队两个面板整块重绘，登记任务按名幂等）。

        force=True 时先重读舰队面板的数据（挂载与点「刷新」时用）。
        """
        self._render_ap_chart()
        self._render_opsi_stats()
        self._render_resource_delta()
        self._render_ship_exp()
        self._render_commission_income()
        self._ft_render_tech_panel(force=force)
        self._ft_render_mind_panel(force=force)

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
