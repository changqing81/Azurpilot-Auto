// 管理页：补老 UI /manage 的缺口（module/webui/app_manage.py）。
//
// 为什么需要它：老 UI 侧栏有「管理」按钮（ui_manage），可增删实例、导入导出配置。
// 这个页面全走 PyWebIO WS 回调，没有 REST。关掉老 UI（UI=new）后点「管理」会 404。
//
// 本轮只迁移「最小可用」三件事（优先级来自「关掉老 UI 也不缺功能」）：
//   ① 实例列表（名称 / 运行状态 / 模块名）
//   ② 新建实例（从模板或现有实例复制，校验规则逐条照抄老 UI）
//   ③ 删除实例（两道保护：不能删最后一个、不能删运行中的）
//
// 未迁移（明确记录）：配置导入/导出、任务摘要展开、legacy 导入。
// 这些在 feature-coverage.md 里有清单，后续补。
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shell } from '../../components/Shell'
import { apiGet } from '../../api/client'

type InstItem = { name: string; state: number; mod: string }
type ListResp = { instances: InstItem[]; templates: string[] }

// 状态文案与老 UI app_manage.py:_INSTANCE_STATUS 同口径
const STATE_LABEL: Record<number, { text: string; color: string }> = {
  1: { text: '运行中', color: '#2eaa7b' },
  2: { text: '待命中', color: '#8a919e' },
  3: { text: '警告', color: '#f0a500' },
  4: { text: '更新中', color: '#4e8ef7' },
}

