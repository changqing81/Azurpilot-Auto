// 更新器页（对照 app_developer_update.py：隐藏更新提示 + 更新状态机 + 版本对照
// + 更新日志 + 详细提交历史）。
//
// 长卿 2026-11-20：「新UI的更新器是不是没有更新入口啊？…检查更新也没有检查到…
// 有立即更新的那个地方吗？…更新器的那个屏蔽更新呢，老UI都有的，新UI怎么没有啊？」
// → 补齐老 UI 三块缺失：① 状态机按钮（检查更新/进行更新/重试/取消）
//   ② 置顶的「隐藏更新提示」开关 ③ 更新日志从裸 JSON 换成老 UI 的折叠卡片。
//
// 文案逐条取自 module/config/i18n/zh-CN.json（Gui.Update.* / Gui.Button.* /
// Gui.DeploySetting.*），与老 UI 完全一致，不做改写。
// 状态轮询等价老 UI 的 Switch(delay=0.5)，这里取 1s：远控下流量更省。
import { useEffect, useRef, useState } from 'react'
import { apiGet, apiPost } from '../../api/client'
import { Shell } from '../../components/Shell'

type Commit = { sha: string; author: string; time: string; message: string }
type UpdateResp = { branch: string; local: Commit; upstream: Commit[] }
type UpdateState = {
  // ⚠️ 后端透出的是 updater.state 原值，它可能是 Python 的 True/False
  // （_check_update 返回 bool，老 UI 靠 False == 0 成立走「已是最新」分支）。
  // JSON 序列化后变成 true/false，直接 String() 得 "false"，命中不了任何分支
  // → 界面显示 "state: false"。这里统一归一成 1/0 或字符串。
  state: number | string | boolean
  restart_enabled: boolean
  hide_notice: boolean
}
type UpdateLog = { entries: unknown[]; html: string; error: string }

/** 把后端 updater.state 归一成状态机用的字符串。
 *
 * 老 UI 的 u(state) 里 state 来自 Python，True == 1、False == 0 天然成立。
 * 经 JSON 到前端后它们变成 true/false，直接 String() 得到 "true"/"false"，
 * 既不等于 "1" 也不等于 "0"，所有分支都命中不了 ——
 * 实测点完「检查更新」界面显示 "state: false"（本 bug）。
 */
function normalizeState(v: number | string | boolean | null | undefined): string {
  if (v === true) return '1'
  if (v === false) return '0'
  if (v === null || v === undefined) return ''
  return String(v)
}

// 老 UI u(state) 的状态机文案（Gui.Update.*，逐字一致）
const STATE_TEXT: Record<string, string> = {
  '0': '已是最新版本',
  '1': '有新版本可用',
  checking: '检查更新中',
  failed: '更新失败，可在./log/*_gui.txt中找到错误日志',
  start: '开始更新',
  wait: '等待所有 AzurPilot 完成当前任务',
  'run update': '更新中',
  reload: '更新成功，正在重启',
  finish: '更新成功，请手动重启',
  cancel: '取消更新，重启 AzurPilot 中',
}

