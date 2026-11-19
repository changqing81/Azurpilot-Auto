// 心智计算器面板：挂载老 UI 渲染器产出的 HTML + 行编辑/删除交互。
//
// 老 UI（app_mind_calculator_tab.py + mind_calculator_view.py）：
//   说明条 + 四张统计卡 + 稀有度汇总表 + 船舶清单表（分页 + 行编辑弹窗 +
//   删除 + 批量编辑）。清单行的 data-mc-edit / data-mc-del / data-mc-select
//   钩子由点击桥转发到 Mixin。REST 化：点击钩子 → 弹窗 → POST
//   /newapi/mind/{name}/ships 全量保存（revision 乐观锁在服务端）。
// 容器 id 必须是 #pywebio-scope-ft_mind（fleet-tech-alas.css 后代选择器契约）。
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiGet, panelPathWithParams } from '../../api/client'
import { useStatText } from './i18n'

type MindData = {
  success: boolean
  intro_html?: string
  stats_html?: string
  summary_html?: string
  list_html?: string
  total?: number
  pages?: number
  page?: number
  error?: string
}

type MindShipsPayload = {
  ships: { name: string; level: number; status: string }[]
  result?: any
  updated_at?: string
  total?: number
  error?: string
}

export function MindPanel({ name }: { name: string }) {
  const text = useStatText()
  const [data, setData] = useState<MindData | null>(null)
  const [err, setErr] = useState('')
  const [editMode, setEditMode] = useState(false)
  const [saving, setSaving] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [notice, setNotice] = useState('')
  // 批量编辑（老 UI MindBatchMixin 的 REST 等价物）：勾选只活在浏览器里，
  // 点「批量修改已选/修改全部筛选结果」才弹表单、保存时一次性提交。
  // 行编辑/删除确认弹窗（老 UI EditTitle popup；替代浏览器原生 prompt/confirm）
  const [editTarget, setEditTarget] = useState<null | {
    idx?: number; name: string; level?: string; status?: string; confirmDelete?: number
  }>(null)
  const [editSaving, setEditSaving] = useState(false)
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchAll, setBatchAll] = useState(false)
  const [batchSel, setBatchSel] = useState<number[]>([])
  const [batchSaving, setBatchSaving] = useState(false)
  const [batchNotice, setBatchNotice] = useState('')
  const [batchForm, setBatchForm] = useState<{ status: string; level_mode: string; level: string }>({
    status: 'keep', level_mode: 'keep', level: '',
  })
  // 本页已选数（受控）：React 重渲染不能丢勾选状态 —— 老桥的 DOM 计数在
  // React 重渲染后会被重置（实测过），计数必须放在 state 里。
  const [selCount, setSelCount] = useState(0)
  const pageRef = useRef(0)
  const shipsRef = useRef<Record<string, any>[]>([])
  const revRef = useRef('')

  const load = useCallback((page: number, edit: boolean, opts?: { soft?: boolean }) => {
    // soft=true：不卸载已渲染内容，只原地替换 list_html —— 首屏、翻页、切批量
    // 模式都走这条；整面板「加载中…」闪烁（长卿 2026-11-19）就是 setData(null)
    // 把说明框/统计卡一起卸载重挂造成的。
    if (!opts?.soft) { setData(null); setErr('') }
    // 远控中继剥 query，分页与编辑态必须走路径段（否则翻页点不动）
      apiGet<MindData>(panelPathWithParams(name, 'mind', { page, edit: edit ? '1' : '0' }), 25000)
      .then(r => {
        if (r.success === false) { setErr(r.error || '加载失败'); return }
        setData(prev => (prev && opts?.soft ? { ...prev, list_html: r.list_html, page: r.page, pages: r.pages, total: r.total } : r))
        pageRef.current = r.page ?? 0
        setSelCount(0)
      })
      .catch(e => { if (!opts?.soft) setErr(String(e)) })
    if (!opts?.soft) {
      // 拉一份原始清单（行编辑需要完整 ships 全量回传）；soft 模式下清单没变不必重拉
      apiGet<any>(`/newapi/stat/${name}/mind`, 25000)
        .then(r => {
          shipsRef.current = (r.ships || []).map((s: any) => ({ ...s }))
        })
        .catch(() => {})
    }
  }, [name])

  useEffect(() => { load(0, false) }, [load])

  const importFileRef = useRef<HTMLInputElement>(null)

  /** 打开批量弹窗（老 UI _mc_batch_open）：selected=本页勾选，all=全部筛选结果。 */
  const openBatch = (all: boolean) => {
    const host = hostRef.current
    const checked = all ? [] : Array.from(host?.querySelectorAll('[data-mc-select]:checked') ?? [])
      .map(el => Number((el as HTMLElement).getAttribute('data-mc-select')))
    if (!all && (!checked.length || checked.some(n => Number.isNaN(n)))) {
      setBatchNotice(text('McSelectionHint') || '请先勾选要修改的舰船')
      return
    }
    setBatchSel(checked)
    setBatchAll(all)
    if (!all) setSelCount(checked.length)
    setBatchNotice('')
    setBatchForm({ status: 'keep', level_mode: 'keep', level: '' })
    setBatchOpen(true)
  }

  /** 行编辑保存（老 UI 单行三态编辑）。 */
  const submitRowEdit = async () => {
    if (!editTarget || editTarget.idx === undefined) return
    const idx = editTarget.idx
    const ship = shipsRef.current[idx]
    if (!ship) return
    setEditSaving(true)
    try {
      const st = editTarget.status || 'included'
      const next = shipsRef.current.slice()
      next[idx] = {
        ...ship,
        level: Number(editTarget.level) || 0,
        excluded: st === 'excluded',
        review: st === 'review',
      }
      const ok = await saveShips(next)
      if (ok) setEditTarget(null)
    } finally {
      setEditSaving(false)
    }
  }

  /** 行删除确认（老 UI confirm 的主题化等价物）。 */
  const submitRowDelete = async () => {
    if (!editTarget || editTarget.confirmDelete === undefined) return
    const idx = editTarget.confirmDelete
    setEditSaving(true)
    try {
      const next = shipsRef.current.filter((_, i) => i !== idx)
      const ok = await saveShips(next)
      if (ok) setEditTarget(null)
    } finally {
      setEditSaving(false)
    }
  }

  /** 批量提交：前端按老 UI apply_batch 语义改 ships，再全量保存（revision 锁在服务端）。 */
  const submitBatch = async () => {
    const changesStatus = batchForm.status !== 'keep'
    const changesLevel = batchForm.level_mode === 'set' && batchForm.level !== ''
    if (!changesStatus && !changesLevel) { setBatchNotice(text('McNoChanges') || '请先选择要修改的字段'); return }
    if (changesStatus && batchForm.status === 'included'
      && (batchAll ? shipsRef.current : batchSel.map(i => shipsRef.current[i]))
        .some(s => s && !(Number(s.level) > 0))) {
      setBatchNotice(text('McBatchLevelRequired') || '有舰船等级未知，无法批量设为计入')
      return
    }
    setBatchSaving(true)
    setBatchNotice('')
    try {
      const target = shipsRef.current
      const next = target.map((s, i) => {
        const inScope = batchAll ? true : batchSel.includes(i)
        if (!inScope) return s
        const out: any = { ...s }
        if (changesStatus) {
          out.excluded = batchForm.status === 'excluded'
          out.review = batchForm.status === 'review'
        }
        if (changesLevel) out.level = Number(batchForm.level) || 0
        return out
      })
      const ok = await saveShips(next)
      if (ok) setBatchOpen(false)
    } finally {
      setBatchSaving(false)
    }
  }

  const handleImportFile = async (ev: React.ChangeEvent<HTMLInputElement>) => {
    const file = ev.target.files?.[0]
    if (!file) return
    const raw = await file.text()
    let rows: Record<string, any>[] = []
    try {
      if (file.name.toLowerCase().endsWith(".csv")) {
        const table = raw.split(/\r?\n/).filter(l => l.trim())
        const header = table[0].split(",").map(h => h.trim().toLowerCase())
        rows = table.slice(1).map(line => {
          const cells = line.split(",")
          const obj: Record<string, any> = {}
          header.forEach((h, i) => { obj[h] = cells[i] })
          return obj
        })
      } else {
        const parsed = JSON.parse(raw)
        rows = Array.isArray(parsed) ? parsed : parsed.rows || []
      }
    } catch (e) {
      setNotice("导入失败: 文件解析错误 " + String(e))
      return
    }
    try {
      const res = await fetch("/newapi/mind/" + name + "/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rows }),
      })
      const j = await res.json().catch(() => null)
      if (!j || j.error) { setNotice("导入失败: " + ((j && j.error) || ("HTTP " + res.status))); return }
      setNotice("导入完成：新增 " + (j.added ?? 0) + "、更新 " + (j.updated ?? 0) + "，清单共 " + (j.total ?? 0) + " 艘")
      load(pageRef.current, editMode)
    } catch (e) {
      setNotice("导入失败: " + String(e))
    } finally {
      ev.target.value = ""
    }
  }
  const startScan = async () => {
    const low = Number((document.getElementById("mc-min-level") as HTMLInputElement).value)
    const high = Number((document.getElementById("mc-max-level") as HTMLInputElement).value)
    setScanning(true)
    try {
      const res = await fetch("/newapi/dev/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "mind_scan", instance: name, min_level: low, max_level: high }),
      })
      const j = await res.json().catch(() => null)
      if (j && j.success) setNotice(text("McScanStarted") || "船坞扫描已启动，完成后会自动保存并载入计算结果。")
      else setNotice("启动失败: " + ((j && j.error) || ("HTTP " + res.status)))
    } catch (e) {
      setNotice("启动失败: " + String(e))
    } finally {
      setTimeout(() => setScanning(false), 4000)
    }
  }
  // 保存（全量回传；服务端做 revision 校验）
  const saveShips = useCallback(async (ships: Record<string, any>[]) => {
    setSaving(true)
    setNotice('')
    try {
      const res = await fetch(`/newapi/mind/${name}/ships`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ships, expect_revision: revRef.current }),
      })
      if (!res.ok) { setNotice('保存失败: HTTP ' + res.status); return false }
      const j: MindShipsPayload = await res.json().catch(() => null)
      if (!j) { setNotice('保存失败: 无法解析响应'); return false }
      if (j.error) { setNotice('保存失败: ' + j.error); return false }
      setNotice('')
      load(pageRef.current, editMode, { soft: true })
      return true
    } catch (e) {
      setNotice('保存失败: ' + String(e))
      return false
    } finally {
      setSaving(false)
    }
  }, [name, editMode, load])

  // 事件委托：清单表的 data-mc-edit / data-mc-del / 分页按钮
  const hostRef = useRef<HTMLDivElement>(null)
  // 勾选桥（老 UI mind_batch_bridge_script 的前端版）：change 委托维护
  // 全选框三态与「本页已选 N」计数、批量按钮 disabled。
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const onChange = (ev: Event) => {
      const target = ev.target as HTMLInputElement
      if (!target || !target.matches('[data-mc-select], [data-mc-select-page]')) return
      const boxes = Array.from(host.querySelectorAll('[data-mc-select]')) as HTMLInputElement[]
      if (target.matches('[data-mc-select-page]')) {
        boxes.forEach(b => { b.checked = target.checked })
      }
      const head = host.querySelector('[data-mc-select-page]') as HTMLInputElement | null
      const checkedCount = boxes.filter(b => b.checked).length
      if (head) head.indeterminate = checkedCount > 0 && checkedCount < boxes.length
      setSelCount(checkedCount)
      // 行高亮（老 UI .ft-selected）
      boxes.forEach(b => { b.closest('tr')?.classList.toggle('ft-selected', b.checked) })
    }
    host.addEventListener('change', onChange)
    return () => host.removeEventListener('change', onChange)
  }, [data])
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const onClick = (ev: Event) => {
      const t = ev.target as HTMLElement
      const editBtn = t.closest('[data-mc-edit]') as HTMLElement | null
      const delBtn = t.closest('[data-mc-del]') as HTMLElement | null
      if (editBtn) {
        ev.preventDefault()
        const idx = Number(editBtn.getAttribute('data-mc-edit'))
        const ship = shipsRef.current[idx]
        if (!ship) return
        // 行编辑弹窗（老 UI EditTitle popup 的 REST 等价物）：window.prompt 是
        // 浏览器原生 UI，不吃页面主题 —— 高级材质暗色下是白块（长卿 2026-11-19）。
        setEditTarget({ idx, name: String(ship.name ?? ''), level: String(ship.level ?? ''), status: ship.review ? 'review' : ship.excluded ? 'excluded' : 'included' })
        return
      }
      if (delBtn) {
        ev.preventDefault()
        const idx = Number(delBtn.getAttribute('data-mc-del'))
        const ship = shipsRef.current[idx]
        if (!ship) return
        setEditTarget({ confirmDelete: idx, name: String(ship.name ?? '') })
        return
      }
      // 批量工具条按钮（老 UI data-mc-batch：selected=本页勾选 / all=全部筛选结果）
      const batchBtn = t.closest('[data-mc-batch]') as HTMLElement | null
      if (batchBtn) {
        ev.preventDefault()
        openBatch(batchBtn.getAttribute('data-mc-batch') === 'all')
        return
      }
      // 分页
      const pg = t.closest('[data-mc-page]') as HTMLElement | null
      if (pg) {
        ev.preventDefault()
        const to = Number(pg.getAttribute('data-mc-page'))
        if (!Number.isNaN(to) && to >= 0) load(to, editMode)
      }
    }
    host.addEventListener('click', onClick)
    return () => host.removeEventListener('click', onClick)
  }, [editMode, load, saveShips, openBatch])

  if (err) return <div className="st-placeholder">{err}</div>

  return (
    <div id="pywebio-scope-ft_mind" ref={hostRef}>
      {/* 老 UI app_mind_calculator_tab.py:138-141 的 scope 顺序：
          ft_mind_overview（说明条+统计卡+汇总表） → ft_mind_toolbar（扫描） →
          ft_mind_filters（批量编辑/导入导出） → ft_mind_list（清单） → 分页。
          2026-11-19 长卿指出扫描/批量编辑按钮跑到了说明框上面，按老 UI 顺序归位：
          按钮在说明框的下面。 */}
      {data && (
        <>
          <div id="pywebio-scope-ft_mind_overview">
            {data.intro_html && <div dangerouslySetInnerHTML={{ __html: data.intro_html }} />}
            {data.stats_html && <div dangerouslySetInnerHTML={{ __html: data.stats_html }} />}
            {data.summary_html && <div dangerouslySetInnerHTML={{ __html: data.summary_html }} />}
          </div>
          {/* 扫描工具栏（老 UI ft_mind_toolbar：扫描范围 + 扫描按钮 + 提示），在说明框下面 */}
          <div
            id="pywebio-scope-ft_mind_toolbar"
            style={{ display: "flex", gap: ".5rem", alignItems: "center", flexWrap: "wrap", margin: "0 0 .5rem" }}
          >
            <span className="ft-toolbar-label">{text("McMinLevel") || "最低扫描等级"}</span>
            <input className="form-control" type="number" min={1} max={125} defaultValue={95} id="mc-min-level" style={{ width: "5rem" }} />
            <span className="ft-toolbar-label">{text("McMaxLevel") || "最高扫描等级"}</span>
            <input className="form-control" type="number" min={1} max={125} defaultValue={125} id="mc-max-level" style={{ width: "5rem" }} />
            <button
              className="btn btn-primary"
              disabled={scanning}
              onClick={startScan}
            >
              {scanning ? text("McScanning") || "正在扫描船坞" : text("McScan") || "自动扫描船坞"}
            </button>
            <span className="ft-hint">{text("McScanHint") || "自动扫描支持国服模拟器，按等级降序读取船坞三行的舰名与等级，完成后保存清单并计算。"}</span>
          </div>
          {/* 操作行（老 UI ft_mind_filters：批量编辑/导出/导入），同样在说明框下面 */}
          <div id="pywebio-scope-ft_mind_filters" className="ft-toolbar" style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap', margin: '0 0 .5rem' }}>
            <button
              className={'btn ' + (editMode ? 'btn-primary' : 'btn-secondary')}
              onClick={() => { const next = !editMode; setEditMode(next); load(pageRef.current, next, { soft: true }) }}
            >
              {editMode ? text('McBatchExit') || '退出批量' : text('McBatchEdit') || '批量编辑'}
            </button>
            {saving && <span className="ft-hint">保存中…</span>}
            {notice && <span style={{ color: 'var(--ft-danger, #dc3545)' }}>{notice}</span>}
            <a
              href={`/newapi/mind/${name}/export`}
              download
              className="btn btn-secondary"
              style={{ textDecoration: 'none' }}
            >
              {text('McExportJson') || '导出 JSON'}
            </a>
            <input
              ref={importFileRef}
              type="file"
              accept=".json,.csv"
              style={{ display: "none" }}
              onChange={e => void handleImportFile(e)}
            />
            <button className="btn btn-secondary" onClick={() => importFileRef.current?.click()}>
              {text("McImport") || "导入舰船清单"}
            </button>
          </div>
          <div id="pywebio-scope-ft_mind_list">
            {/* 批量工具条（老 UI _mc_batch_toolbar：已选计数 + 两个批量入口 + 提示） */}
            {editMode && (
              <div className="ft-batch-toolbar" style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap', margin: '0 0 .4rem' }}>
                <span>{text('McSelected') || '已选'} <b>{selCount}</b></span>
                <button
                  type="button"
                  className="ft-edit-btn"
                  data-mc-batch="selected"
                  disabled={selCount === 0}
                  onClick={() => openBatch(false)}
                >{text('McBatchSelected') || '批量修改已选'}</button>
                <button
                  type="button"
                  className="ft-edit-btn"
                  data-mc-batch="all"
                  onClick={() => openBatch(true)}
                >{(text('McBatchAll') || '修改全部筛选结果（{count} 艘）').replace('{count}', String(data.total ?? 0))}</button>
                <small style={{ opacity: .75 }}>{text('McSelectionHint') || '勾选只对本页有效；翻页、筛选或刷新后清空。跨页请用「修改全部筛选结果」。'}</small>
              </div>
            )}
            {data.list_html && <div dangerouslySetInnerHTML={{ __html: data.list_html }} />}
          </div>
          {/* 行编辑 / 删除确认弹窗（老 UI EditTitle popup；暗色下跟随主题变量） */}
          {editTarget && (
            <div
              className="alas-modal-overlay"
              style={{ position: "fixed", inset: 0, zIndex: 10000, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center" }}
              onClick={e => { if (e.target === e.currentTarget && !editSaving) setEditTarget(null) }}
            >
              <div className="card" style={{ width: "min(460px, 92vw)", maxHeight: "86vh", overflow: "auto", padding: "1rem 1.25rem", borderRadius: 12 }}>
                {editTarget.confirmDelete !== undefined ? (
                  <>
                    <h5 style={{ margin: 0, marginBottom: ".5rem" }}>{text('McDelete') || '删除'}</h5>
                    <p style={{ margin: ".4rem 0 .8rem" }}>{text('McEditTitle') && editTarget.name ? editTarget.name : editTarget.name} —— 确认删除？</p>
                    <div style={{ display: "flex", gap: ".5rem", justifyContent: "flex-end" }}>
                      <button className="btn btn-sm btn-light" disabled={editSaving} onClick={() => setEditTarget(null)}>{text('McCancel') || '取消'}</button>
                      <button className="btn btn-sm btn-danger" disabled={editSaving} onClick={() => void submitRowDelete()}>{text('McDelete') || '删除'}</button>
                    </div>
                  </>
                ) : (
                  <>
                    <h5 style={{ margin: 0, marginBottom: ".25rem" }}>
                      {(text('McEditTitle') || '编辑舰船')}
                    </h5>
                    <p style={{ margin: ".1rem 0 .6rem", opacity: .8 }}>{editTarget.name}</p>
                    <div style={{ display: "grid", gap: ".5rem" }}>
                      <div style={{ display: "grid", gridTemplateColumns: "90px 1fr", alignItems: "center" }}>
                        <span>{text('McLevel') || '等级'}</span>
                        <input
                          className="form-control form-control-sm"
                          type="number"
                          min={1}
                          max={125}
                          value={editTarget.level ?? ''}
                          onChange={e => setEditTarget(x => x ? { ...x, level: e.target.value } : x)}
                          style={{ width: "6rem" }}
                        />
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "90px 1fr", alignItems: "center" }}>
                        <span>{text('FtStatus') || '状态'}</span>
                        <select
                          className="form-control form-control-sm"
                          value={editTarget.status ?? 'included'}
                          onChange={e => setEditTarget(x => x ? { ...x, status: e.target.value } : x)}
                          style={{ width: "auto" }}
                        >
                          <option value="included">{text('McStatusIncluded') || '计入舰船'}</option>
                          <option value="review">{text('McStatusReview') || '待核对'}</option>
                          <option value="excluded">{text('McStatusExcluded') || '已排除'}</option>
                        </select>
                      </div>
                      <div style={{ display: "flex", gap: ".5rem", justifyContent: "flex-end", marginTop: ".25rem" }}>
                        <button className="btn btn-sm btn-light" disabled={editSaving} onClick={() => setEditTarget(null)}>{text('McCancel') || '取消'}</button>
                        <button className="btn btn-sm btn-primary" disabled={editSaving} onClick={() => void submitRowEdit()}>{text('McSave') || '保存'}</button>
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
          {/* 批量编辑弹窗（老 UI _mc_batch_open popup 的 REST 等价物） */}
          {batchOpen && (
            <div
              className="alas-modal-overlay"
              style={{ position: "fixed", inset: 0, zIndex: 10000, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center" }}
              onClick={e => { if (e.target === e.currentTarget && !batchSaving) setBatchOpen(false) }}
            >
              <div className="card" style={{ width: "min(520px, 92vw)", maxHeight: "86vh", overflow: "auto", padding: "1rem 1.25rem", borderRadius: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: ".5rem" }}>
                  <h5 style={{ margin: 0 }}>
                    {(text('McBatchTitle') || '批量编辑 · {count} 艘').replace('{count}', String(batchAll ? (data.total ?? 0) : batchSel.length))}
                  </h5>
                  <button className="btn btn-light btn-sm" disabled={batchSaving} onClick={() => setBatchOpen(false)}>
                    {text('McCancel') || '取消'}
                  </button>
                </div>
                {batchNotice && <div style={{ color: "var(--ft-danger, #dc3545)", margin: ".4rem 0" }}>{batchNotice}</div>}
                <p style={{ opacity: .8, fontSize: '.85rem', margin: '.2rem 0 .6rem' }}>{text('McBatchHint') || '只修改选中的字段；快捷设置只是填表，点保存才生效。'}</p>
                <div style={{ display: "grid", gap: ".5rem" }}>
                  <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", alignItems: "center" }}>
                    <span>{text('FtStatus') || '状态'}</span>
                    <select
                      className="form-control form-control-sm"
                      value={batchForm.status}
                      onChange={e => setBatchForm(f => ({ ...f, status: e.target.value }))}
                      style={{ width: "auto" }}
                    >
                      <option value="keep">{text('McKeep') || '保持原值'}</option>
                      <option value="included">{text('McStatusIncluded') || '计入舰船'}</option>
                      <option value="review">{text('McStatusReview') || '待核对'}</option>
                      <option value="excluded">{text('McStatusExcluded') || '已排除'}</option>
                    </select>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", alignItems: "center" }}>
                    <span>{text('McLevel') || '等级'}</span>
                    <div style={{ display: "flex", gap: ".4rem", alignItems: "center" }}>
                      <select
                        className="form-control form-control-sm"
                        value={batchForm.level_mode}
                        onChange={e => setBatchForm(f => ({ ...f, level_mode: e.target.value }))}
                        style={{ width: "auto" }}
                      >
                        <option value="keep">{text('McKeep') || '保持原值'}</option>
                        <option value="set">{text('McSetLevel') || '设为指定等级'}</option>
                      </select>
                      {batchForm.level_mode === "set" && (
                        <input
                          className="form-control form-control-sm"
                          type="number"
                          min={1}
                          max={125}
                          placeholder={text('McLevelPlaceholder') || '1–125'}
                          value={batchForm.level}
                          onChange={e => setBatchForm(f => ({ ...f, level: e.target.value }))}
                          style={{ width: "5rem" }}
                        />
                      )}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
                    <button className="btn btn-sm btn-secondary" onClick={() => setBatchForm(f => ({ ...f, status: 'included' }))}>{text('McPresetIncluded') || '快捷：全部计入'}</button>
                    <button className="btn btn-sm btn-secondary" onClick={() => setBatchForm(f => ({ ...f, status: 'excluded' }))}>{text('McPresetExcluded') || '快捷：全部排除'}</button>
                    <button className="btn btn-sm btn-secondary" onClick={() => setBatchForm(f => ({ ...f, level_mode: 'set', level: '120' }))}>{text('McPreset120') || '快捷：设为120级'}</button>
                  </div>
                  <div style={{ display: "flex", gap: ".5rem", marginTop: ".25rem" }}>
                    <button className="btn btn-sm btn-light" disabled={batchSaving} onClick={() => setBatchOpen(false)}>{text('McCancel') || '取消'}</button>
                    <button className="btn btn-sm btn-primary" disabled={batchSaving} onClick={() => void submitBatch()}>{text('McSave') || '保存'}</button>
                  </div>
                </div>
              </div>
            </div>
          )}
          {/* 分页（老 UI 页脚：上一页 / N / 下一页） */}
          <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', marginTop: '.5rem' }}>
            <button
              className="btn btn-secondary"
              disabled={(data.page ?? 0) <= 0}
              onClick={() => load((data.page ?? 0) - 1, editMode, { soft: true })}
            >
              {text('McPrevious') || '上一页'}
            </button>
            <span>{(data.page ?? 0) + 1} / {data.pages ?? 1}</span>
            <button
              className="btn btn-secondary"
              disabled={(data.page ?? 0) >= (data.pages ?? 1) - 1}
              onClick={() => load((data.page ?? 0) + 1, editMode, { soft: true })}
            >
              {text('McNext') || '下一页'}
            </button>
          </div>
        </>
      )}
      {!data && !err && <div className="st-placeholder">加载中…</div>}
    </div>
  )
}
