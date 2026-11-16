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

  // 切换界面版本：写 deploy.yaml，然后**提示重启**。
  //
  // 为什么不再做热切换：长卿 2026-11-16 拍板——
  //   「正式版老 UI 完全不需要，不要在后台运行。做不到热加载无所谓，
  //     给个切换按钮 + 重启 GUI 就行。」
  // 现在的实现是启动时按 Webui.UI 决定挂哪一套（UI=new 时把老 UI 页面路由
  // 整条摘掉），所以必须重启才生效 —— 这是有意为之，换来的是老 UI 不再
  // 在后台占资源、也不再有「两套 UI 并存」的复杂度。
  const [switchedTo, setSwitchedTo] = useState<string>('')
  const switchUi = async (target: 'legacy' | 'new') => {
    setRestarting(true)
    setMsg('正在切换界面版本…')
    try {
      const r = await fetch('/newapi/ui/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ui: target }),
      })
      const j = await r.json()
      if (j.success) {
        setSwitchedTo(target)
        setMsg(`已切换为${target === 'new' ? '新 UI' : '老 UI'}，请重启 AzurPilot 生效`)
      } else {
        setMsg('切换失败: ' + (j.error || '未知原因'))
      }
    } catch (e) {
      setMsg('切换失败: ' + String(e))
    } finally {
      setRestarting(false)
    }
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

      {/* 热切换：不需要重启，写配置后刷新页面即生效。
          实现见 module/webui/hot_switch.py（容器 ASGI app 按 deploy.yaml 分流 /）。 */}
      <div id="pywebio-scope-group_ui1" className="cfg-group">
        <div style={{ display: 'grid', gridAutoFlow: 'column', gridTemplateColumns: '1fr 13rem', alignItems: 'center', margin: '.125rem 0' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ ['--arg-title--' as string]: '1' } as React.CSSProperties}>界面版本</div>
            <div style={{ ['--arg-help--' as string]: '1' } as React.CSSProperties}>
              切换后需重启 AzurPilot 生效。老 UI 模式下新 UI 仍可通过 <code>/new/</code> 试用；
              新 UI 模式下老 UI 的页面路由会被移除，不再在后台占用资源。
            </div>
          </div>
          <div style={{ minWidth: 0, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="btn btn-secondary" disabled={restarting} onClick={() => switchUi('legacy')}>
              切到老 UI
            </button>
            <button className="btn btn-primary" disabled={restarting} onClick={() => switchUi('new')}>
              保持新 UI
            </button>
            {switchedTo && (
              <button className="btn btn-danger" disabled={restarting} onClick={restartGui}>
                立即重启生效
              </button>
            )}
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
