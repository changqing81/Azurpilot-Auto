"""WebUI仪表盘刷新逻辑"""

import json
import os
import time

from module.webui.app_dependencies import (
    Function,
    LogRes,
    clear,
    current_time,
    datetime,
    deep_get,
    get_dashboard_scope_id,
    get_group_scope_id,
    logger,
    put_button,
    put_buttons,
    put_column,
    put_html,
    put_row,
    put_scope,
    put_text,
    re,
    run_js,
    t,
    use_scope,
)

from module.webui import maintenance_banner
from module.webui.app_helpers import (
    timedelta_to_text,
)


from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked
from module.config.utils import filepath_config

# 「配置文件从未 stat 过」的哨兵：首跑必须强制 load 一次
_OVERVIEW_MTIME_MISSING = object()


def _overview_count_js(pending: int, waiting: int) -> str:
    """拼出「队列中 / 等待中」数量徽标的客户端更新脚本。

    合成一条 run_js（一次刷新只发一条命令）；只改徽标的 textContent，
    不重建标题 <p>，因此不会引起列表上方的布局跳动。
    """

    def setter(key: str, count: int) -> str:
        var = f"el_{key}"
        badge_id = json.dumps(f"overview-count-{key}")
        return (
            f"var {var}=document.getElementById({badge_id});"
            f"if({var}){var}.textContent={json.dumps(str(count))};"
        )

    return (
        "(function(){"
        + setter("pending", pending)
        + setter("waiting", waiting)
        + "})();"
    )


