// 科技列表面板（fleet_tech_view.py 同口径：汇总+表格+分页）
import { useEffect, useMemo, useState } from 'react'
import { apiGet } from '../../api/client'

type TechRow = {
  name: string; nationality: number; rarity: string; type: number;
  scan_type?: string; blueprint?: boolean; technology?: any;
}
type TechResp = { rows: TechRow[]; revision: string; total: number }

const PAGE_SIZE = 25

export function FleetTechPanel({ name }: { name: string }) {
  const [data, setData] = useState<TechResp | null>(null)
  const [page, setPage] = useState(0)
  const [query, setQuery] = useState('')
  useEffect(() => {
    apiGet<TechResp>(`/newapi/stat/${name}/fleet_tech`).then(setData).catch(() => {})
  }, [name])
  const rows = data?.rows ?? []
  const filtered = useMemo(() => {
    if (!query) return rows
    const q = query.toLowerCase()
    return rows.filter(r => r.name.toLowerCase().includes(q))
  }, [rows, query])
  const pageRows = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  if (!data) return <div className="st-placeholder">加载中…</div>
  return (
    <div>
      <div style={{ padding: '6px 0 10px', display: 'flex', gap: 10, alignItems: 'center' }}>
        <span>共 <b>{filtered.length}</b> 项</span>
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
          <tr><th>舰船</th><th>稀有度</th><th>国籍</th><th>类型</th></tr>
        </thead>
        <tbody>
          {pageRows.map((r, i) => (
            <tr key={r.name + i}>
              <td>{r.name}</td>
              <td>{r.rarity}</td>
              <td>{r.nationality}</td>
              <td>{r.type}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
