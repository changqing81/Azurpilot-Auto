// 统计页：与老 UI 同构（app_statistics_page.py:527-578 的 st-head + st-tabs 结构）。
// 页签顺序与命名 = _STAT_TABS：opsi 大世界 / commission 委托 / research 科研(规划中) / resource 资源 / fleet 科技列表 / mind 心智计算器。
import { useEffect, useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { DeltaSummary } from './DeltaPanel'
import { OpsiPanel } from './OpsiPanel'
import { ResearchPanel } from './ResearchPanel'
import { FleetTechPanel } from './FleetTechPanel'
import { MindPanel } from './MindPanel'
import { CommissionIncome } from './CommissionPanel'
import { Shell } from '../../components/Shell'

// 页签定义（与 _STAT_TABS 顺序一致；文案=Gui.Stat.Tab* 与 Gui.FleetTechnology.Tab*）
const TABS: { id: string; name: string; badge?: string }[] = [
  { id: 'opsi', name: '大世界' },
  { id: 'commission', name: '委托' },
  { id: 'research', name: '科研', badge: '规划中' },
  { id: 'resource', name: '资源' },
  { id: 'fleet', name: '科技列表' },
  { id: 'mind', name: '心智计算器' },
]

// 数据源状态（_get_statistics_data_state 的三态）
function SourceChip({ state }: { state: 'ok' | 'stale' | 'missing' }) {
  const text = state === 'ok' ? '数据源正常' : state === 'stale' ? '数据源长期未更新' : '暂无数据源'
  return (
    <span className={'st-source is-' + state}>
      <i className="st-source-dot" />
      {text}
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
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const indicatorRef = useRef<HTMLSpanElement | null>(null)
  const [meta, setMeta] = useState('')

  // 数据截至时间（取实例配置 mtime 或 dashboard.json 更新时间；先本地时间占位，端点在 P4 补）
  // 指示条跟随激活页签滑动（老 UI statistics-tab-indicator 同款）
  useEffect(() => {
    const el = tabRefs.current[tab]
    const ind = indicatorRef.current
    if (el && ind) {
      ind.style.left = el.offsetLeft + 'px'
      ind.style.width = el.offsetWidth + 'px'
    }
  }, [tab])
  useEffect(() => {
    const now = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    setMeta(`数据截至 ${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())} · 实例 ${name}`)
  }, [name])

  return (
    <Shell active={name} title="统计">
    <div id="pywebio-scope-statistics-content" className="stat-page">
      <div className="st-page">
        <div className="st-head">
          <div>
            <h1>统计</h1>
            <div className="st-head-meta">{meta}</div>
          </div>
          <div className="st-head-actions">
            <SourceChip state="ok" />
            <button className="st-refresh" onClick={() => setMeta(m => m)}>刷新</button>
          </div>
        </div>
        <div className="st-tabs" id="statistics-tabs" role="tablist">
          {TABS.map(t => (
            <button
              key={t.id}
              ref={el => { tabRefs.current[t.id] = el }}
              className={'st-tab' + (tab === t.id ? ' st-tab-active' : '')}
              role="tab"
              id={'statistics-tab-' + t.id}
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
            >
              {t.name}
              {t.badge && <span className="st-tab-badge">{t.badge}</span>}
            </button>
          ))}
          <span className="st-tab-indicator" id="statistics-tab-indicator" />
        </div>

        {tab === 'opsi' && (
          <div id="st-panel-opsi" className="ov-card apple-card st-panel is-active">
            <div className="ov-card-title">大世界</div>
            <OpsiPanel name={name} />
          </div>
        )}
        {tab === 'commission' && (
          <div id="st-panel-commission" className="ov-card apple-card st-panel is-active">
            <div className="ov-card-title">委托</div>
            <CommissionIncome name={name} />
          </div>
        )}
        {tab === 'research' && (
          <div id="st-panel-research" className="ov-card apple-card st-panel is-active">
            <div className="ov-card-title">科研</div>
            <ResearchPanel name={name} />
          </div>
        )}
        {tab === 'resource' && (
          <div id="st-panel-resource" className="ov-card apple-card st-panel is-active">
            <div className="ov-card-title">资源</div>
            <DeltaSummary name={name} />
          </div>
        )}
        {tab === 'fleet' && (
          <div id="st-panel-fleet" className="ov-card apple-card st-panel is-active">
            <div className="ov-card-title">科技列表</div>
            <FleetTechPanel name={name} />
          </div>
        )}
        {tab === 'mind' && (
          <div id="st-panel-mind" className="ov-card apple-card st-panel is-active">
            <div className="ov-card-title">心智计算器</div>
            <MindPanel name={name} />
          </div>
        )}
      </div>
    </div>
    </Shell>
  )
}
