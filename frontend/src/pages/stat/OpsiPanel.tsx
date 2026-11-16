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
import { useEffect, useRef, useState } from 'react'
import { apiGet } from '../../api/client'
import { LegacyHtmlPanel } from './LegacyHtmlPanel'

const VIEW_OPTS = [
  { value: 'line', label: '折线' },
  { value: 'day', label: '按日' },
  { value: 'month', label: '按月' },
]

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

  return (
    <div>
      {/* 标题行 + 月份/视图切换（老 UI 的 _render_ap_chart_view_switcher 等价物） */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '.5rem', flexWrap: 'wrap', gap: '.5rem' }}>
        <div className="st-title-sub">体力变化</div>
        <div style={{ display: 'flex', gap: '.375rem', alignItems: 'center' }}>
          {/* ⚠️ 必须 flexShrink:0 + flexGrow:0：
              alas.css 的 .form-control 是 width:100%，在 flex 容器里会
              把右侧按钮挤出去、视觉上叠在一起（长卿截图：「这里还挤到一起了」）。
              宽度用 flexBasis 固定，不让它参与伸缩。 */}
          <select
            className="form-control"
            style={{ flex: '0 0 auto', width: '7rem', minWidth: '7rem' }}
            value={month}
            onChange={e => setMonth(e.target.value)}
          >
            <option value="">本月</option>
            {months.map(m => (<option key={m} value={m}>{m}</option>))}
          </select>
          {VIEW_OPTS.map(o => (
            <button
              key={o.value}
              className={'btn ' + (o.value === view ? 'btn-on' : 'btn-off')}
              onClick={() => setView(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {chartErr && <div className="st-placeholder">{chartErr}</div>}
      {/* 老 UI 的图表 DOM 挂载点 */}
      <div ref={hostRef} />

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
