// 科研面板：老 UI 同源 HTML + **完整的三行选项控件**。
//
// 老 UI（app_stat_research.py:_output_research_controls）有三行按钮：
//   ① 口径：期数 / 消耗品              → _research_stats_scope
//   ② 周期：本月 / 30天 / 90天 / 全部   → _research_stats_period
//   ③ 期数：具体哪一期（仅期数口径）    → _research_stats_series
//
// 我第一版只挂了 table_html，三行选项全丢 —— 长卿说「我他妈选项呢，
// 为什么老 UI 有这个选项新 UI 就不行」就是这个。现在补齐。
// 按钮走 .btn.btn-on / .btn.btn-off（老 UI 的按钮类），不自造样式。
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

type Resp = {
  success: boolean
  table_html: string
  scope?: string
  series?: number | null
  series_options?: string[]
  error?: string
}

const SCOPE_OPTS = [
  { value: 'series', label: '期数' },
  { value: 'consumable', label: '消耗品' },
]
const PERIOD_OPTS = [
  { value: 'month', label: '本月' },
  { value: 'days30', label: '30 天' },
  { value: 'days90', label: '90 天' },
  { value: 'all', label: '全部' },
]

/** 按钮组：用老 UI 的 .btn-on / .btn-off 表示选中态。 */
function BtnRow({
  options,
  value,
  onChange,
  style,
}: {
  options: { value: string; label: string }[]
  value: string
  onChange: (v: string) => void
  style?: React.CSSProperties
}) {
  return (
    <div style={{ display: 'flex', gap: '.375rem', flexWrap: 'wrap', ...style }}>
      {options.map(o => (
        <button
          key={o.value}
          className={'btn ' + (o.value === value ? 'btn-on' : 'btn-off')}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function ResearchPanel({ name }: { name: string }) {
  const [scope, setScope] = useState('series')
  const [period, setPeriod] = useState('days90')
  const [series, setSeries] = useState(0)
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    const qs = new URLSearchParams({ panel: 'research', period, scope })
    if (series) qs.set('series', String(series))
    apiGet<Resp>(`/newapi/stat/${name}/panels_html?${qs}`, 25000)
      .then(r => {
        if (!alive) return
        setData(r)
        // 后端回传解析出的实际期数（0 = 自动取最新一期）
        if (r.series && r.series !== series) setSeries(r.series)
      })
      .catch(e => {
        if (alive) setData({ success: false, table_html: '', error: String(e) })
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, period, scope, series])

  const seriesOpts = (data?.series_options ?? []).map(s => ({
    value: s,
    label: '第 ' + s + ' 期',
  }))

  return (
    <div>
      <BtnRow
        options={SCOPE_OPTS}
        value={scope}
        onChange={v => { setScope(v); setSeries(0) }}
      />
      <BtnRow
        options={PERIOD_OPTS}
        value={period}
        onChange={setPeriod}
        style={{ marginTop: '.35rem' }}
      />
      {scope === 'series' && seriesOpts.length > 0 && (
        <BtnRow
          options={seriesOpts}
          value={String(series || '')}
          onChange={v => setSeries(Number(v))}
          style={{ marginTop: '.35rem' }}
        />
      )}
      <div style={{ marginTop: '.75rem' }}>
        {loading && !data && <div className="st-placeholder">加载中…</div>}
        {data && !data.success && (
          <div className="st-placeholder">{data.error || '暂无科研掉落数据'}</div>
        )}
        {data?.success && data.table_html && (
          <div dangerouslySetInnerHTML={{ __html: data.table_html }} />
        )}
      </div>
    </div>
  )
}
