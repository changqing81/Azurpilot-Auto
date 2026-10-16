"""舰队科技列表页：浏览筛选、人工校正与心智扫描联动（PyWebIO 版）。

复刻上游 React 版「舰队科技列表」的布局与交互；颜色走主题变量，
表格内的排序与编辑入口通过隐藏 pin 事件桥（见 ``build_fleet_tech_bridge_script``）
转发给服务端回调。所有配置写入都包在 ``config_transaction`` 内，
与运行中的脚本（心智扫描收尾会写同一文件）互斥。
"""
import json
import time

from module.config.transaction import config_transaction
from module.config.utils import filepath_config
from module.runtime import fleet_tech as tech
from module.runtime import mind_calculator as mind_calc
from module.webui import fleet_tech_view as view
from module.webui.app_dependencies import (
    BinarySwitchButton,
    State,
    clear,
    close_popup,
    deep_get,
    deep_set,
    get_config_mod,
    load_config,
    pin,
    pin_on_change,
    popup,
    put_buttons,
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

RECORD_PATH = tech.RECORD_PATH
RESULT_PATH = 'MindCalculatorScan.MindCalculator.Result'
# 扫描范围随扫描任务走：#1230 起科技扫描是独立任务 FleetTechScan。
MIN_LEVEL_PATH = 'FleetTechScan.FleetTech.MinLevel'
MAX_LEVEL_PATH = 'FleetTechScan.FleetTech.MaxLevel'
SCAN_TASK = 'FleetTechScan'

BRIDGE_PIN = 'ft_bridge_pin'
# 扫描按钮「扫描中」反馈的时间窗（alas 在独立进程，当前任务名不可得）。
SCAN_WINDOW_SECONDS = 600
FILTER_TITLES = {'type': 'Category', 'rarity': 'Rarity', 'nation': 'Nation',
                 'bonusType': 'BonusType', 'attr': 'Attr', 'status': 'Status'}
NATION_ROW_SIZE = 4


def build_fleet_tech_bridge_script(pin_name):
    """生成把表格钩子点击（排序/编辑入口）转发给隐藏 pin 的浏览器脚本。"""
    return f"""
        (function () {{
            var listenerName = "__alasFleetTechClick";
            if (window[listenerName]) return;

            window[listenerName] = function (event) {{
                var source = event.target;
                if (!(source instanceof Element)) return;
                var hook = source.closest("[data-ft-sort],[data-ft-edit],[data-ft-tab]");
                if (!hook) return;
                event.preventDefault();

                var bridge = document.querySelector(
                    'input[name="' + {json.dumps(pin_name)} + '"]'
                );
                if (!bridge) return;

                bridge.value = hook.hasAttribute("data-ft-sort")
                    ? "sort|" + hook.getAttribute("data-ft-sort")
                    : hook.hasAttribute("data-ft-edit")
                        ? "edit|" + hook.getAttribute("data-ft-edit")
                        : "tab|" + hook.getAttribute("data-ft-tab");
                bridge.dispatchEvent(new Event("input", {{bubbles: true}}));
                bridge.dispatchEvent(new Event("change", {{bubbles: true}}));
            }};
            document.addEventListener("click", window[listenerName]);
        }})();
    """


def _tri_value(value):
    """三态值 → 下拉框选项值。"""
    return '' if value is None else 'true' if value else 'false'


def _warm_up_catalog():
    """WebUI 进程启动即后台预热科技目录：~300ms 的 JSON 解析不占用首次进页。"""
    import threading

    def run():
        try:
            tech.catalog()
        except Exception:  # 预热失败静默；进页面时正常加载并暴露真实错误。
            pass

    threading.Thread(target=run, name='fleet-tech-warmup', daemon=True).start()


_warm_up_catalog()


def _ft_pin_text(name):
    """读取 pin 控件的当前值并去除空白；缺失或空值返回空串。

    注意：PyWebIO 的 Pin 对象**没有**字典式 ``get``——``pin.get`` 会被
    ``__getattr__`` 兜底成"读取名为 get 的控件"返回 None，再调用即
    TypeError: 'NoneType' object is not callable。
    """
    value = pin[name]
    return '' if value is None else str(value).strip()


class FleetTechnologyMixin(WebUIMixinBase):
    """「舰队管理 → 舰队科技」页面的会话控制器。"""

    # ---------- 状态与数据 ----------

    def _ft_state(self):
        """惰性初始化页面状态；切换实例时整体重置。"""
        if getattr(self, '_ft_instance', None) != self.alas_name or not hasattr(self, '_ft_filters'):
            self._ft_instance = self.alas_name
            self._ft_tab = 'tech'
            self._ft_filters = {}
            self._ft_query = ''
            self._ft_scope = 'all'
            self._ft_stage = 'all'
            self._ft_edit = False
            self._ft_bonuses = False
            self._ft_extra = False
            self._ft_page = 0
            self._ft_sort_key = 'code'
            self._ft_sort_direction = 1
            self._ft_catalog = None
            self._ft_report = None
            self._ft_rows = []
            self._ft_revision = ''
            self._ft_refresh_baseline = None

    def _ft_load(self, force=False):
        """读取实例配置与离线目录；会话内缓存，重载或写入后刷新。"""
        if self._ft_catalog is None or force:
            data = load_config(self.alas_name).read_file(self.alas_name)
            record = tech.read_record(data)
            # report 自带 revision；只补实例与扫描范围，避免重复关键字。
            report = dict(instance=self.alas_name,
                          min_level=deep_get(data, MIN_LEVEL_PATH, 95) or 95,
                          max_level=deep_get(data, MAX_LEVEL_PATH, 125) or 125,
                          **tech.report(record))
            self._ft_catalog = tech.public_catalog()
            self._ft_report = report
            self._ft_revision = report['revision']
            self._ft_rows = view.combine_rows(self._ft_catalog, report)
        return self._ft_catalog, self._ft_report

    def _ft_write_config(self, changes):
        """在配置事务内应用一批 deep_set 变更。"""
        with config_transaction(filepath_config(self.alas_name)):
            config = load_config(self.alas_name)
            data = config.read_file(self.alas_name)
            for path, value in changes:
                deep_set(data, path, value)
            State.config_updater.write_file(self.alas_name, data, get_config_mod(self.alas_name))

    # ---------- 页面 ----------

    @use_scope('content', clear=True)
    def fleet_technology_page(self, task='FleetTechnology'):
        """舰队科技页签工作台：科技列表 / 舰队信息 / 心智计算器。

        菜单「舰队管理」组入口以 ``task='FleetManagement'`` 进入（标题用组名），
        其余调用保持 'FleetTechnology' 兼容。
        """
        self.init_menu(name=task)
        self.set_title(t('Menu.FleetManagement.name') if task == 'FleetManagement'
                       else t(f'Task.{task}.name'))
        self._ft_state()
        self._ft_load(force=True)

        with use_scope('ft_head', clear=True):
            put_row(
                [put_html('<span></span>'),
                 put_buttons([{'label': t('Gui.FleetTechnology.Reload'), 'value': 'reload'}],
                             onclick=self._ft_on_reload, small=True)],
                size='1fr auto',
            )
        put_scope('ft_tabs')
        put_scope('ft_tab_content')

        with use_scope('ft_bridge', clear=True):
            put_input(name=BRIDGE_PIN, value='')
        self._ft_bind_callbacks()
        run_js(build_fleet_tech_bridge_script(BRIDGE_PIN))
        # 扫描/同步等外部写入后自动刷新数字（页面切换时随 pending_delete 清理）。
        self.task_handler.add(self._ft_data_refresh, 5, True)

        self._ft_render_tabs()
        self._ft_render_tab_content()

    def _ft_render_tabs(self):
        with use_scope('ft_tabs', clear=True):
            put_html(view.tabs_html(self._ft_tab))

    def _ft_render_tab_content(self):
        """渲染当前页签；切换页签只重绘这里（筛选/页码等状态跨切换保留）。"""
        with use_scope('ft_tab_content', clear=True):
            if self._ft_tab == 'info':
                self._ft_render_info_tab()
            elif self._ft_tab == 'mind':
                self._ft_render_mind_tab()
            else:
                self._ft_render_tech_tab()

    def _ft_data_refresh(self):
        """周期任务：科技记录 / 舰队信息 / 心智清单被外部写入时自动刷新。

        扫描任务完成、页面之外的同步都会改变这些数据；比对记录 revision
        与扫描时间戳（读盘走 mtime 缓存），有变化才重绘当前页签。
        """
        try:
            data = load_config(self.alas_name).read_file(self.alas_name)
            record_revision = tech.revision(tech.read_record(data))
            fleet_record = str(deep_get(data, 'FleetInfo.FleetInfo.Record', '') or '')
            stored = deep_get(data, RESULT_PATH, {})
            ships = stored.get('ships', []) if isinstance(stored, dict) else []
            result_revision = mind_calc.revision(ships)
        except Exception:
            return
        current = (record_revision, fleet_record, result_revision)
        baseline = getattr(self, '_ft_refresh_baseline', None)
        self._ft_refresh_baseline = current
        if baseline is None or current == baseline:
            return
        self._ft_refresh_render()

    @render_locked
    def _ft_refresh_render(self):
        """外部写入后重绘当前页签；汇总数字随之更新。"""
        self._ft_load(force=True)
        if self._ft_tab == 'mind' and hasattr(self, '_mc_load'):
            self._mc_load(force=True)
        self._ft_render_tab_content()

    def _ft_render_tech_tab(self):
        """科技列表（原有主体：汇总/工具条/筛选/搜索/加成/表格）。"""
        put_scope('ft_summary')
        self._ft_render_toolbar()
        put_scope('ft_filters')
        self._ft_render_filters()
        put_scope('ft_stage')
        put_scope('ft_search')
        self._ft_render_search()
        put_scope('ft_bonuses')
        put_scope('ft_table')
        self._ft_render_summary()
        self._ft_render_stage()
        self._ft_render_bonuses()
        self._ft_render_table()

    def _ft_render_info_tab(self):
        """舰队信息：编队展示 + 舰队扫描开关（对齐上游 FleetInfoPage 的页内扫描）。"""
        with use_scope('ft_info', clear=True):
            put_scope('ft_info_scan_button')
            config = self.alas_config.read_file(self.alas_name)
            result = deep_get(config, 'FleetInfo.FleetInfo.Result', default={})
            record = deep_get(config, 'FleetInfo.FleetInfo.Record', default=None)
            if not isinstance(result, dict) or not result:
                put_html(f'<div class="ft-empty">{t("Gui.FleetManagement.NoResult")}</div>')
            else:
                put_html(self._fleet_info_html(result, record))
        button = BinarySwitchButton(
            get_state=self._fleet_scan_running,
            label_on=t('Gui.FleetManagement.Scanning'),
            label_off=t('Gui.FleetManagement.StartScan'),
            onclick_on=self._fleet_scan_running_click,
            onclick_off=self._fleet_scan_start,
            color_on='off',
            color_off='on',
            scope='ft_info_scan_button',
        )
        self.task_handler.add(button.g(), 1, True)

    def _ft_render_mind_tab(self):
        """心智计算器：第二阶段交付（runtime 计算与识别已就绪，缺清单 UI）。"""
        with use_scope('ft_mind', clear=True):
            put_html(f'<div class="ft-empty">{t("Gui.FleetTechnology.MindComingSoon")}</div>')

    def _ft_bind_callbacks(self):
        """注册一次性 pin 回调；页面重入不重复绑定。

        ``put_select`` 没有 onchange 参数，下拉/输入变更一律经 ``pin_on_change``。
        """
        if getattr(self, '_ft_callbacks_bound', False):
            return
        pin_on_change(name=BRIDGE_PIN, onchange=self._ft_on_bridge, clear=True, serial_mode=True)
        pin_on_change(name='ft_query', onchange=self._ft_on_query, clear=True, serial_mode=True)
        pin_on_change(name='ft_scope', onchange=self._ft_on_scope, clear=True, serial_mode=True)
        pin_on_change(name='ft_stage', onchange=self._ft_on_stage, clear=True, serial_mode=True)
        self._ft_callbacks_bound = True

    # ---------- 渲染 ----------

    def _ft_render_summary(self):
        with use_scope('ft_summary', clear=True):
            put_html(view.summary_html(self._ft_catalog, self._ft_report, self._ft_rows))

    def _ft_render_toolbar(self):
        """扫描参数、扫描/同步按钮与状态提示；按钮状态由 task_handler 轮询。

        元素顺序输出（不用 put_row），由 ft_toolbar 的 flex-wrap 排布，窄屏自然换行。
        """
        report = self._ft_report
        with use_scope('ft_toolbar', clear=True):
            put_html(f'<span class="ft-toolbar-label">{t("Gui.FleetTechnology.Low")}</span>')
            put_input(name='ft_min_level', type='number', value=str(report['min_level']))
            put_html(f'<span class="ft-toolbar-label">{t("Gui.FleetTechnology.High")}</span>')
            put_input(name='ft_max_level', type='number', value=str(report['max_level']))
            put_scope('ft_scan_button')
            put_buttons([{'label': t('Gui.FleetTechnology.Sync'), 'value': 'sync'}],
                        onclick=self._ft_on_sync)
            put_html(f'<div class="ft-hint">{t("Gui.FleetTechnology.Hint")}</div>')
        button = BinarySwitchButton(
            get_state=self._ft_scan_running,
            label_on=t('Gui.FleetManagement.Scanning'),
            label_off=t('Gui.FleetTechnology.Scan'),
            onclick_on=self._ft_scan_running_click,
            onclick_off=self._ft_scan_start,
            color_on='off',
            color_off='on',
            scope='ft_scan_button',
        )
        self.task_handler.add(button.g(), 1, True)

    def _ft_render_filters(self):
        """六组筛选 + 功能开关；每组独立 scope，点击只重绘受影响的组。"""
        for key in view.FILTER_GROUPS:
            self._ft_render_filter_group(key)
        self._ft_render_functions()

    def _ft_render_filter_group(self, key):
        options = view.filter_options(key, self._ft_rows)
        selected = set(self._ft_filters.get(key, []))
        buttons = [{
            'label': label,
            'value': f'{key}|{value}',
            'color': 'primary' if (value in selected or (not value and not selected)) else 'secondary',
        } for value, label in options]
        with use_scope(f'ft_filter_{key}', clear=True):
            put_row(
                [put_html(f'<div class="ft-filter-label">{t(f"Gui.FleetTechnology.{FILTER_TITLES[key]}")}</div>'),
                 put_buttons(buttons, onclick=self._ft_on_filter)],
                size='112px 1fr',
            )

    def _ft_render_functions(self):
        with use_scope('ft_filter_functions', clear=True):
            buttons = [{
                'label': t(f'Gui.FleetTechnology.{name}'),
                'value': key,
                'color': 'primary' if getattr(self, f'_ft_{key}') else 'secondary',
            } for key, name in (('edit', 'Edit'), ('bonuses', 'Bonuses'), ('extra', 'Extra'))]
            put_row(
                [put_html(f'<div class="ft-filter-label">{t("Gui.FleetTechnology.Functions")}</div>'),
                 put_buttons(buttons, onclick=self._ft_on_toggle)],
                size='112px 1fr',
            )

    def _ft_render_stage(self):
        """「显示额外筛选」打开时出现加成阶段选择。"""
        with use_scope('ft_stage', clear=True):
            if not self._ft_extra:
                return
            put_select(
                name='ft_stage',
                label=t('Gui.FleetTechnology.Stage'),
                options=[{'label': t('Gui.FleetTechnology.All'), 'value': 'all'},
                         {'label': t('Gui.FleetTechnology.Unlock'), 'value': 'get'},
                         {'label': 'Lv.120', 'value': 'level'}],
                value=self._ft_stage,
            )

    def _ft_render_search(self):
        """搜索框 + 科技条目范围 + 计数子 scope（计数可单独重绘）。"""
        put_input(name='ft_query', value=self._ft_query,
                  placeholder=t('Gui.FleetTechnology.Search'))
        put_select(
            name='ft_scope',
            label=t('Gui.FleetTechnology.Scope'),
            options=[{'label': t('Gui.FleetTechnology.All'), 'value': 'all'},
                     {'label': t('Gui.FleetTechnology.Tech'), 'value': 'tech'},
                     {'label': t('Gui.FleetTechnology.None'), 'value': 'none'}],
            value=self._ft_scope,
        )
        put_scope('ft_count')

    def _ft_render_bonuses(self):
        """总属性加成视图：各阵营科技等级录入 + 属性加成网格。"""
        with use_scope('ft_bonuses', clear=True):
            if not self._ft_bonuses:
                return
            put_html(f'<h2>{t("Gui.FleetTechnology.Bonuses")}</h2>')
            nations = [(key, levels) for key, levels in self._ft_catalog['nations'].items()]
            for start in range(0, len(nations), NATION_ROW_SIZE):
                chunk = nations[start:start + NATION_ROW_SIZE]
                put_row([self._ft_nation_select(key, levels) for key, levels in chunk])
            put_html(f'<p>{t("Gui.FleetTechnology.FactionHint")}</p>')
            put_html(f'<div class="ft-bonus-grid">{view.bonus_grid_html(self._ft_report)}</div>')

    def _ft_nation_select(self, key, levels):
        """一个阵营的科技等级选择；变更即时保存（幂等，可重复点）。"""
        current = self._ft_report['nation_levels'].get(key)
        bound = getattr(self, '_ft_nation_bound', None)
        if bound is None:
            bound = self._ft_nation_bound = set()
        if key not in bound:
            pin_on_change(name=f'ft_nation_{key}', onchange=self._ft_nation_callback(key),
                          clear=True, serial_mode=True)
            bound.add(key)
        return put_select(
            name=f'ft_nation_{key}',
            label=f'{view.nation_label(int(key))} {t("Gui.FleetTechnology.FactionLevel")}',
            options=[{'label': t('Gui.FleetTechnology.Unknown'), 'value': ''}]
            + [{'label': str(value), 'value': str(value)} for value in range(len(levels) + 1)],
            value='' if current is None else str(current),
        )

    def _ft_nation_callback(self, key):
        """单参数回调：把阵营等级变更转发给对应阵营的保存逻辑。"""

        def onchange(value):
            self._ft_on_nation(key, value)

        return onchange

    def _ft_render_table(self):
        """表格 + 分页 + 页脚；筛选/排序/翻页后只重绘这里与计数。"""
        rows = view.arrange_rows(self._ft_rows, self._ft_filters, self._ft_query,
                                 self._ft_scope, self._ft_stage,
                                 self._ft_sort_key, self._ft_sort_direction)
        shown, page, pages = view.page_slice(rows, self._ft_page)
        self._ft_page = page
        with use_scope('ft_table', clear=True):
            put_html(view.table_html(shown, self._ft_sort_key, self._ft_sort_direction, self._ft_edit))
            if not shown:
                put_html(f'<p class="ft-empty">{t("Gui.FleetTechnology.Empty")}</p>')
            put_row(
                [put_buttons(
                    [{'label': t('Gui.FleetTechnology.Prev'), 'value': 'prev', 'disabled': page <= 0},
                     {'label': t('Gui.FleetTechnology.Next'), 'value': 'next', 'disabled': page + 1 >= pages}],
                    onclick=self._ft_on_page, small=True),
                 put_html(f'<span class="ft-count">{page + 1} / {pages}</span>'),
                 put_html(f'<a href="https://wiki.biligame.com/blhx/舰队科技" target="_blank" '
                          f'rel="noreferrer">{t("Gui.FleetTechnology.Attribution")}</a>')],
                size='auto 1fr auto',
            )
        self._ft_render_count(len(rows))

    def _ft_render_count(self, count):
        with use_scope('ft_count', clear=True):
            put_html(f'<span class="ft-count">{t("Gui.FleetTechnology.RowCount", count=count)}</span>')

    # ---------- 交互 ----------

    @render_locked
    def _ft_on_bridge(self, value):
        """表格钩子点击（排序 / 编辑入口 / 页签切换）经隐藏 pin 转发到此。"""
        kind, _, item = str(value or '').partition('|')
        if kind == 'sort' and item in view.SORT_KEYS:
            if self._ft_sort_key == item:
                self._ft_sort_direction = -self._ft_sort_direction
            else:
                self._ft_sort_key, self._ft_sort_direction = item, 1
            self._ft_page = 0
            self._ft_render_table()
        elif kind == 'edit':
            try:
                self._ft_edit_popup(int(item))
            except ValueError:
                return
        elif kind == 'tab' and item in view.TABS and self._ft_tab != item:
            self._ft_tab = item
            self._ft_render_tabs()
            self._ft_render_tab_content()
        elif kind in ('medit', 'mdel'):
            # 心智计算器页签的行编辑 / 删除，由其 Mixin 承接。
            self._mc_on_bridge(value)

    @render_locked
    def _ft_on_filter(self, value):
        """筛选胶囊：组内多选切换；「全部」清空该组。"""
        key, _, item = str(value or '').partition('|')
        if key not in view.FILTER_GROUPS:
            return
        if not item:
            self._ft_filters.pop(key, None)
        else:
            current = set(self._ft_filters.get(key, []))
            if item in current:
                current.discard(item)
            else:
                current.add(item)
            if current:
                self._ft_filters[key] = sorted(current)
            else:
                self._ft_filters.pop(key, None)
        self._ft_page = 0
        self._ft_render_filter_group(key)
        self._ft_render_table()

    @render_locked
    def _ft_on_toggle(self, value):
        """功能开关：记录编辑模式 / 总属性加成 / 显示额外筛选。"""
        if value not in ('edit', 'bonuses', 'extra'):
            return
        setattr(self, f'_ft_{value}', not getattr(self, f'_ft_{value}'))
        self._ft_page = 0
        self._ft_render_functions()
        self._ft_render_stage()
        self._ft_render_bonuses()
        self._ft_render_table()

    @render_locked
    def _ft_on_page(self, value):
        if value == 'prev':
            self._ft_page -= 1
        elif value == 'next':
            self._ft_page += 1
        else:
            return
        self._ft_render_table()

    @render_locked
    def _ft_on_query(self, value):
        self._ft_query = str(value or '')
        self._ft_page = 0
        self._ft_render_table()

    @render_locked
    def _ft_on_scope(self, value):
        if value in ('all', 'tech', 'none'):
            self._ft_scope = value
            self._ft_page = 0
            self._ft_render_table()

    @render_locked
    def _ft_on_stage(self, value):
        if value in ('all', 'get', 'level'):
            self._ft_stage = value
            self._ft_page = 0
            self._ft_render_table()

    @render_locked
    def _ft_on_reload(self, value):
        self._ft_load(force=True)
        self._ft_render_summary()
        self._ft_render_table()
        self._ft_render_bonuses()

    # ---------- 人工校正 ----------

    def _ft_save_changes(self, changes, nations=None):
        """校验并事务写入；成功返回 None，失败返回错误文案。"""
        try:
            with config_transaction(filepath_config(self.alas_name)):
                config = load_config(self.alas_name)
                data = config.read_file(self.alas_name)
                record = tech.read_record(data)
                if tech.revision(record) != self._ft_revision:
                    return t('Gui.FleetTechnology.Conflict')
                updated = tech.apply_changes(record, changes, nations)
                deep_set(data, RECORD_PATH, updated)
                State.config_updater.write_file(self.alas_name, data, get_config_mod(self.alas_name))
        except tech.FleetTechChangeError as exc:
            return str(exc)
        except (ValueError, TimeoutError, OSError) as exc:
            # 配置损坏、事务锁超时或文件被占用：回显给用户，不中断页面与弹窗。
            return str(exc)
        self._ft_load(force=True)
        return None

    def _ft_collect_change(self, gid, row):
        """只提交与当前显示值不同的字段，保持「仅显式字段」语义。"""
        change = {'group_type': gid}
        for name in ('owned', 'full_star', 'lv120'):
            raw = _ft_pin_text(f'ft_{name}_{gid}')
            value = None if raw == '' else raw == 'true'
            if value is not row[name]:
                change[name] = value
        raw_level = _ft_pin_text(f'ft_level_{gid}')
        level = None
        if raw_level:
            level = int(raw_level)
            if not 1 <= level <= 125:
                raise ValueError
        if level != row['level']:
            change['level'] = level
        return change

    @render_locked
    def _ft_edit_popup(self, gid):
        """人工校正弹窗：三态 + 最高等级 + 恢复自动记录，保存即写盘。"""
        row = next((item for item in self._ft_rows if item['group_type'] == gid), None)
        if row is None:
            return
        title = f'{t("Gui.FleetTechnology.RowEdit")} · {view.display_name(row)}'
        with popup(title) as scope:
            # 控件重建前预置 pin，兜底重开弹窗时的旧草稿残留；
            # 表单初值仍以各控件的 value= 为准（双保险，两者同值）。
            pin[f'ft_owned_{gid}'] = _tri_value(row['owned'])
            pin[f'ft_full_star_{gid}'] = _tri_value(row['full_star'])
            pin[f'ft_lv120_{gid}'] = _tri_value(row['lv120'])
            pin[f'ft_level_{gid}'] = '' if row['level'] is None else str(row['level'])

            def save():
                self._ft_popup_save(gid, scope)

            def restore():
                self._ft_popup_restore(gid, scope)

            for name, label, value in (('owned', t('Gui.FleetTechnology.Owned'), row['owned']),
                                       ('full_star', t('Gui.FleetTechnology.Star'), row['full_star']),
                                       ('lv120', 'Lv.120', row['lv120'])):
                put_select(
                    name=f'ft_{name}_{gid}',
                    label=label,
                    options=[{'label': t('Gui.FleetTechnology.Unknown'), 'value': ''},
                             {'label': t('Gui.FleetTechnology.Yes'), 'value': 'true'},
                             {'label': t('Gui.FleetTechnology.No'), 'value': 'false'}],
                    value=_tri_value(value),
                    scope=scope,
                )
            put_input(
                name=f'ft_level_{gid}',
                label=t('Gui.FleetTechnology.Level'),
                value='' if row['level'] is None else str(row['level']),
                scope=scope,
            )
            put_buttons(
                [{'label': t('Gui.FleetTechnology.Save'), 'value': 'save'},
                 {'label': t('Gui.FleetTechnology.Restore'), 'value': 'restore'}],
                onclick=[save, restore],
                scope=scope,
            )

    @render_locked
    def _ft_popup_save(self, gid, scope):
        """提交弹窗草稿：只提交被改动的字段；失败时 toast 且弹窗保持打开。"""
        row = next((item for item in self._ft_rows if item['group_type'] == gid), None)
        if row is None:
            close_popup()
            return
        try:
            change = self._ft_collect_change(gid, row)
        except ValueError:
            toast(t('Gui.FleetTechnology.InvalidRange'), color='error')
            return
        if len(change) == 1:
            close_popup()
            return
        error = self._ft_save_changes([change])
        if error:
            toast(error, color='error')
            return
        close_popup()
        self._ft_after_save()

    @render_locked
    def _ft_popup_restore(self, gid, scope):
        """恢复自动记录：删除该舰船的人工校正；失败时弹窗保持打开。"""
        error = self._ft_save_changes([{'group_type': gid, 'restore': True}])
        if error:
            toast(error, color='error')
            return
        close_popup()
        self._ft_after_save()

    @render_locked
    def _ft_after_save(self):
        """写入成功后：提示 + 刷新汇总与表格。"""
        toast(t('Gui.FleetTechnology.Saved'), color='success')
        self._ft_render_summary()
        self._ft_render_table()
        self._ft_render_bonuses()

    @render_locked
    def _ft_on_nation(self, key, value):
        """阵营科技等级变更：立即保存（幂等）。"""
        raw = str(value or '').strip()
        error = self._ft_save_changes([], nations={key: None if raw == '' else int(raw)})
        if error:
            toast(error, color='error')
            return
        toast(t('Gui.FleetTechnology.Saved'), color='success')
        self._ft_render_summary()
        self._ft_render_table()
        self._ft_render_bonuses()

    # ---------- 扫描与同步 ----------

    def _ft_scan_running(self):
        """扫描按钮的运行窗；语义见 FleetManagementMixin._fleet_scan_running。"""
        alas = getattr(self, 'alas', None)
        if alas is not None and getattr(alas, '_ui_action_running', False):
            return True
        started = getattr(self, '_ft_scan_started_at', None)
        return scan_window_active(started, SCAN_WINDOW_SECONDS)

    def _ft_scan_running_click(self):
        toast(t('Gui.FleetManagement.ScanAlreadyRunning'), color='warn')

    def _ft_scan_start(self):
        """校验等级范围 → 写配置 → 启动心智单元扫描任务。"""
        if self._ft_scan_running():
            toast(t('Gui.FleetManagement.ScanAlreadyRunning'), color='warn')
            return
        raw_low = _ft_pin_text('ft_min_level')
        raw_high = _ft_pin_text('ft_max_level')
        try:
            low, high = int(raw_low), int(raw_high)
        except ValueError:
            toast(t('Gui.FleetTechnology.InvalidRange'), color='error')
            return
        if not 1 <= low <= high <= 125:
            toast(t('Gui.FleetTechnology.InvalidRange'), color='error')
            return
        try:
            self._ft_write_config([(MIN_LEVEL_PATH, low), (MAX_LEVEL_PATH, high)])
        except (TimeoutError, OSError) as exc:
            toast(f'{t("Gui.FleetManagement.ScanStartFailed")}：{exc}', color='error')
            return
        if not self._start_scan_task(SCAN_TASK):
            toast(t('Gui.FleetManagement.ScanAlreadyRunning'), color='warn')
            return
        if getattr(self, 'alas', None) and self.alas.alive:
            self._ft_scan_started_at = time.monotonic()
            toast(t('Gui.FleetManagement.ScanStarted'), color='info')
        else:
            toast(t('Gui.FleetManagement.ScanStartFailed'), color='error')

    @render_locked
    def _ft_on_sync(self, value):
        """把心智扫描结果累计进科技记录；只增不减、可重复点击。"""
        try:
            with config_transaction(filepath_config(self.alas_name)):
                config = load_config(self.alas_name)
                data = config.read_file(self.alas_name)
                record = tech.read_record(data)
                result = deep_get(data, RESULT_PATH, {})
                ships = result.get('ships', []) if isinstance(result, dict) else []
                if not ships:
                    toast(t('Gui.FleetTechnology.SyncEmpty'), color='warn')
                    return
                merged = tech.merge_mind(record, ships)
                if merged != record:
                    deep_set(data, RECORD_PATH, merged)
                    State.config_updater.write_file(self.alas_name, data, get_config_mod(self.alas_name))
        except (ValueError, TimeoutError, OSError) as exc:
            # 只兜配置/锁/文件这类现实错误；代码缺陷应冒到日志而不是变成 toast。
            toast(f'{t("Gui.FleetTechnology.SyncFailed")}：{exc}', color='error')
            return
        self._ft_load(force=True)
        self._ft_render_summary()
        self._ft_render_table()
        toast(t('Gui.FleetTechnology.SyncDone'), color='success')
