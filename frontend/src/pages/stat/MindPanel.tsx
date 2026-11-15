// 心智计算器面板（mind_calculator.calculate 结果：summary + ships 清单）
import { useEffect, useMemo, useState } from 'react'
import { apiGet } from '../../api/client'

type MindShip = { name: string; level: number; rarity: string; status: string; mind: number; gold: number; excluded: boolean }
type MindResp = { ships: MindShip[]; result: { summary: any; mind: number; gold: number; included: number; merged: number }; updated_at: string; total: number }

const PAGE_SIZE = 25

export function MindPanel({ name }: { name: string }) {
  const [data, setData] = useState<MindResp | null>(null)
  const [page, setPage] = useState(0)
  const [query, setQuery] = useState('')
  useEffect(() => {
    apiGet<MindResp>(`/newapi/stat/${name}/mind`).then(setData).catch(() => {})
  }, [name])
  const ships = data?.ships ?? []
  const summary = data?.result?.summary
  const filtered = useMemo(() => {
    if (!query) return ships
    const q = query.toLowerCase()
    return ships.filter(s => s.name.toLowerCase().includes(q))
  }, [ships, query])
  const pageRows = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  if (!data) return <div className="st-placeholder">加载中…</div>
  return (
    <div>
      <div style={{ padding: '6px 0 10px', fontSize: 14, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {summary && Object.entries(summary).map(([k, v]) => (
          <span key={k}>{k}: <b>{String(v)}</b></span>
        ))}
        <span>清单: <b>{data.total}</b> 艘 · 更新于 {data.updated_at || '-'}</span>
      </div>
      <div style={{ padding: '0 0 10px', display: 'flex', gap: 10, alignItems: 'center' }}>
        <input
          className="cfg-input"
          style={{ width: 220 }}
          placeholder="搜索舰船…"
          value={query}
          onChange={e => { setQuery(e.target.value); setPage(0) }}
        />
        <span style={{ flex: 1 }} />
        <button className="log-btn" disabled={page === 0} onClick={() => setPage(p => p - 1)}>上一页</button>
        <span>{page + 1} / {totalPages || 1}</span>
        <button className="log-btn" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>下一页</button>
      </div>
      <table className="research-table">
        <thead>
          <tr><th>舰船</th><th>等级</th><th>稀有度</th><th>状态</th><th>心智</th><th>金图</th></tr>
        </thead>
        <tbody>
          {pageRows.map((s, i) => (
            <tr key={s.name + i}>
              <td>{s.name}</td>
              <td>{s.level}</td>
              <td>{s.rarity}</td>
              <td>{s.excluded ? '已排除' : s.status}</td>
              <td>{s.mind}</td>
              <td>{s.gold}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
