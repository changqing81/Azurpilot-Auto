"""WebUI 统计页装配器。

改版要点（对应 statistics-v2 原型）：
    页头（标题 + 刷新）→ 下划线页签 → 四个面板
    大世界 / 委托 / 科研（预留） / 资源

实现方式刻意保持「零侵入」：各统计子视图（ap_chart / opsi_stats /
commission_income / resource_delta / ship_exp_table）的渲染代码一行未改，
仍然往同名 scope 里输出。本模块只负责

    1. 在 statistics-content 里铺好页头、页签和一个空的面板容器；
    2. 用一段 JS 把已存在的 scope 元素搬进对应面板，并接上页签交互。

后续任何子视图的增删改都不需要关心分页结构。
"""

from datetime import date, datetime, timedelta
from html import escape as html_escape
from pathlib import Path

import module.webui.lang as lang
from module.webui.app_dependencies import (
    put_button,
    put_html,
    put_scope,
    run_js,
    t,
    use_scope,
)
from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked


# 面板顺序即页签顺序。元组为 (面板 id, 页签文案 i18n 键, 徽标 i18n 键或 None)
_STAT_TABS = (
    ("opsi", "Gui.Stat.TabOpsi", None),
    ("commission", "Gui.Stat.TabCommission", None),
    ("research", "Gui.Stat.TabResearch", "Gui.Stat.TabResearchBadge"),
    ("resource", "Gui.Stat.TabResource", None),
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
        if not hasattr(self, "_commission_income_period"):
            self._commission_income_period = "month"

        cache_key = self._get_statistics_cache_key()
        cached_key = getattr(self, "_statistics_cache_key", None)

        if cached_key != cache_key:
            self._mount_statistics_page(cache_key)
        else:
            self._refresh_statistics_if_changed()

        # 原先的 5 个任务会在进页后立即各重绘一次。现在仅轮询本地数据源
        # 版本并提示存在新数据，不再打断用户正在查看的图表状态。
        self.task_handler.add(self._refresh_statistics_if_changed, 15, True, group="slow")

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

            self._statistics_cache_key = cache_key
            # 先分页归位再渲染：体力图表的 canvas 在绘制时读取容器宽度，
            # 若此时还不在激活面板里会量到 0 宽。
            self._layout_statistics_panels()
            self._render_statistics_sections()
            self._statistics_source_signature = (
                self._get_statistics_source_signature()
            )
            self._statistics_refresh_pending = False

    @render_locked
    def _refresh_statistics_page(self) -> None:
        """刷新已挂载的全部统计模块。"""
        with self._page_lock:
            if getattr(self, "page", None) != "Stat":
                return
            if getattr(self, "_statistics_cache_key", None) is None:
                return

            self._render_statistics_sections()
            self._statistics_source_signature = (
                self._get_statistics_source_signature()
            )
            self._set_statistics_refresh_pending(False)

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

                function moveIndicator(tab) {
                    if (!indicator || !tab) return;
                    indicator.style.width = tab.offsetWidth + "px";
                    indicator.style.transform =
                        "translateX(" + tab.offsetLeft + "px)";
                }

                function activate(id, remember) {
                    if (PANEL_ORDER.indexOf(id) === -1) id = DEFAULT_PANEL;
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
                        if (on) moveIndicator(tabs[t]);
                    }
                    if (remember !== false) window[STORAGE_KEY] = id;
                    /* 图表按容器宽度绘制，切回来时按新宽度重绘一次 */
                    window.dispatchEvent(new Event("resize"));
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

    def _render_statistics_sections(self) -> None:
        """统一刷新各统计子视图。"""
        self._render_ap_chart()
        self._render_opsi_stats()
        self._render_resource_delta()
        self._render_ship_exp()
        self._render_commission_income()

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
