// 委托面板：老 UI 同源 HTML + **周期选项控件**。
//
// 老 UI（app_stat_commission.py）自带选月/周期切换：
//   _commission_income_period: "month"（本月）/ "week"（本周）
//   _commission_income_year / _month: 具体年月
//
// 我第一版只挂 summary/table/recent 三段 HTML，周期选项全丢。
// 现在补周期按钮（本月/本周），走 REST 的 period 参数（后端已支持）。
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

type Resp = {
  success: boolean
  summary_html?: string
  table_html?: string
  recent_html?: string
  error?: string
}

const PERIOD_OPTS = [
  { value: 'month', label: '本月' },
  { value: 'week', label: '本周' },
]

export function CommissionIncome({ name }: { name: string }) {
  const [period, setPeriod] = useState('month')
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    apiGet<Resp>(
      `/newapi/stat/${name}/panels_html?panel=commission&period=${period}`, 25000,
    )
      .then(r => { if (alive) setData(r) })
      .catch(e => { if (alive) setData({ success: false, error: String(e) }) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [name, period])

  return (
    <div>
      {/* 周期按钮：用老 UI 的 .btn-on / .btn-off 表示选中态 */}
      <div style={{ display: 'flex', gap: '.375rem', marginBottom: '.6rem' }}>
        {PERIOD_OPTS.map(o => (
          <button
            key={o.value}
            className={'btn ' + (o.value === period ? 'btn-on' : 'btn-off')}
            onClick={() => setPeriod(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
      {loading && !data && <div className="st-placeholder">加载中…</div>}
      {data && !data.success && (
        <div className="st-placeholder">{data.error || '暂无委托收入数据（实例运行后出现）'}</div>
      )}
      {data?.success && (
        <>
          {data.summary_html && <div dangerouslySetInnerHTML={{ __html: data.summary_html }} />}
          {data.table_html && <div dangerouslySetInnerHTML={{ __html: data.table_html }} />}
          {data.recent_html && <div dangerouslySetInnerHTML={{ __html: data.recent_html }} />}
        </>
      )}
    </div>
  )
}
