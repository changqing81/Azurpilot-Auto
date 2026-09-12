"""WebUI调试工具和远程访问"""

from deploy.atomic import atomic_write
from module.logger import logger

from module.webui.app_dependencies import (
    DEFAULT_CONFIG_NAME,
    Optional,
    ProcessManager,
    RemoteAccess,
    State,
    Switch,
    alas_instance,
    clear,
    json,
    load_config,
    os,
    put_button,
    put_buttons,
    put_html,
    put_link,
    put_loading,
    put_row,
    put_scope,
    put_text,
    put_warning,
    raise_exception,
    run_js,
    t,
    toast,
    use_scope,
)
from module.webui.app_lifecycle import clearup


from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked


def prepare_webui_restart() -> bool:
    """保存当前运行实例，供新 WebUI 在重启后恢复。"""
    try:
        names = [
            f"{alas.config_name}\n" for alas in ProcessManager.running_instances()
        ]
        atomic_write("./config/reloadalas", "".join(names))
    except Exception as exc:
        logger.exception_context(
            title='无法准备 WebUI 手动重启',
            exc=exc,
            impact='继续重启会导致当前运行的 AzurPilot 实例无法自动恢复。',
            action='检查 config 目录写入权限后重试。',
            level=50,
        )
        return False
    return True


def request_webui_restart() -> bool:
    """请求手动重启，且不打断正在执行的更新事务。"""
    if State.restart_event is None:
        return False
    if not State.restart_lock.acquire(blocking=False):
        logger.info("自动更新事务正在进行，忽略本次手动重启请求")
        return False

    try:
        if State._restart_requested:
            return True
        if not prepare_webui_restart():
            return False

        State._restart_requested = True
        try:
            if not clearup():
                logger.warning("WebUI 清理未完成，将由父进程终止完整进程树")
        except Exception as exc:
            logger.exception_context(
                title='WebUI 手动重启清理失败',
                exc=exc,
                impact='父进程仍会终止旧 WebUI 进程树。',
                action='检查 WebUI 清理日志，确认是否有残留资源。',
                level=50,
            )

        try:
            State.restart_event.set()
        except Exception as exc:
            State._restart_requested = False
            logger.exception_context(
                title='无法通知父进程执行 WebUI 手动重启',
                exc=exc,
                impact='当前 WebUI 不会退出，已保存的实例恢复标记将保留。',
                action='检查父子进程事件状态后重新发起重启。',
                level=50,
            )
            return False
        return True
    finally:
        State.restart_lock.release()


