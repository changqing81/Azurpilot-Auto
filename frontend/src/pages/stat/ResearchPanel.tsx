// 科研面板：老 UI 同源 HTML + **完整的三行选项控件**。
//
// 老 UI（app_stat_research.py:228-295 _output_research_controls）有三行按钮：
//   ① 口径：期数视图 / 心智 · 物资       → _research_stats_scope
//   ② 周期：本月 / 近 30 天 / 近 90 天 / 全部 → _research_stats_period
//   ③ 期数：第9期 / 第8期 / …（仅期数口径）→ _research_stats_series
//
// **按钮类必须逐字照抄老 UI**，实测其真实 DOM（22480 legacy）是：
//   <button class="btn btn-primary btn-sm">期数视图</button>
//   <button class="btn btn-secondary btn-sm">心智 / 物资</button>
// 即 small=True（→ btn-sm）+ color=primary|secondary。
//
// 我原来用的是 btn-on / btn-off —— 那是我在别处自造的选中态类，
// 在老 CSS 里既没有 btn-sm 的紧凑尺寸，也没有 Bootstrap 的
// primary/secondary 配色，结果按钮又大又空、间距散开、颜色发闷
// —— 长卿：「这里给我整理好了 我是真无语能做成这么丑」。
//
// 文案也一并按 i18n 原文对齐（Gui.Stat.ResearchScope* / ResearchPeriod* /
// ResearchSeriesOption），注意「心智 / 物资」「近 30 天」「第9期」的
// 空格与写法和老 UI 完全一致。
import { useEffect, useState } from 'react'
import { apiGet, panelPathWithParams } from '../../api/client'
import { useStatText } from './i18n'

type Resp = {
  success: boolean
  /** 进行中的科研队列（老 UI _build_running_research_html，贴在标题下方） */
  running_html?: string
  /** 指标卡（总掉落/今日/本月/记录次数）—— 老 UI 在控件之前 */
  metric_html?: string
  table_html: string
  scope?: string
  series?: number | null
  series_options?: string[]
  error?: string
}

// 文案逐字取自 i18n（Gui.Stat.ResearchScope* / ResearchPeriod*），
// 空格与写法必须与老 UI 一致：「心智 / 物资」「近 30 天」「近 90 天」。
// 文案由组件内用 useStatText 动态取（跟界面语言走），这里只留 value
const SCOPE_VALUES = [
  { value: 'series', key: 'ResearchScopeSeries' },
  { value: 'consumable', key: 'ResearchScopeConsumable' },
]
const PERIOD_VALUES = [
  { value: 'month', key: 'ResearchPeriodMonth' },
  { value: 'days30', key: 'ResearchPeriodDays30' },
  { value: 'days90', key: 'ResearchPeriodDays90' },
  { value: 'all', key: 'ResearchPeriodAll' },
]

/**
 * 胶囊分段控件（与折线图工具栏同款）。
 *
 * 长卿 2026-11-19：「科研那地方可以重构一下，不要沿用老 UI 那种…
 * 改成那种点进去就跟折线图上面 11 月那个那样子的，改成长的改成横的，不要竖的」。
 *
 * 老 UI 的科研控件是三行平铺的 btn-sm 按钮（app_stat_research.py:228-295）——
 * 那是 PyWebIO 时代的产物：每个按钮都是独立输出块，只能一行行堆。
 * 新 UI 不受限，统一改用 ap_chart 工具栏那套 .btn-group 胶囊：
 *   div.btn-group > button.btn（选中 .btn-primary / 未选 .btn-off）
 *
 * 样式契约：statistics-alas.css:837-870 的规则前缀是
 * `#pywebio-scope-statistics-content #pywebio-scope-ap_chart .btn-group`，
 * 只作用于 ap_chart。科研面板要复用同一观感，在 newui-scoped.css 里用
 * #root 前缀把这套规则平行给 research_stats（老 UI 无 #root，不受影响）。
 */
