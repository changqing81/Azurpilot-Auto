// 资源面板（老 UI app_stat_delta.py:_render_resource_delta 的同构实现）。
//
// 长卿：「资源这块完全是半成品做都没做」
//
// 原来我只挂了三段 HTML（stats/legend/summary），老 UI 那一页其实有**四块**：
//   ① _render_delta_toolbar   粒度切换 日/周/月/总
//   ② _render_delta_timeline  任务时间轴（echarts，HTML + JS 两件套）
//   ③ _build_summary_html     增减汇总表
//   ④ _build_ranking_html     消耗排行榜
// 时间轴图和排行榜整个没做，所以页面看起来就是「一条汇总 + 一个表格」的半成品。
//
// 时间轴的做法与大世界体力图一致：后端返回 timeline_html + timeline_js
// （老 UI 的 webapp/resource_delta_timeline.{html,js} 模板填好数据），
// 前端挂 DOM 后执行脚本 —— 就是老 UI 那张图，不是重画的。
import { useEffect, useRef, useState } from 'react'
import { apiGet, panelPathWithParams } from '../../api/client'
import { useStatText } from './i18n'

type PeriodOpt = { value: string; label: string }

type Resp = {
  success: boolean
  period?: string
  period_options?: PeriodOpt[]
  timeline_html?: string
  timeline_js?: string
  stats_html?: string
  legend_html?: string
  summary_html?: string
  error?: string
}

export function DeltaSummary({ name }: { name: string }) {
  const text = useStatText()
  const [period, setPeriod] = useState('day')
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const hostRef = useRef<HTMLDivElement>(null)


  useEffect(() => {
    let alive = true
    setLoading(true)
    // 远控中继剥 query，period（日/周/月/总）必须走路径段
    apiGet<Resp>(panelPathWithParams(name, 'delta', { period }), 30000)
      .then(r => {
        if (!alive) return
        setData(r)
        // 后端会回传当前实际粒度（首次请求后对齐高亮）
        if (r.period && r.period !== period) setPeriod(r.period)
      })
      .catch(e => { if (alive) setData({ success: false, error: String(e) }) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, period])

  // 挂载时间轴：innerHTML + 执行老 UI 的脚本（与大世界体力图同一套路）。
  // 脚本里用 document.getElementById(chart_id) 找容器，所以必须先插 DOM。
  useEffect(() => {
    const host = hostRef.current
    if (!host || !data?.timeline_html) return
    host.innerHTML = data.timeline_html
    let script: HTMLScriptElement | null = null
    if (data.timeline_js) {
      script = document.createElement('script')
      script.textContent = data.timeline_js
      document.body.appendChild(script)
    }
    return () => {
      // 老 UI 的脚本注册了 window.__resourceDeltaChartCleanups，卸载时调用释放
      try {
        const w = window as any
        const cleanups = w.__resourceDeltaChartCleanups
        if (cleanups) {
          Object.values(cleanups).forEach((fn: any) => {
            if (typeof fn === 'function') fn()
          })
        }
      } catch { /* 忽略 */ }
      script?.remove()
      host.innerHTML = ''
    }
  }, [data?.timeline_html, data?.timeline_js])

  return (
    <div>
      {/* ① 粒度切换：日 / 周 / 月 / 总（老 UI _render_delta_toolbar，
          primary 表示选中、off 表示未选中；class 逐字照抄，不自造） */}
      <div style={{ marginBottom: '8px' }}>
        {(data?.period_options ?? [
          { value: 'day', label: text('DeltaPeriodDay') },
          { value: 'week', label: text('DeltaPeriodWeek') },
          { value: 'month', label: text('DeltaPeriodMonth') },
          { value: 'total', label: text('DeltaPeriodTotal') },
        ]).map((o, i) => (
          <button
            key={o.value}
            className={'btn ' + (o.value === period ? 'btn-primary' : 'btn-off')}
            onClick={() => setPeriod(o.value)}
            style={i === 0 ? undefined : { marginLeft: '.3rem' }}
          >
            {o.label}
          </button>
        ))}
      </div>

      {loading && !data && <div className="st-placeholder">加载中…</div>}
      {data && !data.success && (
        <div className="st-placeholder">{data.error || text('NoDeltaData')}</div>
      )}

      {data?.success && (
        <>
          {/* ② 任务时间轴（老 UI 的 echarts 图，HTML+JS 原样复用） */}
          <div ref={hostRef} />
          {/* ③ 增减汇总表 + ④ 消耗排行榜（老 UI resource_delta_rank.html） */}
          {data.summary_html && (
            <div dangerouslySetInnerHTML={{ __html: data.summary_html }} />
          )}
        </>
      )}
    </div>
  )
}