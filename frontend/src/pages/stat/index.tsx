// 统计页：与老 UI 同构（app_statistics_page.py:527-578 的 st-head + st-tabs 结构）。
// 页签顺序与命名 = _STAT_TABS：opsi 大世界 / commission 委托 / research 科研 / resource 资源 / fleet 科技列表 / mind 心智计算器。
import { useEffect, useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { DeltaSummary } from './DeltaPanel'
import { OpsiPanel } from './OpsiPanel'
import { ResearchPanel } from './ResearchPanel'
import { FleetTechPanel } from './FleetTechPanel'
import { MindPanel } from './MindPanel'
import { CommissionIncome } from './CommissionPanel'
import { Shell } from '../../components/Shell'
import { STAT_I18N_FALLBACK as STAT_FALLBACK, StatI18nContext, useStatText } from './i18n'

// 页签定义（与 _STAT_TABS 顺序一致；文案=Gui.Stat.Tab* 与 Gui.FleetTechnology.Tab*）
//
// 注意：**没有 badge**。曾经给「科研」挂过「规划中」角标，是早期功能没做完时的
// 临时标记，现在科研面板已经完整（进行中队列 + 指标卡 + 三行控件 + 掉落表），
// 角标反而误导用户以为这页不能用。长卿：「科研的规划删掉」。
// 页签文案走 i18n（name 字段只是兜底，实际渲染时用 useStatText 取）
const TABS: { id: string; name: string }[] = [
  { id: 'opsi', name: '大世界' },
  { id: 'commission', name: '委托' },
  { id: 'research', name: '科研' },
  { id: 'resource', name: '资源' },
  { id: 'fleet', name: '科技列表' },
  { id: 'mind', name: '心智计算器' },
]

// 数据源状态（_get_statistics_data_state 的三态）；文案 = Gui.Stat.Source*
function SourceChip({ state }: { state: 'ok' | 'stale' | 'missing' }) {
  const text = useStatText()
  const label =
    state === 'ok' ? text('SourceOk')
    : state === 'stale' ? text('SourceStale')
    : text('SourceMissing')
  return (
    <span className={'st-source is-' + state}>
      <i className="st-source-dot" />
      {label}
    </span>
  )
}

export function StatPage() {
  const { name = 'alas' } = useParams()
  const [searchParams] = useSearchParams()
  // 页签记忆：长卿反馈「记不住上次选项」。
  // 优先级：URL ?tab= > localStorage（上次看的页签）> 默认大世界。
  // 老 UI 的每个页签是独立的 PyWebIO 页面回调，切走再回来必然重置；
  // 新 UI 用 localStorage 记住，切页/刷新/从老 UI 切过来都保持上次的页签。
  const STAT_TAB_KEY = 'alas_newui_stat_tab'
  const [tab, setTabRaw] = useState(() => {
    const fromUrl = searchParams.get('tab')
    if (fromUrl && TABS.some(t => t.id === fromUrl)) return fromUrl
    try {
      const saved = localStorage.getItem(STAT_TAB_KEY)
      if (saved && TABS.some(t => t.id === saved)) return saved
    } catch { /* 隐私模式 */ }
    return 'opsi'
  })
  const setTab = (id: string) => {
    setTabRaw(id)
    try { localStorage.setItem(STAT_TAB_KEY, id) } catch { /* 隐私模式 */ }
  }
  // URL ?tab= 变化时跟随（useState 初始化只跑一次，之后 URL 再变就不生效了
  // —— 实测从 #/stat/alas?tab=opsi 改到 ?tab=fleet，激活页签纹丝不动）。
  // 优先级保持 URL > localStorage：显式带 tab 的链接必须落到那个页签。
  useEffect(() => {
    const fromUrl = searchParams.get('tab')
    if (fromUrl && TABS.some(t => t.id === fromUrl)) setTabRaw(fromUrl)
  }, [searchParams])
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const indicatorRef = useRef<HTMLSpanElement | null>(null)
  const [meta, setMeta] = useState('')
  // 页签布局依赖容器宽度/字体，数据加载后才稳定；作为指示条重算的额外触发
  const groupsKey = TABS.map(t => t.id).join(',')

  // 文案：后端按当前语言返回老 UI i18n 原文（长卿：「文案一定要是老UI的文案」）
  const [i18n, setI18n] = useState<Record<string, string>>({})
  useEffect(() => {
    fetch('/newapi/stat/i18n')
      .then(r => r.json())
      .then(setI18n)
      .catch(() => { /* 老后端：用兜底表 */ })
  }, [])

  useEffect(() => {
    let alive = true
    fetch(`/newapi/stat/${name}/state`)
      .then(r => (r.ok ? r.json() : null))
      .then(j => {
        if (!alive || !j) return
        setSourceState((j.state === "stale" || j.state === "missing") ? j.state : "ok")
        // PageMeta 模板用的是 {time} / {instance}（老 UI 的 t() 已经做过一次
        // format，所以这里只替换这两个占位符，不能整串再 format 一次）
        const tpl = i18n.PageMeta || "数据截至 {time} · 实例 {instance}"
        setMeta(String(tpl)
          .replace("{time}", String(j.latest || "--"))
          .replace("{instance}", String(j.instance || name)))
      })
      .catch(() => { /* 端点不可用：保持空 meta */ })
    return () => { alive = false }
  }, [name, i18n.PageMeta])
  // 页头第二行：数据截至时间 + 实例名 + 数据源状态。
  // 口径与老 UI app_statistics_page.py:_get_statistics_data_state 一致：
  // 取 cl1_data.db / azurstats_local.db / resource_delta.db / ship_exp_data.json
  // （含 -wal）的最新 mtime，7 天内有写入 = ok，更早 = stale，一个都没有 = missing。
  const [sourceState, setSourceState] = useState<"ok" | "stale" | "missing">("ok")
  // 指示条跟随激活页签滑动（老 UI statistics-tab-indicator 同款）
  // 指示条跟随激活页签（老 UI 用 JS 同步 left/width，见 _layout_statistics_panels）。
  // 必须在**布局完成后**测量：直接在 useEffect 里读 offsetLeft 有时拿到 0，
  // 导致指示条宽度撑满、视觉上像多个页签叠在一起。
  useEffect(() => {
    const sync = () => {
      const el = tabRefs.current[tab]
      const ind = indicatorRef.current
      if (!el || !ind) return false
      const r = el.getBoundingClientRect()
      if (!r.width) return false
      // 用相对定位容器（.st-tabs，position:relative）的偏移，
      // 不能用 offsetLeft —— 在 flex 容器里它返回的是相对 offsetParent 的值，
      // 实测拿到的位置会把指示条压在第一个页签上（宽度 0 也源于此）。
      const box = el.parentElement
      const boxRect = box ? box.getBoundingClientRect() : null
      const left = boxRect ? r.left - boxRect.left + (box as HTMLElement).scrollLeft : 0
      ind.style.left = left + 'px'
      ind.style.width = r.width + 'px'
      return true
    }
    if (sync()) return
    let raf = 0
    let tries = 0
    const retry = () => {
      if (sync() || ++tries > 30) return
      raf = requestAnimationFrame(retry)
    }
    raf = requestAnimationFrame(retry)
    return () => cancelAnimationFrame(raf)
  }, [tab, groupsKey])
  // 进统计页立刻后台预热 4 个面板的缓存。
  // 首次渲染 opsi 要 1.3s（_build_meow_rows 对侵蚀等级 1~6 各查一次 SQLite），
  // 用户点页签时才开始算就会有明显卡顿。预热后点页签只需 8~40ms。
  useEffect(() => {
    fetch(`/newapi/stat/${name}/panels_warm`, { cache: 'no-store' }).catch(() => {
      /* 预热失败不影响正常请求，用户点页签时再算 */
    })
  }, [name])

  useEffect(() => {
    const now = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    setMeta(`数据截至 ${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())} · 实例 ${name}`)
  }, [name])

  // 注意：StatPage 是 Provider 的宿主组件，**useContext 在这里读到的是上层值**
  // （React 的经典陷阱），所以 StatPage 自己的文案必须直接用 i18n state 取，
  // 不能经过 useStatText。子组件（面板）用 useStatText 没问题。
  const t18n = (key: string) => i18n[key] || STAT_FALLBACK[key] || key
  const text = t18n
  const tabName = (id: string) =>
    t18n(id === 'fleet' ? 'TabFleet' : id === 'mind' ? 'TabMind' : 'Tab' + id[0].toUpperCase() + id.slice(1))

  return (
    <StatI18nContext.Provider value={i18n}>
    <Shell active={name} title={text('PageTitle') || '统计'}>
    <div id="pywebio-scope-statistics-content">
      <div className="st-page">
        <div className="st-head">
          <div>
            <h1>{text('PageTitle') || '统计'}</h1>
            <div className="st-head-meta">{meta}</div>
          </div>
          <div className="st-head-actions">
            <SourceChip state={sourceState} />
            <button className="btn btn-on" onClick={() => setMeta(m => m)}>{text('Refresh') || '刷新'}</button>
          </div>
        </div>
        <div className="st-tabs" id="statistics-tabs" role="tablist">
          {TABS.map(t => (
            <button
              key={t.id}
              ref={el => { tabRefs.current[t.id] = el }}
              className="st-tab"
              role="tab"
              id={'statistics-tab-' + t.id}
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
            >
              {tabName(t.id)}
            </button>
          ))}
          {/* ⚠️ ref 必须挂上：我原来漏了这一行，导致 indicatorRef.current 恒为 null，
              effect 里 if (!el || !ind) return false 直接退出，
              指示条的 style.left/width 从来没被赋值 → 实测 realW=0，
              视觉上就是「页签下面没有下划线，像挤在一起」。 */}
          <span className="st-tab-indicator" id="statistics-tab-indicator" ref={indicatorRef} />
        </div>

        {/* 面板容器：老 UI 是 <div class="st-panels" id="statistics-panels">，
            CSS 靠 .st-panels 给 padding-top:18px，并靠 .st-panel.is-active 控制显隐。
            我原来直接散放 6 个带条件渲染的 div，缺了这层容器。 */}
        <div className="st-panels" id="statistics-panels">
        {tab === 'opsi' && (
          <div id="st-panel-opsi" className="st-panel is-active">
            <OpsiPanel name={name} />
          </div>
        )}
        {tab === 'commission' && (
          <div id="st-panel-commission" className="st-panel is-active">
            <CommissionIncome name={name} />
          </div>
        )}
        {tab === 'research' && (
          <div id="st-panel-research" className="st-panel is-active">
            <ResearchPanel name={name} />
          </div>
        )}
        {tab === 'resource' && (
          <div id="st-panel-resource" className="st-panel is-active">
            <DeltaSummary name={name} />
          </div>
        )}
        {tab === 'fleet' && (
          <div id="st-panel-fleet" className="st-panel is-active">
            <FleetTechPanel name={name} />
          </div>
        )}
        {tab === 'mind' && (
          <div id="st-panel-mind" className="st-panel is-active">
            <MindPanel name={name} />
          </div>
        )}
        </div>
      </div>
    </div>
    </Shell>
    </StatI18nContext.Provider>
  )
}
