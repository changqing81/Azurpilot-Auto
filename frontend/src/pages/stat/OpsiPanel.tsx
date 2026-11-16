// 大世界统计面板：体力变化分钟曲线（对照 golden-stat-opsi.md 与老 UI app_stat_opsi 渲染）。
// 数据源 /newapi/stat/{name}/opsi_summary 的 summary.raw.akashi_ap_entries。
import { useEffect, useRef, useState } from 'react'
import type * as EChartsType from 'echarts'
import { apiGet } from '../../api/client'
import { LegacyHtmlPanel } from './LegacyHtmlPanel'

type ApEntry = { ts: string; ap: number; ap_total: number; yellow_coin: number; asset: number; source: string }
type OpsiSummary = {
  month?: string
  total_battles?: number
  akashi_encounters?: number
  siren_research_devices?: number
  raw?: { akashi_ap?: number; akashi_ap_entries?: ApEntry[] }
}

const fmt = (n: any) => (typeof n === 'number' ? n.toLocaleString() : String(n ?? '-'))

// 深色折线图（对照老 UI：红/绿双色分钟线）
function ApLineChart({ entries }: { entries: ApEntry[] }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let chart: EChartsType.ECharts | null = null
    let cancelled = false

    // ⚠️ 颜色必须从主题变量读，不能写死。
    // 长卿多次反馈「高级材质暗色模式下图表和文字完全看不清」「黑色变白色」——
    // 根因：我之前用 echarts.init(el, 'dark') + 硬编码 #1e2233 / #9aa4c0，
    // 完全不跟随主题。在 advanced_material / dark 主题下就是深底深字。
    // 老 UI 早就把主题色映射成 --st-* 变量（statistics-alas.css 第 0 节），
    // 这里读同一套变量即可。
    const cssVar = (name: string, fallback: string) => {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
      return v || fallback
    }
    const colorText = cssVar('--st-text', '#202124')
    const colorMuted = cssVar('--st-muted', '#6f737a')
    const colorBorder = cssVar('--st-divider', '#e7e9ed')
    const colorSurface = cssVar('--st-surface', '#ffffff')
    const colorGain = cssVar('--st-gain', '#2eaa7b')
    const colorLoss = cssVar('--st-loss', '#e15b64')

    Promise.all([import('echarts')]).then(([echarts]) => {
      if (cancelled || !ref.current) return
      // 不用 'dark' 预设：那个预设自带一套固定的深色配色，会盖掉我们读的主题色
      chart = echarts.init(ref.current)
      const points = entries.map(e => [e.ts, e.ap_total] as [string, number])
      chart.setOption({
        backgroundColor: colorSurface,
        grid: { left: 60, right: 20, top: 20, bottom: 40 },
        tooltip: {
          trigger: 'axis',
          backgroundColor: colorSurface,
          borderColor: colorBorder,
          textStyle: { color: colorText },
        },
        xAxis: {
          type: 'time',
          axisLabel: { color: colorMuted, formatter: '{MM}-{dd} {HH}:{mm}' },
          axisLine: { lineStyle: { color: colorBorder } },
        },
        yAxis: {
          type: 'value',
          axisLabel: { color: colorMuted },
          splitLine: { lineStyle: { color: colorBorder } },
          axisLine: { lineStyle: { color: colorBorder } },
        },
        series: [
          {
            type: 'line',
            data: points,
            smooth: false,
            showSymbol: false,
            lineStyle: { color: colorLoss, width: 1.5 },
            visualMap: {
              show: false,
              pieces: [
                { gt: 0, lt: 1e9, color: colorGain },
                { lte: 0, color: colorLoss },
              ],
              dimension: 1,
            },
          },
        ],
      })
      const resize = () => chart?.resize()
      window.addEventListener('resize', resize)
      // 主题切换时重画（老 UI 换主题也是整页重载，这里做增量更友好）
      const onTheme = () => {
        chart?.dispose()
        chart = null
      }
      window.addEventListener('alas-theme-change', onTheme)
    }).catch(() => {})
    return () => { cancelled = true; chart?.dispose() }
  }, [entries])
  return <div ref={ref} style={{ width: '100%', height: 380, borderRadius: 8 }} />
}

