// 大世界统计面板：**完全复用老 UI 的图表实现**。
//
// 长卿 2026-11-16：「你那个 K 线图是屎啊，老 UI 做的那么漂亮，你新 UI 做的
// 跟屎一样，只有数字没有什么渲染」。
//
// 根因：老 UI 的体力图不是 echarts，而是 webapp/ap_chart.js —— 936 行
// Canvas 2D 手绘实现，支持 K 线/折线双模式、体力/紫币/黄币/资产/海里数
// 五条序列、可点击图例、自定义 tooltip、hover 十字线。
// 我第一版用 echarts 拼了 30 行折线，粗糙到没法看。
//
// 现在的做法：后端 GET /newapi/stat/{name}/ap_chart 返回
//   { html: ap_chart_panel.html 填好数据, js: ap_chart.js 填好数据 }
// 前端挂载 HTML 后把 js 当 <script> 执行 —— 就是老 UI 那张图。
// 其余区块（净赚卡/12指标卡/大世界收获/舰船经验）继续用老 UI 的渲染器。
import { useEffect, useMemo, useRef, useState } from 'react'
import { apiGet } from '../../api/client'
import { LegacyHtmlPanel } from './LegacyHtmlPanel'
import { apMonthCapsuleHtml, apToolbarHtml, type ApView } from './ApToolbar'

