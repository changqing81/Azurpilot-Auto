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
import { apiGet, panelPathWithParams } from '../../api/client'
import { useStatText } from './i18n'

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
// 老 UI i18n：CommissionIncomeDay/Week/Month —— 注意老 UI 显示的是
// 「今日/本周/本月」（app_stat_commission.py:475-490 用这三个键）
const PERIOD_VALUES = [
  { value: 'day', key: 'CommissionIncomeDay' },
  { value: 'week', key: 'CommissionIncomeWeek' },
  { value: 'month', key: 'CommissionIncomeMonth' },
]

export function CommissionIncome({ name }: { name: string }) {
  const text = useStatText()
  const [period, setPeriod] = useState('month')
  // 查看年月：默认当月（0 = 让后端取当月）
  const [ym, setYm] = useState<{ y: number; m: number } | null>(null)
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  // 可选历史月份（新→旧）。与 ap_chart 的月份胶囊同源：/newapi/stat/{name}/months
  const [availableMonths, setAvailableMonths] = useState<string[]>([])
  useEffect(() => {
    apiGet<{ months: string[] }>('/newapi/stat/' + encodeURIComponent(name) + '/months', 10000)
      .then(r => { if (r.months?.length) setAvailableMonths(r.months) })
      .catch(() => { /* 只显示当前月 */ })
  }, [name])

  useEffect(() => {
    let alive = true
    setLoading(true)
    // ⚠️ period 必须传给后端，否则「今日/本周/本月」点了永远返回同一个数
    // —— 这就是长卿 2026-11-19 报的「委托年月日点不动、固定返回 650」的根因：
    // 这一行原来只有 year/month，period 从头到尾没进过 query，
    // 后端 _arg('period') 恒为空 → 永远走默认档。
    // 参数同时写 query 与路径段：远控中继会剥 query，路径段是保底通道。
    const qs = new URLSearchParams({ period })
    if (ym) {
      qs.set('year', String(ym.y))
      qs.set('month', String(ym.m))
    }
    // 用 _p 通用参数段：远控中继剥 query，period/year/month 只有走路径段才保得住。
    apiGet<Resp>(
      panelPathWithParams(name, 'commission', {
        period,
        year: ym ? String(ym.y) : '',
        yearmonth: ym ? String(ym.m) : '',
      }),
      25000,
    )
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

  // 工具栏：一行横向 —— 周期胶囊（今日/本周/本月）+ 月份胶囊（长卿 2026-11-19：
  // 「这个委托也太丑了，参考一下那个折线图上面的」）。
  //
  // 老 UI 的委托控件是两个独立的块：三个 btn 按钮一行、◀ 年月 ▶ 一行
  // （PyWebIO 时代每个是独立输出块，只能堆叠）。新 UI 统一改成折线图那套
  // .btn-group 胶囊，且合并成一行；手机上 flex-wrap 自动换行。
  //
  // 月份同样用原生 select（与 ap_chart 的月份胶囊一致），点开往下选，
  // 不再需要 ◀ ▶ 逐月点 —— 长卿之前就说过「明明可以点击它然后往下拖」。
  const monthKey = ym ? `${ym.y}-${String(ym.m).padStart(2, '0')}` : ''
  const monthOptions = availableMonths.length
    ? availableMonths
    : (monthKey ? [monthKey] : [])

  const controls = (
    <div className="st-commission-toolbar">
      <div className="btn-group st-seg" role="group">
        {PERIOD_VALUES.map(x => {
          const o = { value: x.value, label: text(x.key) }
          const disabled = o.value !== 'month' && !isCurrentMonth
          return (
            <button
              key={o.value}
              type="button"
              className={'btn ' + (o.value === period ? 'btn-primary' : 'btn-off')}
              disabled={disabled}
              title={disabled ? '历史月份下该周期无数据' : undefined}
              onClick={() => setPeriod(o.value)}
            >
              {o.label}
            </button>
          )
        })}
      </div>
      {monthOptions.length > 0 && (
        <select
          className="form-control form-control-sm st-seg-select"
          value={monthKey}
          onChange={e => {
            const [y, m] = e.target.value.split('-').map(Number)
            if (y && m) setYm({ y, m })
          }}
          aria-label="查看月份"
        >
          {[...new Set(monthOptions)].map(k => (
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
      )}
      {!isCurrentMonth && (
        <button
          type="button"
          className="btn btn-off st-back-current"
          onClick={() => {
            setYm({ y: now.getFullYear(), m: now.getMonth() + 1 })
            setPeriod('month')
          }}
        >
          {text('MeowLootBackToCurrent') || '回到本月'}
        </button>
      )}
    </div>
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
  // 把后端 summary_html 里的「委托收益统计」标题抽出来，让标题与工具栏同一行。
  // 后端结构（mind_calculator_view 风格）：
  //   <div id="commission_income_container" class="commission-income-summary">
  //     <div style="font-size:1rem;font-weight:600;...">委托收益统计</div>
  //     <div style="display:grid;...">…五张卡…</div>
  //   </div>
  // 标题那层的判别特征：font-size: 1rem + font-weight: 600。
  // 抽不到就原样整块渲染（不强依赖后端结构）。
  const { summaryTitle, summaryBody } = (() => {
    const html = data?.summary_html || ''
    const m = html.match(/<div style="font-size:\s*1rem;\s*font-weight:\s*600;[^"]*">([^<]*)<\/div>/)
    if (!m) return { summaryTitle: '', summaryBody: html }
    return { summaryTitle: m[1], summaryBody: html.replace(m[0], '') }
  })()

  return (
    // #pywebio-scope-commission_income 是老 UI 委托面板的 scope 宿主：
    // advanced-material-alas.css:1207-1223 的毛玻璃卡片（var(--alas-apple-card-bg)
    // + backdrop-filter）就是挂在这个 id 上的。新 UI 之前没有这个容器，
    // 所以整个委托面板没有玻璃底、内容直接压在壁纸上
    // （长卿 2026-11-17：图二毛玻璃没了，字看不清）。
    <div id="pywebio-scope-commission_income">
      {data?.success && data.running_html && (
        <div dangerouslySetInnerHTML={{ __html: data.running_html }} />
      )}

      {loading && !data && <div className="st-placeholder">加载中…</div>}
      {data && !data.success && (
        <div className="st-placeholder">{data.error || '暂无委托收入数据（实例运行后出现）'}</div>
      )}

      {data?.success && (
        <>
          {data.summary_html && (
            <>
              {/* 标题行与工具栏**合并成一行**（长卿 2026-11-19：
                  「这个委托就跟我第二张图这样子，参考折线图上面的」——
                  即「委托收益统计」左、「日 周 月 2026-11」右）。
                  后端 summary_html 里标题和数据卡是连在一起的
                  （mind_calculator 风格的单个 div），所以这里把标题抽出来
                  单独渲染，其余部分照原样挂载。 */}
              <div className="st-commission-head">
                <span className="st-commission-title">{summaryTitle}</span>
                {controls}
              </div>
              <div dangerouslySetInnerHTML={{ __html: summaryBody }} />
            </>
          )}
          {data.table_html && <div dangerouslySetInnerHTML={{ __html: data.table_html }} />}
          {data.gem_html && <div dangerouslySetInnerHTML={{ __html: data.gem_html }} />}
          {data.recent_html && <div dangerouslySetInnerHTML={{ __html: data.recent_html }} />}
        </>
      )}
    </div>
  )
}
