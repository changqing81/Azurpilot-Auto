"""WebUI部署设置"""

from module.webui.app_dependencies import (
    json,
    put_html,
    put_scope,
    run_js,
    t,
    use_scope,
)


from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked


class DeveloperSettingsMixin(WebUIMixinBase):
    """WebUI部署设置"""

    @render_locked
    @use_scope("content", clear=True)
    def dev_setting(self) -> None:
        self.init_menu(name="Setting", skip_clear=True)
        self.set_title(t("Gui.MenuDevelop.Setting"))
        put_scope("develop_detail")
        # ── 界面版本切换 ──
        # 长卿 2026-11-16：
        #   「老UI这里也得搞一个像新UI的切换入口 点击就切换」
        # 新 UI 的设置页早就有这个入口，老 UI 一直只能改 deploy.yaml 再手动重启。
        # 这里补上同样的入口：点一下 = 写 deploy.yaml + 自动重启 AzurPilot，
        # 重启完自动跳到对应界面，中间不需要用户再点第二次、也不用去翻配置文件。
        #
        # 用老 UI 现成的 /newapi/ui（读）、/newapi/ui/switch（写）、
        # /newapi/restart（重启）三个端点，不新增后端逻辑。
        put_html(
            f"""
            <div id="ui-switch-panel" class="launcher-setting-panel">
              <h2 class="alas-develop-section-title">{t("Gui.DeploySetting.UI")}</h2>
              <div class="launcher-setting-row">
                <div>
                  <div class="launcher-setting-title" id="ui-switch-current">{t("Gui.DeploySetting.Loading")}</div>
                  <div class="launcher-setting-desc">{t("Gui.DeploySetting.UIHelp")}</div>
                </div>
                <div class="deploy-setting-toolbar-actions">
                  <button id="ui-switch-legacy" class="deploy-setting-button" type="button" disabled>{t("Gui.DeploySetting.UISwitchLegacy")}</button>
                  <button id="ui-switch-new" class="deploy-setting-button primary" type="button" disabled>{t("Gui.DeploySetting.UISwitchNew")}</button>
                </div>
              </div>
              <div id="ui-switch-status" class="launcher-setting-status"></div>
            </div>
            """,
            scope="develop_detail",
        )
        run_js(
            f"""
            (function(){{
              const panel = document.getElementById('ui-switch-panel');
              if (!panel) return;
              const currentEl = document.getElementById('ui-switch-current');
              const statusEl = document.getElementById('ui-switch-status');
              const legacyBtn = document.getElementById('ui-switch-legacy');
              const newBtn = document.getElementById('ui-switch-new');
              const text = {{
                legacy: {json.dumps(t("Gui.DeploySetting.UISwitchLegacy"))},
                new: {json.dumps(t("Gui.DeploySetting.UISwitchNew"))},
                applying: {json.dumps(t("Gui.DeploySetting.UISwitchApplying"))},
                done: {json.dumps(t("Gui.DeploySetting.UISwitchDone"))},
                failed: {json.dumps(t("Gui.DeploySetting.UISwitchFailed"))},
                restarting: {json.dumps(t("Gui.DeploySetting.UISwitchRestarting"))},
                restartSent: {json.dumps(t("Gui.DeploySetting.UISwitchRestartSent"))},
                currentTpl: {json.dumps(t("Gui.DeploySetting.UISwitchCurrent"))},
                pendingTpl: {json.dumps(t("Gui.DeploySetting.UISwitchPending"))},
                nameLegacy: {json.dumps(t("Gui.DeploySetting.UISwitchNameLegacy"))},
                nameNew: {json.dumps(t("Gui.DeploySetting.UISwitchNameNew"))}
              }};
              const NAME = {{legacy: text.nameLegacy, 'new': text.nameNew}};
              let busy = false;
              let ui = 'legacy';

              function label(value) {{
                return value === 'new' ? NAME['new'] : NAME.legacy;
              }}

              function paint() {{
                // 与翻译拼装而不是用花括号占位：t() 内部走 str.format()，
                // 翻译文本里出现任何花括号都会抛 KeyError（实测踩过）。
                currentEl.textContent = text.currentTpl + label(ui)
                  + (restartRequired ? '  ' + text.pendingTpl + label(ui === 'new' ? 'legacy' : 'new') : '');
                legacyBtn.disabled = busy || ui === 'legacy';
                newBtn.disabled = busy || ui === 'new';
              }}

              function sleep(ms) {{
                return new Promise(function(resolve) {{ setTimeout(resolve, ms); }});
              }}

              // ui = **进程实际在跑**的界面，不是 deploy.yaml 里写的。
              // 后端只把"真正挂载了哪套"放进 ui，配置值放 configured，
              // 两者不一致时 restartRequired 为真 —— 据此提示"待重启"。
              let restartRequired = false;

              async function refresh() {{
                try {{
                  const resp = await fetch('/newapi/ui', {{cache: 'no-store'}});
                  const data = await resp.json();
                  ui = data.ui === 'new' ? 'new' : 'legacy';
                  restartRequired = !!data.restart_required;
                }} catch (err) {{
                  statusEl.textContent = text.failed + (err.message || err);
                }}
                paint();
              }}

              async function waitForRestart(target) {{
                const deadline = Date.now() + 180000;
                // 重启期间服务会先断开再恢复：断连/非 2xx 都当作"还没起来"，
                // 直到读回的目标界面版本与请求一致，才跳转到该界面。
                while (Date.now() < deadline) {{
                  await sleep(1200);
                  try {{
                    const resp = await fetch('/newapi/ui', {{cache: 'no-store'}});
                    if (!resp.ok) continue;
                    const data = await resp.json();
                    if ((data.ui === 'new' ? 'new' : 'legacy') === target) {{
                      location.href = '/';
                      return;
                    }}
                  }} catch (err) {{
                    // 服务正在重启，继续等
                  }}
                }}
                statusEl.textContent = text.failed + 'timeout';
                busy = false;
                paint();
              }}

              async function switchTo(target) {{
                if (busy || target === ui) return;
                busy = true;
                paint();
                statusEl.textContent = text.applying;
                try {{
                  const resp = await fetch('/newapi/ui/switch', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{ui: target}})
                  }});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || 'unknown error');
                  }}
                }} catch (err) {{
                  statusEl.textContent = text.failed + (err.message || err);
                  busy = false;
                  paint();
                  return;
                }}
                statusEl.textContent = text.done;
                let restartOk = false;
                try {{
                  const resp = await fetch('/newapi/restart', {{method: 'POST'}});
                  const result = await resp.json();
                  restartOk = result.success !== false;
                  if (!restartOk) {{
                    statusEl.textContent = text.failed + (result.message || 'restart refused');
                  }}
                }} catch (err) {{
                  statusEl.textContent = text.failed + (err.message || err);
                }}
                if (!restartOk) {{
                  busy = false;
                  paint();
                  return;
                }}
                statusEl.textContent = text.restartSent;
                await waitForRestart(target);
              }}

              legacyBtn.addEventListener('click', function() {{ switchTo('legacy'); }});
              newBtn.addEventListener('click', function() {{ switchTo('new'); }});
              refresh();
            }})();
            """
        )
        put_html(
            f"""
            <div class="launcher-setting-panel">
              <h2 class="alas-develop-section-title">{t("Gui.Launcher.StartupTitle")}</h2>
              <div class="launcher-setting-row">
                <div>
                  <div class="launcher-setting-title">{t("Gui.Launcher.AutoStart")}</div>
                  <div class="launcher-setting-desc">{t("Gui.Launcher.AutoStartHelp")}</div>
                </div>
                <label class="launcher-switch" title="{t("Gui.Launcher.AutoStart")}">
                  <input id="launcher-autostart-switch" type="checkbox" disabled>
                </label>
              </div>
              <div id="launcher-status" class="launcher-setting-status">{t("Gui.Launcher.Loading")}</div>
            </div>
            """,
            scope="develop_detail",
        )
        run_js(
            f"""
            (function(){{
              const statusEl = document.getElementById('launcher-status');
              const switchEl = document.getElementById('launcher-autostart-switch');
              const text = {{
                loading: {json.dumps(t("Gui.Launcher.Loading"))},
                connected: {json.dumps(t("Gui.Launcher.Connected"))},
                disconnected: {json.dumps(t("Gui.Launcher.Disconnected"))},
                remote: {json.dumps(t("Gui.Launcher.RemoteUnavailable"))},
                unsupported: {json.dumps(t("Gui.Launcher.Unsupported"))},
                enabled: {json.dumps(t("Gui.Launcher.Enabled"))},
                disabled: {json.dumps(t("Gui.Launcher.Disabled"))},
                setting: {json.dumps(t("Gui.Launcher.Setting"))},
                failed: {json.dumps(t("Gui.Launcher.Failed"))}
              }};

              async function refresh() {{
                switchEl.disabled = true;
                statusEl.textContent = text.loading;
                try {{
                  const resp = await fetch('/api/launcher/status', {{cache: 'no-store'}});
                  const data = await resp.json();
                  const enabled = data.autostart_enabled === true;
                  switchEl.checked = enabled;
                  if (!data.request_local) {{
                    statusEl.textContent = text.remote;
                    return;
                  }}
                  if (!data.autostart_supported) {{
                    statusEl.textContent = text.unsupported;
                    return;
                  }}
                  if (!data.launcher_connected) {{
                    statusEl.textContent = text.disconnected;
                    return;
                  }}
                  switchEl.disabled = false;
                  if (data.autostart_enabled === null) {{
                    statusEl.textContent = text.connected + ' · ' + text.loading;
                  }} else {{
                    statusEl.textContent = text.connected + ' · ' + (enabled ? text.enabled : text.disabled);
                  }}
                }} catch (err) {{
                  statusEl.textContent = text.failed + ': ' + err;
                }}
              }}

              switchEl.addEventListener('change', async function() {{
                const target = switchEl.checked;
                switchEl.disabled = true;
                statusEl.textContent = text.setting;
                try {{
                  const resp = await fetch('/api/launcher/startup', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{enabled: target}})
                  }});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || 'unknown error');
                  }}
                }} catch (err) {{
                  switchEl.checked = !target;
                  statusEl.textContent = text.failed + ': ' + err.message;
                  setTimeout(refresh, 1600);
                  return;
                }}
                await refresh();
              }});

              refresh();
            }})();
            """
        )
        put_html(
            f"""
            <div id="deploy-setting-root" class="deploy-setting-panel">
              <div class="deploy-setting-toolbar">
                <div>
                  <h2 class="alas-develop-section-title">{t("Gui.DeploySetting.Title")}</h2>
                  <div id="deploy-setting-notice" class="deploy-setting-notice">{t("Gui.DeploySetting.Loading")}</div>
                </div>
                <div class="deploy-setting-toolbar-actions">
                  <button id="deploy-setting-save" class="deploy-setting-button primary" type="button" disabled>{t("Gui.DeploySetting.Save")}</button>
                  <button id="deploy-setting-refresh" class="deploy-setting-button" type="button">{t("Gui.DeploySetting.Refresh")}</button>
                </div>
              </div>
              <div id="deploy-setting-fields"></div>
              <div id="deploy-setting-status" class="deploy-setting-status"></div>
            </div>
            """,
            scope="develop_detail",
        )
        run_js(
            f"""
            (function(){{
              const fieldsEl = document.getElementById('deploy-setting-fields');
              const noticeEl = document.getElementById('deploy-setting-notice');
              const statusEl = document.getElementById('deploy-setting-status');
              const saveBtn = document.getElementById('deploy-setting-save');
              const refreshBtn = document.getElementById('deploy-setting-refresh');
              const text = {{
                loading: {json.dumps(t("Gui.DeploySetting.Loading"))},
                save: {json.dumps(t("Gui.DeploySetting.Save"))},
                saving: {json.dumps(t("Gui.DeploySetting.Saving"))},
                saved: {json.dumps(t("Gui.DeploySetting.Saved"))},
                failed: {json.dumps(t("Gui.DeploySetting.Failed"))},
                yes: {json.dumps(t("Gui.DeploySetting.Enabled"))},
                no: {json.dumps(t("Gui.DeploySetting.Disabled"))},
                demo: {json.dumps(t("Gui.DeploySetting.DemoDisabled"))}
              }};
              let schema = null;

              function escapeHtml(value) {{
                return String(value == null ? '' : value)
                  .replace(/&/g, '&amp;')
                  .replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;');
              }}

              function fieldHtml(field) {{
                const value = field.value == null ? '' : field.value;
                let input = '';
                if (field.type === 'bool') {{
                  input = `<label class="launcher-switch"><input data-deploy-key="${{escapeHtml(field.key)}}" type="checkbox" ${{value === true ? 'checked' : ''}}></label>`;
                }} else if (field.type === 'select') {{
                  // options 支持两种形态（纯值 / 值+显示名二元组），
                  // 二元组若不归一化会被 JS 模板字符串隐式转成 "new,新 UI"，
                  // 导致下拉框显示错乱、提交值校验失败。
                  const pick = (opt) => Array.isArray(opt) ? {{ value: String(opt[0]), label: String(opt[1]) }} : {{ value: String(opt), label: String(opt) }};
                  const options = (field.options || []).map(opt => {{
                    const o = pick(opt);
                    return `<option value="${{escapeHtml(o.value)}}" ${{o.value === String(value) ? 'selected' : ''}}>${{escapeHtml(o.label)}}</option>`;
                  }}).join('');
                  input = `<select class="deploy-setting-select" data-deploy-key="${{escapeHtml(field.key)}}">${{options}}</select>`;
                }} else if (field.type === 'int') {{
                  input = `<input class="deploy-setting-input" data-deploy-key="${{escapeHtml(field.key)}}" type="number" min="0" value="${{escapeHtml(value)}}">`;
                }} else {{
                  input = `<input class="deploy-setting-input" data-deploy-key="${{escapeHtml(field.key)}}" type="text" value="${{escapeHtml(value)}}">`;
                }}
                return `
                  <div class="deploy-setting-field">
                    <div>
                      <label>${{escapeHtml(field.label)}}</label>
                      <div class="deploy-setting-help">${{escapeHtml(field.help)}}</div>
                    </div>
                    <div>${{input}}</div>
                  </div>
                `;
              }}

              function render(data) {{
                schema = data;
                noticeEl.textContent = data.notice || '';
                fieldsEl.innerHTML = (data.groups || []).map(group => `
                  <div class="deploy-setting-group">
                    <div class="deploy-setting-group-title">${{escapeHtml(group.label)}}</div>
                    ${{(group.fields || []).map(fieldHtml).join('')}}
                  </div>
                `).join('');
                saveBtn.disabled = !!data.demo;
                statusEl.textContent = data.demo ? text.demo : '';
              }}

              function collectValues() {{
                const values = {{}};
                fieldsEl.querySelectorAll('[data-deploy-key]').forEach(el => {{
                  const key = el.getAttribute('data-deploy-key');
                  if (el.type === 'checkbox') {{
                    values[key] = el.checked;
                  }} else if (el.type === 'number') {{
                    values[key] = el.value;
                  }} else {{
                    values[key] = el.value;
                  }}
                }});
                return values;
              }}

              async function refresh() {{
                saveBtn.disabled = true;
                statusEl.textContent = text.loading;
                try {{
                  const resp = await fetch('/api/deploy/settings', {{cache: 'no-store'}});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || 'unknown error');
                  }}
                  render(result.data);
                  statusEl.textContent = '';
                }} catch (err) {{
                  statusEl.textContent = text.failed + ': ' + (err.message || err);
                }}
              }}

              async function save() {{
                if (!schema || saveBtn.disabled) return;
                saveBtn.disabled = true;
                saveBtn.textContent = text.saving;
                statusEl.textContent = text.saving;
                try {{
                  const resp = await fetch('/api/deploy/settings', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{values: collectValues()}})
                  }});
                  const result = await resp.json();
                  if (!result.success) {{
                    throw new Error(result.error || 'unknown error');
                  }}
                  await refresh();
                  statusEl.textContent = text.saved;
                }} catch (err) {{
                  statusEl.textContent = text.failed + ': ' + (err.message || err);
                }} finally {{
                  saveBtn.textContent = text.save;
                  saveBtn.disabled = schema && schema.demo;
                }}
              }}

              refreshBtn.addEventListener('click', refresh);
              saveBtn.addEventListener('click', save);
              refresh();
            }})();
            """
        )
