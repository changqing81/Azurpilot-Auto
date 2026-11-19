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
import { apiGet, panelPath } from '../../api/client'
import { LegacyHtmlPanel } from './LegacyHtmlPanel'
import { apMonthCapsuleHtml, apToolbarHtml, type ApView } from './ApToolbar'
import { useStatText } from './i18n'

export function OpsiPanel({ name }: { name: string }) {
  const text = useStatText()
  const [months, setMonths] = useState<string[]>([])
  const [month, setMonth] = useState('')
  const [view, setView] = useState('line')
  const [chart, setChart] = useState<{ html: string; js: string } | null>(null)
  const [chartLoading, setChartLoading] = useState(false)
  const [chartErr, setChartErr] = useState('')
  const [meowTask, setMeowTask] = useState('')
  const [panels, setPanels] = useState<{
    html: string
    meow: string
    meowHeader: string
    meowStrip: string
    meowFilter: string
    meowDrop: string
  } | null>(null)
  const [panelsLoading, setPanelsLoading] = useState(false)
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
    setChartLoading(true)
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
      .finally(() => { if (alive) setChartLoading(false) })
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
    setPanelsLoading(true)
    apiGet<{
      success: boolean
      html: string
      meow_html: string
      meow_header_html: string
      meow_strip_html: string
      meow_filter_html: string
      meow_drop_html: string
    }>(
      // 参数全部走路径段：远控中继会剥掉 query string（见 panelPath 注释）
      panelPath(name, 'opsi', { month, task: meowTask }), 25000,
    )
      .then(r => {
        if (!alive || !r.success) return
        setPanels({
          html: r.html || '',
          meow: r.meow_html || '',
          meowHeader: r.meow_header_html || '',
          meowStrip: r.meow_strip_html || '',
          meowFilter: r.meow_filter_html || '',
          meowDrop: r.meow_drop_html || '',
        })
      })
      .catch(() => { /* 忽略 */ })
      .finally(() => { if (alive) setPanelsLoading(false) })
    return () => { alive = false }
  }, [name, month, meowTask])

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
    host.innerHTML = apToolbarHtml(view as ApView, text)
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

  const hasMeow = !!(panels && (panels.meow || panels.meowStrip || panels.meowDrop))

  // 任务筛选点击（老 UI _on_meow_task_click 的 REST 等价物）：
  // 点当前选中的再点一次 = 取消筛选回到「全部任务」（老 UI 同款 toggle）。
  const onMeowTaskClick = (ev: React.MouseEvent<HTMLDivElement>) => {
    const btn = (ev.target as HTMLElement).closest('[data-opsi-task]') as HTMLElement | null
    if (!btn) return
    ev.preventDefault()
    const next = btn.getAttribute('data-opsi-task') || ''
    setMeowTask(prev => (prev === next || next === '__all__' ? '' : next))
  }

  return (
    // #pywebio-scope-opsi_stats 是老 UI 大世界统计面板的 scope 宿主：
    // advanced-material-alas.css:1207-1223 的毛玻璃卡片挂在它上面。
    // 新 UI 之前缺这层容器，内容直接压在壁纸上（长卿 2026-11-17 图一）。
    <div id="pywebio-scope-opsi_stats">
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
            <option value="">{text('ResearchPeriodMonth')}</option>
            {months.map(m => (<option key={m} value={m}>{m}</option>))}
          </select>
        </div>
        {chartErr && <div className="st-placeholder">{chartErr}</div>}
        {/* 老 UI 的图表 DOM 挂载点（与工具栏同在 ap_chart scope 内） */}
        <div style={{ opacity: chartLoading ? 0.35 : 1, transition: 'opacity .25s' }}><div ref={hostRef} /></div>
      </div>

      {/* 净赚卡 + 12 指标卡 */}
      <div style={{ marginTop: 18 }}>
        {panels?.html && <div style={{ opacity: panelsLoading ? 0.35 : 1, transition: 'opacity .25s' }} dangerouslySetInnerHTML={{ __html: panels.html }} />}
      </div>

      {/* 大世界收获：老 UI app_stat_opsi_export.py:_render_meowofficer_farming 的四块
          —— 页头 + 合计条 strip + 侵蚀等级卡 + 掉落明细表。

          ⚠️ 两个 scope id 是硬性契约：
            #pywebio-scope-meow_strip_scope —— 合计条（1 条 CSS 规则）
            #pywebio-scope-meow_loot_scope  —— 收获内容区（47 条 CSS 规则）
          meow-loot-alas.css 把掉落明细表的 grid 布局、侵蚀卡、chip、图标、徽标
          全写成 `#pywebio-scope-meow_loot_scope .xxx` 后代选择器。少了这层容器，
          明细表退回默认流式布局 —— 一行字挤在一起看不清（长卿 2026-11-17 截图）。
          与 ft_mind / statistics-content 同源：老 UI 的 scope 容器就是 DOM 契约。 */}
      {hasMeow && (
        <details open style={{ marginTop: 12 }}>
          <summary style={{ cursor: 'pointer', fontSize: '1rem', fontWeight: 600 }}>
            <span>{text('OpsiDropTitle') || '大世界收获'}</span>
            {panels?.meowStrip && (
              <div
                id="pywebio-scope-meow_strip_scope"
                style={{ display: 'inline-block', marginLeft: '.75rem', verticalAlign: 'middle' }}
                dangerouslySetInnerHTML={{ __html: panels.meowStrip }}
              />
            )}
          </summary>
          <div id="pywebio-scope-meow_loot_scope" style={{ marginTop: 8 }}>
            {panels?.meowHeader && <div dangerouslySetInnerHTML={{ __html: panels.meowHeader }} />}
            {/* 两栏布局：老 UI put_row([左栏筛选, 右栏明细], size="168px minmax(0,1fr)")，
                PyWebIO 产出的是一个 grid div。这里用同款内联 grid 复刻 ——
                上下堆叠会让筛选器横铺一整行、与老 UI 的观感完全不同。 */}
            {/* 用 class 而不是内联 style：老 UI 的移动端媒体查询写的是
                div[style*="grid-template-columns: 168px minmax(0, 1fr)"]，
                而 React 会把 0 序列化成 0px（实测 minmax(0px, 1fr)），
                属性选择器匹配不上 → 窄屏两栏不折叠，右栏被挤到 87px，
                侵蚀卡里的指标字变成一字一行（长卿 2026-11-17 图三）。
                注意：JSX 的 children 位置写双斜杠注释会被当成纯文本
                渲染到页面上（长卿 2026-11-17 实测看到一大段注释文字）。 */}
            <div className="meow-loot-two-col">
              {/* 左栏任务筛选（老 UI _meow_filter_placeables 的纯 HTML 版） */}
              <div
                className="meow-loot-filter"
                onClick={onMeowTaskClick}
                dangerouslySetInnerHTML={{ __html: panels?.meowFilter || '' }}
              />
              {/* 右栏：侵蚀等级卡 + 掉落明细表 */}
              <div>
                {panels?.meow && <div dangerouslySetInnerHTML={{ __html: panels.meow }} />}
                {panels?.meowDrop && <div dangerouslySetInnerHTML={{ __html: panels.meowDrop }} />}
              </div>
            </div>
          </div>
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
