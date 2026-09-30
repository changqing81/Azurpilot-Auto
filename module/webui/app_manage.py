"""WebUI实例管理页面"""

from html import escape
from typing import TYPE_CHECKING

from module.webui.app_dependencies import (
    Any,
    Dict,
    IS_ON_PHONE_CLOUD,
    List,
    Optional,
    ProcessManager,
    State,
    actions,
    alas_instance,
    alas_template,
    cast,
    clear,
    download,
    eval_js,
    file_upload,
    filepath_args,
    filepath_config,
    get_config_mod,
    input_group,
    json,
    load_config,
    os,
    parse_task_priority,
    partial,
    pin,
    put_button,
    put_buttons,
    put_column,
    put_error,
    put_html,
    put_input,
    put_markdown,
    put_row,
    put_scope,
    put_select,
    put_text,
    put_warning,
    read_file,
    run_js,
    t,
    task_priority_from_config,
    toast,
    use_scope,
)

if TYPE_CHECKING:
    from module.webui.app import AlasGUI


# 实例卡片左上角的方块图标（lucide `server`），与 React 新前端实例卡片上的图标同一形状。
# 必须带 width/height：只给 viewBox 的话，一旦外层的尺寸约束没落到它身上，
# SVG 会按 300x150 的默认尺寸渲染，把 48px 的方块撑破。
_INSTANCE_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"'
    ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"'
    ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
    '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/>'
    '<rect width="20" height="8" x="2" y="14" rx="2" ry="2"/>'
    '<line x1="6" x2="6.01" y1="6" y2="6"/>'
    '<line x1="6" x2="6.01" y1="18" y2="18"/>'
    "</svg>"
)

# ProcessManager.state 的取值与侧栏的状态图标同一套：1 运行中 / 2 停止 / 3 异常 / 4 更新中。
# 键用于拼 CSS 钩子，值用于取文案。
_INSTANCE_STATUS = {
    1: ("running", "Gui.AppManage.StatusRunning"),
    2: ("stopped", "Gui.AppManage.StatusStopped"),
    3: ("error", "Gui.AppManage.StatusError"),
    4: ("updating", "Gui.AppManage.StatusUpdating"),
}
_INSTANCE_STATUS_FALLBACK = _INSTANCE_STATUS[2]


