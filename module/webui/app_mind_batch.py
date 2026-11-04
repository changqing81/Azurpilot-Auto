"""心智清单批量编辑：浏览器本地勾选，快照校验后一次事务保存。

与科技列表的批量编辑同构（见 app_fleet_tech_batch）：勾选状态只活在浏览器
里，只有「批量修改」动作才把本页选择发给服务端；目标行用清单里的原始
索引冻结，后台刷新不会扩大本次修改范围。客户端桥见
mind_batch_bridge_script，服务端入口是 MindCalculatorTabMixin._mc_on_bridge
分派过来的 mbatch|<payload>。
"""
import json
from html import escape

from module.webui import mind_calculator_view as mind_view
from module.webui.app_dependencies import (
    close_popup, pin, popup, put_buttons, put_html, put_input, put_select, t, toast,
)
from module.webui.base import render_locked

# 批量弹窗里可选的状态：单行编辑的三态（同名合并是计算态，不能手选）。
BATCH_STATUSES = ('included', 'review', 'excluded')
MAX_LEVEL = 125


def mind_batch_bridge_script(pin_name):
    """心智清单的勾选桥：原生复选框本地生效，批量动作才走一次网络往返。

    与科技表格的批量桥分开实现（root scope 与选择器都不同）：两段脚本都用
    document 级委托，互不干扰，共用同一个隐藏 pin，服务端按 mbatch| 前缀
    分派。单独监听 change/click，避免旧触摸桥的 500ms 去重吞掉连续勾选；
    表格每次重绘都会重建复选框，选择随之清空，筛选外的行不会被误改。
    """
    return """
    (function () {
        if (window.__alasMindBatch) return;
        window.__alasMindBatch = true;
        function host() { return document.querySelector('#pywebio-scope-ft_mind_list'); }
        document.addEventListener('change', function (event) {
            var source = event.target;
            if (!(source instanceof Element)
                    || !source.matches('[data-mc-select], [data-mc-select-page]')) return;
            var root = host();
            if (!root) return;
            var boxes = Array.prototype.slice.call(root.querySelectorAll('[data-mc-select]'));
            if (source.matches('[data-mc-select-page]')) {
                boxes.forEach(function (box) { box.checked = source.checked; });
            }
            var count = boxes.filter(function (box) { return box.checked; }).length;
            var head = root.querySelector('[data-mc-select-page]');
            if (head) {
                head.checked = boxes.length > 0 && count === boxes.length;
                head.indeterminate = count > 0 && count < boxes.length;
            }
            boxes.forEach(function (box) {
                box.closest('tr').classList.toggle('ft-selected', box.checked);
            });
            var counter = root.querySelector('[data-mc-selected-count]');
            if (counter) counter.textContent = count;
            var button = root.querySelector('[data-mc-batch="selected"]');
            if (button) button.disabled = count === 0;
        });
        document.addEventListener('click', function (event) {
            if (!(event.target instanceof Element)) return;
            var button = event.target.closest('[data-mc-batch]');
            if (!button || button.disabled) return;
            var root = host();
            if (!root) return;
            var payload = button.dataset.mcBatch === 'all' ? 'all' : JSON.stringify(
                Array.prototype.slice.call(root.querySelectorAll('[data-mc-select]:checked'))
                    .map(function (box) { return Number(box.dataset.mcSelect); })
            );
            var bridge = document.querySelector('input[name="' + PIN_NAME + '"]');
            if (!bridge) return;
            bridge.value = 'mbatch|' + payload;
            bridge.dispatchEvent(new Event('input', {bubbles: true}));
            bridge.dispatchEvent(new Event('change', {bubbles: true}));
        });
    })();
    """.replace('PIN_NAME', json.dumps(pin_name))


def collect_batch_fields(values):
    """只收集显式操作；「保持原值」与显式状态/等级是不同意图。

    Raises:
        ValueError: 状态、等级模式或等级值非法。
    """
    fields = {}
    status = values.get('status', 'keep')
    if status not in ('keep',) + BATCH_STATUSES:
        raise ValueError(t('Gui.MindCalculator.InvalidRange'))
    if status != 'keep':
        fields['status'] = status
    mode = values.get('level_mode', 'keep')
    if mode == 'set':
        try:
            level = int(str(values.get('level') or '').strip())
        except ValueError as exc:
            raise ValueError(t('Gui.MindCalculator.InvalidRange')) from exc
        if not 1 <= level <= MAX_LEVEL:
            raise ValueError(t('Gui.MindCalculator.InvalidRange'))
        fields['level'] = level
    elif mode != 'keep':
        raise ValueError(t('Gui.MindCalculator.InvalidRange'))
    return fields


def apply_batch(ships, indexes, fields):
    """把显式字段写回清单副本，返回新清单（失败即整批不生效）。

    Raises:
        ValueError: 把等级未知（0 或缺失）的舰船批量设为「计入」。
    """
    result = list(ships)
    for index in indexes:
        ship = dict(result[index])
        if 'level' in fields:
            ship['level'] = fields['level']
        if 'status' in fields:
            status = fields['status']
            if status == 'included' and not 1 <= int(ship.get('level') or 0) <= MAX_LEVEL:
                raise ValueError(t('Gui.MindCalculator.BatchLevelRequired'))
            ship['excluded'] = status == 'excluded'
            ship['review'] = status == 'review'
        result[index] = ship
    return result


