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
import { apiGet } from '../../api/client'

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
const SCOPE_OPTS = [
  { value: 'series', label: '期数视图' },
  { value: 'consumable', label: '心智 / 物资' },
]
const PERIOD_OPTS = [
  { value: 'month', label: '本月' },
  { value: 'days30', label: '近 30 天' },
  { value: 'days90', label: '近 90 天' },
  { value: 'all', label: '全部' },
]

/**
 * 按钮组：类名与老 UI put_buttons(small=True, color=…) 的输出**逐字一致**。
 *
 *   选中  → btn btn-primary btn-sm
 *   未选中 → btn btn-secondary btn-sm
 *
 * 不要改成 btn-on/btn-off：那两个类在老 CSS 里没有 btn-sm 的尺寸规则，
 * 按钮会变大、间距散开（长卿截图的「丑」就是这么来的）。
 *
 * 老 UI 的按钮之间靠 PyWebIO 插入的空白文本节点产生 ≈5px 间隙，
 * 这里用父容器 gap 等价实现（老 UI 的 .btn margin 是 4px 0，左右为 0）。
 */
function BtnRow({
  options,
  value,
  onChange,
  marginBottom,
}: {
  options: { value: string; label: string }[]
  value: string
  onChange: (v: string) => void
  marginBottom?: string
}) {
  return (
    <div style={{ marginBottom: marginBottom ?? '.35rem' }}>
      {options.map((o, i) => (
        <button
          key={o.value}
          className={'btn ' + (o.value === value ? 'btn-primary' : 'btn-secondary') + ' btn-sm'}
          onClick={() => onChange(o.value)}
          style={i === 0 ? undefined : { marginLeft: '.3rem' }}
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

  // 「第9期」——无空格，与 i18n ResearchSeriesOption "第{series}期" 一致
  const seriesOpts = (data?.series_options ?? []).map(s => ({
    value: s,
    label: '第' + s + '期',
  }))

  // 三行选项控件（老 UI app_stat_research.py:_output_research_controls）
  const controls = (
    <>
      <BtnRow
        options={SCOPE_OPTS}
        value={scope}
        onChange={v => { setScope(v); setSeries(0) }}
      />
      <BtnRow
        options={PERIOD_OPTS}
        value={period}
        onChange={setPeriod}
      />
      {scope === 'series' && seriesOpts.length > 0 && (
        <BtnRow
          options={seriesOpts}
          value={String(series || '')}
          onChange={v => setSeries(Number(v))}
          marginBottom=".6rem"
        />
      )}
    </>
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
    <div>
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
