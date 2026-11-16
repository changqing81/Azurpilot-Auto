// 管理页：对齐老 UI 的「左侧二级菜单 + 右侧卡片」结构。
//
// 老 UI 的做法（module/webui/app_manage.py）：
//   ui_manage → set_aside → init_menu(name="ManageList")，菜单区换成 5 个入口：
//     配置名称 / 添加配置 / 导入配置 / 导入旧数据 / 导出数据
//   每张实例卡片：蓝方块图标 + 状态胶囊 + 实例名 + 插件名 + 三个等宽按钮
//
// 第一版我把这些全堆在内容区顶部（一排灰按钮 + 两张裸卡片），
// 长卿的反馈是「二级界面十分草率」—— 对比老 UI 确实差一大截。
// 这版按老 UI 的层次重做：二级菜单承载导航，内容区只放当前视图。
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shell } from '../../components/Shell'
import { apiGet } from '../../api/client'

type InstItem = { name: string; state: number; mod: string }
type ListResp = { instances: InstItem[]; templates: string[] }
type View = 'list' | 'new' | 'import' | 'export'

// 与老 UI app_manage.py:_INSTANCE_STATUS 同口径
const STATE_LABEL: Record<number, { text: string; cls: string }> = {
  1: { text: '运行中', cls: 'manage-card-badge-running' },
  2: { text: '待命中', cls: 'manage-card-badge-idle' },
  3: { text: '警告', cls: 'manage-card-badge-warn' },
  4: { text: '更新中', cls: 'manage-card-badge-update' },
}

// 实例图标（照搬老 UI 的蓝色方块 + 白色服务器轮廓）
const INSTANCE_ICON = (
  <svg viewBox="0 0 24 24" width="24" height="24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="4" width="18" height="7" rx="2" />
    <rect x="3" y="13" width="18" height="7" rx="2" />
    <line x1="7" y1="7.5" x2="7.01" y2="7.5" />
    <line x1="7" y1="16.5" x2="7.01" y2="16.5" />
  </svg>
)