function SegGroup({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="btn-group st-seg" role="group">
      {options.map(o => (
        <button
          key={o.value}
          type="button"
          className={'btn ' + (o.value === value ? 'btn-primary' : 'btn-off')}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/**
 * 期数下拉：与折线图的月份胶囊同款（原生 select，点开往下选）。
 *
 * 老 UI 把 9 个期数平铺成一排按钮（第9期…第1期），手机上会折成三行、
 * 占掉半屏。改成下拉后一行放得下，且与月份选择器视觉统一。
 */
function SeriesSelect({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <select
      className="form-control form-control-sm st-seg-select"
      value={value}
      onChange={e => onChange(e.target.value)}
      aria-label="期数"
    >
      {options.map(o => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

export function ResearchPanel({ name }: { name: string }) {
  const text = useStatText()
  const [scope, setScope] = useState('series')
  const [period, setPeriod] = useState('days90')
  const [series, setSeries] = useState(0)
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    setLoading(true)
    // 参数同时写 query 与路径段：远控中继会剥 query，路径段是保底通道。
    // 路径段顺序必须与后端路由一致（panel/month/task/period/scope/year/yearmonth）。
    const qs = new URLSearchParams({ period, scope })
    if (series) qs.set('series', String(series))
    // 参数走 7 段路径 + query：路径段保证 period/scope 不被中继剥掉，
    // series（期数）只能走 query —— 后端第 8 段路由尚未在运行进程里加载，
    // 拼 8 段会落进 SPA 回退拿到 HTML，整块面板报废。
    // 用 _p 通用参数段：远控中继会剥掉 query，只有路径段能保住参数。
    // 这就是「点第 8 期自动跳回第 9 期」的真正原因 —— series 只走 query 时
    // 到后端永远是空，后端回退到最新一期（第 9 期）。
    apiGet<Resp>(
      panelPathWithParams(name, 'research', { period, scope, series: series || '' }),
      25000,
    )
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

  // 「第9期」——无空格，与 i18n ResearchSeriesOption "第{series}期" 一致
  // 期数文案 = ResearchSeriesOption "第{series}期"（无空格，老 UI 原文）
  const seriesOpts = (data?.series_options ?? []).map(s => ({
    value: s,
    // ⚠️ 占位符是 {series} 不是 {n}：i18n 原文 = "第{series}期"。
    // 写成 replace('{n}') 时永远匹配不到，页面上九个按钮全显示字面量「第(series)期」。
    label: (text('ResearchSeriesOption') || '第{series}期').replace('{series}', String(s)),
  }))

  // 工具栏：一行横向（长卿 2026-11-19 要求改成折线图那种胶囊 + 横排）
  //   口径胶囊 | 周期胶囊 | 期数下拉（仅期数口径时出现）
  // 手机上自动换行（flex-wrap），不会挤成一坨。
  const controls = (
    <div className="st-research-toolbar">
      <SegGroup
        options={SCOPE_VALUES.map(x => ({ value: x.value, label: text(x.key) }))}
        value={scope}
        onChange={v => { setScope(v); setSeries(0) }}
      />
      <SegGroup
        options={PERIOD_VALUES.map(x => ({ value: x.value, label: text(x.key) }))}
        value={period}
        onChange={setPeriod}
      />
      {scope === 'series' && seriesOpts.length > 0 && (
        <SeriesSelect
          options={seriesOpts}
          value={String(series || '')}
          onChange={v => setSeries(Number(v))}
        />
      )}
    </div>
  )

  // 渲染顺序**严格照抄老 UI app_stat_research.py:165-205**：
  //   ① 标题「科研」（stat page 已渲染）
  //   ② running_html   进行中的科研   ← 标题正下方（队列在跑什么比历史掉落更即时）
  //   ③ 指标卡（总/今日/本月/记录）
  //   ④ 三行选项控件
  //   ⑤ 掉落表格
  // 之前我把 ④ 控件提到了 ② 之上，与老 UI 相反 —— 长卿：
  //   「科研也是一样 反了的」。
  return (
    // #pywebio-scope-research_stats 是老 UI 科研面板的 scope 宿主：
    // advanced-material-alas.css:1207-1223 的毛玻璃卡片挂在它上面。
    <div id="pywebio-scope-research_stats">
      {data?.running_html && (
        <div
          style={{ marginBottom: '.75rem' }}
          dangerouslySetInnerHTML={{ __html: data.running_html }}
        />
      )}
      {loading && !data && <div className="st-placeholder">加载中…</div>}
      {data && !data.success && (
        <div className="st-placeholder">{data.error || '暂无科研掉落数据'}</div>
      )}
      {data?.success && (
        <>
          {/* 指标卡：老 UI 在控件之前（app_stat_research.py:178-196） */}
          {data.metric_html && (
            <div style={{ marginBottom: '.35rem' }} dangerouslySetInnerHTML={{ __html: data.metric_html }} />
          )}
          {controls}
          {data.table_html && (
            <div style={{ marginTop: '.75rem' }} dangerouslySetInnerHTML={{ __html: data.table_html }} />
          )}
        </>
      )}
    </div>
  )
}
