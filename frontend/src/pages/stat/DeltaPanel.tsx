// 资源变动面板（app_stat_delta.py:_load_delta_data 同口径）
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

type DeltaItem = { name: string; value: number }
type DeltaResp = { timeline: any[]; summary: DeltaItem[]; ranking: DeltaItem[] }

export function DeltaSummary({ name }: { name: string }) {
  const [data, setData] = useState<DeltaResp | null>(null)
  useEffect(() => {
    apiGet<DeltaResp>(`/newapi/stat/${name}/resource_delta`).then(setData).catch(() => {})
  }, [name])
  const summary = Array.isArray(data?.summary) ? data!.summary : []
  const ranking = Array.isArray(data?.ranking) ? data!.ranking : []
  return (
    <div>
      <div className="delta-summary">
        {summary.map((s, i) => (
          <div key={i} className="dashboard-entry">
            <div id={'pywebio-scope-' + s.name + '_group'}>
              <div className="dashboard-value-row">
                <span className="dashboard-value">{s.value}</span>
              </div>
              <div className="dashboard-help">{s.name}</div>
            </div>
          </div>
        ))}
        {!summary.length && <div className="st-placeholder">暂无变动数据（实例运行后出现）</div>}
      </div>
      {ranking.length > 0 && (
        <div className="delta-ranking">
          <div className="ov-card-title" style={{ fontSize: 14 }}>消耗排行</div>
          {ranking.slice(0, 8).map((r, i) => (
            <div key={i} className="delta-rank-row">
              <span>{r.name}</span>
              <span className="dashboard-value">{r.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
