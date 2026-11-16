// 委托面板：老 UI 同源 HTML + **完整交互控件**。
//
// 老 UI（app_stat_commission.py:460-501）的控件：
//   ① 周期：每日 / 每周 / 每月      （_commission_income_period）
//      历史月份下「每日/每周」禁用（那两档只对当月有意义）
//   ② 年月翻页：◀ 2026-09 ▶        （_commission_income_year/_month）
//   ③ 钻石委托区块（app_stat_commission_gem.py）
//   ④ 最近记录分页
//
// 我上一版只放了「本月/本周」两个按钮，顺序还反了 —— 长卿：
//   「每日不翼而飞 而且还反了 顺序是每日 每周 每月」。
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

type Resp = {
  success: boolean
  running_html?: string
  summary_html?: string
  table_html?: string
  gem_html?: string
  recent_html?: string
  period?: string
  year?: number
  month?: number
  error?: string
}

/** 周期按钮 —— 顺序与文案严格照老 UI：每日 / 每周 / 每月。 */
const PERIOD_OPTS = [
  { value: 'day', label: '每日' },
  { value: 'week', label: '每周' },
  { value: 'month', label: '每月' },
]

export function CommissionIncome({ name }: { name: string }) {
  const [period, setPeriod] = useState('month')
  // 查看年月：默认当月（0 = 让后端取当月）
  const [ym, setYm] = useState<{ y: number; m: number } | null>(null)
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    const qs = new URLSearchParams({ panel: 'commission', period })
    if (ym) {
      qs.set('year', String(ym.y))
      qs.set('month', String(ym.m))
    }
    apiGet<Resp>(`/newapi/stat/${name}/panels_html?${qs}`, 25000)
      .then(r => {
        if (!alive) return
        setData(r)
        // 后端回传实际年月（首次请求后才知道当月是哪个月）
        if (!ym && r.year && r.month) setYm({ y: r.year, m: r.month })
      })
      .catch(e => { if (alive) setData({ success: false, error: String(e) }) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, period, ym?.y, ym?.m])

  // 是否当月（决定「每日/每周」是否可用，与老 UI 一致）
  const now = new Date()
  const isCurrentMonth = !ym || (ym.y === now.getFullYear() && ym.m === now.getMonth() + 1)

  const stepMonth = (delta: number) => {
    if (!ym) return
    let y = ym.y
    let m = ym.m + delta
    if (m < 1) { m = 12; y -= 1 }
    if (m > 12) { m = 1; y += 1 }
    // 不允许翻到未来
    if (y > now.getFullYear() || (y === now.getFullYear() && m > now.getMonth() + 1)) return
    setYm({ y, m })
  }

  // 周期按钮 + 年月翻页（老 UI 里它们夹在 summary 和 table 之间）
  const controls = (
    <>
      {/* 周期按钮：每日 / 每周 / 每月（顺序照老 UI）；历史月份下前两档禁用 */}
      <div style={{ display: 'flex', gap: '.375rem', marginBottom: '.5rem' }}>
        {PERIOD_OPTS.map(o => {
          const disabled = o.value !== 'month' && !isCurrentMonth
          return (
            <button
              key={o.value}
              className={'btn ' + (o.value === period ? 'btn-on' : 'btn-off')}
              disabled={disabled}
              title={disabled ? '历史月份下该周期无数据' : undefined}
              onClick={() => setPeriod(o.value)}
            >
              {o.label}
            </button>
          )
        })}
      </div>
      {/* 年月翻页：◀ 2026-11 ▶（对应老 UI _output_commission_month_switch） */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.6rem' }}>
        <button className="btn btn-off" onClick={() => stepMonth(-1)}>◀</button>
        <span style={{ fontSize: '.95rem', fontWeight: 600, minWidth: '5.5rem', textAlign: 'center' }}>
          {ym ? `${ym.y}-${String(ym.m).padStart(2, '0')}` : '—'}
        </span>
        <button className="btn btn-off" disabled={isCurrentMonth} onClick={() => stepMonth(1)}>▶</button>
        {!isCurrentMonth && (
          <button
            className="btn btn-off"
            onClick={() => {
              setYm({ y: now.getFullYear(), m: now.getMonth() + 1 })
              setPeriod('month')
            }}
          >
            回到本月
          </button>
        )}
      </div>
    </>
  )

  // 渲染顺序**严格照抄老 UI app_stat_commission.py:454-499**：
  //   ① running_html   进行中委托   ← 最上面（打开就想知道现在跑什么、何时回来）
  //   ② summary_html   收益汇总
  //   ③ 周期按钮 + 年月翻页
  //   ④ table_html     收益表
  //   ⑤ gem_html       钻石委托
  //   ⑥ recent_html    最近记录
  // 之前我把 ③ 提到最前、running 夹在 ②④ 之间，与老 UI 相反 —— 长卿：
  //   「这个位置反了 进行中的在上面」。
  return (
    <div>
      {data?.success && data.running_html && (
        <div dangerouslySetInnerHTML={{ __html: data.running_html }} />
      )}

      {loading && !data && <div className="st-placeholder">加载中…</div>}
      {data && !data.success && (
        <div className="st-placeholder">{data.error || '暂无委托收入数据（实例运行后出现）'}</div>
      )}

      {data?.success && (
        <>
          {data.summary_html && <div dangerouslySetInnerHTML={{ __html: data.summary_html }} />}
          {controls}
          {data.table_html && <div dangerouslySetInnerHTML={{ __html: data.table_html }} />}
          {data.gem_html && <div dangerouslySetInnerHTML={{ __html: data.gem_html }} />}
          {data.recent_html && <div dangerouslySetInnerHTML={{ __html: data.recent_html }} />}
        </>
      )}
    </div>
  )
}
