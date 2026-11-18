// 科技列表（舰队科技）面板 —— 老 UI app_fleet_technology.py:_ft_render_tech_tab 的同构实现。
//
// 长卿：「说了多少遍了这个得改啊 这TM是什么？老UI抄的是这个你就这么敷衍我？」
//
// 原来我只挂了三段静态 HTML（summary/bonus/table），老 UI 那页其实有**七块**：
//   ft_summary   汇总（最高科技点 / 四大阵营 / Lv.120 进度 / 资料版本）
//   ft_filters   六组筛选 button group
//     ft_filter_type / rarity / nation / bonusType / attr / status
//     ft_filter_functions  记录编辑模式 / 总属性加成 / 显示额外筛选 / 只看满星未确认
//   ft_stage     加成阶段下拉（仅「显示额外筛选」开启时）
//   ft_toolbar   扫描范围 min–max + 扫描/同步按钮
//   ft_search    搜索框 + 科技条目范围下拉 + 计数
//   ft_bonuses   总属性加成视图（仅开关打开时）
//   ft_table     表格 + 分页 + 页脚
//
// 所以新 UI 那边只剩一堆纯文字，按钮、搜索、排序、分页全没有。
//
// DOM 契约：fleet-tech-alas.css 的规则全部以 [id^="pywebio-scope-ft_"] 为前缀
// （54 条），因此容器 id 必须逐个照抄，否则样式一条都不命中。
import { useEffect, useState } from 'react'
import { apiGet, panelPath } from '../../api/client'
import { useStatText } from './i18n'

type FilterButton = { value: string; label: string; active: boolean }
type FilterGroup = { key: string; title: string; buttons: FilterButton[] }

type Resp = {
  success: boolean
  summary_html?: string
  filter_groups?: FilterGroup[]
  function_buttons?: FilterButton[]
  stage_html?: string
  table_html?: string
  bonus_html?: string
  count?: number
  total?: number
  page?: number
  pages?: number
  min_level?: number
  max_level?: number
  query?: string
  scope?: string
  stage?: string
  pending?: boolean
  edit?: boolean
  bonuses?: boolean
  extra?: boolean
  revision?: string
  error?: string
}

// 科技条目范围下拉：文案 = FtAll / FtTech / FtNone（老 UI _ft_render_search）
const SCOPES = [
  { value: 'all', key: 'FtAll' },
  { value: 'tech', key: 'FtTech' },
  { value: 'none', key: 'FtNone' },
]

