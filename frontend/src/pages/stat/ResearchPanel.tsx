// 科研统计面板（research_stats.collect：期数 items 表）
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

type ResearchItem = { name: string; zh: string; rarity: number; amount: number; count: number; avg: number; today: number; month: number }
type ResearchResp = { scope: string; series: number; items: ResearchItem[]; total: number; today?: any; month?: any }

export function ResearchPanel({ name }: { name: string }) {
  const [data, setData] = useState<ResearchResp | null>(null)
  useEffect(() => {
    apiGet<ResearchResp>(`/newapi/stat/${name}/research_stats`).then(setData).catch(() => {})
  }, [name])
  if (!data) return <div className="st-placeholder">加载中…</div>
  const items = data.items ?? []
  return (
    <div>
      <div style={{ padding: '6px 0 10px', fontSize: 14 }}>
        期数 <b>{data.series}</b> · 物品 <b>{items.length}</b> 种 · 累计 <b>{data.total}</b>
      </div>
      <table className="research-table">
        <thead>
          <tr><th>物品</th><th>稀有度</th><th>累计</th><th>次数</th><th>均值</th><th>本月</th></tr>
        </thead>
        <tbody>
          {items.map(it => (
            <tr key={it.name}>
              <td>{it.zh}</td>
              <td>{'★'.repeat(it.rarity)}</td>
              <td>{it.amount}</td>
              <td>{it.count}</td>
              <td>{it.avg}</td>
              <td>{it.month}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!items.length && <div className="st-placeholder">暂无科研记录</div>}
    </div>
  )
}
