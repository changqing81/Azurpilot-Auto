// 图源管理弹窗：对齐老 UI app_home.py:_manage_wallpaper_sources 的四个操作。
//
// 老 UI 是四个 PyWebIO 弹窗（添加 / 启用禁用 / 删除 / 恢复默认）；
// REST 化后数据一个接口（GET /newapi/background/sources 全量），操作收敛为：
//   勾选=启用（开关批量保存）、添加（名称+API地址+直链开关+图片路径）、
//   删除（内置源不可删）、恢复默认（只重置内置源，自定义源保留）。
// 全部操作走 sources/save 或 sources/reset，一次落盘。
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

export type WallpaperSource = {
  key?: string
  type: string
  name: string
  url: string
  image_path?: string
  enabled: boolean
  direct?: boolean
  params?: Record<string, any>
}

const BUILTIN_KEYS = new Set(['lolicon', 'imgapi', 'yeelight', 'mukyu'])

export function WallpaperSourcesModal({ onClose, onChanged }: {
  onClose: () => void
  onChanged?: () => void
}) {
  const [sources, setSources] = useState<WallpaperSource[] | null>(null)
  const [error, setError] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [adding, setAdding] = useState(false)
  const [nName, setNName] = useState('')
  const [nUrl, setNUrl] = useState('')
  const [nDirect, setNDirect] = useState(false)
  const [nImagePath, setNImagePath] = useState('data[0].url')

  useEffect(() => {
    apiGet<{ success: boolean; sources: WallpaperSource[] }>('/newapi/background/sources', 8000)
      .then(r => setSources(r.sources ?? []))
      .catch(e => setError(String(e)))
  }, [])

  const save = async (next: WallpaperSource[]) => {
    setSaving(true)
    setError('')
    try {
      const res = await fetch('/newapi/background/sources/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sources: next }),
      })
      if (!res.ok) { setError('保存失败: HTTP ' + res.status); return }
      const j = await res.json().catch(() => null)
      if (!j || j.success === false) { setError('保存失败: ' + (j?.error || '未知')); return }
      setSources(next)
      setDirty(false)
      onChanged?.()
    } catch (e) {
      setError('保存失败: ' + String(e))
    } finally {
      setSaving(false)
    }
  }

  const resetDefault = async () => {
    if (!window.confirm('恢复默认图源设置？内置源将重置并重新启用，自定义源保留。')) return
    setSaving(true)
    try {
      const res = await fetch('/newapi/background/sources/reset', { method: 'POST' })
      const j = await res.json().catch(() => null)
      if (!j || j.success === false) { setError('恢复失败: ' + (j?.error || '未知')); return }
      const r = await apiGet<{ sources: WallpaperSource[] }>('/newapi/background/sources', 8000)
      setSources(r.sources ?? [])
      setDirty(false)
      onChanged?.()
    } catch (e) {
      setError('恢复失败: ' + String(e))
    } finally {
      setSaving(false)
    }
  }

  const removeSource = (idx: number) => {
    const s = sources?.[idx]
    if (!s) return
    if (BUILTIN_KEYS.has(s.key || '')) { setError('内置图源不能删除（可以禁用）'); return }
    if (!window.confirm('删除图源「' + s.name + '」？')) return
    const next = (sources || []).filter((_, i) => i !== idx)
    void save(next)
  }

  const addSource = () => {
    if (!nName.trim() || !nUrl.trim()) { setError('名称和 API 地址不能为空'); return }
    const next = [...(sources || []), {
      key: 'custom_' + Date.now().toString(36),
      type: 'custom',
      name: nName.trim(),
      url: nUrl.trim(),
      image_path: nDirect ? '' : nImagePath,
      direct: nDirect,
      enabled: true,
    }]
    setAdding(false)
    setNName('')
    setNUrl('')
    setNDirect(false)
    setNImagePath('data[0].url')
    void save(next)
  }

  return (
    <div
      className="alas-modal-overlay"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10000,
        background: 'rgba(0,0,0,.45)',
        display: 'grid',
        placeItems: 'center',
      }}
      onClick={e => { if (e.target === e.currentTarget && !dirty) onClose() }}
    >
      <div
        className="card alas-src-modal"
        style={{
          width: 'min(560px, 92vw)',
          maxHeight: '86vh',
          overflow: 'auto',
          padding: '1rem 1.25rem',
          borderRadius: 12,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.75rem' }}>
          <h5 style={{ margin: 0 }}>图源管理</h5>
          <button className="btn btn-light btn-sm" onClick={onClose}>关闭</button>
        </div>
        {error && <div style={{ color: '#dc3545', margin: '.4rem 0' }}>{error}</div>}
        {!sources && <div>加载中…</div>}
        {sources && sources.length === 0 && <div className="text-muted">暂无图源</div>}
        {sources && sources.map((s, i) => (
          <div key={s.key || i} style={{ display: 'flex', gap: '.5rem', alignItems: 'center', padding: '.3rem 0', borderBottom: '1px solid rgba(128,128,128,.15)' }}>
            <input
              type="checkbox"
              checked={!!s.enabled}
              title="勾选=启用该图源"
              onChange={e => {
                const next = (sources || []).map((x, xi) => xi === i ? { ...x, enabled: e.target.checked } : x)
                setSources(next)
                setDirty(true)
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>
                {s.name}
                {BUILTIN_KEYS.has(s.key || '') && <span className="badge bg-secondary" style={{ marginLeft: '.4rem', fontSize: '.68rem' }}>内置</span>}
                {s.direct && <span className="badge bg-info" style={{ marginLeft: '.4rem', fontSize: '.68rem' }}>直链</span>}
                {!s.enabled && <span className="badge bg-dark" style={{ marginLeft: '.4rem', fontSize: '.68rem' }}>已禁用</span>}
              </div>
              <div style={{ fontSize: '.75rem', color: 'var(--bs-secondary-color, #6c757d)', wordBreak: 'break-all' }}>{s.url}</div>
            </div>
            {!BUILTIN_KEYS.has(s.key || '') && (
              <button className="btn btn-sm btn-outline-danger" onClick={() => removeSource(i)}>删除</button>
            )}
          </div>
        ))}
        {sources && (
          <div style={{ marginTop: '.75rem', display: 'flex', gap: '.5rem', flexWrap: 'wrap' }}>
            <button className="btn btn-sm btn-primary" onClick={() => setAdding(a => !a)}>添加图源</button>
            <button className="btn btn-sm btn-secondary" disabled={saving} onClick={resetDefault}>恢复默认图源设置</button>
            <button
              className="btn btn-sm btn-success"
              disabled={!dirty || saving}
              onClick={() => void save(sources || [])}
            >
              {saving ? '保存中…' : '保存更改'}
            </button>
            <span className="text-muted" style={{ fontSize: '.75rem', alignSelf: 'center' }}>
              勾选=启用该图源，取消勾选=禁用；改动后点「保存更改」一次落盘。
            </span>
          </div>
        )}
        {adding && (
          <div style={{ marginTop: '.75rem', padding: '.75rem', border: '1px solid rgba(128,128,128,.25)', borderRadius: 8, display: 'grid', gap: '.4rem' }}>
            <input className="form-control form-control-sm" placeholder="名称（如：我的图源）" value={nName} onChange={e => setNName(e.target.value)} />
            <input className="form-control form-control-sm" placeholder="API 地址" value={nUrl} onChange={e => setNUrl(e.target.value)} />
            <label style={{ fontSize: '.85rem', display: 'flex', gap: '.35rem', alignItems: 'center' }}>
              <input type="checkbox" checked={nDirect} onChange={e => setNDirect(e.target.checked)} />
              直链图源（响应本体就是图片，不需要 JSON 提取）
            </label>
            {!nDirect && (
              <input className="form-control form-control-sm" placeholder="图片地址 JSON 提取路径（如 data[0].url）" value={nImagePath} onChange={e => setNImagePath(e.target.value)} />
            )}
            <div style={{ display: 'flex', gap: '.5rem' }}>
              <button className="btn btn-sm btn-primary" onClick={addSource}>保存新图源</button>
              <button className="btn btn-sm btn-light" onClick={() => setAdding(false)}>取消</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
