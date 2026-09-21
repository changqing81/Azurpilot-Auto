"""WebUI实例概览和守护模式"""

from module.webui.app_dependencies import (
    BinarySwitchButton,
    LogRes,
    RichLog,
    deep_iter,
    json,
    put_button,
    put_html,
    put_none,
    put_scope,
    put_text,
    run_js,
    t,
    updater,
    use_scope,
)


from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked


# 日志工具栏「导出今日日志」按钮的客户端脚本。
#
# 刻意用普通字符串模板 + 占位符替换，而不是 f-string：
# 1) f-string 里 JS 的每个花括号都要写成双花括号，可读性差；
# 2) 更要命的是正则转义（如 \{、\w）在 f-string 里会触发
#    "SyntaxWarning: invalid escape sequence"，未来 Python 版本直接报错。
_EXPORT_TODAY_JS_TEMPLATE = r"""
(function(){
  var scope = document.querySelector('#pywebio-scope-log_export_btn');
  var btn = scope ? scope.querySelector('.btn') : null;
  if (!btn || btn.disabled) return;
  var label0 = btn.textContent;
  var title0 = btn.title || '';
  function restore(delay){
    setTimeout(function(){ btn.textContent = label0; btn.title = title0; }, delay);
  }
  // 工具栏没有状态行，也没有可用的客户端 toast（pywebio 的 toast 是服务端指令），
  // 所以反馈就落在按钮自身：文字给结论，title 放服务端返回的具体原因。
  function fail(detail){
    btn.textContent = __FAILED__;
    btn.title = detail || '';
    if (window.console && detail) console.warn('[log-export] ' + detail);
    restore(5000);
  }
  // 远控 P2P 入口页面路径形如 /<8位以上小写字母数字>/...，带前缀优先、根相对兜底
  function apiCandidates(path){
    var list = [path];
    var first = (location.pathname.split('/').filter(Boolean)[0] || '');
    if (/^[a-z0-9]{8,}$/.test(first)) list.unshift('/' + first + path);
    return list;
  }
  async function fetchFirst(variants){
    var lastResp = null, lastErr = null;
    for (var i = 0; i < variants.length; i++) {
      var urls = apiCandidates(variants[i]);
      for (var j = 0; j < urls.length; j++) {
        try {
          var r = await fetch(urls[j], {cache: 'no-store'});
          if (r.ok) return r;
          lastResp = r;
        } catch (e) { lastErr = e; }
      }
    }
    if (lastResp) return lastResp;
    throw lastErr || new Error('network error');
  }
  // Starlette 对非 ASCII 文件名（如 小号）用 RFC 5987 的 filename*=utf-8'' 形式
  function parseFileName(h){
    if (!h) return null;
    var star = h.match(/filename\*=(?:utf-8|UTF-8)''([^;]+)/);
    if (star) { try { return decodeURIComponent(star[1]); } catch (e) { return star[1]; } }
    var p = h.match(/filename="?([^";]+)"?/);
    return p ? p[1] : null;
  }
  function saveBlob(resp, fallback){
    return resp.blob().then(function(blob){
      var name = parseFileName(resp.headers.get('Content-Disposition')) || fallback;
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function(){ URL.revokeObjectURL(url); }, 60000);
    });
  }
  btn.textContent = __EXPORTING__;
  btn.disabled = true;
  (async function(){
    try {
      var enc = encodeURIComponent(__INSTANCE__);
      // scope 必须走 path，且**排在最前**：P2P 远控代理转发时会剥掉 query string，
      // 若先试 query 形式，它会以 HTTP 200 返回默认的"全部历史合并"，而 fetchFirst
      // 拿到第一个 200 就收工 —— 用户要今天的日志却得到合并文件，且界面上毫无报错。
      var resp = await fetchFirst([
        '/api/log/runtime/' + enc + '/today',
        '/api/log/runtime?instance=' + enc + '&scope=today',
        '/api/log/runtime/' + enc + '?scope=today'
      ]);
      if (!resp.ok) {
        var detail = '';
        try { var d = await resp.json(); detail = (d && d.error) ? d.error : ''; } catch (e) {}
        fail(detail);
        return;
      }
      // 远控下 Content-Length 被代理剥掉，做不了百分比进度，只做不确定态
      await saveBlob(resp, __INSTANCE__ + '_runtime_log_today.txt');
      btn.textContent = label0;
    } catch (err) {
      fail(err && err.message ? err.message : '');
    } finally {
      btn.disabled = false;
    }
  })();
})();
"""