export function UpdatePage() {
  const [data, setData] = useState<UpdateResp | null>(null)
  const [state, setState] = useState<UpdateState | null>(null)
  const [log, setLog] = useState<UpdateLog | null>(null)
  const [hideSaving, setHideSaving] = useState(false)
  const [hideStatus, setHideStatus] = useState('正在读取部署设置...')
  const [busy, setBusy] = useState<string>('')
  const [msg, setMsg] = useState('')
  const aliveRef = useRef(true)

  // 版本对照表：git 子进程 + fetch，只在页面进入与「检查更新」完成后拉一次
  const loadInfo = () => {
    apiGet<UpdateResp>('/newapi/update/info', 120000).then(d => { if (aliveRef.current) setData(d) }).catch(() => {})
  }
  const loadLog = () => {
    apiGet<UpdateLog>('/newapi/update-log', 20000).then(d => { if (aliveRef.current) setLog(d) }).catch(() => {})
  }

  useEffect(() => {
    aliveRef.current = true
    loadInfo()
    loadLog()
    // 状态轮询：等价老 UI updater_switch.delay=0.5，这里 1s
    const tick = () => {
      apiGet<UpdateState>('/newapi/update/state', 8000)
        .then(s => {
          if (!aliveRef.current) return
          setState(s)
          setHideStatus(s.hide_notice ? '启用' : '禁用')
        })
        .catch(() => { /* 后端旧版本（未重启）时该端点不存在，保持默认 */ })
    }
    tick()
    const timer = setInterval(tick, 1000)
    return () => { aliveRef.current = false; clearInterval(timer) }
  }, [])

  // 动作：POST 后立即刷新状态（轮询也会跟上）。立即更新在后台可能要等
  // 所有实例收尾，最多 10 分钟，故给长超时且不阻塞界面。
  const act = async (kind: string, path: string, timeoutMs = 30000) => {
    setBusy(kind)
    setMsg('')
    try {
      const r = await apiPost<{ success?: boolean; state?: string | number; error?: string }>(path, undefined, timeoutMs)
      setState(s => (s ? { ...s, state: r.state ?? s.state } : s))
      if (r.success === false && r.error) setMsg(r.error)
    } catch (e) {
      // 更新成功会重启进程，fetch 断开属正常；交给轮询与页面刷新收尾
      setMsg('请求已下发，正在等待后台处理…')
    } finally {
      setBusy('')
    }
    loadInfo()
  }

  const toggleHide = async (target: boolean) => {
    setHideSaving(true)
    setHideStatus('正在保存...')
    try {
      const r = await apiPost<{ success?: boolean; hide_notice?: boolean; error?: string }>(
        '/newapi/update/hide-notice',
        { enabled: target },
        15000,
      )
      if (r.success === false) throw new Error(r.error || '操作失败')
      const v = r.hide_notice ?? target
      setState(s => (s ? { ...s, hide_notice: v } : s))
      setHideStatus(v ? '启用' : '禁用')
    } catch (e) {
      setHideStatus('操作失败: ' + String(e))
      // 失败时回读真实值，避免开关与磁盘不一致
      try {
        const s2 = await apiGet<UpdateState>('/newapi/update/state', 8000)
        setState(s2)
        setHideStatus(s2.hide_notice ? '启用' : '禁用')
      } catch { /* ignore */ }
    } finally {
      setHideSaving(false)
    }
  }

  const st = state ? normalizeState(state.state) : ''
  const stateText = st === '' ? '检查更新中' : (STATE_TEXT[st] ?? ('state: ' + st))
  const showDisabledWarn = state !== null && !state.restart_enabled

  // 老 UI u(state) 的按钮分支（同一套语义）
  const actionFor = (): { label: string; kind: string; cls: string; disabled?: boolean } | null => {
    switch (st) {
      case '0': return { label: '检查更新', kind: 'check', cls: 'btn btn-secondary' }
      case '1': return { label: '进行更新', kind: 'run', cls: 'btn btn-primary' }
      case 'failed': return { label: '重试更新', kind: 'run', cls: 'btn btn-primary' }
      case 'start':
      case 'wait': return { label: '取消更新', kind: 'cancel', cls: 'btn btn-danger' }
      case 'run update': return { label: '取消更新', kind: 'cancel', cls: 'btn btn-danger', disabled: true }
      case 'cancel': return { label: '取消更新', kind: 'cancel', cls: 'btn btn-danger', disabled: true }
      default: return null
    }
  }

  const onAction = (kind: string) => {
    if (kind === 'check') return act('check', '/newapi/update/check')
    if (kind === 'run') return act('run', '/newapi/update/run', 900000)
    if (kind === 'cancel') return act('cancel', '/newapi/update/cancel')
  }

  const btn = actionFor()
  const spinning = ['checking', 'start', 'wait', 'run update'].includes(st)

  return (
    <Shell active="Update" title="更新器">
    <div id="pywebio-scope-content" className="config-page">
      <h2 style={{ margin: '.5rem 0' }}>更新器</h2>

      {/* ① 隐藏更新提示（老 UI 置顶块，DOM 复用 startup-run-panel/launcher-switch） */}
      <div id="pywebio-scope-group_ui0" className="cfg-group">
        <div className="startup-run-panel">
          <div className="startup-run-row">
            <div>
              <div className="startup-run-title">隐藏更新提示</div>
              <div className="startup-run-desc">
                开启后检测到新版本时不再弹出更新提示，仍可在开发工具中手动检查更新。
              </div>
            </div>
            <label className="launcher-switch" title="隐藏更新提示">
              <input
                type="checkbox"
                checked={!!state?.hide_notice}
                disabled={hideSaving || state === null}
                onChange={e => toggleHide(e.target.checked)}
              />
            </label>
          </div>
          <div className="startup-run-status">{hideStatus}</div>
        </div>
      </div>

      {/* ② 更新状态机：状态行 + 动作按钮 + 版本对照表（老 UI updater_info） */}
      <div id="pywebio-scope-group_ui1" className="cfg-group">
        {showDisabledWarn && (
          <div className="st-update-warn">更新模块未启用，你需要手动重启 AzurPilot 进行更新</div>
        )}
        <div className="st-update-state">
          <span className={'st-update-spinner' + (spinning ? ' is-spinning' : '')} />
          <span className="st-update-state-text">{stateText}</span>
          {btn && (
            <button
              className={btn.cls}
              disabled={busy !== '' || btn.disabled}
              onClick={() => onAction(btn.kind)}
            >
              {busy !== '' ? '请稍候…' : btn.label}
            </button>
          )}
          {msg && <span className="st-update-msg">{msg}</span>}
        </div>

        <div className="st-table-scroll">
        <table className="research-table" style={{ marginTop: '.5rem' }}>
          <thead>
            <tr><th></th><th>SHA1</th><th>作者</th><th>提交时间</th><th>提交信息</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>本地</td>
              <td>{data?.local.sha}</td><td>{data?.local.author}</td>
              <td>{data?.local.time}</td><td>{data?.local.message}</td>
            </tr>
            <tr>
              <td>上游仓库</td>
              <td>{data?.upstream[0]?.sha ?? '-'}</td><td>{data?.upstream[0]?.author ?? ''}</td>
              <td>{data?.upstream[0]?.time ?? ''}</td><td>{data?.upstream[0]?.message ?? ''}</td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>

      {/* ③ 更新日志（服务端渲染的折叠卡片，与老 UI 同一份 render_entries_html） */}
      <div id="pywebio-scope-group_ui2" className="cfg-group">
        {log?.html ? (
          <div dangerouslySetInnerHTML={{ __html: log.html }} />
        ) : (
          <div className="update-log-placeholder">
            {log === null ? '正在获取更新日志…' : '暂无更新日志'}
          </div>
        )}
      </div>

      {/* ④ 详细提交历史（老 UI updater_detail） */}
      <div id="pywebio-scope-group_ui3" className="cfg-group">
        <div style={{ fontSize: '1.05rem', fontWeight: 600 }}>详细提交历史</div>
        <div className="st-table-scroll">
        <table className="research-table">
          <thead><tr><th>SHA1</th><th>作者</th><th>提交时间</th><th>提交信息</th></tr></thead>
          <tbody>
            {(data?.upstream ?? []).map(c => (
              <tr key={c.sha}><td>{c.sha}</td><td>{c.author}</td><td>{c.time}</td><td>{c.message}</td></tr>
            ))}
          </tbody>
        </table>
        </div>
      </div>
    </div>
    </Shell>
  )
}