export function ManagePage() {
  const navigate = useNavigate()
  const [data, setData] = useState<ListResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [view, setView] = useState<View>('list')
  const [busy, setBusy] = useState(false)
  // 新建表单
  const [newName, setNewName] = useState('')
  const [copyFrom, setCopyFrom] = useState('template-alas')
  // 导出范围
  const [exportScope, setExportScope] = useState('all')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await apiGet<ListResp>('/newapi/instances/detail')
      setData(r)
      if (r.instances.length && !r.instances.some(i => i.name === copyFrom)) {
        setCopyFrom(r.instances[0].name)
      }
    } catch (e) {
      setMsg('加载失败：' + String(e))
    } finally {
      setLoading(false)
    }
  }, [copyFrom])

  useEffect(() => { void load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const flash = (t: string) => { setMsg(t); setTimeout(() => setMsg(''), 5000) }

  const create = async () => {
    if (!newName.trim()) { flash('请输入实例名'); return }
    setBusy(true)
    try {
      const r = await fetch('/newapi/instance/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName.trim(), copy_from: copyFrom }),
      })
      const j = await r.json()
      if (j.success) {
        flash('已创建实例 ' + newName.trim())
        setNewName('')
        setView('list')
        await load()
      } else flash('创建失败：' + (j.error || '未知错误'))
    } catch (e) { flash('创建失败：' + String(e)) }
    finally { setBusy(false) }
  }

  const importFile = async (file: File) => {
    if (!window.confirm(`将导入「${file.name}」。\n\n文件名决定实例名，若同名实例已存在会被覆盖。\n确定继续吗？`)) return
    if (file.size > 1024 * 1024) { flash('文件超过 1MB 限制'); return }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch('/newapi/instance/import', { method: 'POST', body: fd })
      const j = await r.json()
      flash(j.success ? `已导入配置 ${j.name}（模块 ${j.mod}）` : '导入失败：' + (j.error || '未知错误'))
      if (j.success) { setView('list'); await load() }
    } catch (e) { flash('导入失败：' + String(e)) }
    finally { setBusy(false) }
  }

  const exportData = async (scope: string) => {
    setBusy(true)
    flash('正在打包数据（可能几十 MB，请稍候）…')
    try {
      const r = await fetch('/api/data/export' + (scope === 'all' ? '' : '/' + encodeURIComponent(scope)))
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const blob = await r.blob()
      const cd = r.headers.get('content-disposition') || ''
      const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
      const filename = m ? decodeURIComponent(m[1]) : 'AzurPilot-data.zip'
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = filename
      a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 10000)
      flash(`已导出 ${filename}（${Math.round(blob.size / 1024 / 1024)} MB）`)
    } catch (e) { flash('导出失败：' + String(e)) }
    finally { setBusy(false) }
  }

  const remove = async (name: string) => {
    if (!window.confirm(`确定删除实例 ${name} 吗？配置文件的删除不可撤销。`)) return
    setBusy(true)
    try {
      const r = await fetch('/newapi/instance/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      const j = await r.json()
      flash(j.success ? `已删除实例 ${name}` : '删除失败：' + (j.error || '未知错误'))
      if (j.success) await load()
    } catch (e) { flash('删除失败：' + String(e)) }
    finally { setBusy(false) }
  }

  // 二级菜单：与老 UI 的 5 个入口一一对应（导入旧数据暂缺，见 feature-coverage.md）
  const submenu = [
    { key: 'ManageList', name: '配置名称', onClick: () => setView('list') },
    { key: 'ManageNew', name: '添加配置', onClick: () => setView('new') },
    { key: 'ManageImport', name: '导入配置', onClick: () => setView('import') },
    { key: 'ManageExportData', name: '导出数据', onClick: () => setView('export') },
  ]
  const activeKey = view === 'list' ? 'ManageList' : view === 'new' ? 'ManageNew' : view === 'import' ? 'ManageImport' : 'ManageExportData'

  return (
    <Shell active={activeKey} title="管理配置文件" submenu={submenu}>
      <div id="pywebio-scope-content">
        <div style={{ padding: '10px .625rem' }}>
          {msg && (
            <div style={{ marginBottom: 12, fontSize: 13, opacity: .8 }}>{msg}</div>
          )}

          {/* ── 配置名称（列表）：卡片栅格，照老 UI 的 manage_config_list ── */}
          {view === 'list' && (
            <>
              {loading && <div className="st-placeholder">加载中…</div>}
              <div id="pywebio-scope-manage_config_list">
                {(data?.instances ?? []).map(inst => {
                  const st = STATE_LABEL[inst.state] ?? STATE_LABEL[2]
                  return (
                    <div
                      key={inst.name}
                      style={{ ['--manage-config-card--' as string]: '1' } as React.CSSProperties}
                    >
                      <div className="manage-card-head">
                        <span className="manage-card-icon">{INSTANCE_ICON}</span>
                        <span className={'manage-card-badge ' + st.cls}>{st.text}</span>
                      </div>
                      <p style={{ ['--manage-config-name--' as string]: '1' } as React.CSSProperties}>
                        {inst.name}
                      </p>
                      <p style={{ ['--manage-config-meta--' as string]: '1' } as React.CSSProperties}>
                        插件：{inst.mod}
                      </p>
                      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                        <button
                          className="btn btn-primary"
                          style={{ flex: 1 }}
                          onClick={() => navigate('/overview/' + encodeURIComponent(inst.name))}
                        >
                          概览
                        </button>
                        <button
                          className="btn btn-primary"
                          style={{ flex: 1 }}
                          disabled={busy}
                          onClick={() => { setExportScope(inst.name); setView('export') }}
                        >
                          导出
                        </button>
                        <button
                          className="btn btn-danger"
                          style={{ flex: 1 }}
                          disabled={busy}
                          onClick={() => remove(inst.name)}
                        >
                          删除
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
              {!loading && !(data?.instances ?? []).length && (
                <div className="st-placeholder">暂无实例</div>
              )}
            </>
          )}

          {/* ── 添加配置 ── */}
          {view === 'new' && (
            <div className="ov-card" style={{ padding: '20px 22px', maxWidth: 560 }}>
              <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>添加配置</div>
              <label style={{ display: 'block', marginBottom: 14 }}>
                <div style={{ fontSize: 13, marginBottom: 6 }}>配置名称</div>
                <input
                  className="deploy-setting-input"
                  value={newName}
                  placeholder="例如 alas2"
                  onChange={e => setNewName(e.target.value)}
                />
              </label>
              <label style={{ display: 'block', marginBottom: 20 }}>
                <div style={{ fontSize: 13, marginBottom: 6 }}>复制配置来源</div>
                <select
                  className="deploy-setting-select"
                  value={copyFrom}
                  onChange={e => setCopyFrom(e.target.value)}
                >
                  {(data?.templates ?? []).map(t => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                  {(data?.instances ?? []).map(i => (
                    <option key={i.name} value={i.name}>{i.name}</option>
                  ))}
                </select>
              </label>
              <button className="btn btn-primary" onClick={create} disabled={busy}>
                {busy ? '处理中…' : '确认'}
              </button>
            </div>
          )}

          {/* ── 导入配置 ── */}
          {view === 'import' && (
            <div className="ov-card" style={{ padding: '20px 22px', maxWidth: 560 }}>
              <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 10 }}>导入配置</div>
              <div style={{ fontSize: 13, opacity: .75, lineHeight: 1.7, marginBottom: 16 }}>
                选择 <code>.json</code> 配置文件。文件名决定实例名：<br />
                <code>alas.json</code> → 实例 alas；<code>alas2.alas.json</code> → 实例 alas2。<br />
                <b>同名实例会被覆盖</b>，请谨慎操作。
              </div>
              <label className="btn btn-primary" style={{ cursor: 'pointer', display: 'inline-block' }}>
                选择文件
                <input
                  type="file"
                  accept=".json"
                  style={{ display: 'none' }}
                  onChange={e => {
                    const f = e.target.files?.[0]
                    if (f) void importFile(f)
                    e.target.value = ''
                  }}
                />
              </label>
            </div>
          )}

          {/* ── 导出数据 ── */}
          {view === 'export' && (
            <div className="ov-card" style={{ padding: '20px 22px', maxWidth: 560 }}>
              <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 10 }}>导出数据</div>
              <div style={{ fontSize: 13, opacity: .75, lineHeight: 1.7, marginBottom: 16 }}>
                导出统计库与掉落记录（zip）。全量包保留 <code>config/</code>、<code>log/</code> 目录结构，
                可被「导入旧数据」原样还原。
              </div>
              <label style={{ display: 'block', marginBottom: 20 }}>
                <div style={{ fontSize: 13, marginBottom: 6 }}>导出范围</div>
                <select
                  className="deploy-setting-select"
                  value={exportScope}
                  onChange={e => setExportScope(e.target.value)}
                >
                  <option value="all">全部实例</option>
                  {(data?.instances ?? []).map(i => (
                    <option key={i.name} value={i.name}>{i.name}</option>
                  ))}
                </select>
              </label>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() => exportData(exportScope)}
              >
                {busy ? '打包中…' : '开始导出'}
              </button>
            </div>
          )}

          <div style={{ marginTop: 20 }}>
            <button className="btn btn-secondary" onClick={() => navigate('/')}>← 返回主页</button>
          </div>
        </div>
      </div>
    </Shell>
  )
}
