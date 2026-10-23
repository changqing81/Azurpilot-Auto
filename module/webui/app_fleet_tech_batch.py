"""科技条目批量编辑：浏览器本地勾选，快照校验后一次事务保存。"""
import json
from html import escape

from module.webui import fleet_tech_view as view
from module.webui.app_dependencies import (
    close_popup, pin, popup, put_buttons, put_html, put_input, put_select, t, toast,
)
from module.webui.base import render_locked


def batch_bridge_script(pin_name):
    """原生复选框无需网络往返；批量操作才把本页选择发送给服务端。

    单独监听 change/click，避免旧触摸桥的 500ms 去重吞掉快速连续勾选。
    表格重绘后选择自然清空，避免筛选外的隐藏舰船被意外修改。
    """
    return """
    (function () {
        if (window.__alasFleetTechBatch) return;
        window.__alasFleetTechBatch = true;
        document.addEventListener('change', function (event) {
            var source = event.target;
            if (!(source instanceof Element) || !source.matches('[data-ft-select], [data-ft-select-page]')) return;
            var root = source.closest('#pywebio-scope-ft_table');
            if (!root) return;
            var boxes = Array.from(root.querySelectorAll('[data-ft-select]'));
            if (source.matches('[data-ft-select-page]')) {
                boxes.forEach(function (box) { box.checked = source.checked; });
            }
            var count = boxes.filter(function (box) { return box.checked; }).length;
            var head = root.querySelector('[data-ft-select-page]');
            head.checked = boxes.length > 0 && count === boxes.length;
            head.indeterminate = count > 0 && count < boxes.length;
            boxes.forEach(function (box) {
                box.closest('tr').classList.toggle('ft-selected', box.checked);
            });
            root.querySelector('[data-ft-selected-count]').textContent = count;
            root.querySelector('[data-ft-batch="selected"]').disabled = count === 0;
        });
        document.addEventListener('click', function (event) {
            if (!(event.target instanceof Element)) return;
            var button = event.target.closest('[data-ft-batch]');
            if (!button || button.disabled) return;
            var root = button.closest('#pywebio-scope-ft_table');
            var payload = button.dataset.ftBatch === 'all' ? 'all' : JSON.stringify(
                Array.from(root.querySelectorAll('[data-ft-select]:checked')).map(function (box) {
                    return Number(box.dataset.ftSelect);
                })
            );
            var bridge = document.querySelector('input[name="' + PIN_NAME + '"]');
            if (!bridge) return;
            bridge.value = 'batch|' + payload;
            bridge.dispatchEvent(new Event('input', {bubbles: true}));
            bridge.dispatchEvent(new Event('change', {bubbles: true}));
        });
    })();
    """.replace('PIN_NAME', json.dumps(pin_name))


def collect_batch_fields(values):
    """只收集显式操作；保留、未知和恢复扫描结果是三个不同意图。"""
    fields = {}
    for name in ('owned', 'full_star', 'lv120'):
        raw = values.get(name, 'keep')
        if raw not in ('keep', 'true', 'false', 'unknown'):
            raise ValueError(t('Gui.FleetTechnology.InvalidRange'))
        if raw != 'keep':
            fields[name] = {'true': True, 'false': False, 'unknown': None}[raw]
    mode = values.get('level_mode', 'keep')
    if mode == 'set':
        level = int(values.get('level') or '')
        if not 1 <= level <= 125:
            raise ValueError(t('Gui.FleetTechnology.InvalidRange'))
        fields['level'] = level
    elif mode == 'unknown':
        fields['level'] = None
    elif mode != 'keep':
        raise ValueError(t('Gui.FleetTechnology.InvalidRange'))
    return fields