export function OpsiPanel({ name }: { name: string }) {
  const [months, setMonths] = useState<string[]>([])
  const [month, setMonth] = useState('')
  const [view, setView] = useState('line')
  const [chart, setChart] = useState<{ html: string; js: string } | null>(null)
  const [chartErr, setChartErr] = useState('')
  const [panels, setPanels] = useState<{ html: string; meow: string } | null>(null)
  const hostRef = useRef<HTMLDivElement>(null)

  // 可选月份
  useEffect(() => {
    apiGet<{ months: string[] }>(`/newapi/stat/${name}/months`, 10000)
      .then(r => { if (r.months?.length) setMonths(r.months) })
      .catch(() => { /* 只显示本月 */ })
  }, [name])

  // 体力图（老 UI 的 ap_chart）
  useEffect(() => {
    let alive = true
    setChart(null)
    setChartErr('')
    const qs = new URLSearchParams({ view })
    if (month) qs.set('month', month)
    apiGet<{ success: boolean; html: string; js: string; error: string }>(
      `/newapi/stat/${name}/ap_chart?${qs}`, 25000,
    )
      .then(r => {
        if (!alive) return
        if (!r.success) { setChartErr(r.error || '暂无体力数据'); return }
        if (!r.html) { setChartErr('暂无体力数据'); return }
        setChart(r)
      })
      .catch(e => { if (alive) setChartErr(String(e)) })
    return () => { alive = false }
  }, [name, view, month])

  // 挂载图表：innerHTML + 执行老 UI 的脚本。
  // 脚本里用 document.getElementById(chartId) 找画布，所以必须先插 DOM 再执行。
  useEffect(() => {
    const host = hostRef.current
    if (!host || !chart) return
    host.innerHTML = chart.html
    let script: HTMLScriptElement | null = null
    if (chart.js) {
      script = document.createElement('script')
      script.textContent = chart.js
      document.body.appendChild(script)
    }
    return () => {
      // 老 UI 的脚本自己注册了 window.__apChartCleanups[chartId]，
      // 卸载时调用它释放事件监听
      try {
        const w = window as any
        const cleanups = w.__apChartCleanups
        if (cleanups) Object.values(cleanups).forEach((fn: any) => typeof fn === 'function' && fn())
      } catch { /* 忽略 */ }
      script?.remove()
      host.innerHTML = ''
    }
  }, [chart])

  // 净赚卡 + 12 指标卡 + 大世界收获（老 UI 渲染器）
  useEffect(() => {
    let alive = true
    setPanels(null)
    apiGet<{ success: boolean; html: string; meow_html: string }>(
      `/newapi/stat/${name}/panels_html?panel=opsi` + (month ? `&month=${month}` : ''), 25000,
    )
      .then(r => { if (alive && r.success) setPanels({ html: r.html, meow: r.meow_html }) })
      .catch(() => { /* 忽略 */ })
    return () => { alive = false }
  }, [name, month])

  // 月份胶囊文案：老 UI 显示 "YYYY-MM" 键，本月显示当前月键
  const monthLabel = useMemo(() => {
    if (month) return month
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  }, [month])

  // 工具栏与图表必须同处 #pywebio-scope-ap_chart 内 —— statistics-alas.css:809
  // 的分段控件规则前缀就是 #pywebio-scope-statistics-content #pywebio-scope-ap_chart，
  // 少了这层包裹，.btn-group 一条样式都不命中（这正是之前「太丑」的原因）。
  const toolbarRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const host = toolbarRef.current
    if (!host) return
    host.innerHTML = apToolbarHtml(view as ApView)
    // 视图分段：点按钮切 view（与老 UI _switch_ap_chart_view 同语义）
    const viewGroup = host.querySelector('[data-ap-view-group]') as HTMLElement | null
    if (viewGroup) {
      viewGroup.onclick = (ev) => {
        const btn = (ev.target as HTMLElement).closest('button') as HTMLButtonElement | null
        const v = btn?.dataset.apView as ApView | undefined
        if (v && v !== view) setView(v)
      }
    }
    // 月份胶囊：老 UI 点开共用月份选择器；这里用原生 select 弹层，
    // 但**外观仍是胶囊**（button.btn.btn-secondary 包在 .btn-group 里）。
    const monthGroup = host.querySelector('[data-ap-month-group]') as HTMLElement | null
    if (monthGroup) {
      monthGroup.innerHTML = apMonthCapsuleHtml(monthLabel)
      const btn = monthGroup.querySelector('button')
      if (btn) {
        btn.onclick = () => {
          const picker = document.getElementById('alas-ap-month-picker') as HTMLInputElement | null
          if (picker) picker.style.display = ''
          const sel = document.getElementById('alas-ap-month-select') as HTMLSelectElement | null
          sel?.focus()
          sel?.click()
        }
      }
    }
  }, [view, monthLabel])

  return (
    <div>
      {/* 图表标题行 + 月份/视图切换（老 UI _render_ap_chart_view_switcher 同构） */}
      <div id="pywebio-scope-ap_chart">
        <div ref={toolbarRef} />
        {/* 月份选择：隐藏的原生控件，由胶囊按钮触发（老 UI 是弹窗，等价交互） */}
        <div id="alas-ap-month-picker" style={{ display: 'none' }}>
          <select
            id="alas-ap-month-select"
            className="form-control"
            value={month}
            onChange={e => setMonth(e.target.value)}
          >
            <option value="">本月</option>
            {months.map(m => (<option key={m} value={m}>{m}</option>))}
          </select>
        </div>
        {chartErr && <div className="st-placeholder">{chartErr}</div>}
        {/* 老 UI 的图表 DOM 挂载点（与工具栏同在 ap_chart scope 内） */}
        <div ref={hostRef} />
      </div>

      {/* 净赚卡 + 12 指标卡 */}
      <div style={{ marginTop: 18 }}>
        <div className="st-title-sub">大世界</div>
        {panels?.html && <div dangerouslySetInnerHTML={{ __html: panels.html }} />}
      </div>

      {/* 大世界收获 */}
      {panels?.meow && (
        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: 'pointer', fontSize: '1rem', fontWeight: 600 }}>
            大世界收获
          </summary>
          <div dangerouslySetInnerHTML={{ __html: panels.meow }} />
        </details>
      )}

      {/* 舰船经验进度 */}
      <div style={{ marginTop: 12 }}>
        <LegacyHtmlPanel
          name={name}
          panel="ship_exp"
          fields={['html']}
          errorText="暂无舰船经验数据"
        />
      </div>
    </div>
  )
}
