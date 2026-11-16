// 开发者工具页：**所有按钮都真的能跑**，不再显示「请切回老 UI」。
//
// 老 UI 的实现（module/webui/app_developer_tools.py:dev_utils）是一堆
// PyWebIO 会话内回调。我原来偷懒把它们标成"需老 UI"，等于功能没做。
// 现在逐个搬到 POST /newapi/dev/action，用 action 字段分派。
//
// 不做的只有一项：「模拟错误弹窗」需要一个 DOM 弹窗（alas-update-notice），
// 它依赖 alas-utils.js 的渲染函数——那个函数是 window 全局的，这里直接调用。
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { Shell } from '../../components/Shell'

type InstItem = { name: string; state: number; mod: string }
type ListResp = { instances: InstItem[] }

const COLOR: Record<string, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  success: 'btn-success',
  danger: 'btn-danger',
  warning: 'btn-warning',
  info: 'btn-info',
  light: 'btn-light',
  dark: 'btn-dark',
}

export function ToolPage() {
  const navigate = useNavigate()
  const { name = 'alas' } = useParamsSafe()
  const [instances, setInstances] = useState<InstItem[]>([])
  const [instance, setInstance] = useState(name)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  useEffect(() => {
    apiGet<ListResp>('/newapi/instances/detail')
      .then(r => { setInstances(r.instances || []) })
      .catch(() => { /* 实例列表拿不到：按钮仍可用，instance 用默认值 */ })
  }, [])

  const flash = (t: string) => { setMsg(t); setTimeout(() => setMsg(''), 6000) }

  const call = async (action: string, extra: Record<string, any> = {}, label = '') => {
    setBusy(action)
    try {
      const r = await fetch('/newapi/dev/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, instance, ...extra }),
      })
      const j = await r.json()
      flash(j.success ? `${label || action}：成功` : `${label || action}：${j.error || '失败'}`)
    } catch (e) {
      flash(`${label || action}：${String(e)}`)
    } finally {
      setBusy('')
    }
  }

  /**
   * 预览更新提示 / 模拟错误弹窗：**前端本地 DOM 弹窗**。
   *
   * 老 UI（app_developer_menu.py:69 _preview_update_notice）就是往 ROOT 里
   * 注入 #alas-update-notice DOM —— 它从头到尾都是**浏览器端渲染**，
   * 后端只负责"该不该弹"的数据。所以这里同样在前端注入同款 DOM
   * （类名逐字照抄 entry-alas.css 的 .alas-update-notice 规则），8 秒自动消失。
   */
  const showUpdateNotice = () => {
    document.getElementById('alas-update-notice')?.remove()
    const el = document.createElement('div')
    el.id = 'alas-update-notice'
    el.className = 'alas-update-notice'
    el.setAttribute('role', 'status')
    el.innerHTML =
      '<div class="alas-update-notice__halo"></div>' +
      '<div class="alas-update-notice__icon" aria-hidden="true">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>' +
      '<path d="M7 10l5 5 5-5"></path><path d="M12 15V3"></path></svg></div>' +
      '<div class="alas-update-notice__body">' +
      '<div class="alas-update-notice__eyebrow">发现新版本</div>' +
      '<div class="alas-update-notice__title">有可用更新！</div>' +
      '<div class="alas-update-notice__text">预览：这是开发者工具的测试弹窗，不会真的更新。</div>' +
      '</div>'
    document.body.appendChild(el)
    setTimeout(() => el.remove(), 8000)
    flash('预览更新提示：已显示（8 秒后消失）')
  }

  /** 模拟错误弹窗：同款机制，用更新通知的容器承载错误文案。 */
  const simulateErrorPopup = () => {
    document.getElementById('alas-update-notice')?.remove()
    const el = document.createElement('div')
    el.id = 'alas-update-notice'
    el.className = 'alas-update-notice'
    el.setAttribute('role', 'alert')
    el.innerHTML =
      '<div class="alas-update-notice__halo"></div>' +
      '<div class="alas-update-notice__icon" aria-hidden="true" style="color:#c0392b">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">' +
      '<circle cx="12" cy="12" r="10"></circle><path d="M12 8v4"></path><path d="M12 16h.01"></path></svg></div>' +
      '<div class="alas-update-notice__body">' +
      '<div class="alas-update-notice__eyebrow">任务异常</div>' +
      '<div class="alas-update-notice__title">Commission · GameStuckError</div>' +
      '<div class="alas-update-notice__text">预览：这是开发者工具的模拟错误弹窗。</div>' +
      '</div>'
    document.body.appendChild(el)
    setTimeout(() => el.remove(), 8000)
    flash('模拟错误弹窗：已显示（8 秒后消失）')
  }

  const btn = (label: string, color: string, onClick: () => void) => (
    <button
      className={'btn ' + (COLOR[color] ?? 'btn-secondary')}
      disabled={!!busy}
      onClick={onClick}
      style={{ margin: '.25rem .25rem .25rem 0' }}
    >
      {label}
    </button>
  )

  return (
    <Shell active="Utils" title="开发者工具">
      <div id="pywebio-scope-content">
        <div style={{ padding: '.625rem' }}>
          <h2 style={{ fontSize: '1.15rem', margin: '.25rem 0 .75rem' }}>GUI测试</h2>
          {btn('抛出异常事件', 'danger', () => call('raise_exception', {}, '抛出异常事件'))}
          {btn('预览更新提示', 'primary', showUpdateNotice)}

          <hr className="hr-group" />
          <h2 style={{ fontSize: '1.15rem', margin: '.25rem 0 .75rem' }}>图标状态模拟</h2>
          {btn('模拟运行图标(10s)', 'success', () => call('mock_icon', { state: 1, seconds: 10 }, '模拟运行图标'))}
          {btn('模拟错误图标(10s)', 'danger', () => call('mock_icon', { state: 3, seconds: 10 }, '模拟错误图标'))}
          {btn('模拟更新图标(10s)', 'warning', () => call('mock_icon', { state: 4, seconds: 10 }, '模拟更新图标'))}
          {btn('清除图标模拟状态', 'secondary', () => call('clear_icon', {}, '清除图标模拟状态'))}

          <hr className="hr-group" />
          <h2 style={{ fontSize: '1.15rem', margin: '.25rem 0 .75rem' }}>弹窗与预演</h2>
          {btn('模拟错误弹窗', 'danger', simulateErrorPopup)}
          {btn('跨月预演(仅清理)', 'warning', () => call('cross_month', { mode: 'cleanup' }, '跨月预演(仅清理)'))}
          {btn('跨月预演(全流程)', 'warning', () => call('cross_month', { mode: 'full' }, '跨月预演(全流程)'))}
          {btn('取消跨月预演', 'secondary', () => call('cancel_cross_month', {}, '取消跨月预演'))}

          <hr className="hr-group" />
          <h2 style={{ fontSize: '1.15rem', margin: '.25rem 0 .75rem' }}>实例操作</h2>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: '.5rem', marginRight: '.75rem' }}>
            <span style={{ fontSize: '.9rem' }}>实例</span>
            <select
              className="form-control"
              style={{ width: 'auto', minWidth: '8rem' }}
              value={instance}
              onChange={e => setInstance(e.target.value)}
            >
              {instances.length === 0 && <option value={instance}>{instance}</option>}
              {instances.map(i => <option key={i.name} value={i.name}>{i.name}</option>)}
            </select>
          </label>
          {btn('重启Alas', 'danger', () => call('restart_alas', {}, '重启 Alas'))}

          <hr className="hr-group" />
          <h2 style={{ fontSize: '1.15rem', margin: '.25rem 0 .75rem' }}>推送测试</h2>
          {btn('测试更新推送 (updata=True)', 'danger', () => call('notify_test', { kind: 'update' }, '测试更新推送'))}
          {btn('测试公告推送 (updata=False)', 'info', () => call('notify_test', { kind: 'announcement' }, '测试公告推送'))}
          {btn('测试错误推送', 'danger', () => call('notify_test', { kind: 'error' }, '测试错误推送'))}

          {msg && (
            <p style={{ marginTop: '.75rem', fontSize: '.9rem', opacity: .85 }}>{msg}</p>
          )}

          <hr className="hr-group" />
          <h2 style={{ fontSize: '1.15rem', margin: '.25rem 0 .75rem' }}>日志导出</h2>
          <LogExport name={instance} />

          <div style={{ marginTop: '1rem' }}>
            <button className="btn btn-secondary" onClick={() => navigate('/')}>← 返回主页</button>
          </div>
        </div>
      </div>
    </Shell>
  )
}