def _export_today_js(instance: str) -> str:
    """拼出「导出今日日志」按钮的客户端脚本（占位符替换，不用 f-string）。"""
    return (
        _EXPORT_TODAY_JS_TEMPLATE
        .replace("__INSTANCE__", json.dumps(instance))
        .replace("__EXPORTING__", json.dumps(t("Gui.LogExport.Exporting")))
        .replace("__FAILED__", json.dumps(t("Gui.LogExport.Failed")))
    )


class OverviewMixin(WebUIMixinBase):
    """WebUI实例概览和守护模式"""

    def _log_export_toolbar_button(self):
        """日志工具栏的「导出今日日志」按钮。

        跟随当前页实例（与「截图预览」同款写法），一键下载当天运行日志：
        不做实例下拉、不做日期下拉、不做确认框 —— 那些都在开发者工具的完整面板里。
        scope=today 由服务端解析成日期，避免客户端时区/时钟与服务器不一致时取错天。
        """
        return put_scope(
            "log_export_btn",
            [
                put_button(
                    label=t("Gui.LogExport.ExportToday"),
                    onclick=lambda: run_js(_export_today_js(self.alas_name)),
                    color="off",
                )
            ],
        )

    @render_locked
    @use_scope("content", clear=True)
    def alas_overview(self) -> None:
        self.init_menu(name="Overview", skip_clear=True)
        self.set_title(t(f"Gui.MenuAlas.Overview"))
        self._overview_snapshot = None

        put_scope("overview", [put_scope("schedulers"), put_scope("logs")])

        with use_scope("schedulers"):
            put_scope(
                "scheduler-bar",
                [
                    put_text(t("Gui.Overview.Scheduler")).style(
                        "font-size: 1.25rem; margin: auto .5rem auto;"
                    ),
                    put_scope("scheduler_btn"),
                ],
            )
            put_scope(
                "stat-bar",
                [
                    put_text(t("Gui.Overview.Stat")).style(
                        "font-size: 1.25rem; margin: auto .5rem auto;"
                    ),
                    put_button(
                        label=t("Gui.Button.Open"),
                        onclick=self.alas_set_stat,
                        color="on",
                    ),
                ],
            )
            put_scope(
                "running",
                [
                    put_text(t("Gui.Overview.Running")),
                    put_html('<hr class="hr-group">'),
                    put_scope("running_tasks"),
                ],
            )
            put_scope(
                "pending",
                [
                    put_text(t("Gui.Overview.Pending")),
                    put_html('<hr class="hr-group">'),
                    put_scope("pending_tasks"),
                ],
            )
            put_scope(
                "waiting",
                [
                    put_text(t("Gui.Overview.Waiting")),
                    put_html('<hr class="hr-group">'),
                    put_scope("waiting_tasks"),
                ],
            )

        switch_scheduler = BinarySwitchButton(
            label_on=t("Gui.Button.Stop"),
            label_off=t("Gui.Button.Start"),
            onclick_on=lambda: self.alas.stop_by_user(),
            onclick_off=self._alas_start,
            get_state=lambda: self.alas.alive,
            color_on="off",
            color_off="on",
            scope="scheduler_btn",
        )

        # April Fools: runaway start button
        if getattr(self, "af_flag", False):
            run_js("""
(function(){
    var surrendered = false;
    var bar = document.getElementById('pywebio-scope-scheduler-bar');
    if (!bar) return;
    bar.style.position = 'relative';
    bar.style.overflow = 'hidden';

    var flag = document.createElement('button');
    flag.textContent = '🏳️';
    flag.title = 'I give up...';
    flag.style.cssText = 'border:none;background:transparent;font-size:1.1rem;cursor:pointer;padding:0 4px;margin:auto 2px;opacity:0.45;transition:opacity .2s;flex-shrink:0;';
    flag.onmouseenter = function(){ flag.style.opacity='1'; };
    flag.onmouseleave = function(){ flag.style.opacity='0.45'; };
    flag.onclick = function(){
        surrendered = true;
        flag.style.display = 'none';
        var b = bar.querySelector('.btn-on');
        if(b){ b.style.transition='transform .35s cubic-bezier(.34,1.56,.64,1)'; b.style.transform=''; }
    };
    bar.appendChild(flag);

    bar.addEventListener('mousemove', function(e){
        if (surrendered) return;
        var btn = bar.querySelector('.btn-on');
        if (!btn) return;
        var r = btn.getBoundingClientRect();
        var bx = r.left + r.width/2, by = r.top + r.height/2;
        var dx = bx - e.clientX, dy = by - e.clientY;
        var dist = Math.sqrt(dx*dx + dy*dy);
        if (dist < 100 && dist > 1) {
            var pr = bar.getBoundingClientRect();
            var push = 100 - dist;
            var nx = dx/dist * push, ny = dy/dist * push * 0.3;
            var cur = btn.style.transform.match(/translate\\(([^,]+)px,\\s*([^)]+)px\\)/);
            var ox = cur ? parseFloat(cur[1]) : 0, oy = cur ? parseFloat(cur[2]) : 0;
            var tx = ox + nx, ty = oy + ny;
            var maxX = (pr.width - r.width) / 2 - 4;
            var maxY = (pr.height - r.height) / 2;
            tx = Math.max(-maxX, Math.min(maxX, tx));
            ty = Math.max(-maxY, Math.min(maxY, ty));
            btn.style.transition = 'transform .13s ease-out';
            btn.style.transform = 'translate('+tx+'px,'+ty+'px)';
        }
    });
})();
""")

        if (
            self._overview_log is None
            or self._overview_log_config_name != self.alas_name
        ):
            self._overview_log = RichLog("log")
            self._overview_log_config_name = self.alas_name
        else:
            self._overview_log.scope = "log"
        log = self._overview_log
        log.first_display = True
        log.last_display_time = {}
        self._log = log
        self._log.dashboard_arg_group = LogRes(self.alas_config).groups

        with use_scope("logs"):
            if "Maa" in self.ALAS_ARGS:
                (
                    put_scope(
                        "log-bar",
                        [
                            put_text(t("Gui.Overview.Log")).style(
                                "font-size: 1.25rem; margin: auto .5rem auto;"
                            ),
                            put_scope(
                                "log-bar-btns",
                                [
                                    put_scope("log_scroll_btn"),
                                    # Maa 分支没有「截图预览」，按钮紧接自动滚动
                                    self._log_export_toolbar_button(),
                                ],
                            ),
                        ],
                    ),
                )
            else:
                (
                    put_scope(
                        "log-bar",
                        [
                            put_text(t("Gui.Overview.Log")).style(
                                "font-size: 1.25rem; margin: auto .5rem auto;"
                            ),
                            put_scope(
                                "log-bar-btns",
                                [
                                    put_scope("log_scroll_btn"),
                                    put_button(
                                        label="截图预览",
                                        onclick=lambda: run_js(
                                            f"window.alasToggleLivePreview({json.dumps(self.alas_name)});"
                                        ),
                                        color="off",
                                    ),
                                    self._log_export_toolbar_button(),
                                    put_scope("dashboard_btn"),
                                ],
                            ),
                            put_html('<hr class="hr-group">'),
                            put_scope("dashboard"),
                        ],
                    ),
                )
            # version
            local_commit = updater.get_commit(short_sha1=True)
            version = local_commit[0] if local_commit and local_commit[0] else "Unknown"
            put_scope("log-container", [put_scope("log", [put_html("")])]).style(
                f"--version: 'Ver.{version}';"
            )

        log.console.width = log.get_width()

        switch_log_scroll = BinarySwitchButton(
            label_on=t("Gui.Button.ScrollON"),
            label_off=t("Gui.Button.ScrollOFF"),
            onclick_on=lambda: log.set_scroll(False),
            onclick_off=lambda: log.set_scroll(True),
            get_state=lambda: log.keep_bottom,
            color_on="on",
            color_off="off",
            scope="log_scroll_btn",
        )
        switch_dashboard = BinarySwitchButton(
            label_on=t("Gui.Button.DashboardON"),
            label_off=t("Gui.Button.DashboardOFF"),
            onclick_on=lambda: self.set_dashboard_display(False),
            onclick_off=lambda: self.set_dashboard_display(True),
            get_state=lambda: log.display_dashboard,
            color_on="off",
            color_off="on",
            scope="dashboard_btn",
        )
        self.task_handler.add(switch_scheduler.g(), 1, True)
        self.task_handler.add(switch_log_scroll.g(), 1, True)
        if "Maa" not in self.ALAS_ARGS:
            self.task_handler.add(switch_dashboard.g(), 1, True)
        # 首屏同步渲染一次任务列表：后台任务首跑要与页面渲染争抢
        # render_lock，远控慢链路下可能滞后数秒甚至被可见性窗口拦截，
        # 导致进入页面后运行中/队列中/等待中长期空白。
        self.alas_update_overview_task()
        self.task_handler.add(self.alas_update_overview_task, 10, True, group="slow")
        if "Maa" not in self.ALAS_ARGS:
            self.task_handler.add(self.alas_update_dashboard, 10, True, group="slow")
            self.alas_update_dashboard(True)
        if hasattr(self, "alas") and self.alas is not None:
            self.task_handler.add(log.put_log(self.alas), 0.25, True)

    def set_dashboard_display(self, b):
        self._log.set_dashboard_display(b)
        self.alas_update_dashboard(True)

    @render_locked
    @use_scope("content", clear=True)
    def alas_daemon_overview(self, task: str) -> None:
        self.init_menu(name=task, skip_clear=True)
        self.set_title(t(f"Task.{task}.name"))

        log = RichLog("log")
        # 「指挥喵评分」页只去掉**日志内容区**（含任务收尾打出的 rich 汇总表）：这一页的
        # 主体是评分报告，日志内容会和报告抢 _daemon 的行高，把报告压成一条缝、汇总表
        # 还会盖在参数区上。顶部日志工具栏（自动滚动 / 截图预览 / 导出今日日志）照常保留。
        show_log_content = task != "MeowfficerScore"

        if self.is_mobile:
            scopes = [
                put_scope("scheduler-bar"),
                put_scope("stat-bar"),
                put_scope("groups"),
                put_scope("log-bar"),
            ]
            if show_log_content:
                scopes.append(put_scope("log", [put_html("")]))
            put_scope("daemon-overview", scopes)
        else:
            children = [
                put_scope(
                    "_daemon_upper",
                    [put_scope("scheduler-bar"), put_scope("log-bar")],
                ),
                put_scope("groups"),
            ]
            if show_log_content:
                children.append(put_scope("log", [put_html("")]))
            put_scope(
                "daemon-overview",
                [put_none(), put_scope("_daemon", children), put_none()],
            )

        if show_log_content:
            log.console.width = log.get_width()

        with use_scope("scheduler-bar"):
            put_text(t("Gui.Overview.Scheduler")).style(
                "font-size: 1.25rem; margin: auto .5rem auto;"
            )
            put_scope("scheduler_btn")

        # 统计入口只在移动端布局里占位（见上方 daemon-overview 的
        # put_scope("stat-bar")）。桌面端 _daemon 是三行 grid
        # （_daemon_upper / groups / log），_daemon_upper 只放 scheduler-bar 与
        # log-bar，没有 stat-bar 的容器。若在这里无条件 use_scope("stat-bar")，
        # PyWebIO 找不到该 scope 会在 ROOT 下自动创建孤儿容器，把「统计界面 +
        # 打开」渲染到内容区之外——表现为所有工具页底部多出一块悬空卡片，
        # 并挤压 _daemon 的 grid 高度。桌面端统计入口在「总览」页。
        if self.is_mobile:
            with use_scope("stat-bar"):
                put_text(t("Gui.Overview.Stat")).style(
                    "font-size: 1.25rem; margin: auto .5rem auto;"
                )
                put_button(
                    label=t("Gui.Button.Open"),
                    onclick=self.alas_set_stat,
                    color="on",
                )

        switch_scheduler = BinarySwitchButton(
            label_on=t("Gui.Button.Stop"),
            label_off=t("Gui.Button.Start"),
            onclick_on=lambda: self.alas.stop_by_user(),
            onclick_off=lambda: self.alas.start(task),
            get_state=lambda: self.alas.alive,
            color_on="off",
            color_off="on",
            scope="scheduler_btn",
        )

        with use_scope("log-bar"):
            put_text(t("Gui.Overview.Log")).style(
                "font-size: 1.25rem; margin: auto .5rem auto;"
            )
            put_scope(
                "log-bar-btns",
                [
                    put_scope("log_scroll_btn"),
                    put_button(
                        label="截图预览",
                        onclick=lambda: run_js(
                            f"window.alasToggleLivePreview({json.dumps(self.alas_name)});"
                        ),
                        color="off",
                    ),
                    self._log_export_toolbar_button(),
                ],
            )

        switch_log_scroll = BinarySwitchButton(
            label_on=t("Gui.Button.ScrollON"),
            label_off=t("Gui.Button.ScrollOFF"),
            onclick_on=lambda: log.set_scroll(False),
            onclick_off=lambda: log.set_scroll(True),
            get_state=lambda: log.keep_bottom,
            color_on="on",
            color_off="off",
            scope="log_scroll_btn",
        )

        config = self.alas_config.read_file(self.alas_name)
        if task == "MeowfficerScore":
            # 评分结果面板挂在参数卡上方，与上游 PR #998 的面板位置一致
            with use_scope("groups"):
                self.put_meowfficer_score_panel()
        for group, arg_dict in deep_iter(self.ALAS_ARGS[task], depth=1):
            if group[0] == "Storage":
                continue
            self.set_group(group, arg_dict, config, task)

        if show_log_content:
            run_js(
                """
                $("#pywebio-scope-log").css(
                    "grid-row-start",
                    -2 - $("#pywebio-scope-_daemon").children().filter(
                        function(){
                            return $(this).css("display") === "none";
                        }
                    ).length
                );
                $("#pywebio-scope-log").css(
                    "grid-row-end",
                    -1
                );
            """
            )
        elif self.is_mobile:
            # 容器模板是写死行数的，少了日志内容行必须一并改掉：空出来的那一行会按
            # 原模板把日志工具栏拉成 1fr（占满剩余高度），评分面板反而没有高度。
            run_js(
                '$("#pywebio-scope-daemon-overview")'
                '.css("grid-template-rows", "auto auto 1fr auto");'
            )
        else:
            # 桌面端同理把 _daemon 收成两行；另外 daemon-overview 的列模板原本是
            # 1fr minmax(25rem, 6fr) 1fr —— 两侧各留 1/8 空白做居中，报告页要铺满，
            # 所以把两侧收成 0。
            run_js(
                '$("#pywebio-scope-_daemon")'
                '.css("grid-template-rows", "auto minmax(6rem, 1fr)");'
                '$("#pywebio-scope-daemon-overview")'
                '.css("grid-template-columns", "0 minmax(0, 1fr) 0");'
            )

        self.task_handler.add(switch_scheduler.g(), 1, True)
        self.task_handler.add(switch_log_scroll.g(), 1, True)
        if show_log_content and hasattr(self, "alas") and self.alas is not None:
            self.task_handler.add(log.put_log(self.alas), 0.25, True)
