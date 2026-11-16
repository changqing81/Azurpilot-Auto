// 统计面板通用组件：直接挂载老 UI 渲染器产出的 HTML。
//
// 为什么这样做（长卿 2026-11-16 反馈「统计页全是半成品」）：
//   老 UI 的统计面板由 webapp/*.html 模板 + app_stat_*.py 里几百行指标口径
//   代码构成。在 React 里重写等于翻译一遍，必然出现数字对不上、格式不一致。
//   正确做法：后端调同一套 Python 构建函数产出 HTML，前端 innerHTML 挂载。
//   这样结构/样式/数字与老 UI **逐字节同源**，不是「模仿」。
//
// 用法：
//   <OpsiLikePanel name={name} panel="commission" fields={['summary_html','table_html','recent_html']} />
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

export function LegacyHtmlPanel({
  name,
  panel,
  fields,
  errorText = '暂无数据',
}: {
  name: string
  panel: string
  fields: string[]
  errorText?: string
}) {
  const [data, setData] = useState<Record<string, string> | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let alive = true
    setData(null)
    setErr('')
    apiGet<Record<string, any>>(
      `/newapi/stat/${name}/panels_html?panel=${panel}`, 25000,
    )
      .then(r => {
        if (!alive) return
        if (!r.success) { setErr(r.error || errorText); return }
        const out: Record<string, string> = {}
        fields.forEach(f => { if (r[f]) out[f] = String(r[f]) })
        if (!Object.keys(out).length) { setErr(errorText); return }
        setData(out)
      })
      .catch(e => { if (alive) setErr(String(e)) })
    return () => { alive = false }
  }, [name, panel, fields.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return <div className="st-placeholder">{err}</div>
  if (!data) return <div className="st-placeholder">加载中…</div>
  return (
    <>
      {fields.map(f =>
        data[f] ? (
          <div key={f} dangerouslySetInnerHTML={{ __html: data[f] }} />
        ) : null,
      )}
    </>
  )
}
