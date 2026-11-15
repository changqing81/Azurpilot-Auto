// 委托收益面板（app_stat_commission.py 本月口径）
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

export function CommissionIncome({ name }: { name: string }) {
  const [data, setData] = useState<{ period: string; summary: any } | null>(null)
  useEffect(() => {
    apiGet<{ period: string; summary: any }>(`/newapi/stat/${name}/commission_income`).then(setData).catch(() => {})
  }, [name])
  const s = data?.summary
  const items: { key: string; label: string }[] = [
    { key: 'Gem', label: '钻石' }, { key: 'Cube', label: '魔方' }, { key: 'Chip', label: '芯片' },
    { key: 'Oil', label: '石油' }, { key: 'Coin', label: '物资' },
  ]
  if (!s || !s.total_commissions) return <div className="st-placeholder">本月暂无委托记录</div>
  return (
    <div>
      <div style={{ padding: '6px 0 12px', fontSize: 14 }}>
        本月委托 <b>{s.total_commissions}</b> 次
      </div>
      <div className="delta-summary">
        {items.map(it => {
          const item = s.items?.[it.key]
          if (!item) return null
          return (
            <div key={it.key} className="dashboard-entry">
              <div>
                <div className="dashboard-value-row">
                  <span className="dashboard-value">{item.total}</span>
                </div>
                <div className="dashboard-help">{it.label} · {item.count} 次 · 均 {item.avg}</div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
