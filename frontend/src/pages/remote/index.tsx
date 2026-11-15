// 远程控制页（对照 app_developer_tools.py dev_remote：状态机 + 开关）
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'

type RemoteStatus = {
  enabled: boolean; state: number; running: boolean;
  connection_state: string; connection_text: string;
  entry_point: string; error: string; is_alive: boolean
}

export function RemotePage() {
  const navigate = useNavigate()
  const [st, setSt] = useState<RemoteStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () => apiGet<RemoteStatus>('/newapi/remote/status').then(setSt).catch(() => {})
  useEffect(() => { load() }, [])
  const toggle = async () => {
    if (!st) return
    setBusy(true)
    try {
      await fetch('/newapi/remote/toggle', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !st.enabled }),
      })
      await load()
    } finally { setBusy(false) }
  }
  return (
    <div id="pywebio-scope-content" className="config-page">
      <div className="cfg-header">
        <button className="log-btn" onClick={() => navigate('/')}>← 返回总览</button>
        <h2>远程控制</h2>
        <span className="cfg-dirty-hint" />
      </div>
      <section className="ov-card cfg-group">
        <div className="cfg-row">
          <div className="cfg-title-wrap">
            <div className="arg-title">远程访问</div>
            <div className="arg-help">
              {st?.running
                ? `运行中 · ${st.connection_text}`
                : st?.enabled ? '已启用 · 未运行' : '未启用'}
            </div>
            {st?.entry_point && <div className="arg-help">入口: {st.entry_point}</div>}
            {st?.error && <div className="arg-help" style={{ color: '#c0392b' }}>{st.error}</div>}
          </div>
          <div className="cfg-control">
            <button className="ov-btn-primary" disabled={busy} onClick={toggle}>
              {busy ? '处理中…' : st?.enabled ? '关闭' : '开启'}
            </button>
          </div>
        </div>
        <div className="cfg-row">
          <div className="cfg-title-wrap">
            <div className="arg-help">连接状态: {st?.connection_state ?? '-'} · 服务存活: {st?.is_alive ? '是' : '否'}</div>
          </div>
        </div>
      </section>
    </div>
  )
}
