// 体力图视图切换工具栏（老 UI app_stat_action_point.py:821-869
// _render_ap_chart_view_switcher 的逐行同构）。
//
// 长卿：「这里太丑了 得复刻老UI的」——截图里是我自造的「下拉框 + 三个独立按钮」，
// 既没有老 UI 的工具栏排版，也没有连体分段控件的观感。
//
// 老 UI 的真实结构与样式来源：
//   #pywebio-scope-ap_chart                      ← statistics-alas.css:809 的选择器前缀
//     > div (put_row, grid auto 1fr auto auto;
//            style=align-items:center;margin-top:16px;margin-bottom:8px)
//       > <div font-weight:600;font-size:14px>体力变化 - 折线</div>
//       > 占位（put_row 的 None）
//       > 月份胶囊  = div.btn-group > button.btn.btn-secondary  「2026-11」
//       > 视图分段  = div.btn-group > button.btn.btn-primary|btn-off  折线/按日/按月
//
// **外观全部来自 .btn-group .btn 规则**（statistics-alas.css:809-842）：
//   外层 .btn-group: inline-flex + padding 3px + 1px 边框 + 圆角 10px + 浅底
//   内部 .btn:      padding 5px 12px + 边框 0 + 圆角 8px + 12px 字号
//   选中 .btn-primary: 白底 + 强调色文字 + 轻微投影
// 所以这里不能自己写外观，也不能把按钮平铺：必须是
// `div.btn-group > button.btn` 的直接父子结构，否则那套 CSS 一条都不命中。
//
// 标题是「体力变化 - <当前视图名>」，随选中项变（老 UI 既有行为）。
import { esc } from '../../lib/pywebio'

export type ApView = 'line' | 'day' | 'month'

export const AP_VIEW_LABELS: Record<ApView, string> = {
  line: '折线',
  day: '按日',
  month: '按月',
}

export const AP_VIEW_ORDER: ApView[] = ['line', 'day', 'month']

/**
 * 视图切换工具栏 HTML。
 *
 * 用字符串而非 JSX：`.btn-group` 在 Bootstrap 里自带 display 规则，
 * 必须让 button 是它的**直接子元素**且中间不能夹 React 包裹节点；
 * 字符串能保证与老 UI 逐字节同构。
 */
export function apToolbarHtml(view: ApView, text: (key: string) => string = k => AP_VIEW_LABELS[k as ApView] ?? k): string {
  const label = (k: string) => text(k) || AP_VIEW_LABELS[k as ApView] || k
  const segs = AP_VIEW_ORDER.map(v => {
    // 选中 = btn-primary，未选中 = btn-off（老 UI color 映射原样）
    const cls = v === view ? 'btn btn-primary' : 'btn btn-off'
    return `<button class="${cls}" data-ap-view="${v}">${esc(label('View' + v[0].toUpperCase() + v.slice(1) + 'Button'))}</button>`
  }).join('')
  return (
    `<div style="display: grid; grid-auto-flow: column; grid-template-columns: auto 1fr auto auto;;;align-items: center; margin-top: 16px; margin-bottom: 8px;">` +
    `<div style="font-weight: 600; font-size: 14px;">${esc(label('ApChartTitle'))} - ${esc(AP_VIEW_LABELS[view])}</div>` +
    `<div></div>` +
    // 月份：老 UI 是胶囊按钮 + PyWebIO 弹窗（PyWebIO 只能那样做）。
    // 新 UI 不受限，直接给原生 select —— 点开往下滚就能选年月
    // （长卿 2026-11-19：「明明可以点击它然后往下拖」）。
    `<div data-ap-month-group="1"></div>` +
    `<div class="btn-group" data-ap-view-group="1">${segs}</div>` +
    `</div>`
  )
}

/**
 * 月份下拉（新 UI 直接给原生 select，不用老 UI 的弹出选项框）。
 *
 * current 是当前月的 YYYY-MM 键；month 为空串表示「本月」。
 * 选项顺序：当前月在最前（选中即本月），其后是有记录的历史月份（新→旧）。
 * 宽度写死 7.2rem：form-control 默认铺满整行，之前就是它变成大框的原因。
 */
export function apMonthSelectHtml(current: string, months: string[], month: string): string {
  const rest = months.filter(m => m !== current)
  const opts = [`<option value=""${month === '' ? ' selected' : ''}>${esc(current)}</option>`]
    .concat(rest.map(m => `<option value="${esc(m)}"${month === m ? ' selected' : ''}>${esc(m)}</option>`))
    .join('')
  return `<select class="form-control form-control-sm ap-month-select" data-ap-month-select="1" style="width: 7.2rem; display: inline-block;">${opts}</select>`
}