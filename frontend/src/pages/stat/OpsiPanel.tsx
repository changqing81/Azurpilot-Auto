// 大世界统计面板（app_stat_opsi.py:_render_opsi_summary_cards 口径：当月指标卡）
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

type OpsiSummary = {
  month?: string
  total_battles?: number
  akashi_encounters?: number
  siren_research_devices?: number
  raw?: { akashi_ap?: number; battle_count?: number }
}

const fmt = (n: any) => (typeof n === 'number' ? n.toLocaleString() : String(n ?? '-'))

export function OpsiPanel({ name }: { name: string }) {
  const [s, setS] = useState<OpsiSummary | null>(null)
  useEffect(() => {
    apiGet<{ summary: OpsiSummary }>(`/newapi/stat/${name}/opsi_summary`).then(r => setS(r.summary)).catch(() => {})
  }, [name])
  if (!s) return <div className="st-placeholder">加载中…</div>
  const cards = [
    { label: '战斗次数', value: fmt(s.total_battles) },
    { label: '明石遭遇', value: fmt(s.akashi_encounters) },
    { label: '塞壬研究装置', value: fmt(s.siren_research_devices) },
    { label: '明石行动力', value: fmt(s.raw?.akashi_ap) },
  ]
  return (
    <div>
      <div className="delta-summary">
        {cards.map(c => (
          <div key={c.label} className="dashboard-entry">
            <div>
              <div className="dashboard-value-row">
                <span className="dashboard-value">{c.value}</span>
              </div>
              <div className="dashboard-help">{c.label}</div>
            </div>
          </div>
        ))}
      </div>
      {!s.total_battles && <div className="st-placeholder">本月暂无大世界记录</div>}
    </div>
  )
}
