"""心智计算器页签：清单管理、统计汇总与心智扫描触发（PyWebIO 版）。

对照上游 MindCalculator.tsx 的功能集；清单存于
``MindCalculatorScan.MindCalculator.Result``（stored），计算用
``module.runtime.mind_calculator.calculate`` 实时进行，编辑即存盘
（配置事务 + revision 乐观锁，与运行中的扫描任务互斥）。
"""
import io
import time
from datetime import datetime

from module.config.transaction import config_transaction
from module.config.utils import filepath_config
from module.runtime import mind_calculator as mind_calc
from module.webui import mind_calculator_view as mind_view
from module.webui.app_dependencies import (
    BinarySwitchButton,
    State,
    actions,
    clear,
    close_popup,
    deep_get,
    deep_set,
    download,
    file_upload,
    get_config_mod,
    input_group,
    load_config,
    pin,
    pin_on_change,
    popup,
    put_buttons,
    put_error,
    put_html,
    put_input,
    put_row,
    put_scope,
    put_select,
    run_js,
    t,
    toast,
    use_scope,
)
from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked
from module.webui.utils import scan_window_active
from module.webui.widgets import add_switch_button_task

RESULT_PATH = mind_calc.RESULT_PATH
MIN_LEVEL_PATH = 'MindCalculatorScan.MindCalculator.MinLevel'
MAX_LEVEL_PATH = 'MindCalculatorScan.MindCalculator.MaxLevel'
SCAN_TASK = 'MindCalculatorScan'
# 扫描按钮「扫描中」反馈的时间窗（alas 在独立进程，当前任务名不可得）。
SCAN_WINDOW_SECONDS = 600
MAX_SHIPS = 5000
STATUS_OPTIONS = ('included', 'review', 'excluded')


