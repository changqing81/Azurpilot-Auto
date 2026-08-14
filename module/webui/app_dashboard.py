"""WebUI仪表盘刷新逻辑"""

import json
import os

from module.webui.app_dependencies import (
    Function,
    LogRes,
    clear,
    close_popup,
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
    popup,
    re,
    t,
    use_scope,
)

from module.webui.app_helpers import (
    timedelta_to_text,
)


from module.webui.app_types import WebUIMixinBase


class DashboardMixin(WebUIMixinBase):
    """WebUI仪表盘刷新逻辑"""

    def alas_update_overview_task(self) -> None:
        if not self.visible:
            return
        self.alas_config.load()
        self.alas_config.get_next_task()

        # 检查任务失败保护通知
        self._check_task_failure_notifications()

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
        self._overview_snapshot = snapshot

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

    def _check_task_failure_notifications(self) -> None:
        """检查任务失败保护通知并弹出提示。

        读取当前实例对应的失败记录文件，如果存在未读通知则弹出对话框。
        用户点击"去处理"跳转到该任务的设置页，点击"我已知晓"关闭弹窗。
        已展示过的通知不会重复弹出。
        """
        if not self.alas_name:
            return

        # 弹窗已显示时不重复检查
        if getattr(self, "_failure_popup_shown", False):
            return

        # 读取失败记录文件
        filepath = os.path.join('./config', f'{self.alas_name}.task_failure.json')
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

        # 取第一条未读通知展示
        notice = unread[0]
        task = notice.get('task', '')
        reason = notice.get('reason', '')
        count = notice.get('count', 0)
        timestamp = notice.get('timestamp', '')

        # 避免重复弹窗：记录已展示的通知标识
        notice_key = f'{task}_{timestamp}'
        shown_set = getattr(self, '_shown_failure_keys', set())
        if notice_key in shown_set:
            return
        shown_set.add(notice_key)
        self._shown_failure_keys = shown_set
        self._failure_popup_shown = True

        # 获取任务显示名
        task_display = task
        try:
            task_display = t(f'Task.{task}.name')
        except Exception:
            pass

        title = f'任务 {task_display} 已自动关闭'
        content_html = (
            f'<div style="padding: .5rem 0;">'
            f'<p style="font-size: 1rem; margin-bottom: .5rem;">'
            f'任务 <b>{task_display}</b> 因连续失败已被自动关闭'
            f'</p>'
            f'<div style="background: rgba(255,255,255,.06); border-radius: .375rem; padding: .5rem .75rem; margin-bottom: .5rem;">'
            f'<p style="margin: 0 0 .25rem;">错误原因：<b>{reason}</b></p>'
            f'<p style="margin: 0 0 .25rem;">累计失败：<b>{count}</b> 次</p>'
            f'<p style="margin: 0;">触发时间：{timestamp}</p>'
            f'</div>'
            f'<p style="color: rgba(255,255,255,.6); font-size: .85rem; margin: 0;">'
            f'请检查该任务的配置是否正确，确认后手动重新启用该任务。'
            f'</p>'
            f'</div>'
        )

        def go_handle():
            """跳转到出错任务的设置页。"""
            close_popup()
            self._failure_popup_shown = False
            # 标记通知已读
            self._mark_failure_notification_read(task)
            # 跳转到任务设置页
            self.alas_set_group(task)

        def acknowledge():
            """关闭弹窗，标记通知已读。"""
            close_popup()
            self._failure_popup_shown = False
            self._mark_failure_notification_read(task)

        try:
            popup(
                title=title,
                content=[
                    put_html(content_html),
                    put_buttons(
                        [
                            {'label': '去处理', 'value': 'handle', 'color': 'danger'},
                            {'label': '我已知晓', 'value': 'ack', 'color': 'secondary'},
                        ],
                        onclick=[go_handle, acknowledge],
                    ),
                ],
                implicit_close=False,
                closable=True,
            )
        except Exception as e:
            logger.warning(f'[WebUI] 任务失败保护弹窗显示失败: {e}')
            self._failure_popup_shown = False

    def _mark_failure_notification_read(self, task: str) -> None:
        """将指定任务的失败保护通知标记为已读。

        Args:
            task: 任务名称。
        """
        if not self.alas_name:
            return
        filepath = os.path.join('./config', f'{self.alas_name}.task_failure.json')
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

    def _update_dashboard(self, num=None, groups_to_display=None):
        x = 0
        _num = 10000 if num is None else num
        _arg_group = (
            self._log.dashboard_arg_group
            if groups_to_display is None
            else groups_to_display
        )
        time_now = current_time().replace(microsecond=0)
        for group_name in _arg_group:
            group = LogRes(self.alas_config).group(group_name)
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
            with use_scope(scope_id, clear=True):
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

    def alas_update_dashboard(self, _clear=False):
        if not self.visible:
            return
        with use_scope("dashboard", clear=_clear):
            if not self._log.display_dashboard:
                self._update_dashboard(
                    num=4, groups_to_display=["Oil", "Coin", "Gem", "Pt"]
                )
            elif self._log.display_dashboard:
                self._update_dashboard()
