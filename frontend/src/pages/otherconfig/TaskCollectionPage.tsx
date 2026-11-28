// 「任务集合」页签页 —— 把若干任务的配置收成一个页面，顶部用页签切换。
//
// 长卿 2026-11-20 两次要求：
//   ①「这几个东西能不能整合成一个界面，占位置太多，整成类似统计界面那种
//      但是界面不动 参考图片2 外面显示的配置名叫其他配置」
//   ②「这几个也这样合 主线图1 2 3 合成一个主线图 紧急委托1 2 +3油低耗
//      +1-1刷伏击一个叫紧急委托 活动图就图片中的 活动每日」
//
// 所以本组件做成通用的：传一组 task + 显示名，就是那个页签页。
// 四个具体页面在 collections.tsx 里按集合实例化。
//
// 关键：**每个任务的配置内容完全不变** —— 复用 ConfigBody（从 ConfigPage
// 拆出来的纯配置主体），传不同 task 即可，配置项/保存/校验逻辑一行没改。
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Shell } from '../../components/Shell'
import { ConfigBody } from '../config'

/** 一个集合页的定义 */
export type TaskCollection = {
  /** 路由段，如 other-config / main-chapters */
  route: string
  /** 页面标题（也叫菜单上显示的配置名） */
  title: string
  /** 说明文字（可为空） */
  desc?: string
  /** 页签：[任务 key, 显示名] */
  tabs: { id: string; label: string }[]
}

export function TaskCollectionPage({ collection }: { collection: TaskCollection }) {
  const { name = 'alas' } = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const TABS = collection.tabs
  const TAB_KEY = 'alas_collection_tab_' + collection.route

  // 优先级：URL ?tab= > localStorage（上次看的页签）> 第一个
  // 配置搜索的落点参数（?arg=Group.Arg）：页签由 ?tab= 决定，
  // 具体参数由 ConfigBody 负责滚动高亮。
  const targetArg = searchParams.get('arg') || ''
  const [tab, setTabRaw] = useState<string>(() => {
    const fromUrl = searchParams.get('tab')
    if (fromUrl && TABS.some(t => t.id === fromUrl)) return fromUrl
    try {
      const saved = localStorage.getItem(TAB_KEY)
      if (saved && TABS.some(t => t.id === saved)) return saved
    } catch { /* 隐私模式 */ }
    return TABS[0].id
  })

  const setTab = (id: string) => {
    setTabRaw(id)
    try { localStorage.setItem(TAB_KEY, id) } catch { /* 隐私模式 */ }
    // 同步到 URL，刷新/分享能恢复
    navigate(
      `/${collection.route}/${encodeURIComponent(name)}?tab=${encodeURIComponent(id)}`,
      { replace: true },
    )
  }

  // URL 变化时跟随（显式带 tab 的链接必须落到那个页签）
  useEffect(() => {
    const fromUrl = searchParams.get('tab')
    if (fromUrl && TABS.some(t => t.id === fromUrl) && fromUrl !== tab) setTabRaw(fromUrl)
  }, [searchParams])

  // 页签指示条跟随激活页签滑动（与统计页 st-tab-indicator 同款）
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const [indicator, setIndicator] = useState({ left: 0, width: 0 })
  useEffect(() => {
    const el = tabRefs.current[tab]
    if (!el) return
    const parent = el.parentElement
    if (!parent) return
    const pr = parent.getBoundingClientRect()
    const r = el.getBoundingClientRect()
    setIndicator({ left: r.left - pr.left + parent.scrollLeft, width: r.width })
  }, [tab])

  return (
    <Shell active="CollectionEntry" instance={name} title={collection.title}>
      <div id="pywebio-scope-content" className="config-page">
        {/* 整块毛玻璃容器（长卿：「最好是连成整块」＋「这块也得加上毛玻璃」）
            标题 / 说明 / 页签 / 配置内容全包在一块里，壁纸不从缝隙透上来。 */}
        <div className="oc-glass">
          <h2 className="oc-title">{collection.title}</h2>
          {collection.desc && <p className="oc-desc">{collection.desc}</p>}

          {/* 页签条：照着统计页胶囊外观做的（.oc-* 自己一套，统计页那套选择器
              全挂在 #pywebio-scope-statistics-content 下，跨容器复用不了）。 */}
          <div className="oc-tabs" role="tablist">
            {TABS.map(t => (
              <button
                key={t.id}
                ref={el => { tabRefs.current[t.id] = el }}
                role="tab"
                aria-selected={tab === t.id}
                className={'oc-tab' + (tab === t.id ? ' active' : '')}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
            <span
              className="oc-tab-indicator"
              style={{ width: indicator.width, transform: `translateX(${indicator.left}px)` }}
            />
          </div>

          {/* 配置主体：key 用 tab 保证切换时重新挂载 ——
              不同任务的分组结构不同，复用同一实例会残留上一个任务的 groups 状态。 */}
          <div className="oc-body" key={tab}>
            <ConfigBody name={name} task={tab} hideShell targetArg={targetArg} />
          </div>
        </div>
      </div>
    </Shell>
  )
}