export function FleetTechPanel({ name }: { name: string }) {
  const text = useStatText()
  const [filters, setFilters] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState('all')
  const [stage, setStage] = useState('all')
  const [page, setPage] = useState(0)
  const [pending, setPending] = useState(false)
  const [edit, setEdit] = useState(false)
  const [bonuses, setBonuses] = useState(false)
  const [extra, setExtra] = useState(false)
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const [refresh, setRefresh] = useState(0)

  // 搜索防抖：老 UI 是 pin_on_change，输入即刷新；这里 300ms 合并输入
  const [queryInput, setQueryInput] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setQuery(queryInput), 300)
    return () => clearTimeout(timer)
  }, [queryInput])

  useEffect(() => {
    let alive = true
    setLoading(true)
    const qs = new URLSearchParams({
      
      filters: filters.join(','),
      q: query,
      scope,
      stage,
      sort: 'code',
      dir: '1',
      page: String(page),
      pending: pending ? '1' : '0',
      edit: edit ? '1' : '0',
      bonuses: bonuses ? '1' : '0',
      extra: extra ? '1' : '0',
    })
    apiGet<Resp>(panelPath(name, 'fleet') + `?${qs}`, 30000)
      .then(r => { if (alive) setData(r) })
      .catch(e => { if (alive) setData({ success: false, error: String(e) }) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [name, filters, query, scope, stage, page, pending, edit, bonuses, extra, refresh])

  /**
   * 筛选点击。token 形如 "type|3"（组|值），空值表示该组的「全部」。
   *
   * 老 UI 的语义（app_fleet_technology.py:_ft_on_filter）是**每组内多选**：
   * 点某个值把它加入/移出该组的 selected 集合；点「全部」（value 为空）
   * 清空整组。组与组之间是「且」。
   */
  const onFilter = (token: string) => {
    setPage(0)
    const [group, value] = token.split('|')
    setFilters(prev => {
      const others = prev.filter(t => t.split('|')[0] !== group)
      if (!value) return others
      const already = prev.includes(token)
      return already ? others : [...others, token]
    })
  }

  // ── 批量编辑（老 UI _ft_batch_open 的 DOM 契约：data-ft-batch 工具栏按钮 +
  //    [data-ft-select] 勾选框；REST 化后 gids 在前端收集，乐观锁用 panel.revision）
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchAll, setBatchAll] = useState(false)
  const [batchSel, setBatchSel] = useState<number[]>([])
  const [batchSaving, setBatchSaving] = useState(false)
  const [batchNotice, setBatchNotice] = useState("")
  const [batchForm, setBatchForm] = useState<{
    owned: string
    full_star: string
    lv120: string
    level_mode: string
    level: string
  }>({ owned: "keep", full_star: "keep", lv120: "keep", level_mode: "keep", level: "" })

  const openBatch = (all: boolean) => {
    if (!all) {
      const host = document.getElementById("pywebio-scope-ft_table")
      const checked = Array.from(host?.querySelectorAll("[data-ft-select]:checked") ?? [])
        .map(el => Number(el.getAttribute("data-ft-select")))
        .filter(n => !Number.isNaN(n))
      if (!checked.length) {
        setBatchNotice("请先勾选要修改的舰船（勾选只对本页有效）；跨页请用「修改全部筛选结果」")
        return
      }
      setBatchSel(checked)
    } else {
      setBatchSel([])
    }
    setBatchNotice("")
    setBatchForm({ owned: "keep", full_star: "keep", lv120: "keep", level_mode: "keep", level: "" })
    setBatchAll(all)
    setBatchOpen(true)
  }

  const presetBatch = (preset: "owned" | "full_star" | "lv120") => {
    setBatchForm(f => ({
      ...f,
      owned: preset === "owned" ? "true" : "keep",
      full_star: preset === "full_star" ? "true" : "keep",
      lv120: preset === "lv120" ? "true" : "keep",
      level_mode: "keep",
      level: "",
    }))
  }

  // 只收显式字段（老 UI collect_batch_fields 语义）：keep 与空 level 不发，
  // 后端对非法组合直接报错
  const collectChanges = () => {
    const changes: Record<string, any> = {}
    for (const f of ["owned", "full_star", "lv120"] as const) {
      if (batchForm[f] !== "keep") {
        changes[f] = batchForm[f] === "unknown" ? null : batchForm[f] === "true"
      }
    }
    if (batchForm.level_mode === "set") changes.level = Number(batchForm.level)
    else if (batchForm.level_mode === "unknown") changes.level = null
    return changes
  }

  const submitBatch = async (action: "save" | "restore") => {
    setBatchSaving(true)
    setBatchNotice("")
    try {
      const url = "/newapi/fleet/" + name + "/batch"
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gids: batchAll ? "all" : batchSel,
          changes: collectChanges(),
          expected_revision: data?.revision ?? "",
          action,
        }),
      })
      if (!res.ok) { setBatchNotice("保存失败: HTTP " + res.status); return }
      const j = await res.json().catch(() => null)
      if (!j) { setBatchNotice("保存失败: 无法解析响应"); return }
      if (j.error) { setBatchNotice("保存失败: " + j.error); return }
      setBatchOpen(false)
      setRefresh(v => v + 1)
    } catch (e) {
      setBatchNotice("保存失败: " + String(e))
    } finally {
      setBatchSaving(false)
    }
  }

  const [scanning, setScanning] = useState(false)
  const [syncing, setSyncing] = useState(false)

  // 扫描船坞（老 UI _ft_scan_start）：写扫描范围 → ProcessManager 两段启动
  const startScan = async () => {
    const low = Number((document.getElementById("ft-min-level") as HTMLInputElement).value)
    const high = Number((document.getElementById("ft-max-level") as HTMLInputElement).value)
    setScanning(true)
    try {
      const res = await fetch("/newapi/fleet/" + name + "/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ min_level: low, max_level: high }),
      })
      const j = await res.json().catch(() => null)
      if (j && j.success) setBatchNotice(text("FtScanStarted") || "船坞扫描已启动，完成后自动保存并刷新。")
      else setBatchNotice("启动失败: " + ((j && j.error) || ("HTTP " + res.status)))
    } catch (e) {
      setBatchNotice("启动失败: " + String(e))
    } finally {
      setTimeout(() => setScanning(false), 4000)
    }
  }

  // 同步已有心智扫描（老 UI _ft_on_sync）：merge_mind 只增不减
  const syncMind = async () => {
    setSyncing(true)
    try {
      const res = await fetch("/newapi/fleet/" + name + "/sync", { method: "POST" })
      const j = await res.json().catch(() => null)
      if (j && j.success) {
        setBatchNotice(j.changed ? (text("FtSyncDone") || "已同步心智扫描结果") : "没有新的扫描结果可同步")
        setRefresh(v => v + 1)
      } else {
        setBatchNotice("同步失败: " + ((j && j.error) || ("HTTP " + res.status)))
      }
    } catch (e) {
      setBatchNotice("同步失败: " + String(e))
    } finally {
      setSyncing(false)
    }
  }
  // 勾选态汇总：更新「本页已选 N」计数与「批量修改已选舰船」按钮的 disabled。
  // 单独抽出来给「行勾选」与「表头全选」两条路径共用。
  const syncBatchCount = (host: HTMLElement) => {
    const count = host.querySelectorAll("[data-ft-select]:checked").length
    const counter = host.querySelector("[data-ft-selected-count]")
    if (counter) counter.textContent = String(count)
    const selBtn = host.querySelector("[data-ft-batch=selected]") as HTMLButtonElement | null
    if (selBtn) {
      if (count > 0) selBtn.removeAttribute("disabled")
      else selBtn.setAttribute("disabled", "")
    }
  }
  // 工具栏按钮（data-ft-batch）是表格 HTML 里的原生按钮 —— 事件委托接管
  useEffect(() => {
    const host = document.getElementById("pywebio-scope-ft_table")
    if (!host) return
    const onClick = (ev: Event) => {
      const btn = (ev.target as HTMLElement).closest("[data-ft-batch]") as HTMLElement | null
      if (!btn || btn.hasAttribute("disabled")) return
      ev.preventDefault()
      openBatch(btn.getAttribute("data-ft-batch") === "all")
    }
    // 勾选联动：解除/设置「批量修改已选舰船」的 disabled + 更新计数。
    // 表格是 innerHTML 注入的原生 DOM（React 不管），只能在这里委托 change。
    const onChange = (ev: Event) => {
      const target = ev.target as HTMLElement
      if (!target.hasAttribute) return
      // 表头「全选本页」：老 UI 的表格 JS 在这里勾选/取消当前页所有行。
      // 少了这段时点表头框只会把自己勾上（长卿：选择全选他只单勾一个）。
      if (target.hasAttribute("data-ft-select-page")) {
        const boxes = Array.from(host.querySelectorAll("[data-ft-select]")) as HTMLInputElement[]
        const want = (target as HTMLInputElement).checked
        boxes.forEach(b => { b.checked = want })
        syncBatchCount(host)
        return
      }
      if (!target.hasAttribute("data-ft-select")) return
      syncBatchCount(host)
    }
    host.addEventListener("click", onClick)
    host.addEventListener("change", onChange)
    return () => {
      host.removeEventListener("click", onClick)
      host.removeEventListener("change", onChange)
    }
  })

  const toggleFunction = (key: string) => {
    setPage(0)
    if (key === 'edit') setEdit(v => !v)
    else if (key === 'bonuses') setBonuses(v => !v)
    else if (key === 'extra') setExtra(v => !v)
    else if (key === 'pending') setPending(v => !v)
  }

  return (
    // #pywebio-scope-ft_tech 是 fleet-tech-alas.css 里 [id^="pywebio-scope-ft_"]
    // 后代选择器的宿主容器，不能省。
    <div id="pywebio-scope-ft_tech">
      {loading && !data && <div className="st-placeholder">加载中…</div>}
      {data && !data.success && (
        <div className="st-placeholder">
          {data.error || '暂无舰队科技数据（先在配置里跑一次科技扫描）'}
        </div>
      )}

      {data?.success && (
        <>
          {/* ① 汇总 */}
          <div
            id="pywebio-scope-ft_summary"
            dangerouslySetInnerHTML={{ __html: data.summary_html || '' }}
          />

          {/* ② 六组筛选 + 功能开关 */}
          <div id="pywebio-scope-ft_filters">
            {(data.filter_groups ?? []).map(g => (
              <div key={g.key} id={`pywebio-scope-ft_filter_${g.key}`}>
                <div
                  style={{
                    display: 'grid',
                    gridAutoFlow: 'column',
                    gridTemplateColumns: '112px 1fr',
                  }}
                >
                  <div className="ft-filter-label">{g.title}</div>
                  <div>
                    {g.buttons.map((b, i) => (
                      <button
                        key={b.value}
                        className={'btn ' + (b.active ? 'btn-primary' : 'btn-secondary')}
                        onClick={() => onFilter(b.value)}
                        style={i === 0 ? undefined : { marginLeft: '.25rem' }}
                      >
                        {b.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ))}
            {/* 功能开关（老 UI ft_filter_functions） */}
            <div id="pywebio-scope-ft_filter_functions">
              <div
                style={{
                  display: 'grid',
                  gridAutoFlow: 'column',
                  gridTemplateColumns: '112px 1fr',
                }}
              >
                <div className="ft-filter-label">{text('FtFunctions')}</div>
                <div>
                  {(data.function_buttons ?? []).map((b, i) => (
                    <button
                      key={b.value}
                      className={'btn ' + (b.active ? 'btn-primary' : 'btn-secondary')}
                      onClick={() => toggleFunction(b.value)}
                      style={i === 0 ? undefined : { marginLeft: '.25rem' }}
                    >
                      {b.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* ③ 加成阶段（仅「显示额外筛选」开启时） */}
          {data.stage_html && (
            <div id="pywebio-scope-ft_stage">
              {/* 加成阶段：老 UI 是 put_select(name='ft_stage')，三个选项
                  全部/解锁/Lv.120。这里用受控 select 完全等价。 */}
              <select
                className="form-control"
                value={stage}
                onChange={e => { setPage(0); setStage(e.target.value) }}
                style={{ width: 'auto' }}
              >
                <option value="all">{text('FtAll')}</option>
                <option value="get">{text('FtUnlock')}</option>
                <option value="level">Lv.120</option>
              </select>
            </div>
          )}

          {/* ④ 扫描范围 + 扫描/同步（老 UI ft_toolbar） */}
          <div id="pywebio-scope-ft_toolbar">
            <span className="ft-toolbar-label">{text("FtScanRange")}</span>
            <input
              className="form-control"
              type="number"
              id="ft-min-level"
              defaultValue={data.min_level}
              style={{ width: "5rem" }}
            />
            <span className="ft-range-dash">–</span>
            <input
              className="form-control"
              type="number"
              id="ft-max-level"
              defaultValue={data.max_level}
              style={{ width: "5rem" }}
            />
            {/* 老 UI ft_toolbar：扫描按钮（BinarySwitchButton）+ 同步按钮 + Hint。
                之前只做了扫描范围输入，两个按钮和提示全缺 —— 长卿：
                「筛选扫描什么各个同步…这些怎么都没有」。 */}
            <div id="pywebio-scope-ft_scan_button">
              <button className="btn btn-primary btn-sm" disabled={scanning} onClick={startScan}>
                {scanning ? text("FtScanning") || "正在扫描" : text("FtScan") || "扫描船坞"}
              </button>
            </div>
            <button className="btn btn-secondary btn-sm" disabled={syncing} onClick={syncMind}>
              {text("FtSync") || "同步已有心智扫描"}
            </button>
            <span className="ft-hint">{text("FtHint") || ""}</span>
          </div>

          {/* ⑤ 搜索 + 科技条目范围 + 计数（老 UI ft_search） */}
          <div id="pywebio-scope-ft_search">
            <input
              className="form-control"
              placeholder={text('FtSearch')}
              value={queryInput}
              onChange={e => { setPage(0); setQueryInput(e.target.value) }}
            />
            <select
              className="form-control"
              value={scope}
              onChange={e => { setPage(0); setScope(e.target.value) }}
              style={{ width: 'auto' }}
            >
              {SCOPES.map(o => (
                <option key={o.value} value={o.value}>{text(o.key)}</option>
              ))}
            </select>
            <div id="pywebio-scope-ft_count">
              {(text('FtRowCount') || '共 {count} 条').replace('{count}', String(data.count ?? 0))}
            </div>
          </div>

          {/* ⑥ 总属性加成视图 */}
          {data.bonus_html && (
            <div
              id="pywebio-scope-ft_bonuses"
              dangerouslySetInnerHTML={{ __html: data.bonus_html }}
            />
          )}

          {/* ⑦ 表格 + 分页 */}
          <div
            id="pywebio-scope-ft_table"
            dangerouslySetInnerHTML={{ __html: data.table_html || '' }}
          />
          {/* ⑧ 批量编辑弹窗（老 UI popup 的 REST 等价物） */}
          {batchOpen && (
            <div
              className="alas-modal-overlay"
              style={{ position: "fixed", inset: 0, zIndex: 10000, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center" }}
              onClick={e => { if (e.target === e.currentTarget && !batchSaving) setBatchOpen(false) }}
            >
              <div className="card" style={{ width: "min(520px, 92vw)", maxHeight: "86vh", overflow: "auto", padding: "1rem 1.25rem", borderRadius: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: ".5rem" }}>
                  <h5 style={{ margin: 0 }}>
                    {batchAll ? (text("FtBatchAll") || "").replace("{count}", String(data.total ?? 0)) : (text("FtBatchTitle") || "").replace("{count}", String(batchSel.length))}
                    {batchAll ? "（" + (data.total ?? 0) + " 艘）" : "（" + batchSel.length + " 艘）"}
                  </h5>
                  <button className="btn btn-light btn-sm" disabled={batchSaving} onClick={() => setBatchOpen(false)}>关闭</button>
                </div>
                {batchNotice && <div style={{ color: "#dc3545", margin: ".4rem 0" }}>{batchNotice}</div>}
                <div style={{ display: "grid", gap: ".5rem" }}>
                  {([["owned", text("FtOwned") || "已有舰船"], ["full_star", text("FtStar") || "满星"], ["lv120", text("FtLevel") || "Lv.120"]] as const).map(([key, label]) => (
                    <div key={key} style={{ display: "grid", gridTemplateColumns: "120px 1fr", alignItems: "center" }}>
                      <span>{label}</span>
                      <select
                        className="form-control form-control-sm"
                        value={batchForm[key]}
                        onChange={e => setBatchForm(f => ({ ...f, [key]: e.target.value }))}
                        style={{ width: "auto" }}
                      >
                        <option value="keep">{text("FtKeep") || "保持原值"}</option>
                        <option value="true">{text("FtYes") || "是"}</option>
                        <option value="false">{text("FtNo") || "否"}</option>
                        <option value="unknown">{text("FtUnknown") || "未知"}</option>
                      </select>
                    </div>
                  ))}
                  <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", alignItems: "center" }}>
                    <span>{text("FtLevel") || "最高等级"}</span>
                    <div style={{ display: "flex", gap: ".4rem", alignItems: "center" }}>
                      <select
                        className="form-control form-control-sm"
                        value={batchForm.level_mode}
                        onChange={e => setBatchForm(f => ({ ...f, level_mode: e.target.value }))}
                        style={{ width: "auto" }}
                      >
                        <option value="keep">{text("FtKeep") || "保持原值"}</option>
                        <option value="set">{text("FtSetLevel") || "设为指定等级"}</option>
                        <option value="unknown">{text("FtUnknown") || "未知"}</option>
                      </select>
                      {batchForm.level_mode === "set" && (
                        <input
                          className="form-control form-control-sm"
                          type="number"
                          min={1}
                          max={125}
                          placeholder="1–125"
                          value={batchForm.level}
                          onChange={e => setBatchForm(f => ({ ...f, level: e.target.value }))}
                          style={{ width: "5rem" }}
                        />
                      )}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
                    <button className="btn btn-sm btn-secondary" onClick={() => presetBatch("owned")}>{text("FtPresetOwned") || "快捷：设为已有"}</button>
                    <button className="btn btn-sm btn-secondary" onClick={() => presetBatch("full_star")}>{text("FtPresetStar") || "快捷：满星"}</button>
                    <button className="btn btn-sm btn-secondary" onClick={() => presetBatch("lv120")}>{text("FtPreset120") || "快捷：设为120级"}</button>
                  </div>
                  <div style={{ display: "flex", gap: ".5rem", marginTop: ".25rem" }}>
                    <button className="btn btn-sm btn-secondary" disabled={batchSaving} onClick={() => void submitBatch("restore")}>{text("FtRestore") || "恢复自动记录"}</button>
                    <button className="btn btn-sm btn-light" disabled={batchSaving} onClick={() => setBatchOpen(false)}>{text("FtCancel") || "取消"}</button>
                    <button className="btn btn-sm btn-primary" disabled={batchSaving} onClick={() => void submitBatch("save")}>{text("FtSave") || "保存"}</button>
                  </div>
                  <small className="text-muted">{text("FtBatchHint") || "只修改选中的字段；快捷设置只是填表，点保存才生效。"}</small>
                </div>
              </div>
            </div>
          )}
          {(data.pages ?? 1) > 1 && (
            <div style={{ marginTop: '.5rem' }}>
              <button
                className="btn btn-secondary"
                disabled={(data.page ?? 0) <= 0}
                onClick={() => setPage(p => Math.max(0, p - 1))}
              >
                {text('FtPrevPage') || '上一页'}
              </button>
              <span style={{ margin: '0 .5rem' }}>
                第 {(data.page ?? 0) + 1} / {data.pages} 页
              </span>
              <button
                className="btn btn-secondary"
                disabled={(data.page ?? 0) >= (data.pages ?? 1) - 1}
                onClick={() => setPage(p => p + 1)}
              >
                {text('FtNextPage') || '下一页'}
              </button>
              <a
                href="https://wiki.biligame.com/blhx/舰队科技"
                target="_blank"
                rel="noreferrer"
                style={{ marginLeft: '.75rem', fontSize: '.8rem' }}
              >
                {text('FtAttribution')}
              </a>
            </div>
          )}
        </>
      )}
    </div>
  )
}