export function ManagePage() {
  const navigate = useNavigate()
  const [data, setData] = useState<ListResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [newName, setNewName] = useState('')
  const [copyFrom, setCopyFrom] = useState('')
  const [busy, setBusy] = useState(false)
  const [showCreate, setShowCreate] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await apiGet<ListResp>('/newapi/instances/detail')
      setData(r)
      if (!copyFrom && r.instances.length) setCopyFrom(r.instances[0].name)
    } catch (e) {
      setMsg('加载失败：' + String(e))
    } finally {
      setLoading(false)
    }
  }, [copyFrom])

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const flash = (t: string) => {
    setMsg(t)
    setTimeout(() => setMsg(''), 5000)
  }

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
        setShowCreate(false)
        await load()
      } else {
        flash('创建失败：' + (j.error || '未知错误'))
      }
    } catch (e) {
      flash('创建失败：' + String(e))
    } finally {
      setBusy(false)
    }
  }

  // ── 配置导入（对应老 UI app_manage.py:_import）──
  // 文件名决定实例名/模块名，覆盖同名实例（老 UI 有 OverrideWarning 提示）
  const importFile = async (file: File) => {
    if (!window.confirm(
      `将导入「${file.name}」。\n\n文件名决定实例名，若同名实例已存在会被**覆盖**。\n确定继续吗？`,
    )) return
    if (file.size > 1024 * 1024) { flash('文件超过 1MB 限制'); return }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch('/newapi/instance/import', { method: 'POST', body: fd })
      const j = await r.json()
      flash(j.success ? `已导入配置 ${j.name}（模块 ${j.mod}）` : '导入失败：' + (j.error || '未知错误'))
      if (j.success) await load()
    } catch (e) {
      flash('导入失败：' + String(e))
    } finally {
      setBusy(false)
    }
  }

  // ── 数据导出（复用老 UI 现成的 /api/data/export）──
  // 说明见 api.py:2056-2088：全量包保留 config/ log/ 目录结构，可被「导入旧数据」还原
  const exportData = async (scope: 'all' | string) => {
    setBusy(true)
    flash('正在打包数据（约 30MB，请稍候）…')
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
    } catch (e) {
      flash('导出失败：' + String(e))
    } finally {
      setBusy(false)
    }
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
    } catch (e) {
      flash('删除失败：' + String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell active="Manage" title="管理">
      <div id="pywebio-scope-content">
        <div style={{ padding: '10px .625rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <h1 style={{ fontSize: 20, margin: 0 }}>管理</h1>
            <button className="btn btn-primary" onClick={() => setShowCreate(v => !v)}>
              {showCreate ? '取消' : '新建实例'}
            </button>
            <button className="btn btn-secondary" onClick={() => load()} disabled={loading}>
              刷新
            </button>
            <label className="btn btn-secondary" style={{ cursor: 'pointer', margin: 0 }}>
              导入配置
              <input
                type="file"
                accept=".json"
                style={{ display: 'none' }}
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) void importFile(f)
                  e.target.value = ''  // 允许重复选同一文件
                }}
              />
            </label>
            <button
              className="btn btn-secondary"
              disabled={busy}
              onClick={() => exportData('all')}
            >
              导出全部数据
            </button>
            <span style={{ marginLeft: 'auto', fontSize: 12, opacity: .75 }}>{msg}</span>
          </div>

          {showCreate && (
            <div className="ov-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-end' }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={{ fontSize: 12, opacity: .75 }}>新实例名</span>
                  <input
                    className="deploy-setting-input"
                    style={{ width: 220, minHeight: 38 }}
                    value={newName}
                    placeholder="例如 alas2"
                    onChange={e => setNewName(e.target.value)}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={{ fontSize: 12, opacity: .75 }}>复制配置来源</span>
                  <select
                    className="deploy-setting-select"
                    style={{ width: 'auto', minWidth: 180, minHeight: 38 }}
                    value={copyFrom}
                    onChange={e => setCopyFrom(e.target.value)}
                  >
                    {(data?.instances ?? []).map(i => (
                      <option key={i.name} value={i.name}>{i.name}</option>
                    ))}
                  </select>
                </label>
                <button className="ov-btn-primary" onClick={create} disabled={busy}>
                  {busy ? '处理中…' : '创建'}
                </button>
              </div>
              <div style={{ fontSize: 12, opacity: .6, marginTop: 8 }}>
                规则与老 UI 一致：不能重名、不能含 . \ / : * ? &quot; &apos; &lt; &gt; |、不能以 template 开头。
              </div>
            </div>
          )}

          {loading && <div className="st-placeholder">加载中…</div>}

          {/* 卡片 DOM 必须与老 UI app_manage.py:_show_list 同构：
              外层带 style 钩子 --manage-config-card--（entry-alas.css:527 等靠它选中），
              卡头用真实类 .manage-card-head（注释说明：用钩子会落到 svg 上导致撑破）。
              列表容器 #pywebio-scope-manage_config_list 也照抄（栅格 auto-fill 340px）。 */}
          <div id="pywebio-scope-manage_config_list">
            {(data?.instances ?? []).map(inst => {
              const st = STATE_LABEL[inst.state] ?? STATE_LABEL[2]
              return (
                <div
                  key={inst.name}
                  style={{ ['--manage-config-card--' as string]: '1' } as React.CSSProperties}
                >
                  <div className="manage-card-head">
                    <span className="manage-card-badge" style={{ color: st.color }}>
                      {st.text}
                    </span>
                  </div>
                  <p style={{ ['--manage-config-name--' as string]: '1' } as React.CSSProperties}>
                    {inst.name}
                  </p>
                  <p style={{ ['--manage-config-meta--' as string]: '1' } as React.CSSProperties}>
                    模块：{inst.mod}
                  </p>
                  <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                    <button
                      className="btn btn-secondary"
                      onClick={() => navigate('/overview/' + encodeURIComponent(inst.name))}
                    >
                      打开总览
                    </button>
                    <button
                      className="btn btn-secondary"
                      disabled={busy}
                      onClick={() => exportData(inst.name)}
                    >
                      导出数据
                    </button>
                    <button
                      className="btn btn-danger"
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

          <div style={{ marginTop: 16 }}>
            <button className="btn btn-secondary" onClick={() => navigate('/')}>← 返回主页</button>
          </div>
        </div>
      </div>
    </Shell>
  )
}
