// 大世界统计面板：体力变化分钟曲线（对照 golden-stat-opsi.md 与老 UI app_stat_opsi 渲染）。
// 数据源 /newapi/stat/{name}/opsi_summary 的 summary.raw.akashi_ap_entries。
import { useEffect, useRef, useState } from 'react'
import type * as EChartsType from 'echarts'
import { apiGet } from '../../api/client'

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
    Promise.all([import('echarts')]).then(([echarts]) => {
      if (cancelled || !ref.current) return
      chart = echarts.init(ref.current, 'dark')
      const points = entries.map(e => [e.ts, e.ap_total] as [string, number])
      chart.setOption({
        backgroundColor: '#1e2233',
        grid: { left: 60, right: 20, top: 20, bottom: 40 },
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'time', axisLabel: { color: '#9aa4c0', formatter: '{MM}-{dd} {HH}:{mm}' } },
        yAxis: { type: 'value', axisLabel: { color: '#9aa4c0' }, splitLine: { lineStyle: { color: '#2a2f45' } } },
        series: [
          {
            type: 'line',
            data: points,
            smooth: false,
            showSymbol: false,
            lineStyle: { color: '#e15b64', width: 1.5 },
            // 增量为正绿、负红（对照老 UI 双色线）
            visualMap: {
              show: false,
              pieces: [{ gt: 0, lt: 1e9, color: '#3aa274' }, { lte: 0, color: '#e15b64' }],
              dimension: 1,
            },
          },
        ],
      })
      const resize = () => chart?.resize()
      window.addEventListener('resize', resize)
    }).catch(() => {})
    return () => { cancelled = true; chart?.dispose() }
  }, [entries])
  return <div ref={ref} style={{ width: '100%', height: 380, borderRadius: 8 }} />
}

export function OpsiPanel({ name }: { name: string }) {
  const [s, setS] = useState<OpsiSummary | null>(null)
  const [timeline, setTimeline] = useState<ApEntry[]>([])
  // 老 UI 渲染器产出的完整 HTML（净赚卡 + 12 指标卡 + 大世界收获）。
  // 复用后端同一套 Python 构建函数，结构/样式/数字与老 UI 逐字节一致 ——
  // 比在 React 里重写几百行指标口径可靠得多。
  const [panels, setPanels] = useState<{ html: string; meow: string } | null>(null)
  useEffect(() => {
    apiGet<{ success: boolean; html: string; meow_html: string; error: string }>(
      `/newapi/stat/${name}/panels_html?panel=opsi`, 20000,
    )
      .then(r => { if (r.success) setPanels({ html: r.html, meow: r.meow_html }) })
      .catch(() => { /* 拿不到就只显示折线图 */ })
  }, [name])
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
      {/* 老 UI 同源渲染：净赚卡 + 12 指标卡 + 大世界收获。
          用 dangerouslySetInnerHTML 是因为内容来自后端 Python 模板，
          与老 UI 完全一致；CSS 由 statistics-alas.css 提供（已在 <head> 里）。 */}
      {panels?.html && (
        <div dangerouslySetInnerHTML={{ __html: panels.html }} />
      )}
      {panels?.meow && (
        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: 'pointer', fontSize: '1rem', fontWeight: 600 }}>
            大世界收获
          </summary>
          <div dangerouslySetInnerHTML={{ __html: panels.meow }} />
        </details>
      )}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, marginTop: 16 }}>
        <div className="ov-card-title">体力变化 - 分钟曲线（详细波动）</div>
        <div style={{ display: 'flex', gap: 8 }}>
          {['2026-11', '折线', '按日', '按月'].map(x => (
            <button key={x} className="log-btn">{x}</button>
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
    </div>
  )
}
