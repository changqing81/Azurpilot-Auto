// 统计页：页签结构 + 图表面板。
// 老结构对照：_mount_statistics_page（app_statistics_page.py:240-280）——
// statistics-tabs / statistics-panels + ap_chart/opsi_stats/commission_income/research_stats/resource_delta/ship_exp_table 六面板 scope。
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ApChart } from '../../charts/ApChart'
import { apiGet } from '../../api/client'

type DeltaItem = { name: string; value: number; time?: string }
type DeltaResp = { timeline: any[]; summary: DeltaItem[]; ranking: DeltaItem[] }

const TABS = [
  { key: 'ap_chart', name: '行动力图表' },
  { key: 'opsi_stats', name: '大世界统计' },
  { key: 'commission_income', name: '委托收益' },
  { key: 'research_stats', name: '科研统计' },
  { key: 'resource_delta', name: '资源变动' },
  { key: 'ship_exp_table', name: '舰船经验' },
]

// 资源变动面板（summary 是 list、ranking 是 list）
function DeltaSummary({ name }: { name: string }) {
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

export function StatPage() {
  const { name = 'alas' } = useParams()
  const navigate = useNavigate()
  const [tab, setTab] = useState('ap_chart')
  return (
    <div id="pywebio-scope-statistics-content" className="stat-page">
      <div className="cfg-header">
        <button className="log-btn" onClick={() => navigate('/')}>← 返回总览</button>
        <h2>统计</h2>
      </div>
      <div className="st-tabs">
        {TABS.map(t => (
          <button
            key={t.key}
            className={'st-tab' + (tab === t.key ? ' st-tab-active' : '')}
            onClick={() => setTab(t.key)}
          >
            {t.name}
          </button>
        ))}
      </div>
      <div id="statistics-panels" className="st-panels">
        {tab === 'ap_chart' && (
          <div id="pywebio-scope-ap_chart" className="ov-card st-panel">
            <div className="ov-card-title">行动力图表</div>
            <ApChart name={name} />
          </div>
        )}
        {tab === 'opsi_stats' && (
          <div id="pywebio-scope-opsi_stats" className="ov-card st-panel">
            <div className="ov-card-title">大世界统计</div>
            <div className="st-placeholder">面板接入中</div>
          </div>
        )}
        {tab === 'commission_income' && (
          <div id="pywebio-scope-commission_income" className="ov-card st-panel">
            <div className="ov-card-title">委托收益</div>
            <div className="st-placeholder">面板接入中</div>
          </div>
        )}
        {tab === 'research_stats' && (
          <div id="pywebio-scope-research_stats" className="ov-card st-panel">
            <div className="ov-card-title">科研统计</div>
            <div className="st-placeholder">面板接入中</div>
          </div>
        )}
        {tab === 'resource_delta' && (
          <div id="pywebio-scope-resource_delta" className="ov-card st-panel">
            <div className="ov-card-title">资源变动</div>
            <DeltaSummary name={name} />
          </div>
        )}
        {tab === 'ship_exp_table' && (
          <div id="pywebio-scope-ship_exp_table" className="ov-card st-panel">
            <div className="ov-card-title">舰船经验</div>
            <div className="st-placeholder">面板接入中</div>
          </div>
        )}
      </div>
    </div>
  )
}