def app_manage(gui: "AlasGUI") -> None:
    """显示实例创建、导入、导出和删除管理页。

    Args:
        gui: 当前 WebUI 会话对象。
    """
    expanded_summaries: set[str] = set()

    def _read_config_mapping(path: str) -> Dict[str, Any]:
        """读取配置 JSON，并确保根节点是对象。"""
        data = read_file(path)
        if not isinstance(data, dict):
            raise ValueError(f"配置文件根节点不是对象：{path}")
        return cast(Dict[str, Any], data)

    def _show_legacy_import_result():
        raw = eval_js(
            "(function(){var r=sessionStorage.getItem('import_msg');"
            "if(r){sessionStorage.removeItem('import_msg');return r;}"
            "return null;})()"
        )
        if not isinstance(raw, str):
            return
        try:
            result = json.loads(raw)
        except TypeError, ValueError:
            return
        if not isinstance(result, dict):
            return
        legacy_result = cast(Dict[str, Any], result)
        if legacy_result.get("ok"):
            toast(
                t("Gui.AppManage.ImportLegacySuccess"),
                color="success",
                duration=10,
            )
        else:
            toast(
                t(
                    "Gui.AppManage.ImportLegacyFailed",
                    error=legacy_result.get(
                        "error", t("Gui.AppManage.ImportLegacyUnknownError")
                    ),
                ),
                color="error",
                duration=10,
            )

    def get_unused_name():
        all_name = alas_instance()
        for i in range(2, 100):
            if f"alas{i}" not in all_name:
                return f"alas{i}"
        return ""

    def validate_name(name: str):
        if not name or not name.strip():
            return t("Gui.AppManage.InvalidChar")
        if name in alas_instance():
            return t("Gui.AppManage.NameExist")
        if set(name) & set(".\\/:*?\"'<>|"):
            return t("Gui.AppManage.InvalidChar")
        if name.lower().startswith("template"):
            return t("Gui.AppManage.InvalidPrefixTemplate")
        return None

    def _export(config_name: str):
        mod_name = get_config_mod(config_name)
        if mod_name == "alas":
            filename = f"{config_name}.json"
        else:
            filename = f"{config_name}.{mod_name}.json"
        with open(filepath_config(config_name, mod_name), "rb") as f:
            download(filename, f.read())

    def _get_enabled_tasks(config_name: str, mod_name: str) -> List[str]:
        config = _read_config_mapping(filepath_config(config_name, mod_name))
        args = _read_config_mapping(filepath_args("args", mod_name))
        priority = parse_task_priority(task_priority_from_config(config, args))

        enabled: List[str] = []
        for task_data in config.values():
            if not isinstance(task_data, dict):
                continue
            scheduler = task_data.get("Scheduler")
            if not isinstance(scheduler, dict) or scheduler.get("Enable") is not True:
                continue
            command = scheduler.get("Command")
            if isinstance(command, str) and command and command not in enabled:
                enabled.append(command)

        enabled_set = set(enabled)
        ordered = [task for task in priority if task in enabled_set]
        ordered.extend(task for task in enabled if task not in ordered)
        return ordered

    def _toggle_summary(config_name: str, index: int):
        summary_scope = f"manage_config_summary_{index}"
        if config_name in expanded_summaries:
            expanded_summaries.remove(config_name)
            clear(summary_scope)
            _render_config_actions(config_name, index)
            return

        mod_name = get_config_mod(config_name)
        try:
            tasks = _get_enabled_tasks(config_name, mod_name)
        except (OSError, ValueError) as e:
            toast(
                t("Gui.AppManage.SummaryLoadFailed", error=e),
                color="error",
            )
            return

        expanded_summaries.add(config_name)
        with use_scope(summary_scope, clear=True):
            summary_content = [
                put_text(f"{t('Gui.AppManage.EnabledTasks')}: {len(tasks)}").style(
                    "--manage-summary-title--"
                ),
                put_text(t("Gui.AppManage.SchedulerOrderHint")).style(
                    "--manage-summary-hint--"
                ),
            ]
            if not tasks:
                summary_content.append(
                    put_text(t("Gui.Overview.NoTask")).style("--manage-summary-empty--")
                )
            else:
                for task_index, task in enumerate(tasks, start=1):
                    summary_content.append(
                        put_row(
                            [
                                put_text(str(task_index)).style(
                                    "--manage-summary-rank--"
                                ),
                                put_column(
                                    [
                                        put_text(t(f"Task.{task}.name")).style(
                                            "--manage-summary-name--"
                                        ),
                                        put_text(task).style("--manage-summary-code--"),
                                    ],
                                    size="auto auto",
                                ),
                            ],
                            size="2.25rem minmax(0, 1fr)",
                        ).style("--manage-summary-task--")
                    )
            put_column(summary_content).style("--manage-summary-panel--")
        _render_config_actions(config_name, index)

    def _render_config_actions(config_name: str, index: int):
        action_scope = f"manage_config_actions_{index}"
        with use_scope(action_scope, clear=True):
            put_buttons(
                buttons=[
                    {
                        "label": t(
                            "Gui.AppManage.Collapse"
                            if config_name in expanded_summaries
                            else "Gui.AppManage.Summary"
                        ),
                        "value": "summary",
                        "color": "primary",
                    },
                    {
                        "label": t("Gui.AppManage.Export"),
                        "value": "export",
                        "color": "primary",
                    },
                    {
                        "label": t("Gui.AppManage.Delete"),
                        "value": "delete",
                        "color": "danger",
                        "disabled": IS_ON_PHONE_CLOUD,
                    },
                ],
                onclick=[
                    partial(_toggle_summary, config_name, index),
                    partial(_export, config_name),
                    partial(_delete, config_name),
                ],
            ).style("--manage-config-actions--")

    def _delete_block_reason(config_name: str) -> Optional[str]:
        if len(alas_instance()) <= 1:
            return t("Gui.AppManage.DeleteLast")
        if ProcessManager.is_running(config_name):
            return t("Gui.AppManage.DeleteRunning", name=config_name)
        return None

    def _delete(config_name: str):
        if IS_ON_PHONE_CLOUD:
            return

        reason = _delete_block_reason(config_name)
        if reason:
            toast(reason, color="warning")
            return

        resp = input_group(
            label=f"{t('Gui.AppManage.Delete')}: {config_name}",
            inputs=[
                actions(
                    name="action",
                    label=t("Gui.AppManage.DeleteConfirm", name=config_name),
                    buttons=[
                        {
                            "label": t("Gui.AppManage.Delete"),
                            "value": "confirm",
                            "type": "submit",
                            "color": "danger",
                        },
                        {
                            "label": t("Gui.AppManage.Back"),
                            "type": "cancel",
                            "color": "light",
                        },
                    ],
                )
            ],
        )
        if resp is None:
            return

        reason = _delete_block_reason(config_name)
        if reason:
            toast(reason, color="warning")
            return

        mod_name = get_config_mod(config_name)
        try:
            os.remove(filepath_config(config_name, mod_name))
        except OSError as e:
            toast(
                t("Gui.AppManage.DeleteFailed", error=e),
                color="error",
            )
            return

        ProcessManager.remove_manager(config_name)
        gui.refresh_aside_instances(force=True)
        toast(
            t("Gui.AppManage.DeleteSuccess", name=config_name),
            color="success",
        )
        _show_list()

    def _instance_status(config_name: str):
        """返回实例的 (CSS 钩子后缀, 文案 key)。

        取值与侧栏实例图标同一来源；探测失败时按「待命中」处理，
        避免管理页因为单个实例的状态读不到而整页渲染不出来。
        """
        try:
            state = ProcessManager.get_manager(config_name).state
        except Exception:
            return _INSTANCE_STATUS_FALLBACK
        return _INSTANCE_STATUS.get(state, _INSTANCE_STATUS_FALLBACK)

    @use_scope("content", clear=True)
    def _show_list():
        with gui.render_lock:
            expanded_summaries.clear()
            gui.init_menu(name="ManageList")
            gui.set_title(t("Gui.AppManage.PageTitle"))
            put_scope("manage_config_list")
            with use_scope("manage_config_list"):
                for index, name in enumerate(alas_instance()):
                    mod_name = get_config_mod(name)
                    status_key, status_label = _instance_status(name)
                    action_scope = f"manage_config_actions_{index}"
                    summary_scope = f"manage_config_summary_{index}"
                    put_scope(
                        f"manage_config_card_{index}",
                        [
                            put_html(
                                '<div class="manage-card-head">'
                                f'<span class="manage-card-icon">{_INSTANCE_ICON}</span>'
                                '<span class="manage-card-badge'
                                f' manage-card-badge-{status_key}">'
                                f"{escape(t(status_label))}</span>"
                                "</div>"
                            ),
                            put_text(name).style("--manage-config-name--"),
                            put_text(
                                f"{t('Gui.AppManage.Mod')}: {mod_name}"
                            ).style("--manage-config-meta--"),
                            put_scope(action_scope),
                            put_scope(summary_scope),
                        ],
                    ).style("--manage-config-card--")
                    _render_config_actions(name, index)

    def _create():
        name = cast(str, pin["ManageNew_name"])
        origin = cast(str, pin["ManageNew_copyfrom"])
        clear("manage_add_feedback")
        gui.pin_remove_invalid_mark("ManageNew_name")

        error = validate_name(name)
        if error:
            gui.pin_set_invalid_mark("ManageNew_name")
            put_error(error, scope="manage_add_feedback")
            return

        config = load_config(origin).read_file(origin)
        State.config_updater.write_file(name, config, get_config_mod(origin))
        toast(t("Gui.AppManage.NewSuccess"), color="success")
        gui.refresh_aside_instances(force=True)
        _show_list()

    @use_scope("content", clear=True)
    def _show_new():
        with gui.render_lock:
            gui.init_menu(name="ManageNew", skip_clear=True)
            gui.set_title(t("Gui.AppManage.TitleNew"))
            put_scope("manage_add_form")
            with use_scope("manage_add_form"):
                put_input(
                    name="ManageNew_name",
                    label=t("Gui.AppManage.NewName"),
                    value=get_unused_name(),
                )
                put_select(
                    name="ManageNew_copyfrom",
                    label=t("Gui.AppManage.CopyFrom"),
                    options=alas_template() + alas_instance(),
                    value="template-alas",
                )
                put_scope("manage_add_feedback")
                put_buttons(
                    buttons=[
                        {
                            "label": t("Gui.AddAlas.Confirm"),
                            "value": "confirm",
                            "color": "on",
                        },
                        {
                            "label": t("Gui.AppManage.Back"),
                            "value": "back",
                            "color": "off",
                        },
                    ],
                    onclick=[_create, _show_list],
                )

    def _import():
        resp = cast(
            Optional[Dict[str, Any]],
            input_group(
                label=t("Gui.AppManage.Import"),
                inputs=[
                    file_upload(
                        label=t("Gui.AppManage.Import"),
                        name="file",
                        placeholder=t("Gui.Text.ChooseFile"),
                        help_text=t("Gui.AppManage.OverrideWarning"),
                        accept=".json",
                        required=True,
                        max_size="1M",
                    ),
                    actions(
                        name="action",
                        buttons=[
                            {
                                "label": t("Gui.AppManage.Import"),
                                "value": "confirm",
                                "type": "submit",
                                "color": "primary",
                            },
                            {
                                "label": t("Gui.AppManage.Back"),
                                "type": "cancel",
                                "color": "light",
                            },
                        ],
                    ),
                ],
            ),
        )

        if resp is None:
            return

        upload = cast(Dict[str, Any], resp["file"])
        file = cast(bytes, upload["content"])
        file_name = cast(str, upload["filename"])

        if IS_ON_PHONE_CLOUD:
            config_name = mod_name = "alas"
        elif len(file_name.split(".")) == 2:
            config_name, _ = file_name.split(".")
            mod_name = "alas"
        else:
            config_name, mod_name, _ = file_name.rsplit(".", maxsplit=2)

        config = cast(Dict[str, Any], json.loads(file.decode(encoding="utf-8")))
        State.config_updater.write_file(config_name, config, mod_name)
        toast(t("Gui.AppManage.ImportSuccess"), color="success")

        gui.refresh_aside_instances(force=True)
        _show_list()

    @use_scope("content", clear=True)
    def _show_import():
        with gui.render_lock:
            gui.init_menu(name="ManageImport")
            gui.set_title(t("Gui.AppManage.Import"))
            put_scope("manage_import_panel")
            with use_scope("manage_import_panel"):
                put_warning(t("Gui.AppManage.OverrideWarning"), closable=False)
                put_button(
                    t("Gui.Text.ChooseFile"),
                    onclick=_import,
                    color="on",
                )

    @use_scope("content", clear=True)
    def _show_export_data():
        """管理菜单：导出数据（统计库 + 掉落记录，单个 zip，可选全部或单实例）。

        下载走 fetch → Blob → <a download>，与开发者工具页的日志导出同一模式：
        PyWebIO 的 download() 会把文件字节塞进 UI 的 WebSocket，几十 MB 的统计
        数据包在远控（P2P/SSH 隧道共享通道）下会撑爆积压上限被断连；走 HTTP
        路由则与主会话解耦，远控下同样可用（鉴权沿用登录与隧道口令）。
        导出范围（全部 / 单实例）只放 path（P2P 代理会剥 query string）。
        """
        with gui.render_lock:
            gui.init_menu(name="ManageExportData", skip_clear=True)
            gui.set_title(t("Gui.AppManage.ExportDataTitle"))
            put_scope("manage_export_panel")
            with use_scope("manage_export_panel"):
                put_html(
                    '<h2 class="alas-develop-section-title">'
                    f"{t('Gui.AppManage.ExportDataTitle')}</h2>"
                )
                put_markdown(
                    f"{t('Gui.AppManage.ExportDataHint')}\n\n"
                    f"{t('Gui.AppManage.ExportDataContent')}\n\n"
                    f"{t('Gui.AppManage.ExportDataFilteredHint')}\n\n"
                    f"{t('Gui.AppManage.ExportDataRemote')}"
                )
                # 导出范围下拉：服务端按当前实例列表渲染（all + 各实例）
                instance_options = "".join(
                    f'<option value="{escape(name)}">{escape(name)}</option>'
                    for name in alas_instance()
                )
                put_html(
                    '<div class="log-export-panel"><div class="log-export-row">'
                    f'<label class="log-export-instance">{escape(t("Gui.AppManage.ExportDataScopeLabel"))}'
                    '<select id="data-export-instance" class="deploy-setting-select">'
                    f'<option value="all" selected>{escape(t("Gui.AppManage.ExportDataScopeAll"))}</option>'
                    f"{instance_options}</select></label>"
                    f'<span id="data-export-size" class="deploy-setting-status">'
                    f"{escape(t('Gui.AppManage.ExportDataChecking'))}</span>"
                    '<button id="data-export-start" class="deploy-setting-button primary"'
                    f' type="button">{escape(t("Gui.AppManage.ExportDataStart"))}</button>'
                    "</div>"
                    '<div id="data-export-status" class="deploy-setting-status"></div>'
                    "</div>"
                )
                # 面板脚本不用 f-string：JS 的花括号很多，文案统一从 run_js kwargs 传入
                run_js(
                    r"""
                (function(){
                    var btn = document.getElementById('data-export-start');
                    var sizeEl = document.getElementById('data-export-size');
                    var statusEl = document.getElementById('data-export-status');
                    var sel = document.getElementById('data-export-instance');
                    if (!btn || !sizeEl || !statusEl || !sel) return;

                    // 远控入口路径形如 /<8位以上小写字母数字>/...，与服务端 WebSocket
                    // 采用同款前缀启发式（见开发者工具页日志导出的 apiCandidates）：
                    // 带前缀优先，再退回根相对，两种部署都能命中
                    function apiCandidates(path) {
                        var list = [path];
                        var parts = location.pathname.split('/').filter(Boolean);
                        var first = parts.length ? parts[0] : '';
                        if (/^[a-z0-9]{8,}$/.test(first)) list.unshift('/' + first + path);
                        return list;
                    }

                    // 逐个候选尝试，取第一个 200；全失败时返回最后一个响应，
                    // 交给调用方展示服务端返回的错误文案
                    async function fetchFirst(path) {
                        var lastResponse = null, lastError = null;
                        var urls = apiCandidates(path);
                        for (var i = 0; i < urls.length; i++) {
                            try {
                                var resp = await fetch(urls[i], {cache: 'no-store'});
                                if (resp.ok) return resp;
                                lastResponse = resp;
                            } catch (err) { lastError = err; }
                        }
                        if (lastResponse) return lastResponse;
                        throw lastError || new Error('network error');
                    }

                    function readError(resp) {
                        return resp.json().then(function (data) {
                            return data && data.error ? data.error : '';
                        }).catch(function () { return ''; });
                    }

                    function rearm() { btn.disabled = false; }

                    // 导出范围只放 path（远控代理剥 query string）；all 与实例名同形
                    function scopeUrl(suffix) {
                        return '/api/data/export/' + encodeURIComponent(sel.value) + suffix;
                    }

                    function loadInfo() {
                        fetchFirst(scopeUrl('/info')).then(function (resp) {
                            return resp.json();
                        }).then(function (result) {
                            if (result && result.success && result.data && result.data.files) {
                                sizeEl.textContent = sizeText
                                    .replace('{files}', result.data.files)
                                    .replace('{size}', result.data.human_bytes);
                                btn.disabled = false;
                            } else if (result && result.success && result.data) {
                                btn.disabled = true;
                                sizeEl.textContent = noFilesText;
                            } else {
                                sizeEl.textContent = infoFailedText;
                            }
                        }).catch(function (err) {
                            sizeEl.textContent = infoFailedText + ' (' + err.message + ')';
                        });
                    }

                    sel.addEventListener('change', loadInfo);
                    loadInfo();

                    btn.addEventListener('click', function () {
                        btn.disabled = true;
                        statusEl.textContent = packingText;
                        fetchFirst(scopeUrl('')).then(function (resp) {
                            if (!resp.ok) {
                                return readError(resp).then(function (msg) {
                                    statusEl.textContent = failedText + (msg ? msg : '(' + resp.status + ')');
                                    rearm();
                                });
                            }
                            return resp.blob().then(function (blob) {
                                // 服务端给的文件名带日期与范围；解析失败时退回固定名
                                var name = 'AzurPilot-data.zip';
                                var header = resp.headers.get('Content-Disposition');
                                if (header) {
                                    var star = header.match(/filename\*=(?:utf-8|UTF-8)''([^;]+)/);
                                    if (star) {
                                        try { name = decodeURIComponent(star[1]); }
                                        catch (e) { name = star[1]; }
                                    } else {
                                        var plain = header.match(/filename="?([^";]+)"?/);
                                        if (plain) name = plain[1];
                                    }
                                }
                                var link = document.createElement('a');
                                link.href = URL.createObjectURL(blob);
                                link.download = name;
                                document.body.appendChild(link);
                                link.click();
                                link.remove();
                                setTimeout(function () { URL.revokeObjectURL(link.href); }, 60000);
                                statusEl.textContent = startedText;
                                rearm();
                            });
                        }).catch(function (err) {
                            statusEl.textContent = failedText + err.message;
                            rearm();
                        });
                    });
                })();
                """,
                    sizeText=t("Gui.AppManage.ExportDataSize"),
                    noFilesText=t("Gui.AppManage.ExportDataNoFiles"),
                    infoFailedText=t("Gui.AppManage.ExportDataInfoFailed"),
                    packingText=t("Gui.AppManage.ExportDataPacking"),
                    startedText=t("Gui.AppManage.ExportDataStarted"),
                    failedText=t("Gui.AppManage.ExportDataFailed"),
                )

    with use_scope("menu", clear=True):
        put_button(
            t("Gui.AppManage.Name"),
            onclick=_show_list,
            color="menu",
        ).style("--menu-ManageList--")
        put_button(
            t("Gui.AppManage.New"),
            onclick=_show_new,
            color="menu",
            disabled=IS_ON_PHONE_CLOUD,
        ).style("--menu-ManageNew--")
        put_button(
            t("Gui.AppManage.Import"),
            onclick=_show_import,
            color="menu",
        ).style("--menu-ManageImport--")
        put_button(
            t("Gui.AppManage.ImportLegacy"),
            onclick=gui.ui_import_legacy,
            color="menu",
        ).style("--menu-ManageImportLegacy--")
        put_button(
            t("Gui.AppManage.ExportData"),
            onclick=_show_export_data,
            color="menu",
        ).style("--menu-ManageExportData--")

    _show_legacy_import_result()
    _show_list()