/** 日志导出（老 UI _render_log_export_panel 的等价物，端点已在 P3 实现）。 */
function LogExport({ name }: { name: string }) {
  const [scope, setScope] = useState('all')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const download = async (kind: 'runtime' | 'error' | 'text') => {
    setBusy(kind)
    setMsg('')
    try {
      const qs = new URLSearchParams({ instance: name, scope })
      const r = await fetch(`/api/log/${kind}?${qs}`)
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const blob = await r.blob()
      const cd = r.headers.get('content-disposition') || ''
      const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = m ? decodeURIComponent(m[1]) : kind + '.zip'
      a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 10000)
      setMsg('已导出 ' + a.download)
    } catch (e) {
      setMsg('导出失败：' + String(e))
    } finally {
      setBusy('')
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '.5rem', marginBottom: '.5rem' }}>
        <span style={{ fontSize: '.9rem' }}>日期</span>
        <select className="form-control" style={{ flex: '0 0 auto', width: 'auto' }} value={scope} onChange={e => setScope(e.target.value)}>
          <option value="all">全部（含历史，合并）</option>
          {[`${new Date().getFullYear()}-${String(new Date().getMonth()+1).padStart(2,'0')}-${String(new Date().getDate()).padStart(2,'0')}`].map(d => (
            <option key={d} value={d}>{d}（今天）</option>
          ))}
        </select>
      </div>
      <button className="btn btn-secondary" disabled={!!busy} onClick={() => download('runtime')} style={{ marginRight: '.5rem' }}>
        {busy === 'runtime' ? '导出中…' : '导出运行日志'}
      </button>
      <button className="btn btn-primary" disabled={!!busy} onClick={() => download('error')} style={{ marginRight: '.5rem' }}>
        {busy === 'error' ? '导出中…' : '导出错误日志目录（含截图）'}
      </button>
      <button className="btn btn-secondary" disabled={!!busy} onClick={() => download('text')}>
        {busy === 'text' ? '导出中…' : '仅导出日志文本（不含截图）'}
      </button>
      {msg && <p style={{ marginTop: '.5rem', fontSize: '.85rem', opacity: .8 }}>{msg}</p>}
    </div>
  )
}

/** 取路由参数（ToolPage 既支持 /tool/:name 也支持无参数）。 */
function useParamsSafe(): { name?: string } {
  // 直接读 hash，避免为了一个可选参数引入 useParams 的类型复杂度
  const h = typeof window !== 'undefined' ? window.location.hash : ''
  const m = h.match(/^#\/tool\/([^/]+)/)
  return { name: m ? decodeURIComponent(m[1]) : 'alas' }
}