class MindBatchMixin:
    """复用清单的筛选条件、事务保存与汇总刷新。"""

    def _mc_filtered_rows(self):
        """当前筛选结果里的 (原始索引, 计算行)；批量目标以此为准。"""
        rows = mind_view.calculate_rows(self._mc_ships)[1]
        return mind_view.filter_rows(rows, self._mc_query, self._mc_status, self._mc_rarity,
                                     self._mc_min, self._mc_max,
                                     getattr(self, '_mc_mind', 'all'))

    def _mc_batch_toolbar(self, count):
        """显示本页勾选数量及跨页范围，空结果禁用批量入口。"""
        return (
            '<div class="ft-batch-toolbar">'
            f'<span>{escape(t("Gui.MindCalculator.Selected"))} '
            '<b data-mc-selected-count>0</b></span>'
            '<button type="button" class="ft-edit-btn" data-mc-batch="selected" disabled>'
            f'{escape(t("Gui.MindCalculator.BatchSelected"))}</button>'
            f'<button type="button" class="ft-edit-btn" data-mc-batch="all" '
            f'{"disabled" if not count else ""}>'
            f'{escape(t("Gui.MindCalculator.BatchAll", count=count))}</button>'
            f'<small>{escape(t("Gui.MindCalculator.SelectionHint"))}</small></div>'
        )

    @render_locked
    def _mc_batch_open(self, payload):
        """冻结目标行、配置实例与清单版本；后台刷新不能扩大本次修改范围。"""
        if not getattr(self, '_mc_batch', False):
            return
        rows = self._mc_filtered_rows()
        if payload == 'all':
            indexes = [index for index, _ in rows]
        else:
            try:
                selected = json.loads(payload)
            except (ValueError, TypeError):
                return
            shown, _, _ = mind_view.page_slice(rows, self._mc_page)
            allowed = {index for index, _ in shown}
            if (not isinstance(selected, list) or not selected
                    or any(type(index) is not int or index not in allowed for index in selected)):
                return
            indexes = list(dict.fromkeys(selected))
        if not indexes:
            return
        instance, revision = self.alas_name, self._mc_revision
        with popup(t('Gui.MindCalculator.BatchTitle', count=len(indexes))):
            put_html('<p class="ft-batch-hint">'
                     + escape(t('Gui.MindCalculator.BatchHint')) + '</p>')
            pin['mc_batch_status'] = 'keep'
            put_select(
                name='mc_batch_status',
                label=t('Gui.MindCalculator.Status'),
                value='keep',
                options=[{'label': t('Gui.MindCalculator.Keep'), 'value': 'keep'}]
                + [{'label': t(f'Gui.MindCalculator.Status.{status.capitalize()}'),
                    'value': status} for status in BATCH_STATUSES],
            )
            pin['mc_batch_level_mode'], pin['mc_batch_level'] = 'keep', ''
            put_select(
                name='mc_batch_level_mode',
                label=t('Gui.MindCalculator.Level'),
                value='keep',
                options=[{'label': t('Gui.MindCalculator.Keep'), 'value': 'keep'},
                         {'label': t('Gui.MindCalculator.SetLevel'), 'value': 'set'}],
            )
            put_input(name='mc_batch_level', type='number', value='',
                      placeholder=t('Gui.MindCalculator.LevelPlaceholder'))
            put_buttons([
                {'label': t('Gui.MindCalculator.PresetIncluded'), 'value': 'included',
                 'color': 'secondary'},
                {'label': t('Gui.MindCalculator.PresetExcluded'), 'value': 'excluded',
                 'color': 'secondary'},
                {'label': t('Gui.MindCalculator.Preset120'), 'value': 'level120',
                 'color': 'secondary'},
            ], onclick=self._mc_batch_preset, small=True)
            put_buttons([
                {'label': t('Gui.MindCalculator.Cancel'), 'value': 'cancel', 'color': 'light'},
                {'label': t('Gui.MindCalculator.Save'), 'value': 'save', 'color': 'primary'},
            ], onclick=lambda action: self._mc_batch_save(action, indexes, instance, revision))

    @render_locked
    def _mc_batch_preset(self, field):
        """快捷设置只填表单；仍由保存按钮统一提交。"""
        if field not in BATCH_STATUSES + ('level120',):
            return
        pin['mc_batch_status'] = field if field in BATCH_STATUSES else 'keep'
        if field == 'level120':
            pin['mc_batch_level_mode'], pin['mc_batch_level'] = 'set', str(MAX_LEVEL)
        else:
            pin['mc_batch_level_mode'], pin['mc_batch_level'] = 'keep', ''

    @render_locked
    def _mc_batch_save(self, action, indexes, instance, revision):
        """同一批次只写一次；冲突或非法组合保留表单供用户修正。"""
        if action == 'cancel':
            close_popup()
            return
        if action != 'save':
            return
        if self.alas_name != instance or revision != self._mc_revision:
            toast(t('Gui.MindCalculator.SaveConflict'), color='error')
            return
        try:
            fields = collect_batch_fields({'status': pin['mc_batch_status'],
                                           'level_mode': pin['mc_batch_level_mode'],
                                           'level': pin['mc_batch_level']})
        except (ValueError, TypeError):
            toast(t('Gui.MindCalculator.InvalidRange'), color='error')
            return
        if not fields:
            toast(t('Gui.MindCalculator.NoChanges'), color='warn')
            return
        try:
            ships = apply_batch(self._mc_ships, indexes, fields)
        except (ValueError, TypeError, IndexError) as exc:
            toast(str(exc) or t('Gui.MindCalculator.InvalidRange'), color='error')
            return
        error = self._mc_save(ships)
        if error:
            toast(error, color='error')
            return
        close_popup()
        toast(t('Gui.MindCalculator.Saved'), color='success')
        self._mc_render_after_change()