class MindCalculatorTabMixin(WebUIMixinBase):
    """统计页「心智计算器」页签的会话控制器。"""

    # ---------- 状态与数据 ----------

    def _mc_state(self):
        """惰性初始化页签状态；切换实例时整体重置。"""
        if getattr(self, '_mc_instance', None) != self.alas_name or not hasattr(self, '_mc_ships'):
            self._mc_instance = self.alas_name
            self._mc_ships = None
            self._mc_revision = ''
            self._mc_result = None
            self._mc_updated_at = None
            self._mc_query = ''
            self._mc_status = 'all'
            self._mc_rarity = ''
            self._mc_min = ''
            self._mc_max = ''
            self._mc_page = 0

    def _mc_load(self, force=False):
        """读取心智清单并实时计算；会话内缓存，重载或保存后刷新。"""
        if self._mc_ships is None or force:
            data = load_config(self.alas_name).read_file(self.alas_name)
            stored = deep_get(data, RESULT_PATH, {})
            stored = stored if isinstance(stored, dict) else {}
            ships = stored.get('ships', [])
            if not isinstance(ships, list):
                ships = []
            self._mc_ships = ships
            self._mc_revision = mind_calc.revision(ships)
            self._mc_updated_at = stored.get('updated_at')
            self._mc_result = mind_calc.calculate(ships)
        return self._mc_ships

    def _mc_save(self, ships):
        """事务化保存清单；成功返回 None，失败返回错误文案。"""
        try:
            with config_transaction(filepath_config(self.alas_name)):
                config = load_config(self.alas_name)
                data = config.read_file(self.alas_name)
                stored = deep_get(data, RESULT_PATH, {})
                current = stored.get('ships', []) if isinstance(stored, dict) else []
                if mind_calc.revision(current) != self._mc_revision:
                    return t('Gui.MindCalculator.SaveConflict')
                deep_set(data, RESULT_PATH,
                         dict(ships=ships, updated_at=datetime.now().isoformat(timespec='seconds')))
                State.config_updater.write_file(self.alas_name, data, get_config_mod(self.alas_name))
        except Exception as exc:  # 事务锁超时 / 配置读写失败等，均以提示呈现。
            return str(exc)
        self._mc_ships = ships
        self._mc_revision = mind_calc.revision(ships)
        self._mc_result = mind_calc.calculate(ships)
        return None

    # ---------- 页面 ----------

    def _ft_render_mind_tab(self):
        """心智计算器：清单管理 + 统计汇总 + 心智扫描触发。"""
        self._mc_state()
        self._mc_load()
        with use_scope('ft_mind', clear=True):
            put_html(mind_view.intro_html(self._mc_result, self._mc_updated_at))
            put_scope('ft_mind_toolbar')
            put_html(mind_view.stats_html(self._mc_result))
            put_html(mind_view.summary_table_html(self._mc_result))
            put_scope('ft_mind_filters')
            put_scope('ft_mind_list')
            if not getattr(self, '_mc_callbacks_bound', False):
                pin_on_change(name='mc_query', onchange=self._mc_on_query, clear=True, serial_mode=True)
                for kind in ('status', 'rarity', 'min', 'max'):
                    pin_on_change(name=f'mc_{kind}',
                                  onchange=self._mc_filter_callback(kind),
                                  clear=True, serial_mode=True)
                self._mc_callbacks_bound = True
            self._mc_render_toolbar()
            self._mc_render_filters()
            self._mc_render_list()

    def _mc_render_toolbar(self):
        """扫描范围与扫描船坞开关（驱动 MindCalculatorScan 产出清单）。"""
        config = self.alas_config.read_file(self.alas_name)
        with use_scope('ft_mind_toolbar', clear=True):
            put_html(f'<span class="ft-toolbar-label">{t("Gui.MindCalculator.MinLevel")}</span>')
            put_input(name='mc_min_level', type='number',
                      value=str(deep_get(config, MIN_LEVEL_PATH, 95) or 95))
            put_html(f'<span class="ft-toolbar-label">{t("Gui.MindCalculator.MaxLevel")}</span>')
            put_input(name='mc_max_level', type='number',
                      value=str(deep_get(config, MAX_LEVEL_PATH, 125) or 125))
            put_scope('ft_mind_scan_button')
            put_html(f'<div class="ft-hint">{t("Gui.MindCalculator.ScanHint")}</div>')
        button = BinarySwitchButton(
            get_state=self._mc_scan_running,
            label_on=t('Gui.MindCalculator.Scanning'),
            label_off=t('Gui.MindCalculator.Scan'),
            onclick_on=self._mc_scan_running_click,
            onclick_off=self._mc_scan_start,
            color_on='off',
            color_off='on',
            scope='ft_mind_scan_button',
        )
        add_switch_button_task(self.task_handler, button)

    def _mc_render_filters(self):
        """清单操作行：搜索 + 状态 + 基础稀有度 + 等级范围 + 添加 / 导入 / 导出。

        添加舰船与导入导出按钮组与筛选控件同排（原为清单下方的独立两行，
        用户要求上移），窄屏由 flex-wrap 自然换行。
        """
        statuses, rarities = mind_view.filter_options()
        with use_scope('ft_mind_filters', clear=True):
            put_input(name='mc_query', value=self._mc_query,
                      placeholder=t('Gui.MindCalculator.Search'))
            put_select(
                name='mc_status',
                label=t('Gui.MindCalculator.Filter'),
                options=[{'label': label, 'value': value} for value, label in statuses],
                value=self._mc_status,
            )
            put_select(
                name='mc_rarity',
                label=t('Gui.MindCalculator.FilterRarity'),
                options=[{'label': label, 'value': value} for value, label in rarities],
                value=self._mc_rarity,
            )
            put_input(name='mc_min', type='number',
                      label=t('Gui.MindCalculator.FilterMin'), value=self._mc_min)
            put_input(name='mc_max', type='number',
                      label=t('Gui.MindCalculator.FilterMax'), value=self._mc_max)
            put_buttons([{'label': t('Gui.MindCalculator.Add'), 'value': 'add'}],
                        onclick=self._mc_add_popup)
            put_buttons(
                [{'label': t('Gui.MindCalculator.Import'), 'value': 'import'},
                 {'label': t('Gui.MindCalculator.Screenshots'), 'value': 'shots'},
                 {'label': t('Gui.MindCalculator.ExportJson'), 'value': 'json'},
                 {'label': t('Gui.MindCalculator.ExportCsv'), 'value': 'csv'}],
                onclick=self._mc_on_io)

    def _mc_render_list(self):
        """过滤后的清单表 + 分页 + 计数；筛选/翻页后只重绘这里。"""
        rows = mind_view.calculate_rows(self._mc_ships)[1]
        rows = mind_view.filter_rows(rows, self._mc_query, self._mc_status,
                                     self._mc_rarity, self._mc_min, self._mc_max)
        shown, page, pages = mind_view.page_slice(rows, self._mc_page)
        self._mc_page = page
        with use_scope('ft_mind_list', clear=True):
            put_html(mind_view.ships_table_html(shown, self._mc_result))
            if not shown:
                put_html(f'<p class="ft-empty">{t("Gui.MindCalculator.Empty")}</p>')
            put_row(
                [put_buttons(
                    [{'label': t('Gui.MindCalculator.Previous'), 'value': 'prev',
                      'disabled': page <= 0},
                     {'label': t('Gui.MindCalculator.Next'), 'value': 'next',
                      'disabled': page + 1 >= pages}],
                    onclick=self._mc_on_page, small=True),
                 put_html(f'<span class="ft-count">{t("Gui.MindCalculator.RowCount", count=len(rows))}</span>')],
                size='auto 1fr',
            )

    # ---------- 交互 ----------

    @render_locked
    def _mc_on_bridge(self, value):
        """清单行钩子点击（编辑 / 删除）经点击桥转发到此。"""
        kind, _, item = str(value or '').partition('|')
        try:
            index = int(item)
        except ValueError:
            return
        if not 0 <= index < len(self._mc_ships):
            return
        if kind == 'medit':
            self._mc_edit_popup(index)
        elif kind == 'mdel':
            ships = list(self._mc_ships)
            del ships[index]
            self._mc_commit(ships)

    def _mc_commit(self, ships):
        """保存并在成功后刷新页签内容。"""
        error = self._mc_save(ships)
        if error:
            toast(error, color='error')
            return False
        toast(t('Gui.MindCalculator.Saved'), color='success')
        self._mc_render_list()
        return True

    @render_locked
    def _mc_on_query(self, value):
        self._mc_query = str(value or '')
        self._mc_page = 0
        self._mc_render_list()

    def _mc_filter_callback(self, kind):
        """单参数回调：把过滤维度的新值写回状态并刷新清单。"""

        def onchange(value):
            self._mc_filter_changed(kind, value)

        return onchange

    def _mc_filter_changed(self, kind, value):
        if kind == 'status':
            self._mc_status = value if value in ('all', 'included', 'merged', 'review', 'excluded') else 'all'
        elif kind == 'rarity':
            self._mc_rarity = str(value or '')
        elif kind == 'min':
            self._mc_min = str(value or '')
        elif kind == 'max':
            self._mc_max = str(value or '')
        self._mc_page = 0
        self._mc_render_list()

    @render_locked
    def _mc_on_page(self, value):
        if value == 'prev':
            self._mc_page -= 1
        elif value == 'next':
            self._mc_page += 1
        else:
            return
        self._mc_render_list()

    # ---------- 行编辑（弹窗） ----------

    def _mc_popup_error(self, scope, message):
        """弹窗内的错误位；重绘覆盖，避免多次失败堆叠提示。"""
        with use_scope('ft_mind_edit_error', clear=True, container_scope=scope):
            put_error(message)

    @render_locked
    def _mc_edit_popup(self, index):
        """行编辑弹窗：等级与状态（计入/待核对/排除），保存即写盘。"""
        ship = self._mc_ships[index]
        title = f'{t("Gui.MindCalculator.EditTitle")} · {ship.get("name", "")}'
        # 状态是运行时按「显式排除 / 待核对 / 等级与稀有度 / 自动排除」推出来的
        # 计算字段（mind_calculator.calculate），原始记录里只有 excluded/review
        # 两个布尔位；弹窗初值必须取计算行，直接读 ship['status'] 会 KeyError
        # （2026-10-16 17:44 用户点「编辑」即报错现场）。同名合并等非可选态
        # 回落到「计入」。
        computed = dict(mind_view.calculate_rows(self._mc_ships)[1]).get(index, {})
        status = computed.get('status')
        if status not in STATUS_OPTIONS:
            status = 'included'
        with popup(title) as scope:

            def save():
                self._mc_popup_save(index, scope)

            put_input(name=f'mc_level_{index}', type='number',
                      label=t('Gui.MindCalculator.Level'),
                      value=str(ship.get('level') or 0),
                      scope=scope)
            put_select(
                name=f'mc_status_{index}',
                label=t('Gui.MindCalculator.Status'),
                options=[{'label': t('Gui.MindCalculator.Included'), 'value': 'included'},
                         {'label': t('Gui.MindCalculator.Review'), 'value': 'review'},
                         {'label': t('Gui.MindCalculator.Excluded'), 'value': 'excluded'}],
                value=status,
                scope=scope,
            )
            put_scope('ft_mind_edit_error', scope=scope)
            put_buttons([{'label': t('Gui.MindCalculator.Save'), 'value': 'save'}],
                        onclick=[lambda: self._mc_popup_save(index, scope)],
                        scope=scope)

    @render_locked
    def _mc_popup_save(self, index, scope):
        """把弹窗的等级与状态写回清单并保存。"""
        raw_level = str(pin[f'mc_level_{index}'] or '').strip()
        try:
            level = int(raw_level) if raw_level else 0
        except ValueError:
            self._mc_popup_error(scope, t('Gui.MindCalculator.InvalidRange'))
            return
        if not 0 <= level <= 125:
            self._mc_popup_error(scope, t('Gui.MindCalculator.InvalidRange'))
            return
        status = str(pin[f'mc_status_{index}'] or 'included')
        if status not in STATUS_OPTIONS:
            status = 'included'
        if status == 'included' and not 1 <= level <= 125:
            self._mc_popup_error(scope, t('Gui.MindCalculator.InvalidRange'))
            return
        ships = list(self._mc_ships)
        ships[index] = dict(ships[index], level=level,
                            excluded=status == 'excluded',
                            review=status == 'review')
        error = self._mc_save(ships)
        if error:
            self._mc_popup_error(scope, error)
            return
        close_popup()
        toast(t('Gui.MindCalculator.Saved'), color='success')
        self._mc_render_list()

    # ---------- 添加舰船 ----------

    @render_locked
    def _mc_add_popup(self, _value=None):
        """添加舰船：自由输入船名（目录归一化匹配），同名取最高等级。

        put_buttons 的回调会带上按钮的 value（'add'），这里不需要该参数。
        """
        with popup(t('Gui.MindCalculator.Add')) as scope:

            def save():
                name = str(pin['mc_add_name'] or '').strip()
                raw_level = str(pin['mc_add_level'] or '').strip()
                rarity = str(pin['mc_add_rarity'] or '')
                try:
                    level = int(raw_level) if raw_level else 0
                except ValueError:
                    self._mc_popup_error(scope, t('Gui.MindCalculator.InvalidRange'))
                    return
                if not name:
                    self._mc_popup_error(scope, t('Gui.MindCalculator.ChooseRarity'))
                    return
                info = mind_calc.find_ship(name)
                base_rarity = rarity or (info or {}).get('base_rarity', '')
                if not base_rarity:
                    self._mc_popup_error(scope, t('Gui.MindCalculator.ChooseRarity'))
                    return
                new = dict(name=info['name'] if info else name, level=level,
                           rarity=(info or {}).get('rarity', ''), base_rarity=base_rarity)
                ships = mind_calc.highest_ships(list(self._mc_ships) + [new])
                if len(ships) > MAX_SHIPS:
                    self._mc_popup_error(scope, t('Gui.MindCalculator.TooMany'))
                    return
                error = self._mc_save(ships)
                if error:
                    self._mc_popup_error(scope, error)
                    return
                close_popup()
                toast(t('Gui.MindCalculator.Saved'), color='success')
                self._mc_render_list()

            put_input(name='mc_add_name', label=t('Gui.MindCalculator.Name'), scope=scope)
            put_input(name='mc_add_level', type='number',
                      label=t('Gui.MindCalculator.Level'), scope=scope)
            put_select(
                name='mc_add_rarity',
                label=t('Gui.MindCalculator.BaseRarity'),
                options=[{'label': t('Gui.MindCalculator.AutoRarity'), 'value': ''}]
                + [{'label': mind_view.rarity_label(rarity), 'value': rarity}
                   for rarity in mind_calc.RARITIES],
                scope=scope,
            )
            put_scope('ft_mind_edit_error', scope=scope)
            put_buttons([{'label': t('Gui.MindCalculator.Save'), 'value': 'save'}],
                        onclick=[save], scope=scope)

    # ---------- 导入 / 导出 ----------

    @render_locked
    def _mc_on_io(self, value):
        """导入 / 导出入口：弹上传表单或直接下载。"""
        if value == 'import':
            self._mc_import_list()
        elif value == 'shots':
            self._mc_import_shots()
        elif value in ('json', 'csv'):
            self._mc_export(value)

    def _mc_upload_form(self, label, accept, *, multiple=False):
        """统一的文件上传表单；取消（cancel）返回 None。"""
        upload_kwargs = dict(label=label, name='files' if multiple else 'file',
                             placeholder=t('Gui.Text.ChooseFile'), accept=list(accept),
                             required=True, max_size='4M')
        if multiple:
            upload_kwargs.update(multiple=True, max_total_size='12M')
        return input_group(
            label=label,
            inputs=[
                file_upload(**upload_kwargs),
                actions(name='action', buttons=[
                    {'label': t('Gui.MindCalculator.Save'), 'value': 'confirm',
                     'type': 'submit', 'color': 'primary'},
                    {'label': t('Gui.MindCalculator.Cancel'), 'type': 'cancel', 'color': 'light'},
                ]),
            ],
        )

    def _mc_commit_import(self, added):
        """导入结果同名合并后保存；成功返回合并后的清单，失败返回 None。"""
        merged = mind_calc.highest_ships(list(self._mc_ships) + added)
        if len(merged) > MAX_SHIPS:
            toast(t('Gui.MindCalculator.TooMany'), color='error')
            return None
        error = self._mc_save(merged)
        if error:
            toast(error, color='error')
            return None
        return merged

    @render_locked
    def _mc_import_list(self):
        """导入清单文件（.json / .csv）：解析、合并、保存。"""
        resp = self._mc_upload_form(t('Gui.MindCalculator.Import'), mind_view.IMPORT_ACCEPT)
        if not resp:
            return
        upload = resp.get('file') or {}
        try:
            content = (upload.get('content') or b'').decode('utf-8', 'replace')
            ships = mind_view.parse_import(str(upload.get('filename', '')), content)
        except ValueError as exc:
            toast(f'{t("Gui.MindCalculator.ImportFailed")}：{exc}', color='error', duration=6)
            return
        merged = self._mc_commit_import(ships)
        if merged is None:
            return
        toast(t('Gui.MindCalculator.ImportComplete', count=len(ships), total=len(merged)),
              color='success')
        self._mc_render_list()

    @render_locked
    def _mc_import_shots(self):
        """导入船坞截图并逐张识别；失败逐张提示，成功结果合并保存。"""
        resp = self._mc_upload_form(t('Gui.MindCalculator.Screenshots'),
                                    mind_view.SHOT_ACCEPT, multiple=True)
        if not resp:
            return
        files = resp.get('files') or []
        if not files:
            return
        from PIL import Image
        added, failures = [], []
        for index, item in enumerate(files, start=1):
            filename = str(item.get('filename', ''))
            toast(t('Gui.MindCalculator.ImportProgress', current=index, total=len(files),
                    file=filename), color='info', duration=4)
            try:
                image = Image.open(io.BytesIO(item.get('content') or b''))
                added.extend(mind_calc.recognize(image, source=filename))
            except Exception as exc:  # 单张失败不阻塞其余截图。
                failures.append(f'{filename}: {exc}')
        if not added:
            detail = '; '.join(failures) if failures else t('Gui.MindCalculator.Empty')
            toast(f'{t("Gui.MindCalculator.ImportFailed")}：{detail}', color='error', duration=8)
            return
        if failures:
            toast('; '.join(failures), color='warn', duration=8)
        merged = self._mc_commit_import(added)
        if merged is None:
            return
        toast(t('Gui.MindCalculator.ImportComplete', count=len(added), total=len(merged)),
              color='success')
        self._mc_render_list()

    def _mc_export(self, fmt):
        """导出清单（含计算列）并触发浏览器下载。"""
        if not self._mc_result:
            return
        filename, _mime, text = mind_view.export_content(self._mc_result, fmt)
        # pywebio 的 download() 只收 bytes；CSV 的 BOM 也会随 UTF-8 编码保留。
        download(filename, text.encode('utf-8'))
        toast(t('Gui.MindCalculator.ExportReady'), color='success')

    # ---------- 扫描 ----------

    def _mc_scan_running(self):
        """扫描按钮的运行窗；语义见 FleetManagementMixin._fleet_scan_running。"""
        started = getattr(self, '_mc_scan_started_at', None)
        return scan_window_active(started, SCAN_WINDOW_SECONDS)

    def _mc_scan_running_click(self):
        toast(t('Gui.FleetManagement.ScanAlreadyRunning'), color='warn')

    def _mc_scan_start(self):
        """校验等级范围 → 写配置 → 启动心智单元船坞扫描。

        启动与舰队科技页同一套语义：worker 存活时先停（后台等待收尾）再以
        扫描任务启动。``ProcessManager.start`` 在 worker 存活时是 no-op，
        直接调用会「静默不启动还弹已启动」（2026-10-16 实测踩过）。
        """
        if self._mc_scan_running():
            toast(t('Gui.FleetManagement.ScanAlreadyRunning'), color='warn')
            return
        raw_low = str(pin['mc_min_level'] or '').strip()
        raw_high = str(pin['mc_max_level'] or '').strip()
        try:
            low, high = int(raw_low), int(raw_high)
        except ValueError:
            toast(t('Gui.MindCalculator.InvalidRange'), color='error')
            return
        if not 1 <= low <= high <= 125:
            toast(t('Gui.MindCalculator.InvalidRange'), color='error')
            return
        self._ft_write_config([(MIN_LEVEL_PATH, low), (MAX_LEVEL_PATH, high)])
        if not self._start_scan_task(SCAN_TASK):
            toast(t('Gui.FleetManagement.ScanAlreadyRunning'), color='warn')
            return
        if bool(getattr(self, 'alas', None) and self.alas.alive):
            self._mc_scan_started_at = time.monotonic()
            toast(t('Gui.MindCalculator.ScanStarted'), color='info')
        else:
            toast(t('Gui.FleetManagement.ScanStartFailed'), color='error')
