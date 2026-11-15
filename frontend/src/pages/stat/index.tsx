// 统计页壳（P3 第一阶段）：页签结构 + 图表面板占位。
// 老结构对照：_mount_statistics_page（app_statistics_page.py:240-280）——
// statistics-head / statistics-tabs / statistics-panels + ap_chart/opsi_stats/commission_income/research_stats/resource_delta/ship_exp_table 六个面板 scope。
import { useParams, useNavigate } from 'react-router-dom'

const TABS = [
  { key: 'ap_chart', name: '行动力图表' },
  { key: 'opsi_stats', name: '大世界统计' },
  { key: 'commission_income', name: '委托收益' },
  { key: 'research_stats', name: '科研统计' },
  { key: 'resource_delta', name: '资源变动' },
  { key: 'ship_exp_table', name: '舰船经验' },
]

export function StatPage() {
  const { name = 'alas' } = useParams()
  const navigate = useNavigate()
  return (
    <div id="pywebio-scope-statistics-content" className="stat-page">
      <div className="cfg-header">
        <button className="log-btn" onClick={() => navigate('/')}>← 返回总览</button>
        <h2>统计</h2>
      </div>
      <div className="st-tabs">
        {TABS.map(t => (
          <button key={t.key} className="st-tab" onClick={() => document.getElementById('statistics-panels')?.scrollIntoView({ behavior: 'smooth' })}>
            {t.name}
          </button>
        ))}
      </div>
      <div id="statistics-panels" className="st-panels">
        <div id="pywebio-scope-ap_chart" className="ov-card st-panel">
          <div className="ov-card-title">行动力图表</div>
          <div className="st-placeholder">图表数据接入中（P3-下阶段：复用 /api/ap_timeline 与 ECharts）</div>
        </div>
        <div id="pywebio-scope-opsi_stats" className="ov-card st-panel">
          <div className="ov-card-title">大世界统计</div>
          <div className="st-placeholder">面板接入中</div>
        </div>
        <div id="pywebio-scope-commission_income" className="ov-card st-panel">
          <div className="ov-card-title">委托收益</div>
          <div className="st-placeholder">面板接入中</div>
        </div>
        <div id="pywebio-scope-research_stats" className="ov-card st-panel">
          <div className="ov-card-title">科研统计</div>
          <div className="st-placeholder">面板接入中</div>
        </div>
        <div id="pywebio-scope-resource_delta" className="ov-card st-panel">
          <div className="ov-card-title">资源变动</div>
          <div className="st-placeholder">面板接入中</div>
        </div>
        <div id="pywebio-scope-ship_exp_table" className="ov-card st-panel">
          <div className="ov-card-title">舰船经验</div>
          <div className="st-placeholder">面板接入中</div>
        </div>
      </div>
    </div>
  )
}
