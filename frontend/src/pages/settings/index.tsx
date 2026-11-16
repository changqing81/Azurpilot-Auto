// 设置页（对照老 UI app_developer_settings.py：启动器自启动 + deploy.yaml 可视化配置）。
// 数据全部走老 UI 现成 REST：/api/launcher/status + /api/deploy/settings。
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Shell } from '../../components/Shell'

type LauncherStatus = {
  autostart_enabled: boolean | null
  autostart_supported: boolean
  launcher_connected: boolean
  request_local: boolean
}
// 注意：options 是后端 deploy_settings_schema 原样序列化的 tuple 列表，
// JSON 里落地成 [["legacy","老 UI"],["new","新 UI"]]（value,label 二元组），
// **不是** {label,value} 对象。两种都兼容，避免下拉框渲染成空值。
type RawOption = [string, string] | { label: string; value: string } | string
type DeployField = {
  key: string; label: string; help?: string;
  type: string; value: any; options?: RawOption[]
}

function optionOf(o: RawOption): { value: string; label: string } {
  if (Array.isArray(o)) return { value: String(o[0]), label: String(o[1]) }
  if (typeof o === 'string') return { value: o, label: o }
  return { value: String(o.value), label: String(o.label) }
}
type DeployGroup = { key: string; label: string; fields: DeployField[] }
type DeploySchema = { groups?: DeployGroup[]; title?: string }