export function OpsiPanel({ name }: { name: string }) {
  // 月份切换：老 UI 用弹窗选月（_open_stat_month_picker），这里用下拉框。
  // 可选月份来自 /newapi/stat/{name}/months（cl1_data.db ∪ azurstats_local.db ∪ 本月）。
  const [months, setMonths] = useState<string[]>([])
  const [month, setMonth] = useState<string>('')  // '' = 本月
  const [s, setS] = useState<OpsiSummary | null>(null)
  const [timeline, setTimeline] = useState<ApEntry[]>([])
  // 老 UI 渲染器产出的完整 HTML（净赚卡 + 12 指标卡 + 大世界收获）。
  // 复用后端同一套 Python 构建函数，结构/样式/数字与老 UI 逐字节一致 ——
  // 比在 React 里重写几百行指标口径可靠得多。
  const [panels, setPanels] = useState<{ html: string; meow: string } | null>(null)
  useEffect(() => {
    apiGet<{ months: string[]; current: string }>(`/newapi/stat/${name}/months`, 10000)
      .then(r => { if (r.months?.length) setMonths(r.months) })
      .catch(() => { /* 读不到月份就只显示本月 */ })
  }, [name])

  useEffect(() => {
    let alive = true
    setPanels(null)
    apiGet<{ success: boolean; html: string; meow_html: string; error: string }>(
      `/newapi/stat/${name}/panels_html?panel=opsi` + (month ? `&month=${month}` : ''), 20000,
    )
      .then(r => { if (alive && r.success) setPanels({ html: r.html, meow: r.meow_html }) })
      .catch(() => { /* 拿不到就只显示折线图 */ })
    return () => { alive = false }
  }, [name, month])
  useEffect(() => {
    Promise.all([
      apiGet<{ summary: OpsiSummary }>(`/newapi/stat/${name}/opsi_summary`),
      apiGet<any>(`/api/ap_timeline?instance=${name}`),
    ]).then(([r, tl]) => {
      setS(r.summary)
      setTimeline(tl?.data ?? [])
    }).catch(() => {})
  }, [name])
  if (!s) return <div className="st-placeholder">加载中…</div>
  const entries = timeline as ApEntry[]
  // 区间指标：最高/最低/平均/变化
  const values = entries.map(e => e.ap_total)
  const current = values.length ? values[values.length - 1] : 0
  const delta = current - (values.length ? values[0] : 0)
  const max = Math.max(...(values.length ? values : [0]))
  const min = Math.min(...(values.length ? values : [0]))
  const avg = values.length ? Math.round(values.reduce((a, b) => a + b, 0) / values.length) : 0
  return (
    <div>
      {/* ⚠️ 顺序必须与老 UI _STAT_PANEL_RENDERERS["opsi"] 一致：
            "_render_ap_chart"    ← 体力图在**最上面**
            "_render_opsi_stats"  ← 净赚卡 + 12 指标卡
            "_render_ship_exp"    ← 舰船经验
          我原来把体力图放在最下面（净赚卡→卡片→折线图），与老 UI 完全相反，
          长卿截图里说「你把 k 线图移到最底下去了」就是这个。

          另外「大世界收获」在 _render_opsi_stats 内部、12 张卡之后，
          所以它要排在卡片后面、舰船经验前面。 */}

      {/* ① 体力变化图（顶部） */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div className="st-title-sub">体力变化 - 分钟曲线（详细波动）</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            className="form-control"
            style={{ width: 'auto', minWidth: '7rem' }}
            value={month}
            onChange={e => setMonth(e.target.value)}
          >
            <option value="">本月</option>
            {months.map(m => (<option key={m} value={m}>{m}</option>))}
          </select>
          {['折线', '按日', '按月'].map(x => (
            <button key={x} className="btn btn-off" disabled title="视图切换暂未迁移">{x}</button>
          ))}
        </div>
      </div>
      <div className="delta-summary" style={{ marginBottom: 12 }}>
        {[
          { label: '当前行动力', value: fmt(current), color: '' },
          { label: '区间变化', value: (delta >= 0 ? '+' : '') + fmt(delta), color: delta >= 0 ? '#2eaa7b' : '#e15b64' },
          { label: '区间最高', value: fmt(max), color: '' },
          { label: '区间最低', value: fmt(min), color: '#2eaa7b' },
          { label: '区间平均', value: fmt(avg), color: '' },
        ].map(c => (
          <div key={c.label} className="dashboard-entry">
            <div>
              <div className="dashboard-value-row">
                <span className="dashboard-value" style={c.color ? { color: c.color } : undefined}>{c.value}</span>
              </div>
              <div className="dashboard-help">{c.label}</div>
            </div>
          </div>
        ))}
      </div>
      <ApLineChart entries={entries} />

      {/* ② 净赚卡 + 12 指标卡（老 UI 同源渲染） */}
      <div style={{ marginTop: 18 }}>
        <div className="st-title-sub">大世界</div>
        {panels?.html && <div dangerouslySetInnerHTML={{ __html: panels.html }} />}
      </div>

      {/* ③ 大世界收获（折叠加） */}
      {panels?.meow && (
        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: 'pointer', fontSize: '1rem', fontWeight: 600 }}>
            大世界收获
          </summary>
          <div dangerouslySetInnerHTML={{ __html: panels.meow }} />
        </details>
      )}

      {/* ④ 舰船经验进度（老 UI 第三个 renderer） */}
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
