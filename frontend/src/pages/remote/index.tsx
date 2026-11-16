// 远程控制页（对照 module/webui/app_developer_tools.py:dev_remote 的状态机）。
//
// 老 UI 的三态显示（Switch 状态 -1/0/1/2/3/4）：
//   运行中(1,2)   → 绿色加载圈 + 「运行中 · <连接方式>」+ 入口 url 链接
//   连接中(0,3,4) → 灰色加载圈 + 「未运行，…」+ 配置教程链接
//   关闭(-1)      → 「未启用，…」+ 配置教程链接
// 之前只做了两行文字（长卿反馈「干秃秃」），这版按老 UI 补齐状态图标、
// 连接方式文案映射、入口链接和配置教程。
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { Shell } from '../../components/Shell'

type RemoteStatus = {
  enabled: boolean; state: number; running: boolean;
  connection_state: string; connection_text: string;
  entry_point: string; error: string; is_alive: boolean
}

// 连接方式文案映射 = 老 UI dev_remote 里的 status_map
const CONN_TEXT: Record<string, string> = {
  direct_p2p: 'P2P 直连',
  turn_relay: 'TURN 中继',
  ssh_forward: 'SSH 转发',
  waiting_peer: '等待 P2P 连接',
  signaling: '等待 P2P 连接',
  starting: '正在连接',
  dependency_missing: 'SSH 转发',
  failed: '连接失败',
}

// 状态圈：运行中绿色脉冲 / 连接中灰色描边 / 关闭灰色空心（与 put_loading 同观感）
function StateDot({ running, connecting }: { running: boolean; connecting: boolean }) {
  if (running) {
    return (
      <span
        className="remote-dot is-running"
        aria-label="运行中"
      />
    )
  }
  if (connecting) {
    return <span className="remote-dot is-connecting" aria-label="连接中" />
  }
  return <span className="remote-dot is-off" aria-label="未启用" />
}

export function RemotePage() {
  const navigate = useNavigate()
  const [st, setSt] = useState<RemoteStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () => apiGet<RemoteStatus>('/newapi/remote/status').then(setSt).catch(() => {})
  useEffect(() => {
    load()
    // 老 UI 的 Switch 是 1s 轮询刷新状态，这里保持同节奏
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [])

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

  // 状态归类（= dev_remote 的 state 分支）
  const running = !!st?.running                       // state 1,2
  const connecting = !running && (st?.state === 0 || st?.state === 3 || st?.state === 4)
  const enabled = !!st?.enabled
  const connText = CONN_TEXT[st?.connection_state ?? ''] ?? st?.connection_state ?? ''

  return (
    <Shell active="Remote" title="远程控制">
    <div id="pywebio-scope-content" className="config-page">
      <div className="cfg-header">
        <button className="log-btn" onClick={() => navigate('/')}>← 返回总览</button>
        <h2>远程控制</h2>
        <span className="cfg-dirty-hint" />
      </div>
      <section className="ov-card apple-card cfg-group">
        {/* 状态行：圆点 + 主状态文案 + 开关（与老 UI put_row[loading|state] 同构） */}
        <div className="cfg-row">
          <div className="cfg-title-wrap" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <StateDot running={running} connecting={connecting} />
            <div>
              <div className="arg-title">
                {running
                  ? `运行中 · ${connText}`
                  : enabled ? '未运行，与服务器的连接断开或服务器离线' : '未启用，在 deploy.yaml 中设置 webui 密码并启用远程控制'}
              </div>
              {running && (
                <div className="arg-help">远程访问 url 地址:</div>
              )}
              {running && (
                <div>
                  {st?.entry_point ? (
                    <a href={st.entry_point} target="_blank" rel="noreferrer"
                       style={{ color: 'var(--alas-entry-action, #2563eb)', textDecoration: 'underline' }}>
                      {st.entry_point}
                    </a>
                  ) : (
                    <span className="arg-help">Loading...</span>
                  )}
                </div>
              )}
              {st?.error && (
                <div className="arg-help" style={{ color: 'var(--alas-entry-badge-error, #c0392b)' }}>
                  {st.error}
                </div>
              )}
            </div>
          </div>
          <div className="cfg-control">
            <button className="ov-btn-primary" disabled={busy} onClick={toggle}>
              {busy ? '处理中…' : enabled ? '关闭' : '开启'}
            </button>
          </div>
        </div>
        {/* 配置教程（= Gui.Remote.ConfigureHint + app.azurlane.cloud 链接） */}
        {!running && (
          <div className="cfg-row">
            <div className="cfg-title-wrap">
              <div className="arg-help">配置教程：</div>
              <a
                href="http://app.azurlane.cloud"
                target="_blank"
                rel="noreferrer"
                style={{ color: 'var(--alas-entry-action, #2563eb)', textDecoration: 'underline' }}
              >
                http://app.azurlane.cloud
              </a>
            </div>
          </div>
        )}
        <div className="cfg-row">
          <div className="cfg-title-wrap">
            <div className="arg-help">连接状态: {st?.connection_state ?? '-'} · 服务存活: {st?.is_alive ? '是' : '否'}</div>
          </div>
        </div>
      </section>
    </div>
    </Shell>
  )
}