export function SettingsPage() {
  const navigate = useNavigate()
  const { name = 'alas' } = useParams()
  const [launcher, setLauncher] = useState<LauncherStatus | null>(null)
  const [schema, setSchema] = useState<DeploySchema | null>(null)
  const [values, setValues] = useState<Record<string, any>>({})
  const [dirty, setDirty] = useState<Record<string, any>>({})
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)
  const [restarting, setRestarting] = useState(false)

  useEffect(() => {
    fetch('/api/launcher/status', { cache: 'no-store' }).then(r => r.json()).then(setLauncher).catch(() => {})
    fetch('/api/deploy/settings').then(r => r.json()).then(j => {
      setSchema(j.data)
      const init: Record<string, any> = {}
      for (const g of j.data?.groups ?? []) for (const f of g.fields ?? []) init[f.key] = f.value
      setValues(init)
    }).catch(() => {})
  }, [])

  const setValue = (key: string, v: any) => {
    setValues(s => ({ ...s, [key]: v }))
    setDirty(d => ({ ...d, [key]: v }))
  }

  const save = async () => {
    setSaving(true)
    try {
      const resp = await fetch('/api/deploy/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(dirty),
      })
      const j = await resp.json()
      setMsg(j.success ? '已保存' : '保存失败: ' + (j.error || '未知错误'))
      if (j.success) {
        setDirty({})
      }
    } catch (e) { setMsg('保存失败: ' + String(e)) }
    finally { setSaving(false); setTimeout(() => setMsg(''), 4000) }
  }

  const toggleAutostart = async (target: boolean) => {
    try {
      await fetch('/api/launcher/startup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: target }),
      })
      const fresh = await fetch('/api/launcher/status', { cache: 'no-store' }).then(r => r.json())
      setLauncher(fresh)
    } catch (e) { setMsg('自启动设置失败: ' + String(e)) }
  }

  // 切换界面版本：写 deploy.yaml → **自动重启** → 轮询到新版本起来后跳转。
  //
  // 为什么不再做热切换：长卿 2026-11-16 拍板——
  //   「正式版老 UI 完全不需要，不要在后台运行。做不到热加载无所谓，
  //     给个切换按钮 + 重启 GUI 就行。」
  // 现在的实现是启动时按 Webui.UI 决定挂哪一套（UI=new 时把老 UI 页面路由
  // 整条摘掉），所以必须重启才生效 —— 这是有意为之，换来的是老 UI 不再
  // 在后台占资源、也不再有「两套 UI 并存」的复杂度。
  //
  // 2026-11-16 追加：长卿要求「点一下就切换」，所以这里不再让用户自己点
  // 「立即重启」第二次 —— 写配置后直接下发重启，再轮询问 /newapi/ui 直到
  // 读回目标版本，然后 location.href='/' 落地。老 UI 的设置页同款行为。
  // uiNow = **进程实际在跑**的界面（不是 deploy.yaml 里写的那个）。
  // 两者可能不一致：点了切换但重启还没完成/失败，此时 pendingUi 有值，
  // 界面上给出「待重启」提示 + 手动重启按钮，避免用户以为切换没生效。
  const [uiNow, setUiNow] = useState<'legacy' | 'new'>('new')
  const [pendingUi, setPendingUi] = useState<'legacy' | 'new' | ''>('')

  // 后端版本兼容：老后端的 /newapi/ui 只返回 {ui}，且那个 ui 读的是
  // deploy.yaml（配置值，不是运行时值）。这种情况**不能**照抄当成"当前界面"
  // 显示——刚切换完还没重启时会显示错误的界面。标记 uiKnown=false，
  // 界面改说「重启后可见」，让用户重启一次把后端也换成新版。
  const [uiKnown, setUiKnown] = useState(true)

  const readUi = async () => {
    try {
      const j = await fetch('/newapi/ui', { cache: 'no-store' }).then(r => r.json())
      const hasRuntimeFields =
        typeof j.configured === 'string' && typeof j.restart_required === 'boolean'
      setUiKnown(hasRuntimeFields)
      setUiNow(j.ui === 'legacy' ? 'legacy' : 'new')
      setPendingUi(
        hasRuntimeFields &&
          j.restart_required &&
          (j.configured === 'legacy' || j.configured === 'new')
          ? j.configured
          : '',
      )
      return j
    } catch {
      return null
    }
  }

  useEffect(() => { void readUi() }, [])

  const switchUi = async (target: 'legacy' | 'new') => {
    if (target === uiNow) return
    setRestarting(true)
    setMsg('正在切换界面版本…')
    try {
      const r = await fetch('/newapi/ui/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ui: target }),
      })
      const j = await r.json()
      if (!j.success) {
        setMsg('切换失败: ' + (j.error || '未知原因'))
        setRestarting(false)
        return
      }
      // 只改 pendingUi：uiNow 仍是「正在跑的」，等重启完成、轮询读回
      // 运行时值变成 target 才算真的切成功。
      setPendingUi(target)
      setMsg(`已切换为${target === 'new' ? '新 UI' : '老 UI'}，正在重启 AzurPilot…`)
    } catch (e) {
      setMsg('切换失败: ' + String(e))
      setRestarting(false)
      return
    }
    // 重启：断开/非 2xx 都当作"还没起来"，直到读回目标版本再跳转。
    try {
      await fetch('/newapi/restart', { method: 'POST' })
    } catch {
      /* 重启指令下发即断连，报错属正常 */
    }
    setMsg('重启中，稍后自动刷新…')
    const deadline = Date.now() + 180000
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 1200))
      try {
        const resp = await fetch('/newapi/ui', { cache: 'no-store' })
        if (!resp.ok) continue
        const data = await resp.json()
        // ui 是运行时值，只有它变成 target 才说明新进程真的起来了
        if ((data.ui === 'legacy' ? 'legacy' : 'new') === target) {
          location.href = '/'
          return
        }
      } catch {
        /* 服务重启中 */
      }
    }
    setMsg('重启超时，请手动刷新页面')
    setRestarting(false)
  }

  // 重启 GUI：老 UI 的 /newapi/restart 会保存实例状态并重启进程
  const restartGui = async () => {
    setRestarting(true)
    setMsg('正在重启 AzurPilot…（实例会自动保存并恢复）')
    try {
      await fetch('/newapi/restart', { method: 'POST' })
      setMsg('重启指令已下发，页面稍后会自动恢复')
    } catch (e) {
      setMsg('重启失败: ' + String(e))
      setRestarting(false)
    }
  }

  const launcherText = (() => {
    if (!launcher) return '加载中…'
    if (!launcher.request_local) return '远控模式下不可用'
    if (!launcher.autostart_supported) return '当前平台不支持'
    if (!launcher.launcher_connected) return '启动器未连接'
    return launcher.autostart_enabled === null ? '已连接 · 读取中' : '已连接 · ' + (launcher.autostart_enabled ? '已开启' : '未开启')
  })()

  return (
    <Shell active={name} title="设置">
    <div id="pywebio-scope-content" className="config-page">
      <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', margin: '.5rem 0' }}>
        <button className="log-btn" onClick={() => navigate('/')}>← 返回总览</button>
        <h2>设置</h2>
        <button className="log-btn" onClick={() => navigate('/update')}>更新器</button>
        <button className="log-btn" onClick={() => navigate('/remote')}>远程控制</button>
        <span style={{ fontSize: '.8rem', opacity: .7 }}>{msg}</span>
      </div>

      {/* 界面版本切换：一次点击 = 写 deploy.yaml + 自动重启 + 轮询落地。
          运行时值由后端 _RUNTIME_UI 报告（不读 deploy.yaml），切换未重启时
          pendingUi 有值，这里给出提示与手动重启兜底。
          DOM 复用老 UI 设置页的 launcher-setting-panel / launcher-setting-row
          （entry-alas.css 里是 minmax(0,1fr) auto 两列；换成 cfg-group 的
          1fr 13rem 会把三个按钮挤成三行，实测过）。 */}
      <div id="pywebio-scope-group_ui1" className="cfg-group">
        <div className="launcher-setting-panel">
          <h2 className="alas-develop-section-title">界面版本</h2>
          <div className="launcher-setting-row">
            <div>
              <div className="launcher-setting-title">当前界面版本</div>
              <div className="launcher-setting-desc">
                点一下即切换并自动重启 AzurPilot（实例会自动保存并恢复）。老 UI 模式下新 UI 仍可通过{' '}
                <code>/new/</code> 试用；新 UI 模式下老 UI 的页面路由会被移除，不再在后台占用资源。
              </div>
              <div className="launcher-setting-status">
                {uiKnown
                  ? uiNow === 'new'
                    ? '新 UI（React）'
                    : '老 UI（PyWebIO）'
                  : '重启后可见（当前后端版本较旧）'}
                {uiKnown && pendingUi && pendingUi !== uiNow
                  ? ` → 待重启切换为${pendingUi === 'new' ? '新 UI' : '老 UI'}`
                  : ''}
              </div>
            </div>
            <div className="deploy-setting-toolbar-actions">
              <button
                className="btn btn-secondary"
                disabled={restarting || (uiNow === 'legacy' && !pendingUi)}
                onClick={() => switchUi('legacy')}
              >
                切换到老 UI
              </button>
              <button
                className="btn btn-primary"
                disabled={restarting || (uiNow === 'new' && !pendingUi)}
                onClick={() => switchUi('new')}
              >
                切换到新 UI
              </button>
              <button className="btn btn-danger" disabled={restarting} onClick={restartGui}>
                重启 AzurPilot
              </button>
            </div>
          </div>
        </div>
      </div>

      <div id="pywebio-scope-group_ui2" className="cfg-group">
        <div style={{ fontSize: '1.05rem', fontWeight: 600 }}>启动器</div>
        <div style={{ display: 'grid', gridAutoFlow: 'column', gridTemplateColumns: '1fr 13rem', alignItems: 'center', margin: '.125rem 0' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ ['--arg-title--' as string]: '1' } as React.CSSProperties}>开机自启动</div>
            <div style={{ ['--arg-help--' as string]: '1' } as React.CSSProperties}>随系统启动时自动运行 AzurPilot 启动器</div>
            <div style={{ ['--arg-help--' as string]: '1' } as React.CSSProperties}>{launcherText}</div>
          </div>
          <div style={{ minWidth: 0 }}>
            <input
              type="checkbox"
              disabled={!launcher?.request_local || !launcher?.autostart_supported || !launcher?.launcher_connected}
              checked={launcher?.autostart_enabled === true}
              onChange={e => toggleAutostart(e.target.checked)}
            />
          </div>
        </div>
      </div>

      <div id="pywebio-scope-group_ui3" className="cfg-group">
        <div className="deploy-setting-toolbar" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontSize: '1.05rem', fontWeight: 600, borderBottom: 'none' }}>部署设置（deploy.yaml）</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="log-btn" onClick={() => location.reload()}>刷新</button>
            <button className="btn btn-on" disabled={!Object.keys(dirty).length || saving} onClick={save}>
              {saving ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
        {(schema?.groups ?? []).map(g => (
          <div key={g.key}>
            <div style={{ fontSize: '1.05rem', fontWeight: 600, marginTop: 10 }}>{g.label}</div>
            {g.fields.map(f => (
              <div key={f.key} style={{ display: 'grid', gridAutoFlow: 'column', gridTemplateColumns: '1fr 13rem', alignItems: 'center', margin: '.125rem 0' }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ ['--arg-title--' as string]: '1' } as React.CSSProperties}>{f.label}</div>
                  {f.help && <div style={{ ['--arg-help--' as string]: '1' } as React.CSSProperties}>{f.help}</div>}
                </div>
                <div style={{ minWidth: 0 }}>
                  {f.type === 'select' && f.options?.length ? (
                    <select className="form-control" value={values[f.key] ?? ''} onChange={e => setValue(f.key, e.target.value)}>
                      {f.options.map(optionOf).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  ) : f.type === 'checkbox' || typeof (values[f.key] ?? f.value) === 'boolean' ? (
                    <input type="checkbox" checked={!!(values[f.key] ?? f.value)} onChange={e => setValue(f.key, e.target.checked)} />
                  ) : (
                    <input className="form-control" value={values[f.key] ?? f.value ?? ''} onChange={e => setValue(f.key, e.target.value)} />
                  )}
                </div>
              </div>
            ))}
          </div>
        ))}
        {!schema?.groups?.length && <div className="st-placeholder">部署设置加载中…（仅本机可用）</div>}
      </div>
    </div>
    </Shell>
  )
}
