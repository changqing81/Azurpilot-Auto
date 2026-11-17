// 心智计算器面板：挂载老 UI 渲染器产出的 HTML + 行编辑/删除交互。
//
// 老 UI（app_mind_calculator_tab.py + mind_calculator_view.py）：
//   说明条 + 四张统计卡 + 稀有度汇总表 + 船舶清单表（分页 + 行编辑弹窗 +
//   删除 + 批量编辑）。清单行的 data-mc-edit / data-mc-del / data-mc-select
//   钩子由点击桥转发到 Mixin。REST 化：点击钩子 → 弹窗 → POST
//   /newapi/mind/{name}/ships 全量保存（revision 乐观锁在服务端）。
// 容器 id 必须是 #pywebio-scope-ft_mind（fleet-tech-alas.css 后代选择器契约）。
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiGet, panelPath } from '../../api/client'
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
  const pageRef = useRef(0)
  const shipsRef = useRef<Record<string, any>[]>([])
  const revRef = useRef('')

  const load = useCallback((page: number, edit: boolean) => {
    setData(null)
    setErr('')
    apiGet<MindData>(panelPath(name, 'mind', {}) + `?page=${page}&edit=${edit ? 1 : 0}`, 25000)
      .then(r => {
        if (r.success === false) { setErr(r.error || '加载失败'); return }
        setData(r)
        pageRef.current = r.page ?? 0
      })
      .catch(e => setErr(String(e)))
    // 拉一份原始清单（行编辑需要完整 ships 全量回传）
    apiGet<any>(`/newapi/stat/${name}/mind`, 25000)
      .then(r => {
        shipsRef.current = (r.ships || []).map((s: any) => ({ ...s }))
      })
      .catch(() => {})
  }, [name])

  useEffect(() => { load(0, false) }, [load])

  const importFileRef = useRef<HTMLInputElement>(null)

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
      load(pageRef.current, editMode)
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
        const level = window.prompt(`修改等级（${ship.name}）`, String(ship.level))
        if (level === null) return
        // 状态 = excluded/review 两个存储布尔的组合（老 UI 行编辑三选）
        const curState = ship.review ? '待核对' : ship.excluded ? '已排除' : '计入'
        const status = window.prompt(`修改状态（${ship.name}）：1=计入 2=待核对 3=排除（当前 ${curState}）`, ship.review ? '2' : ship.excluded ? '3' : '1')
        if (status === null) return
        const next = shipsRef.current.slice()
        const st = status.trim()
        next[idx] = {
          ...ship,
          level: Number(level) || 0,
          excluded: st === '3',
          review: st === '2',
        }
        return
      }
      if (delBtn) {
        ev.preventDefault()
        const idx = Number(delBtn.getAttribute('data-mc-del'))
        const ship = shipsRef.current[idx]
        if (!ship) return
        if (!window.confirm(`确认删除 ${ship.name}？`)) return
        const next = shipsRef.current.filter((_, i) => i !== idx)
        void saveShips(next)
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
  }, [editMode, load, saveShips])

  if (err) return <div className="st-placeholder">{err}</div>

  return (
    <div id="pywebio-scope-ft_mind" ref={hostRef}>
      {/* 扫描工具栏（老 UI ft_mind_toolbar：扫描范围 + 扫描按钮 + 提示） */}
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
      {/* 编辑开关 + 提示行（老 UI 的批量编辑开关在筛选行） */}
      <div id="pywebio-scope-ft_mind_filters" className="ft-toolbar" style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap', margin: '0 0 .5rem' }}>
        <button
          className={'btn ' + (editMode ? 'btn-primary' : 'btn-secondary')}
          onClick={() => { setEditMode(m => !m); load(pageRef.current, !editMode) }}
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
      {data && (
        <>
          {/* 老 UI app_mind_calculator_tab.py:134-141 的四段 scope：
              ft_mind_overview / ft_mind_toolbar / ft_mind_filters / ft_mind_list。
              fleet-tech-alas.css:88-94 的垫面规则（background: var(--ft-surface)）与
              advanced-material-alas.css:1866-1872 的毛玻璃都挂在这些 id 上 ——
              平铺 div 时一张面都垫不上，表格文字直接压在壁纸上。 */}
          <div id="pywebio-scope-ft_mind_overview">
            {data.intro_html && <div dangerouslySetInnerHTML={{ __html: data.intro_html }} />}
            {data.stats_html && <div dangerouslySetInnerHTML={{ __html: data.stats_html }} />}
            {data.summary_html && <div dangerouslySetInnerHTML={{ __html: data.summary_html }} />}
          </div>
          <div id="pywebio-scope-ft_mind_list">
            {data.list_html && <div dangerouslySetInnerHTML={{ __html: data.list_html }} />}
          </div>
          {/* 分页（老 UI 页脚：上一页 / N / 下一页） */}
          <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', marginTop: '.5rem' }}>
            <button
              className="btn btn-secondary"
              disabled={(data.page ?? 0) <= 0}
              onClick={() => load((data.page ?? 0) - 1, editMode)}
            >
              {text('McPrevious') || '上一页'}
            </button>
            <span>{(data.page ?? 0) + 1} / {data.pages ?? 1}</span>
            <button
              className="btn btn-secondary"
              disabled={(data.page ?? 0) >= (data.pages ?? 1) - 1}
              onClick={() => load((data.page ?? 0) + 1, editMode)}
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