class DeveloperToolsMixin(WebUIMixinBase):
    """WebUI调试工具和远程访问"""

    @render_locked
    @use_scope("content", clear=True)
    def dev_utils(self) -> None:
        self.init_menu(name="Utils", skip_clear=True)
        self.set_title(t("Gui.MenuDevelop.Utils"))
        put_scope("develop_detail")
        put_button(
            label=t("GUI测试 抛出异常事件"),
            onclick=raise_exception,
            scope="develop_detail",
        )
        put_button(
            label=t("预览更新提示"),
            onclick=self._preview_update_notice,
            scope="develop_detail",
        )

        def _get_debug_target_instance() -> Optional[str]:
            if getattr(self, "alas_name", ""):
                return self.alas_name
            all_instances = alas_instance()
            if all_instances:
                return all_instances[0]
            return None

        def _refresh_debug_status():
            self.set_aside_status()
            if hasattr(self, "state_switch"):
                try:
                    self.state_switch.switch()
                except Exception:
                    pass

        def _mock_icon_state(state: int, seconds: int = 10):
            target = _get_debug_target_instance()
            if not target:
                toast("未找到可用实例，无法模拟图标状态", color="warning")
                return
            ProcessManager.get_manager(target).set_state_override(
                state, duration=seconds
            )
            _refresh_debug_status()
            toast(f"已为 {target} 模拟状态 {state}（{seconds}s）", color="info")

        def _clear_mock_icon_state():
            target = _get_debug_target_instance()
            if not target:
                toast("未找到可用实例，无法清除模拟状态", color="warning")
                return
            ProcessManager.get_manager(target).clear_state_override()
            _refresh_debug_status()
            toast(f"已清除 {target} 的图标状态模拟", color="success")

        put_buttons(
            buttons=[
                {"label": "模拟运行图标(10s)", "value": 1, "color": "success"},
                {"label": "模拟错误图标(10s)", "value": 3, "color": "danger"},
                {"label": "模拟更新图标(10s)", "value": 4, "color": "warning"},
            ],
            onclick=lambda state: _mock_icon_state(state, 10),
            scope="develop_detail",
        )
        put_button(
            label="清除图标模拟状态",
            onclick=_clear_mock_icon_state,
            color="secondary",
            scope="develop_detail",
        )

        def _simulate_error_popup():
            """直接弹出错误提示卡片（复用 alas-update-notice 完整模板）。"""
            from module.handler.task_failure_protection import TaskFailureTracker, _now_iso
            import time

            instance = _get_debug_target_instance()
            if not instance:
                toast("未找到可用实例，无法模拟错误弹窗", color="warning")
                return

            try:
                tracker = TaskFailureTracker(instance)
                tracker._data.setdefault('notifications', [])
                tracker._data['notifications'].append({
                    'task': 'Commission',
                    'reason': 'GameStuckError',
                    'count': 3,
                    'timestamp': _now_iso(),
                    'read': False,
                })
                tracker._save()
            except Exception as e:
                toast(f"写入记录失败：{e}", color="warning")

            notice_id = f"task_failure_sim_{int(time.time() * 1000)}"
            actions_id = f"task_failure_sim_actions_{int(time.time() * 1000)}"

            remove_js = (
                f"var el=document.getElementById('{notice_id}');"
                f"if(el&&el.parentNode)el.parentNode.removeChild(el);"
            )

            html = (
                '<div id="' + notice_id + '" class="alas-update-notice" role="status" aria-live="polite">'
                '<div class="alas-update-notice__halo"></div>'
                '<div class="alas-update-notice__icon" aria-hidden="true" style="color:#f03e3e;background:rgba(240,62,62,0.08)">'
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
                ' stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
                '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
                '<line x1="12" y1="9" x2="12" y2="13"/>'
                '<line x1="12" y1="17" x2="12.01" y2="17"/>'
                '</svg>'
                '</div>'
                '<div class="alas-update-notice__body">'
                '<div class="alas-update-notice__eyebrow" style="color:#f03e3e">任务异常</div>'
                '<div class="alas-update-notice__title">委托任务已自动关闭</div>'
                '<div class="alas-update-notice__text">'
                '该任务因 GameStuckError 连续失败 3 次，已触发失败保护自动关闭。请检查配置后重新启用。'
                '</div>'
                '<div id="pywebio-scope-' + actions_id + '" class="alas-update-notice__actions"></div>'
                '</div>'
                '</div>'
            )

            with use_scope("ROOT"):
                put_html(html)

                def _go_handle():
                    run_js(remove_js)
                    try:
                        self.alas_set_group('Commission')
                    except Exception:
                        pass

                def _later():
                    run_js(remove_js)

                put_buttons(
                    [
                        {"label": "去处理", "value": "handle", "color": "danger"},
                        {"label": "稍后再说", "value": "later", "color": "secondary"},
                    ],
                    onclick=[_go_handle, _later],
                    scope=actions_id,
                )

            toast("模拟错误弹窗已弹出", color="success")

        put_button(
            label="模拟错误弹窗",
            onclick=_simulate_error_popup,
            color="danger",
            scope="develop_detail",
        )

        def _force_restart():
            if State.restart_event is None:
                toast(t("Gui.Toast.ReloadEnabled"), color="error")
                return
            if request_webui_restart():
                toast(t("Gui.Toast.AlasRestart"), duration=0, color="error")
            else:
                toast("自动更新正在进行或无法保存运行实例，已取消重启", color="error")

        put_button(label=t("重启Alas"), onclick=_force_restart, scope="develop_detail")

        def _test_notify_update():
            from module.notify.notify import notify_webui

            instance = getattr(self, "alas_name", DEFAULT_CONFIG_NAME)
            notify_webui(
                instance=instance,
                title="发现更新喵！",
                content="测试更新推送逻辑，启动器应显示专用标题。",
                update=True,
            )
            toast("已发送更新测试通知", color="success")

        def _test_notify_announcement():
            from module.notify.notify import notify_webui

            instance = getattr(self, "alas_name", DEFAULT_CONFIG_NAME)
            notify_webui(
                instance=instance,
                title="新公告喵！",
                content="测试公告推送逻辑，启动器应显示专用标题。",
                updata=False,
            )
            toast("已发送公告测试通知", color="info")

        def _test_notify_error():
            from module.notify import handle_notify

            instance = _get_debug_target_instance()
            if not instance:
                toast("未找到可用实例，无法发送错误推送测试", color="warning")
                return
            config = load_config(instance)
            success = handle_notify(
                config.Error_OnePushConfig,
                title=f"AzurPilot <{instance}> 崩溃",
                content=f"<{instance}> 开发者错误推送测试",
            )
            if success:
                toast("已发送错误推送测试", color="success")
            else:
                toast("错误推送测试发送失败，请检查错误推送设置", color="error")

        put_buttons(
            buttons=[
                {
                    "label": "测试更新推送 (updata=True)",
                    "value": "update",
                    "color": "danger",
                },
                {
                    "label": "测试公告推送 (updata=False)",
                    "value": "announcement",
                    "color": "info",
                },
                {
                    "label": "测试错误推送",
                    "value": "error",
                    "color": "danger",
                },
            ],
            onclick=[
                _test_notify_update,
                _test_notify_announcement,
                _test_notify_error,
            ],
            scope="develop_detail",
        )

        self._render_log_export_panel()

    def _render_log_export_panel(self) -> None:
        """渲染日志导出面板：实例下拉 + 导出今日运行日志 / 导出错误日志压缩包。

        下载全程走 fetch → Blob → <a download>，不使用 PyWebIO 的 download()：
        后者把文件字节塞进 pywebio 自己的 WebSocket，几十 MB 的错误日志会撑到
        SafeWebSocketConnection 的积压上限而被主动断连；走 HTTP 路由则与主会话解耦，
        且与远控（P2P/SSH 代理）链路上的其它 /api 请求同一条通路。

        两个导出接口都刻意不做本机限制，远控下直接可用。
        """
        instances = alas_instance()
        current = getattr(self, "alas_name", "") or (instances[0] if instances else "")
        options = "".join(
            f'<option value="{name}"{" selected" if name == current else ""}>'
            f"{name}</option>"
            for name in instances
        )

        put_html(
            f"""
            <div class="log-export-panel">
              <h2 class="alas-develop-section-title">{t("Gui.LogExport.Title")}</h2>
              <div class="log-export-row">
                <label class="log-export-instance">{t("Gui.LogExport.InstanceLabel")}
                  <select id="log-export-instance" class="deploy-setting-select">{options}</select>
                </label>
                <button id="log-export-runtime" class="deploy-setting-button" type="button">{t("Gui.LogExport.RuntimeLog")}</button>
                <button id="log-export-error" class="deploy-setting-button primary" type="button">{t("Gui.LogExport.ErrorLogs")}</button>
                <button id="log-export-error-text" class="deploy-setting-button" type="button">{t("Gui.LogExport.ErrorLogsText")}</button>
              </div>
              <div id="log-export-status" class="deploy-setting-status"></div>
            </div>
            """,
            scope="develop_detail",
        )
        run_js(
            f"""
            (function(){{
              var sel = document.getElementById('log-export-instance');
              var runtimeBtn = document.getElementById('log-export-runtime');
              var errorBtn = document.getElementById('log-export-error');
              var errorTextBtn = document.getElementById('log-export-error-text');
              var statusEl = document.getElementById('log-export-status');
              if (!sel || !runtimeBtn || !errorBtn || !errorTextBtn || !statusEl) return;
              var text = {{
                pending: {json.dumps(t("Gui.LogExport.Pending"))},
                packing: {json.dumps(t("Gui.LogExport.Packing"))},
                started: {json.dumps(t("Gui.LogExport.Started"))},
                failed: {json.dumps(t("Gui.LogExport.Failed"))},
                noInstance: {json.dumps(t("Gui.LogExport.NoInstance"))},
                confirm: {json.dumps(t("Gui.LogExport.Confirm"))},
                confirmSize: {json.dumps(t("Gui.LogExport.ConfirmSize"))},
                infoLoading: {json.dumps(t("Gui.LogExport.InfoLoading"))},
                noFiles: {json.dumps(t("Gui.LogExport.NoFiles"))}
              }};

              // 远控入口路径形如 /<8位以上小写字母数字>/...，与服务端 WebSocket
              // 采用同款前缀启发式（见 alas-utils.js 的 getSocketCandidates）：
              // 带前缀优先，再退回根相对，两种部署都能命中
              function apiCandidates(path) {{
                var list = [path];
                var parts = location.pathname.split('/').filter(Boolean);
                var first = parts.length ? parts[0] : '';
                if (/^[a-z0-9]{{8,}}$/.test(first)) list.unshift('/' + first + path);
                return list;
              }}

              // 逐个候选尝试，取第一个 200；全失败时返回最后一个响应，
              // 交给调用方展示服务端返回的错误文案
              async function fetchFirst(variants) {{
                var lastResponse = null;
                var lastError = null;
                for (var i = 0; i < variants.length; i++) {{
                  var urls = apiCandidates(variants[i]);
                  for (var j = 0; j < urls.length; j++) {{
                    try {{
                      var resp = await fetch(urls[j], {{cache: 'no-store'}});
                      if (resp.ok) return resp;
                      lastResponse = resp;
                    }} catch (err) {{
                      lastError = err;
                    }}
                  }}
                }}
                if (lastResponse) return lastResponse;
                throw lastError || new Error('network error');
              }}

              // 运行日志接口的多种取法：query 形式优先，path 形式兜底。
              // 本仓库既有事故：P2P 远控代理转发 WebSocket 握手时会剥掉 query string，
              // 因此不能把实例名唯一的寄托在 query 上
              function runtimeVariants(instance) {{
                var enc = encodeURIComponent(instance);
                return ['/api/log/runtime?instance=' + enc, '/api/log/runtime/' + enc];
              }}

              // Content-Disposition 两式都解析：Starlette 对非 ASCII 文件名
              // （如 小号）用 RFC 5987 的 filename*=utf-8'' 形式
              function parseFileName(header) {{
                if (!header) return null;
                var star = header.match(/filename\\*=(?:utf-8|UTF-8)''([^;]+)/);
                if (star) {{
                  try {{ return decodeURIComponent(star[1]); }}
                  catch (e) {{ return star[1]; }}
                }}
                var plain = header.match(/filename="?([^";]+)"?/);
                return plain ? plain[1] : null;
              }}

              async function readError(resp) {{
                try {{
                  var data = await resp.json();
                  return data && data.error ? data.error : '';
                }} catch (e) {{
                  return '';
                }}
              }}

              function saveBlob(resp, fallbackName) {{
                return resp.blob().then(function(blob){{
                  // P2P 代理会剥掉 Content-Length，远控下拿不到总长度，
                  // 不做百分比进度，只做不确定态提示 + 落地
                  var name = parseFileName(resp.headers.get('Content-Disposition')) || fallbackName;
                  var url = URL.createObjectURL(blob);
                  var a = document.createElement('a');
                  a.href = url;
                  a.download = name;
                  document.body.appendChild(a);
                  a.click();
                  document.body.removeChild(a);
                  setTimeout(function(){{ URL.revokeObjectURL(url); }}, 60000);
                }});
              }}

              async function exportLog(variants, fallbackName, withInstance) {{
                var instance = sel.value;
                if (withInstance && !instance) {{
                  statusEl.textContent = text.noInstance;
                  return;
                }}
                // 错误日志接口不带参数，直接使用传入的单一候选
                var candidates = withInstance ? runtimeVariants(instance) : variants;
                runtimeBtn.disabled = true;
                errorBtn.disabled = true;
                statusEl.textContent = withInstance ? text.pending : text.packing;
                try {{
                  var resp = await fetchFirst(candidates);
                  if (!resp.ok) {{
                    var detail = await readError(resp);
                    statusEl.textContent = text.failed + (detail ? ': ' + detail : '');
                    return;
                  }}
                  await saveBlob(resp, fallbackName);
                  statusEl.textContent = text.started;
                }} catch (err) {{
                  // fetch 通路整体不可用时，退回浏览器直接导航下载（内容同样经隧道）
                  try {{
                    window.location.href = apiCandidates(candidates[0])[0];
                    statusEl.textContent = text.started;
                  }} catch (e2) {{
                    statusEl.textContent = text.failed + (err && err.message ? ': ' + err.message : '');
                  }}
                }} finally {{
                  runtimeBtn.disabled = false;
                  errorBtn.disabled = false;
                }}
              }}

              runtimeBtn.addEventListener('click', function(){{
                exportLog(['/api/log/runtime'], 'alas_runtime_log.txt', true);
              }});

              // 文案里的 {{files}}/{{size}}/{{zip}} 由 t() 的 .format() 还原成
              // 单花括号后在此替换（i18n 里必须写双花括号，否则 t() 会抛 KeyError）。
              // 用 split/join 而非正则：f-string 里写带反斜杠的正则转义会触发
              // "invalid escape sequence" 警告，未来 Python 版本会直接报错
              function formatConfirm(template, values) {{
                var out = String(template);
                Object.keys(values).forEach(function(key){{
                  out = out.split('{{' + key + '}}').join(String(values[key]));
                }});
                return out;
              }}

              async function fetchInfo(scope) {{
                var resp = await fetchFirst(['/api/log/error/info?scope=' + scope]);
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var data = await resp.json();
                if (!data.success) throw new Error(data.error || 'unknown error');
                return data.data;
              }}

              // 先统计真实体积再确认：远控下几十 MB 要传很久，
              // 让用户在点下去之前就知道要等多久，而不是盯着没反应的界面猜
              async function exportErrorLogs(scope) {{
                errorBtn.disabled = true;
                errorTextBtn.disabled = true;
                statusEl.textContent = text.infoLoading;
                var proceed = false;
                var empty = false;
                try {{
                  var info = await fetchInfo(scope);
                  if (info.files) {{
                    proceed = confirm(formatConfirm(text.confirmSize, {{
                      files: info.files,
                      size: info.human_bytes,
                      zip: info.human_estimate
                    }}));
                  }} else {{
                    empty = true;
                  }}
                }} catch (err) {{
                  // 统计失败不阻断导出，退回通用确认
                  proceed = confirm(text.confirm);
                }} finally {{
                  errorBtn.disabled = false;
                  errorTextBtn.disabled = false;
                }}
                if (empty) {{
                  statusEl.textContent = text.noFiles;
                  return;
                }}
                if (!proceed) {{
                  statusEl.textContent = '';
                  return;
                }}
                var fallback = scope === 'text'
                  ? 'AzurPilot-error-logs-text.zip'
                  : 'AzurPilot-error-logs.zip';
                exportLog(['/api/log/error?scope=' + scope], fallback, false);
              }}

              errorBtn.addEventListener('click', function(){{
                exportErrorLogs('full');
              }});
              errorTextBtn.addEventListener('click', function(){{
                exportErrorLogs('text');
              }});
            }})();
            """
        )

    @render_locked
    @use_scope("content", clear=True)
    def dev_remote(self) -> None:
        self.init_menu(name="Remote")
        self.set_title(t("Gui.MenuDevelop.Remote"))
        put_scope("develop_detail")
        with use_scope("develop_detail"):
            put_row(
                content=[put_scope("remote_loading"), None, put_scope("remote_state")],
                size="auto .25rem 1fr",
            )
            put_scope("remote_info")

        def u(state):
            if state == -1:
                return
            status_map = {
                "direct_p2p": t("Gui.Remote.StatusDirect"),
                "turn_relay": t("Gui.Remote.StatusTurn"),
                "ssh_forward": t("Gui.Remote.StatusSsh"),
                "waiting_peer": t("Gui.Remote.StatusSignaling"),
                "signaling": t("Gui.Remote.StatusSignaling"),
                "starting": t("Gui.Remote.StatusStarting"),
                "dependency_missing": t("Gui.Remote.StatusSsh"),
                "failed": t("Gui.Remote.StatusFailed"),
            }
            clear("remote_loading")
            clear("remote_state")
            clear("remote_info")
            if state in (1, 2):
                put_loading("grow", "success", "remote_loading").style(
                    "--loading-grow--"
                )
                remote_status = RemoteAccess.get_connection_state()
                put_text(
                    f"{t('Gui.Remote.Running')} · {status_map.get(remote_status, remote_status)}",
                    scope="remote_state",
                )
                put_text(t("Gui.Remote.EntryPoint"), scope="remote_info")
                entrypoint = RemoteAccess.get_entry_point()
                if entrypoint:
                    if State.electron:  # Prevent click into url in electron client
                        put_text(entrypoint, scope="remote_info").style(
                            "text-decoration-line: underline"
                        )
                    else:
                        put_link(name=entrypoint, url=entrypoint, scope="remote_info")
                else:
                    put_text("Loading...", scope="remote_info")
                remote_error = RemoteAccess.get_error()
                if remote_error and remote_status in ("dependency_missing", "failed"):
                    put_warning(remote_error, closable=False, scope="remote_info")
            elif state in (0, 3, 4):
                put_loading("border", "secondary", "remote_loading").style(
                    "--loading-border-fill--"
                )
                if State.deploy_config.EnableRemoteAccess and (
                    State.deploy_config.Password or os.environ.get("DEMO") == "1"
                ):
                    put_text(t("Gui.Remote.NotRunning"), scope="remote_state")
                else:
                    put_text(t("Gui.Remote.NotEnable"), scope="remote_state")
                put_text(t("Gui.Remote.ConfigureHint"), scope="remote_info")
                url = "http://app.azurlane.cloud" + (
                    "" if State.deploy_config.Language.startswith("zh") else "/en.html"
                )
                put_html(
                    f'<a href="{url}" target="_blank">{url}</a>', scope="remote_info"
                )
                if state == 3:
                    put_warning(
                        t("Gui.Remote.SSHNotInstall"),
                        closable=False,
                        scope="remote_info",
                    )

        remote_switch = Switch(
            status=u, get_state=RemoteAccess.get_state, name="remote"
        )

        self.task_handler.add(remote_switch.g(), delay=1, pending_delete=True)