class DashboardMixin(WebUIMixinBase):
    """WebUI仪表盘刷新逻辑"""

    # ------------------------------------------------------------------
    # 维护等待横幅
    #
    # 实例侧（alas.py _maintenance_wait）会把等待状态写进
    # log/<name>.maintenance.json；WebUI 是独立进程，只能读这份文件。
    # 更新只在总览页的后台任务里做，且只发一条 run_js 增量改 DOM，
    # 不触发整页重渲染（守住懒渲染 / 骨架屏的性能约定）。
    # ------------------------------------------------------------------

    def _maintenance_labels(self) -> dict:
        return {
            "state": t("Gui.Maintenance.StateWaiting"),
            "state_open": t("Gui.Maintenance.StateOpen"),
            "desc": t("Gui.Maintenance.DescWaiting"),
            "desc_running": t("Gui.Maintenance.DescRunning"),
            "desc_open": t("Gui.Maintenance.DescOpen"),
            "elapsed": t("Gui.Maintenance.Elapsed"),
            "next": t("Gui.Maintenance.NextPoll"),
        }

    def _maintenance_demo_state(self) -> dict:
        """测试模式的固定假数据：只生成一次，避免每轮刷新把倒计时重置。"""
        state = getattr(self, "_maint_demo_state", None)
        if state is None:
            now = time.time()
            state = {
                "waiting": True,
                "emulator_closed": True,  # 与关模拟器等开服的真实形态一致
                "server": "示例服务器",    # 演示服务器名（真实环境取配置值）
                "since": now - 4212,      # 已等待 1:10:12
                "last_poll": now - 18,    # 42 秒后下次查询
                "poll_seconds": 60,
            }
            self._maint_demo_state = state
        return state

    def _update_maintenance_banner(self) -> None:
        """按实例状态文件的 diff 更新横幅；无变化不重发 JS。"""
        if getattr(self, "_maint_demo", False):
            # 测试模式接管：页面重渲染后（alas_overview 会把 key 清空）需要
            # 重新补显，否则从开发者工具页切到总览会看不到横幅。
            if getattr(self, "_maint_banner_key", None) == "demo":
                return
            self._maint_banner_key = "demo"
            run_js(
                maintenance_banner.build_js(
                    self._maintenance_demo_state(), self._maintenance_labels()
                )
            )
            return
        raw = maintenance_banner.read_state(self.alas_name)
        state = maintenance_banner.normalize(
            raw, getattr(self.alas, "alive", False)
        )
        # 去重键只取「显示什么」，不取倒计时用的 last_poll：
        # 维护期间实例每 60 秒重写一次文件，last_poll 会变，但横幅内容不变，
        # 用整份 state 做键会每分钟白发一条 run_js。
        key = json.dumps(
            {
                "waiting": bool(state.get("waiting")) if state else False,
                "opened": bool(state.get("opened")) if state else False,
                "emulator_closed": bool(state.get("emulator_closed")) if state else False,
            },
            sort_keys=True,
        ) if state else ""
        # 「已开服」横幅在浏览器端按 TTL 自行收起；状态文件仍在时 normalize
        # 到期返回 None，key 从有值退化成空串，正好触发一次隐藏。
        if key == getattr(self, "_maint_banner_key", None):
            return
        self._maint_banner_key = key
        run_js(maintenance_banner.build_js(state, self._maintenance_labels()))

    def _show_maintenance_demo(self) -> None:
        """开发者工具：用假数据点亮横幅，便于人工验收样式与倒计时。"""
        self._maint_demo_state = None
        self._maint_banner_key = None
        run_js(
            maintenance_banner.build_js(
                self._maintenance_demo_state(), self._maintenance_labels()
            )
        )

    def _hide_maintenance_demo(self) -> None:
        self._maint_demo_state = None
        self._maint_banner_key = None
        run_js(maintenance_banner.build_js(None, self._maintenance_labels()))

    def _overview_config_mtime(self):
        """当前实例配置文件的 mtime_ns；文件不存在时返回 None。

        实例进程每次保存配置（任务调度改 next_run 等）都会触碰 mtime，
        WebUI 侧据此判断要不要重新 load。
        """
        try:
            return os.stat(filepath_config(self.alas_name, self.alas_mod)).st_mtime_ns
        except OSError:
            return None

    @render_locked
    def alas_update_overview_task(self) -> None:
        if not self.visible:
            return
        # 页面守卫：用户离开总览页后，此任务可能在被移除前仍执行一次。
        # 此时 running_tasks 等 scope 已随 content 清空，use_scope 会在
        # ROOT 下自动创建孤儿容器，任务列表将永久裸奔在页面顶层。
        if self.page != "Overview":
            return
        # 配置文件 mtime 短路：load()=deepcopy 全量配置(~12ms)+override+重放
        # modified，本任务周期性跑一次。文件没变（实例空闲时占大多数轮次）
        # 就直接沿用上次加载的数据，只重算任务队列。
        # mtime 为 None（stat 失败，如 mod 实例路径不规则）时视为未知，
        # 回退到每轮强制 load 的旧行为，宁可白读不可漏读。
        mtime = self._overview_config_mtime()
        seen = getattr(self, "_overview_config_mtime_seen", _OVERVIEW_MTIME_MISSING)
        if mtime is None or seen is _OVERVIEW_MTIME_MISSING or mtime != seen:
            self.alas_config.load()
            self._overview_config_mtime_seen = mtime
        self.alas_config.get_next_task()

        # 检查任务失败保护通知
        self._check_task_failure_notifications()

        # 维护等待横幅：必须在下面的 snapshot 提前 return 之前更新，
        # 否则任务列表没变化时横幅永远不刷新。
        self._update_maintenance_banner()

        if len(self.alas_config.pending_task) >= 1:
            if self.alas.alive:
                running = self.alas_config.pending_task[:1]
                pending = self.alas_config.pending_task[1:]
            else:
                running = []
                pending = self.alas_config.pending_task[:]
        else:
            running = []
            pending = []
        waiting = self.alas_config.waiting_task

        snapshot = {
            "running": tuple((task.command, task.next_run) for task in running),
            "pending": tuple((task.command, task.next_run) for task in pending),
            "waiting": tuple((task.command, task.next_run) for task in waiting),
            "alive": self.alas.alive,
        }
        if self._overview_snapshot == snapshot:
            return
        # 用户正在等渲染锁（切页/点击）：立即让出。snapshot 不提交，
        # 下一轮按同样的 diff 重跑，避免「快照已更新但列表没渲染」的断档。
        if self.render_should_yield():
            return

        def put_task(func: Function):
            with use_scope(f"overview-task_{func.command}"):
                put_column(
                    [
                        put_text(t(f"Task.{func.command}.name")).style("--arg-title--"),
                        put_text(str(func.next_run)).style("--arg-help--"),
                    ],
                    size="auto auto",
                )
                put_button(
                    label=t("Gui.Button.Setting"),
                    onclick=lambda: self.alas_set_group(func.command),
                    color="off",
                )

        clear("running_tasks")
        clear("pending_tasks")
        clear("waiting_tasks")
        with use_scope("running_tasks"):
            if running:
                for task in running:
                    put_task(task)
            else:
                put_text(t("Gui.Overview.NoTask")).style("--overview-notask-text--")
        with use_scope("pending_tasks"):
            if pending:
                for task in pending:
                    put_task(task)
            else:
                put_text(t("Gui.Overview.NoTask")).style("--overview-notask-text--")
        with use_scope("waiting_tasks"):
            if waiting:
                for task in waiting:
                    put_task(task)
            else:
                put_text(t("Gui.Overview.NoTask")).style("--overview-notask-text--")

        # 队列中 / 等待中标题上的任务数量徽标：与列表同一次刷新写入（数字
        # 与列表必然同源，分开算会不同步）。0 也照常显示 —— 用户要的是
        # 「总数量」，有 0 这个读数才能一眼确认队列真的空了。
        #
        # 徽标走的是 JS 通道，是本任务里唯一会「发命令给浏览器」的步骤：会话
        # 断开/远控掉线时 run_js 会抛。不能让这个异常穿出去 —— 任务调度器遇
        # 到异常会直接把任务移除，列表+徽标就永久停在旧内容（且不再自动恢复）。
        # 失败时保持快照不提交，下一轮按同样的 diff 重试；列表照常显示，只是
        # 数字暂时停在旧值，属可降级的损失。
        try:
            run_js(_overview_count_js(len(pending), len(waiting)))
        except Exception as e:
            logger.warning(f"[WebUI-总览] 任务数量徽标更新失败，下轮重试: {e}")
            return

        # 渲染完整完成才提交快照：上方让位中断时快照保持旧值，下轮重渲染
        self._overview_snapshot = snapshot

    def _check_task_failure_notifications(self) -> None:
        """检查任务失败保护通知并弹出浮动提示卡片。

        读取当前实例对应的失败记录文件（存放在 ``log/`` 目录），
        如果存在未读通知则显示类似更新提示的浮动通知卡片。
        用户点击"去处理"跳转到该任务的设置页，点击"我已知晓"关闭提示。
        已展示过的通知不会重复弹出。
        """
        if not self.alas_name:
            return

        # 弹窗已显示时不重复检查
        if getattr(self, "_failure_popup_shown", False):
            return

        # 失败记录文件存放在 log 目录下
        filepath = os.path.join('./log', f'{self.alas_name}.task_failure.json')
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return

        notifications = data.get('notifications', [])
        unread = [n for n in notifications if not n.get('read', False)]
        if not unread:
            return

        # 避免重复弹窗：所有未读通知组合成一个标识
        notice_key = '_'.join(
            f'{n.get("task", "")}_{n.get("timestamp", "")}' for n in unread
        )
        shown_set = getattr(self, '_shown_failure_keys', set())
        if notice_key in shown_set:
            return
        shown_set.add(notice_key)
        self._shown_failure_keys = shown_set
        self._failure_popup_shown = True

        # 生成唯一 scope id
        notice_id = f"task_failure_batch_{int(time.time() * 1000)}"

        _remove_js = (
            "(function () {\n"
            "    var el = document.getElementById('" + notice_id + "');\n"
            "    if (el && el.parentNode) {\n"
            "        el.parentNode.removeChild(el);\n"
            "    }\n"
            "})();\n"
        )

        def _remove_failure_notice():
            run_js(_remove_js)

        # 为每个未读通知准备任务信息
        task_rows = []
        for n in unread:
            task = n.get('task', '')
            reason = n.get('reason', '')
            count = n.get('count', 0)
            ts = n.get('timestamp', '')
            task_display = task
            try:
                task_display = t(f'Task.{task}.name')
            except Exception:
                pass
            task_rows.append((task, task_display, reason, count, ts))

        unread_count = len(task_rows)
        title_text = (
            f'{unread_count} 个任务已自动关闭' if unread_count > 1
            else '1 个任务已自动关闭'
        )

        # 构建每个任务的列表行 HTML（信息展示，不绑按钮）
        rows_html = []
        for idx, (task, task_display, reason, count, ts) in enumerate(task_rows):
            row_html = (
                '<div style="display: flex; align-items: center; justify-content: space-between;'
                ' padding: 6px 8px; margin-bottom: 4px;'
                ' background: rgba(240, 62, 62, 0.06); border-radius: 6px; gap: 8px;">'
                '<div style="min-width: 0; flex: 1;">'
                '<div style="font-weight: 700; font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">'
                '{task_display}</div>'
                '<div style="font-size: 0.78rem; opacity: 0.7; line-height: 1.3;">'
                '{reason} &middot; {count} 次</div>'
                '</div>'
                '</div>'
            ).format(task_display=task_display, reason=reason, count=count)
            rows_html.append(row_html)
        rows_html_str = '\n'.join(rows_html)

        html = (
            '<div id="{}" class="alas-update-notice" role="status" aria-live="polite" '
            'style="max-height: 70vh; overflow: hidden;">'
            '<div class="alas-update-notice__halo"></div>'
            '<div class="alas-update-notice__icon" aria-hidden="true">'
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
            ' stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M12 9v4"></path>'
            '<path d="M12 17h.01"></path>'
            '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>'
            '</svg>'
            '</div>'
            '<div class="alas-update-notice__body">'
            '<div class="alas-update-notice__eyebrow">任务异常</div>'
            '<div class="alas-update-notice__title">{title_text}</div>'
            '<div class="alas-update-notice__text">'
            '以下任务因连续失败已被自动关闭，请逐一处理。</div>'
            '<div style="max-height: 180px; overflow-y: auto; margin: 4px 0 8px 0;">'
            '{rows_html}'
            '</div>'
            '<div id="pywebio-scope-{scope_id}_actions" class="alas-update-notice__actions"></div>'
            '</div>'
            '</div>'
        ).format(notice_id, title_text=title_text, rows_html=rows_html_str,
                 scope_id=notice_id)

        # 先移除已存在的同 id 提示
        run_js(_remove_js)

        with use_scope("ROOT"):
            put_html(html)

            # 汇总所有未读通知，"去处理"跳转到第一个出错任务的设置页
            first_task = task_rows[0][0] if task_rows else ''

            def _go_handle_first():
                _remove_failure_notice()
                self._failure_popup_shown = False
                for n in unread:
                    self._mark_failure_notification_read(n.get('task', ''))
                if first_task:
                    self.alas_set_group(first_task)

            def acknowledge_all():
                """关闭提示，标记所有通知已读。"""
                _remove_failure_notice()
                self._failure_popup_shown = False
                for n in unread:
                    self._mark_failure_notification_read(n.get('task', ''))

            put_buttons(
                [
                    {"label": "去处理", "value": "handle", "color": "danger"},
                    {"label": "全部已知晓", "value": "ack", "color": "secondary"},
                ],
                onclick=[_go_handle_first, acknowledge_all],
                small=True,
                scope=f"{notice_id}_actions",
            )

    def _mark_failure_notification_read(self, task: str) -> None:
        """将指定任务的失败保护通知标记为已读。

        Args:
            task: 任务名称。
        """
        if not self.alas_name:
            return
        filepath = os.path.join('./log', f'{self.alas_name}.task_failure.json')
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, encoding='utf-8') as f:
                data = json.load(f)
            changed = False
            for n in data.get('notifications', []):
                if n.get('task') == task and not n.get('read', False):
                    n['read'] = True
                    changed = True
            if changed:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f'[WebUI] 标记失败保护通知已读失败: {e}')

    def _update_dashboard(self, num=None, groups_to_display=None, force_clear=False):
        x = 0
        _num = 10000 if num is None else num
        _arg_group = (
            self._log.dashboard_arg_group
            if groups_to_display is None
            else groups_to_display
        )
        time_now = current_time().replace(microsecond=0)
        # LogRes 的 groups 是 cached_property，每实例独立缓存。
        # 提出循环外复用同一实例，避免每个资源都重新读盘解析 dashboard.json。
        log_res = LogRes(self.alas_config)
        # 首显（切回总览页）时 dashboard 容器刚由 alas_overview 重建，各资源
        # scope 尚不存在：此时 clear 只会多发 12 条 WS 指令、并触发前端强制
        # 布局重算（高级材质主题下成本翻倍）。只有增量刷新，或显式切换显示
        # 模式（force_clear=True，scope 已存在）才需要清空重写。
        first_display = self._log.first_display
        for group_name in _arg_group:
            # 分段中断：用户正在等渲染锁（如切页）时放弃剩余条目。
            # 检查在任何状态写入之前，保证本轮不留下半更新的缓存
            # （first_display 保持 True、last_display_time 不污染），
            # 下轮从头全量重跑。
            if self.render_should_yield():
                return
            group = log_res.group(group_name)
            if group is None:
                continue

            value = str(group["Value"])
            value_total = ""
            if "Limit" in group.keys():
                value_limit = f" / {group['Limit']}"
            elif "Total" in group.keys():
                value_total = f" ({group['Total']})"
                value_limit = ""
            elif group_name == "Pt":
                value_limit = " / " + re.sub(
                    r'[,.\'"，。]',
                    "",
                    str(
                        deep_get(
                            self.alas_config.data, "EventGeneral.EventGeneral.PtLimit"
                        )
                    ),
                )
                if value_limit == " / 0":
                    value_limit = ""
            else:
                value_limit = ""
                value_total = ""

            value_time = group["Record"]
            if value_time is None or value_time == datetime(2020, 1, 1, 0, 0, 0):
                value_time = datetime(2023, 1, 1, 0, 0, 0)

            # Handle time delta
            if value_time == datetime(2023, 1, 1, 0, 0, 0):
                value = "None"
                delta = timedelta_to_text()
            else:
                _s = int(abs((value_time - time_now).total_seconds()))
                _m, _s = divmod(_s, 60)
                _h, _m = divmod(_m, 60)
                _d, _h = divmod(_h, 24)
                _Y, _d = divmod(_d, 365)
                _M, _d = divmod(_d, 30)
                delta = timedelta_to_text({"Y": _Y, "M": _M, "D": _d, "h": _h, "m": _m, "s": _s})

            if group_name not in self._log.last_display_time.keys():
                self._log.last_display_time[group_name] = ""
            if (
                self._log.last_display_time[group_name] == delta
                and not self._log.first_display
            ):
                continue
            self._log.last_display_time[group_name] = delta

            # if self._log.first_display:
            # Handle width
            # value_width = len(value) * 0.7 + 0.6 if value != 'None' else 4.5
            # value_width = str(value_width/1.12) + 'rem' if self.is_mobile else str(value_width) + 'rem'
            value_limit = "" if value == "None" else value_limit
            # limit_width = len(value_limit) * 0.7
            # limit_width = str(limit_width) + 'rem'
            value_total = "" if value == "None" else value_total
            limit_style = (
                "--dashboard-limit--" if value_limit else "--dashboard-total--"
            )
            value_limit = value_limit if value_limit else value_total
            # Handle dot color
            # 旧配置可能缺少颜色字段，仍渲染条目而不是中断整个仪表盘刷新。
            color_value = deep_get(group, "Color") or ""
            _color = f"background-color:{color_value.replace('^', '#')}"
            color = f'<div class="status-point" style={_color}>'
            # 使用集中管理的辅助函数生成 scope_id，确保命名一致性和安全性
            scope_id = get_dashboard_scope_id(group_name)
            with use_scope(scope_id, clear=force_clear or not first_display):
                put_row(
                    [
                        put_html(color),
                        put_scope(
                            get_group_scope_id(group_name),
                            [
                                put_column(
                                    [
                                        put_row(
                                            [
                                                put_text(value).style(
                                                    f"--dashboard-value--"
                                                ),
                                                put_text(value_limit).style(
                                                    limit_style
                                                ),
                                            ],
                                        ).style(
                                            "grid-template-columns:min-content auto;align-items: baseline;"
                                        ),
                                        put_text(
                                            t(f"Gui.Dashboard.{group_name}")
                                            + " - "
                                            + delta
                                        ).style("---dashboard-help--"),
                                    ],
                                    size="auto auto",
                                ),
                            ],
                        ),
                    ],
                    size="20px 1fr",
                ).style("height: 1fr")
            x += 1
            if x >= _num:
                break
        if self._log.first_display:
            self._log.first_display = False

    @render_locked
    def alas_update_dashboard(self, _clear=False):
        if not self.visible:
            return
        # 页面守卫：dashboard scope 仅存在于总览页，页外渲染会产生孤儿容器。
        if self.page != "Overview":
            return
        # 用户正在等渲染锁：连容器清空都先不做，让用户立即拿到锁。
        if self.render_should_yield():
            return
        # _clear=True：切换仪表盘显示模式（4 项 ⇄ 全量），容器与条目都要清空重建；
        # _clear=False：切回总览页的后台首跑，或 10s 周期刷新——由 _update_dashboard
        # 依据 first_display 自行决定条目是否清空。
        with use_scope("dashboard", clear=_clear):
            if not self._log.display_dashboard:
                self._update_dashboard(
                    num=4,
                    groups_to_display=["Oil", "Coin", "Gem", "Pt"],
                    force_clear=_clear,
                )
            elif self._log.display_dashboard:
                self._update_dashboard(force_clear=_clear)