class FleetTechBatchMixin:
    """复用科技页筛选条件、事务写入及汇总刷新。"""

    def _ft_batch_toolbar(self, count):
        """显示本页勾选数量及跨页范围，空结果禁用批量入口。"""
        return (
            '<div class="ft-batch-toolbar">'
            f'<span>{escape(t("Gui.FleetTechnology.Selected"))} <b data-ft-selected-count>0</b></span>'
            f'<button type="button" class="ft-edit-btn" data-ft-batch="selected" disabled>'
            f'{escape(t("Gui.FleetTechnology.BatchEdit"))}</button>'
            f'<button type="button" class="ft-edit-btn" data-ft-batch="all" {"disabled" if not count else ""}>'
            f'{escape(t("Gui.FleetTechnology.BatchAll", count=count))}</button>'
            f'<small>{escape(t("Gui.FleetTechnology.SelectionHint"))}</small></div>'
        )

    @render_locked
    def _ft_batch_open(self, payload):
        """冻结目标舰船、配置实例和记录版本；后台刷新不能扩大本次修改范围。"""
        if not self._ft_edit:
            return
        rows = view.arrange_rows(self._ft_rows, self._ft_filters, self._ft_query,
                                 self._ft_scope, self._ft_stage,
                                 self._ft_sort_key, self._ft_sort_direction)
        if payload == 'all':
            gids = tuple(row['group_type'] for row in rows)
        else:
            try:
                selected = json.loads(payload)
            except (ValueError, TypeError):
                return
            shown, _, _ = view.page_slice(rows, self._ft_page)
            allowed = {row['group_type'] for row in shown}
            if (not isinstance(selected, list) or not selected
                    or any(type(gid) is not int or gid not in allowed for gid in selected)):
                return
            gids = tuple(dict.fromkeys(selected))
        if not gids:
            return
        instance, revision = self.alas_name, self._ft_revision
        with popup(t('Gui.FleetTechnology.BatchTitle', count=len(gids))):
            put_html(f'<p>{escape(t("Gui.FleetTechnology.BatchHint"))}</p>')
            for name, label in (('owned', t('Gui.FleetTechnology.Owned')),
                                ('full_star', t('Gui.FleetTechnology.Star')), ('lv120', 'Lv.120')):
                pin[f'ft_batch_{name}'] = 'keep'
                put_select(name=f'ft_batch_{name}', label=label, value='keep', options=[
                    {'label': t('Gui.FleetTechnology.Keep'), 'value': 'keep'},
                    {'label': t('Gui.FleetTechnology.Yes'), 'value': 'true'},
                    {'label': t('Gui.FleetTechnology.No'), 'value': 'false'},
                    {'label': t('Gui.FleetTechnology.Unknown'), 'value': 'unknown'},
                ])
            pin['ft_batch_level_mode'], pin['ft_batch_level'] = 'keep', ''
            put_select(name='ft_batch_level_mode', label=t('Gui.FleetTechnology.Level'), value='keep', options=[
                {'label': t('Gui.FleetTechnology.Keep'), 'value': 'keep'},
                {'label': t('Gui.FleetTechnology.SetLevel'), 'value': 'set'},
                {'label': t('Gui.FleetTechnology.Unknown'), 'value': 'unknown'},
            ])
            put_input(name='ft_batch_level', type='number', value='',
                      placeholder=t('Gui.FleetTechnology.LevelPlaceholder'))
            put_buttons([
                {'label': t('Gui.FleetTechnology.PresetOwned'), 'value': 'owned'},
                {'label': t('Gui.FleetTechnology.PresetStar'), 'value': 'full_star'},
                {'label': t('Gui.FleetTechnology.Preset120'), 'value': 'lv120'},
            ], onclick=self._ft_batch_preset, small=True)
            put_buttons([
                {'label': t('Gui.FleetTechnology.Save'), 'value': 'save'},
                {'label': t('Gui.FleetTechnology.Restore'), 'value': 'restore'},
                {'label': t('Gui.FleetTechnology.Cancel'), 'value': 'cancel'},
            ], onclick=lambda action: self._ft_batch_save(action, gids, instance, revision))

    @render_locked
    def _ft_batch_preset(self, field):
        """快捷设置只填表单；仍由保存按钮统一提交。"""
        if field not in ('owned', 'full_star', 'lv120'):
            return
        for name in ('owned', 'full_star', 'lv120'):
            pin[f'ft_batch_{name}'] = 'true' if name in ('owned', field) else 'keep'
        pin['ft_batch_level_mode'] = 'keep'
        pin['ft_batch_level'] = ''

    @render_locked
    def _ft_batch_save(self, action, gids, instance, revision):
        """同一批次只写一次；冲突或非法组合保留表单供用户修正。"""
        if action == 'cancel':
            close_popup()
            return
        if action not in ('save', 'restore'):
            return
        if self.alas_name != instance:
            toast(t('Gui.FleetTechnology.Conflict'), color='error')
            return
        try:
            fields = {'restore': True} if action == 'restore' else collect_batch_fields({
                name: pin[f'ft_batch_{name}']
                for name in ('owned', 'full_star', 'lv120', 'level_mode', 'level')
            })
        except (ValueError, TypeError):
            toast(t('Gui.FleetTechnology.InvalidRange'), color='error')
            return
        if not fields:
            toast(t('Gui.FleetTechnology.NoChanges'), color='warn')
            return
        error = self._ft_save_changes([dict(group_type=gid, **fields) for gid in gids],
                                      expected_revision=revision)
        if error:
            toast(error, color='error')
            return
        close_popup()
        self._ft_after_save()
