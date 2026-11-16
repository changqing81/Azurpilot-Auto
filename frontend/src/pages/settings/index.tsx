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

  // 热切换界面版本：/newapi/ui/switch 写 deploy.yaml，
  // module/webui/hot_switch.py 在下一次请求按 mtime 感知 → 刷新即生效，无需重启。
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
        setMsg(`已切换到${target === 'new' ? '新 UI' : '老 UI'}，2 秒后刷新…`)
        setTimeout(() => location.reload(), 2000)
      } else {
        setMsg('切换失败: ' + (j.error || '未知原因'))
        setRestarting(false)
      }
    } catch (e) {
      setMsg('切换失败: ' + String(e))
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
      <div className="cfg-header">
        <button className="log-btn" onClick={() => navigate('/')}>← 返回总览</button>
        <h2>设置</h2>
        <button className="log-btn" onClick={() => navigate('/update')}>更新器</button>
        <button className="log-btn" onClick={() => navigate('/remote')}>远程控制</button>
        <span className="cfg-dirty-hint">{msg}</span>
      </div>

      {/* 热切换：不需要重启，写配置后刷新页面即生效。
          实现见 module/webui/hot_switch.py（容器 ASGI app 按 deploy.yaml 分流 /）。 */}
      <section className="ov-card cfg-group">
        <div className="cfg-row">
          <div className="cfg-title-wrap">
            <div className="arg-title">界面版本</div>
            <div className="arg-help">
              当前使用新 UI。切到老 UI 会立即生效（无需重启），刷新页面即可。
              老 UI 仍可通过 <code>/manage</code> 访问管理页；新 UI 在 <code>/new/</code> 始终可用。
            </div>
          </div>
          <div className="cfg-control" style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-secondary" disabled={restarting} onClick={() => switchUi('legacy')}>
              切到老 UI
            </button>
            <button className="btn btn-primary" disabled={restarting} onClick={() => switchUi('new')}>
              保持新 UI
            </button>
          </div>
        </div>
      </section>

      <section className="ov-card cfg-group">
        <div className="cfg-group-title">启动器</div>
        <div className="cfg-row">
          <div className="cfg-title-wrap">
            <div className="arg-title">开机自启动</div>
            <div className="arg-help">随系统启动时自动运行 AzurPilot 启动器</div>
            <div className="arg-help">{launcherText}</div>
          </div>
          <div className="cfg-control">
            <input
              type="checkbox"
              disabled={!launcher?.request_local || !launcher?.autostart_supported || !launcher?.launcher_connected}
              checked={launcher?.autostart_enabled === true}
              onChange={e => toggleAutostart(e.target.checked)}
            />
          </div>
        </div>
      </section>

      <section className="ov-card cfg-group">
        <div className="deploy-setting-toolbar" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div className="cfg-group-title" style={{ borderBottom: 'none' }}>部署设置（deploy.yaml）</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="log-btn" onClick={() => location.reload()}>刷新</button>
            <button className="ov-btn-primary" disabled={!Object.keys(dirty).length || saving} onClick={save}>
              {saving ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
        {(schema?.groups ?? []).map(g => (
          <div key={g.key}>
            <div className="cfg-group-title" style={{ marginTop: 10 }}>{g.label}</div>
            {g.fields.map(f => (
              <div key={f.key} className="cfg-row">
                <div className="cfg-title-wrap">
                  <div className="arg-title">{f.label}</div>
                  {f.help && <div className="arg-help">{f.help}</div>}
                </div>
                <div className="cfg-control">
                  {f.type === 'select' && f.options?.length ? (
                    <select className="cfg-input" value={values[f.key] ?? ''} onChange={e => setValue(f.key, e.target.value)}>
                      {f.options.map(optionOf).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  ) : f.type === 'checkbox' || typeof (values[f.key] ?? f.value) === 'boolean' ? (
                    <input type="checkbox" checked={!!(values[f.key] ?? f.value)} onChange={e => setValue(f.key, e.target.checked)} />
                  ) : (
                    <input className="cfg-input" value={values[f.key] ?? f.value ?? ''} onChange={e => setValue(f.key, e.target.value)} />
                  )}
                </div>
              </div>
            ))}
          </div>
        ))}
        {!schema?.groups?.length && <div className="st-placeholder">部署设置加载中…（仅本机可用）</div>}
      </section>
    </div>
    </Shell>
  )
}
