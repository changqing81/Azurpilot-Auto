// 页面壳：与老 UI（PyWebIO）1:1 同构的 DOM 输出。
//
// 结构来源（已实测老 UI 真实 DOM，见 .workbuddy/perf/out/oldui-ref/oldui_overview_root.html）：
//   BODY.webio-theme-default.no-animation
//     > DIV.pywebio
//       > DIV#output-container.container.no-fix-height
//         > DIV#markdown-body.markdown-body
//           > DIV#pywebio-scope-ROOT
//             > DIV#pywebio-scope-header
//             > DIV#pywebio-scope-contents
//               > DIV#pywebio-scope-aside
//               > DIV#pywebio-scope-menu
//               > DIV#pywebio-scope-content
//               > DIV#pywebio-scope-statistics-content（常驻隐藏）
//
// 必须同构的原因：alas.css / light-alas.css / advanced-material-alas.css 里
// 大量规则是「后代选择器 + 内联 style 子串匹配」，例如
//   #pywebio-scope-ROOT { height:100vh; display:grid; grid-template-rows: auto 1fr }
//   *[style*="--header-text--"] { font-size:1rem; font-weight:bold }
//   div[style*="--menu-X--"]>button { ... }
// 所以 aside/menu/header 也必须是同一套 id 与标记。
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { apiGet } from '../api/client'
import { injectWallpaperToggle, hideWallpaperToggle } from '../lib/wallpaperToggle'
import { applyLayoutLayer } from '../lib/layoutApply'
import { putLoading } from '../lib/pywebio'
import { SchedulerFab } from './SchedulerFab'
import { MenuSearch } from './MenuSearch'

/**
 * 菜单分组的展开态（跨页面共享）。
 *
 * 为什么是模块级变量而不是组件 state：每个页面各自渲染一个 <Shell>，
 * 路由切换会卸载重建组件，组件内 state 直接归零 —— 表现就是
 * 「点开出击分组、点里面的主线图，整组缩回去，页面还跳一下」
 * （长卿 2026-11-20 反馈，实测复现：overview → 展开 Farm → 点主线图 → 全收起）。
 *
 * 用模块级变量而不是 localStorage：
 *   - 同一次会话内跨路由保留，正是需要的粒度；
 *   - 刷新页面回到「全收起」，与老 UI（PyWebIO 整页重载）一致；
 *   - 无序列化/跨标签页同步的额外复杂度。
 */
let persistedOpenGroups: Record<string, boolean> = {}

/**
 * 菜单/页眉/固定菜单/实例列表的上一次结果（跨页面共享）。
 *
 * 为什么需要：每个页面各自渲染一个 <Shell>，路由切换会卸载重建组件，
 * 所有 state 从空开始，要等 /newapi/menu 等接口回来才有内容。
 * 实测（2026-11-20，overview → 点主线图）：切换瞬间菜单 innerHTML
 * 从 8180 字符掉到 519（只剩搜索框），10 个分组全消失，约 0.9 秒后才回来。
 * 这正是长卿说的「还会大幅度刷新加跳动一下」。
 *
 * 修法：新实例初次渲染时先用上次的数据兜底，接口回来后覆盖。
 * 菜单内容对同一实例/语言是稳定的，所以复用是安全的。
 */
let cachedMenu: MenuGroup[] = []
let cachedFixedMenu: FixedMenuResp | null = null
let cachedHeader: HeaderResp | null = null
let cachedInstances: Instance[] = []

/** 调度器快启悬浮球开关（模块级单例 + localStorage 持久化）。
 *  总览页开关（home/index.tsx）与 Shell 的球显隐是两个组件，
 *  用 CustomEvent('alas-sched-fab-change') 双向同步。 */
const schedFabStore = {
  get(): boolean {
    try { return localStorage.getItem('alas_sched_fab_enabled') !== '0' } catch { return true }
  },
  set(v: boolean) {
    try { localStorage.setItem('alas_sched_fab_enabled', v ? '1' : '0') } catch { /* 隐私模式 */ }
    window.dispatchEvent(new CustomEvent('alas-sched-fab-change', { detail: v }))
  },
}
/** 总览页开关用（home/index.tsx 引不到 Shell 内部 state）。 */
export function toggleSchedFab() {
  schedFabStore.set(!schedFabStore.get())
}

/**
 * 菜单收起态（桌面端）：true = 菜单列收起、aside 下显示任务缩写片。
 * localStorage 持久记忆（跨标签/刷新都保持，长卿 2026-11-21 反馈 sessionStorage 在
 * 新标签/重开页面时会丢）；写入同时落 localStorage，读不到时按展开处理。
 */
let persistedMenuCollapsed = (() => {
  try { return localStorage.getItem('alas_newui_menu_collapsed') === '1' } catch { return false }
})()

/**
 * 收起态任务片的两字缩写（组 key → 短名）。仅中文文案可用；
 * 非中文（en/ja）退化为名称前两字符。日活/OS 为长卿 2026-11-21 定稿。
 */
const MENU_SHORT_ZH: Record<string, string> = {
  Overview: '总览',
  Alas: '智慧',
  Farm: '出击',
  Event: '活动',
  EventDaily: '日活',
  Reward: '收获',
  DailyMission: '每日',
  Opsi: 'OS',
  Island: '岛屿',
  FleetManagement: '舰队',
  Tool: '工具',
  HomePage: '主页',
  Update: '更新',
  Remote: '远程',
  Setting: '设置',
  Announcement: '公告',
  Utils: '开发',
}

type Instance = { name: string; state: number }
type InstancesResp = { instances: Instance[] }
type MenuGroup = { key: string; name: string; page: string; tasks: { key: string; name: string }[] }
type MenuResp = { groups: MenuGroup[] }
type HeaderResp = { icon: string; prefix: string; title: string }
type FixedMenuResp = {
  items: { key: string; name: string }[]
  aside_home: string
  aside_manage: string
  search_placeholder: string
}

// 侧栏图标 SVG（与 assets/gui/icon/*.svg 一致；老 UI 用 filepath_icon 读盘后内联）
const ICON = {
  develop: `<svg class="aside-icon icon-develop" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
    <rect x="140" y="180" width="744" height="520" rx="100" fill="none" stroke="currentColor" stroke-width="80"></rect>
    <path d="M360 380l-80 60 80 60M664 380l80 60-80 60M540 320l-60 240M340 820h344" fill="none" stroke="currentColor" stroke-width="80" stroke-linecap="round" stroke-linejoin="round"></path>
</svg>`,
  run: `<svg class="aside-icon icon-run" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
    <path d="M320 200c-40-22-90 6-90 52v520c0 46 50 74 90 52l440-260c40-22 40-78 0-100L320 200z" fill="none" stroke="currentColor" stroke-width="80" stroke-linejoin="round" stroke-linecap="round"></path>
</svg>`,
  running: `<svg class="aside-icon icon-running-state" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
    <circle cx="512" cy="512" r="420" fill="none" stroke="#2ECC71" stroke-width="120" stroke-linecap="round" stroke-dasharray="800 2640">
        <animateTransform attributeName="transform" type="rotate" from="0 512 512" to="360 512 512" dur="1.2s" repeatCount="indefinite"></animateTransform>
    </circle>
    <rect x="312" y="312" width="400" height="400" rx="80" fill="currentColor"></rect>
</svg>`,
  // 以下两个状态图标原样内联 assets/gui/icon/status_*.svg（老 UI utils.py:804
  // Icon.ERROR / Icon.UPDATE 同一份）。第一版只做了 running，错误/更新中
  // 状态全落到空闲图标 —— 长卿：「我总览的小圈呢」。
  error: `<svg class="aside-icon icon-run-error" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <mask id="play-error-mask-fixed">
            <rect width="1024" height="1024" fill="white" />
            <circle cx="740" cy="740" r="320" fill="black" />
        </mask>
    </defs>

    <path mask="url(#play-error-mask-fixed)" d="M320 200c-40-22-90 6-90 52v520c0 46 50 74 90 52l440-260c40-22 40-78 0-100L320 200z" fill="none" stroke="currentColor" stroke-width="80" stroke-linejoin="round" stroke-linecap="round"></path>

    <g class="error-badge">
        <circle cx="740" cy="740" r="260" fill="#FF4D4F" />
        <g stroke="white" stroke-width="80" stroke-linecap="round">
            <line x1="630" y1="630" x2="850" y2="850" />
            <line x1="850" y1="630" x2="630" y2="850" />
        </g>
    </g>
</svg>`,
  update: `<svg class="aside-icon icon-run-update-fly" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <mask id="play-mask-hole">
            <rect width="1024" height="1024" fill="white" />
            <circle cx="740" cy="740" r="320" fill="black" />
        </mask>
        
        <clipPath id="badge-circle-clip">
            <circle cx="740" cy="740" r="260" />
        </clipPath>
    </defs>

    <path mask="url(#play-mask-hole)" d="M320 200c-40-22-90 6-90 52v520c0 46 50 74 90 52l440-260c40-22 40-78 0-100L320 200z" fill="none" stroke="currentColor" stroke-width="80" stroke-linejoin="round" stroke-linecap="round"></path>

    <circle cx="740" cy="740" r="260" fill="#4A90E2" />

    <g clip-path="url(#badge-circle-clip)">
        <g fill="none" stroke="white" stroke-width="60" stroke-linecap="round" stroke-linejoin="round">
            <animateTransform 
                attributeName="transform" 
                type="translate" 
                from="0 400" 
                to="0 -400" 
                dur="1.2s" 
                repeatCount="indefinite" />
            
            <path d="M 740 854 L 740 615" /> 
            <path d="M 636 719 L 740 615 L 844 719" /> 
        </g>
    </g>
</svg>`,
  setting: `<svg class="aside-icon icon-setting" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
    <defs><mask id="gear-hole"><rect width="1024" height="1024" fill="white"></rect><circle cx="512" cy="512" r="175" fill="black"></circle></mask></defs>
    <g mask="url(#gear-hole)" fill="currentColor">
        <circle cx="512" cy="512" r="320"></circle>
        <rect x="432" y="72" width="160" height="880" rx="60" transform="rotate(0, 512, 512)"></rect>
        <rect x="432" y="72" width="160" height="880" rx="60" transform="rotate(45, 512, 512)"></rect>
        <rect x="432" y="72" width="160" height="880" rx="60" transform="rotate(90, 512, 512)"></rect>
        <rect x="432" y="72" width="160" height="880" rx="60" transform="rotate(135, 512, 512)"></rect>
    </g>
</svg>`,
}

/** put_icon_buttons 同构（module/webui/widgets.py:447-481）。
 * 外层 put_column（grid-template-rows: 0）→ 图标行（相对定位 + z-index 3/4）→
 * 按钮行（style 带 --aside-{value}--）。aside 高亮 CSS 靠的就是这个 style 标记。 */
function iconButton(icon: string, label: string, value: string, active: boolean): string {
  const stateIcon = ''
  return (
    `<div style="display: grid; grid-auto-flow: row; grid-template-rows: 0;">` +
    `<div style="position: relative; width: 4rem; display: flex; justify-content: center; pointer-events: none;">` +
    `<div style="z-index: 3;">${icon}</div>` +
    `<div style="z-index: 4; position: absolute; margin-left: 24px;">${stateIcon}</div>` +
    `</div>` +
    `<div style=";;z-index: 2; --aside-${value}--;">` +
    `<button class="btn btn-aside${active ? ' btn-aside-active' : ''}">${label}</button>` +
    `</div></div>`
  )
}

/** 二级菜单项（老 UI 的「管理」「开发者工具」等页面会在菜单区放自己的入口列表，
 *  而不是显示实例的配置树）。key 用于 --menu-{key}-- 高亮标记。 */
export type SubMenuItem = { key: string; name: string; onClick?: () => void }

/** 收起态任务片数据（D4a）：subs 非空 = 有二级（红点 + 弹层预览）。 */
type ChipItem = { key: string; name: string; short: string; subs?: { key: string; name: string }[] }

export function Shell({
  active,
  instance,
  title,
  children,
  submenu,
}: {
  active: string
  /**
   * 当前实例名（`ProcessManager` 里的 config_name）。
   *
   * 为什么必须显式传：`active` 是**高亮用的 key**，配置页传的是任务名
   * （Alas/Commission…）、开发者工具传的是 'Utils'、设置页传的是 'Setting'、
   * 集合页传的是 'CollectionEntry'，这些都不是实例名。不传就只能靠下面的
   * 「回退到 instNames[0]」兜底，而 alas_instance() 的顺序来自 os.listdir，
   * 第一个通常是 alas —— 于是一个**多实例**用户在小号配置页点任何菜单项，
   * 都会被带回 alas 的配置页（长卿 2026-11-25 实测：「点小号的联合作战
   * 能给我调到 alas 的实例配置页里」）。
   *
   * 结论：凡是有实例名的页面，必须把它传进来；只有真正的全局页
   * （更新器/远程控制/公告/管理）才允许省略。
   */
  instance?: string
  title: string
  children: React.ReactNode
  /** 传了就用它替换配置树菜单（老 UI 的 init_menu(name="ManageXxx") 同语义） */
  submenu?: SubMenuItem[]
}) {
  const navigate = useNavigate()
  // 总览页判定：调度器快启悬浮球在总览页不显示（长卿 2026-11-22）。
  // Overview 传的 active 是实例名，不能靠 active；直接看路由。
  const location = useLocation()
  const isOverviewRoute = /^\/overview\//.test(location.pathname)
  // 初值取模块级缓存：路由切换会重建本组件，若从空数组起步，
  // 菜单区会先塌成「只剩搜索框」再被接口内容填回来（实测 8180 → 519 字符，
  // 约 0.9 秒），表现为页面大幅跳动。用上次数据兜底可让切换瞬间菜单不消失。
  const [instances, setInstances] = useState<Instance[]>(() => cachedInstances)
  const [menu, setMenu] = useState<MenuGroup[]>(() => cachedMenu)
  // 移动端内容区与菜单互斥：菜单展开时给内容区挂 container-content-collapsed
  // （alas-mobile.css 里就是 display:none）。同一时刻只有一个可见。
  // 纯背景模式圆点：仅主页显示（app_home.py:519 老 UI 在 show_home 注入，
  // 离开主页由 base.py 导航钩子隐藏；React 里用 active 驱动等价语义）。
  useEffect(() => {
    applyLayoutLayer() // shell 挂载/切页时幂等纠正布局层（修复 theme-css 异步覆盖后回退）
    if (active === 'Home') injectWallpaperToggle()
    else hideWallpaperToggle()
  }, [active])

  useEffect(() => {
    if (!isMobile) return
    const ids = ['pywebio-scope-content', 'pywebio-scope-statistics-content']
    ids.forEach(id => {
      const el = document.getElementById(id)
      if (el) el.classList.toggle('container-content-collapsed', menuOpen)
    })
  })
  
  // 移动端判定与后端 /newapi/bundle.css 的 UA 判定保持一致（同一组 token）
  const [isMobile] = useState(() => {
    const ua = (navigator.userAgent || '').toLowerCase()
    return ['mobile', 'android', 'iphone', 'ipod', 'ipad', 'windows phone', 'blackberry'].some(t =>
      ua.includes(t),
    )
  })
  const [instNames, setInstNames] = useState<string[]>([])
  // 调度器快启悬浮球开关（长卿 2026-11-22：加个开关，总览页随处可见）。
  // 状态放模块级 store（schedFabStore）：总览页的开关与 Shell 的球显示
  // 是两个组件，用事件同步；localStorage 持久化。
  // 总览页（active==='Overview'）始终隐藏球，开关只管其他页面。
  const [schedFabOn, setSchedFabOn] = useState<boolean>(() => schedFabStore.get())
  useEffect(() => {
    const on = (ev: Event) => setSchedFabOn((ev as CustomEvent<boolean>).detail)
    window.addEventListener('alas-sched-fab-change', on)
    return () => window.removeEventListener('alas-sched-fab-change', on)
  }, [])
  // 固定菜单文案（主页 6 个入口 + 侧栏「主页/管理」）由后端按当前语言返回。
  // 以前这些是**前端硬编码中文**，导致切英文后配置页已经是 "For example,
  // 7-2..." 了，侧栏还写着「主页/更新器/远程控制」。
  // 同样用缓存兜底：固定菜单/页眉文案在切换瞬间若为空，
  // 菜单会短暂退回 6 个入口或空标题，视觉上也是「跳一下」。
  const [fixedMenu, setFixedMenu] = useState<FixedMenuResp>(() => cachedFixedMenu ?? {
    items: [],
    aside_home: '主页',
    aside_manage: '管理',
    search_placeholder: '搜索配置',
  })
  const [header, setHeader] = useState<HeaderResp>(() => cachedHeader ?? {
    icon: '/static/assets/spa/spa-icon-192x192.png',
    prefix: '待到山花烂漫时，她在丛中笑',
    title: 'AzurPilot',
  })

  // 语言切换后必须**重拉**菜单/页眉/实例：它们的文案由后端按当前语言翻译，
  // 不重拉就是「切了英文但侧栏还是中文」。老 UI 是整页重渲染，这里用事件通知。
  const [langTick, setLangTick] = useState(0)
  useEffect(() => {
    const onLang = () => setLangTick(v => v + 1)
    window.addEventListener('alas-language-change', onLang)
    return () => window.removeEventListener('alas-language-change', onLang)
  }, [])

  // 实例状态（侧栏图标：运行圈 / 空闲 / 异常角标 / 更新角标）需要**轮询**。
  //
  // 老 UI 用 task_handler 每 2 秒跑一次 set_aside_status
  // （app_home.py:2073），只在该实例状态变化时重绘那一个 scope。
  // 新 UI 原来只在挂载时拉一次 /newapi/instances，于是调度器启动后侧栏
  // 图标永远停在空闲态 —— 长卿：「这里运行的小圆圈记得需要加回去」。
  //
  // 轮询间隔取 3s（老 UI 是 2s；这里省一次请求，状态切换的延迟感知相同）。
  //
  // **不要按 document.hidden 跳过请求**：无头浏览器/后台标签页里 hidden 恒为
  // true，首帧那次 loadInstances 会直接 return，实例列表永远是空的 ——
  // 实测侧栏只剩「主页 / 管理」，alas 和小号整个消失（CDP 截图确认）。
  // 3 秒一次的本地请求开销可以忽略，老 UI 也是无条件轮询。
  useEffect(() => {
    let alive = true
    const loadInstances = () => {
      apiGet<InstancesResp>('/newapi/instances', 8000)
        .then(r => {
          if (!alive) return
          // 写回模块级缓存，供下次路由切换的首帧兜底（否则侧栏实例图标会闪一下空的）
          cachedInstances = r.instances
          // 状态没变就不 setState，避免每 3 秒重建 aside 的 HTML 字符串
          setInstances(prev => {
            const sig = (list: Instance[]) => list.map(i => i.name + ':' + i.state).join('|')
            return sig(prev) === sig(r.instances) ? prev : r.instances
          })
          setInstNames(prev => {
            const next = r.instances.map(i => i.name)
            return prev.join('|') === next.join('|') ? prev : next
          })
        })
        .catch(() => {})
    }
    loadInstances()
    const timer = setInterval(loadInstances, 3000)
    const onVis = () => { loadInstances() }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      alive = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [langTick])

  useEffect(() => {
    // 每份数据拿到后都写回模块级缓存，供下一次路由切换的首帧兜底。
    // 语言变化（langTick）时会重新拉取并覆盖，文案不会串语言。
    apiGet<MenuResp>('/newapi/menu')
      .then(r => { cachedMenu = r.groups; setMenu(r.groups) })
      .catch(() => {})
    apiGet<HeaderResp>('/newapi/header')
      .then(h => { cachedHeader = h; setHeader(h) })
      .catch(() => {})
    apiGet<FixedMenuResp>('/newapi/menu/fixed')
      .then(f => { cachedFixedMenu = f; setFixedMenu(f) })
      .catch(() => {})
  }, [langTick])

  // 当前实例名（菜单跳转用），优先级：
  //   ① 调用方显式传入的 instance —— 唯一可靠来源，见 Shell 参数说明；
  //   ② active 本身就是实例名（总览页、统计页就是这么传的）；
  //   ③ 回退到实例列表第一个（仅全局页/降级路径会走到，等于老 UI 行为）。
  const instanceName = instance || (instNames.includes(active) ? active : instNames[0] ?? 'alas')

  // 页眉状态圈（老 UI app_shell.py:318-359 set_status 的同构实现）。
  // 长卿：「在运行中，她在丛中笑那后面的小⭕呢，怎么不加回来？」
  // 老 UI 状态 → 样式映射（逐字照抄 340-349 行）：
  //   1 running  → spinner-border success（绿圈）；zh 系状态文字为空，只留圈
  //   2 inactive → spinner-border secondary + fill（**静止**灰圆环，不是转圈）
  //   3 warning  → spinner-grow warning（黄色呼吸圈）
  //   4 updating → spinner-grow success（绿色呼吸圈）
  //   0/其他 → 不渲染（主页 app_home.py:378 就是 set_status(0)）
  // 数据源：轮询 /newapi/instances 拿到的当前实例 state。
  // 主页不显示状态圈（老 UI app_home.py:378 show_home 里就是 set_status(0)）。
  // 之前只在实例页显示是对的，但第一版实现忘了排除主页/管理，实测主页挂着一个绿圈。
  // 只有**实例页**（active 是真实实例名）才显示状态圈 —— 与老 UI 对齐：
  // show_home / ui_manage / 公告弹窗等非实例页面均为 set_status(0)（不显示）。
  const isInstancePage = instances.some(i => i.name === active)
  const headerState = isInstancePage
    ? instances.find(i => i.name === active)?.state ?? 0
    : 0
  const headerStatusHtml = (() => {
    // 老 UI 用 pywebio 的 spinner-border/grow + text-success 等配色类；
    // 尺寸与钩子由 alas.css:1084-1100 的 --loading-*-- 规则给定，
    // 所以这里必须复用 putLoading 的逐字节同构 DOM，不能自己拼。
    // ⚠️ state=2（非活动）必须 fill=true —— 老 UI set_status 里 `fill = state == 2`，
    // 用的是 --loading-border-fill--（四边都有色的完整圆环，旋转看不出动静）。
    // 我之前用了带缺口（border-right-color: transparent）的 --loading-border--，
    // 于是实例停止后那个灰圈**还在转** —— 长卿 2026-11-17：
    // 「我的停止了，他那个圆圈还在转，那圆圈应该消失啊，他变灰了」。
    if (headerState === 1) return putLoading('border', 'success')
    if (headerState === 2) return putLoading('border', 'secondary', true)
    if (headerState === 3) return putLoading('grow', 'warning')
    if (headerState === 4) return putLoading('grow', 'success')
    return ''
  })()

  // 侧栏/菜单由 HTML 字符串注入，导航靠事件委托（与老 UI onclick 语义一致）。
  const asideRef = (el: HTMLDivElement | null) => {
    if (!el) return
    // ===== 菜单收起态（D4a）事件委托：防重复绑定，chips 悬停/点击/弹层 =====
    if (!(el as any).__alasChipBound) {
      ;(el as any).__alasChipBound = true
      // 悬停 0.2s 弹层预览（悬停=预览，不改变当前任务）
      el.addEventListener('mouseover', (ev: MouseEvent) => {
        if (!persistedMenuCollapsed) return
        const chip = (ev.target as HTMLElement).closest('.alas-menu-chip') as HTMLElement | null
        if (!chip) return
        if (hoverTimerRef.current) window.clearTimeout(hoverTimerRef.current)
        const key = chip.dataset.key ?? ''
        hoverTimerRef.current = window.setTimeout(() => {
          const item = chipItemsRef.current.find(x => x.key === key)
          if (!item) return
          // 无二级的项不弹窗（长卿 2026-11-21）：悬停只对有二级的片生效
          if (!item.subs || !item.subs.length) return
          const cr = chip.getBoundingClientRect()
          // 弹层用 fixed 定位挂 body（aside 有 overflow:auto 会裁掉溢出的弹层，
          // 且内容区卡片会盖住 aside 内的浮层——长卿 2026-11-21 反馈）
          setChipPop({ key, name: item.name, top: cr.top, left: cr.right + 8 })
        }, 200)
      })
      // 移出 0.26s 后收回；移入弹层不收回
      el.addEventListener('mouseout', (ev: MouseEvent) => {
        if (!persistedMenuCollapsed) return
        const chip = (ev.target as HTMLElement).closest('.alas-menu-chip') as HTMLElement | null
        if (!chip) return
        if (hoverTimerRef.current) window.clearTimeout(hoverTimerRef.current)
        const rel = ev.relatedTarget as HTMLElement | null
        if (rel && rel.closest && rel.closest('.alas-chip-pop')) return
        window.setTimeout(() => {
          setChipPop(cur => (cur && document.querySelector('.alas-chip-pop') && (document.querySelector('.alas-chip-pop') as HTMLElement).matches(':hover')) ? cur : null)
        }, 260)
      })
      // 点击=确认：片跳组内第一项/自身；弹层子项直达；展开按钮恢复菜单列
      el.addEventListener('click', (ev: MouseEvent) => {
        if (!persistedMenuCollapsed) return
        const t = ev.target as HTMLElement
        const expandBtn = t.closest('.alas-chip-expand') as HTMLElement | null
        if (expandBtn) {
          ev.stopPropagation()
          toggleMenuCollapsed()
          return
        }
        const popItem = t.closest('.alas-chip-pop-item') as HTMLElement | null
        if (popItem) {
          ev.stopPropagation()
          setChipPop(null)
          navigateMenuKeyRef.current?.(popItem.dataset.key ?? '')
          return
        }
        const chip = t.closest('.alas-menu-chip') as HTMLElement | null
        if (!chip) return
        setChipPop(null)
        const key = chip.dataset.key ?? ''
        const item = chipItemsRef.current.find(x => x.key === key)
        if (!item) return
        navigateMenuKeyRef.current?.(item.subs && item.subs.length ? item.subs[0].key : key)
      })
      document.addEventListener('keydown', (ev: KeyboardEvent) => {
        if (ev.key === 'Escape') setChipPop(null)
      })
    }
    el.onclick = (ev) => {
      const btnEl = (ev.target as HTMLElement).closest('button')
      if (!btnEl) return
      // ⚠️ 必须按 **key** 判断，不能按按钮文案判断。
      // 文案随界面语言变化（「主页」↔ "Home"），原来写 label === '主页'
      // 在切英文后所有侧栏导航全部失效 —— 点 Home 什么都不会发生。
      const holder = btnEl.closest('[style*="--aside-"]') as HTMLElement | null
      const asideKey = (holder?.getAttribute('style') ?? '').match(/--aside-(.+?)--/)?.[1]
      // 移动端：侧栏顶部的「主页」图标在 alas-mobile.css 下退化为「菜单开关」
      // （老 UI app_developer_menu.py:78-84 的 ui_develop 二次点击语义：
      //  点一次展开菜单/内容，再点一次收起）。
      // 移动端：只有**已激活**的主页图标才退化为菜单开关（老 UI
      // app_developer_menu.py:78-84 的语义：再次点击已激活的侧栏图标仅展开
      // 二级菜单，不重渲染内容）。
      // ⚠️ 原来漏了 active === 'Home' 这个前提，导致在统计/配置等页面点「主页」
      // 只切换菜单、不跳转，看起来像「点主页给我跳到总览里去了」
      // （长卿 2026-11-17 实测：在 #/stat/alas 点主页，hash 未变）。
      if (isMobile && asideKey === 'Home' && active === 'Home') {
        setMenuOpen(v => !v)
        return
      }
      if (asideKey === 'Home') { navigate('/'); return }
      if (asideKey === 'Manage') { navigate('/manage'); return }
      if (asideKey) {
        // 实例按钮（asideKey = 实例名），语义对齐老 UI ui_alas
        // （module/webui/app_instances.py:41-45）：
        //   点的实例 == 当前实例 → expand_menu() 展开二级任务菜单，**不重渲染内容**；
        //   点的实例 != 当前实例 → 切换实例进它的总览。
        //
        // 之前一律 navigate('/overview/...')，已在总览页时同路由重渲染 = 长卿
        // 2026-11-19 图三的「闪一下回到总览」，任务菜单永远出不来，手机端没法
        // 进任务配置（只能开电脑模式操作）。
        if (asideKey === active) {
          if (isMobile) setMenuOpen(true) // 老 UI expand_menu：只展开不收起
          return
        }
        navigate('/overview/' + encodeURIComponent(asideKey))
      }
    }
  }
  const menuRef = (el: HTMLDivElement | null) => {
    if (!el) return
    // 分组展开态：用 toggle 事件的**捕获阶段**监听（toggle 不冒泡，但捕获能收到）。
    // 这样拿到的一定是翻转后的真实状态，不用猜、不会与原生行为打架。
    // 必须同步进 openGroups，否则 menuHtml 每次重建（dangerouslySetInnerHTML）
    // 都会按旧状态写 open 属性 → 点二级菜单跳转后分组「缩回去」（长卿 2026-11-19）。
    // 防止 callback ref 重复调用时叠加监听（同一元素只绑一次）。
    // ⚠️ 守卫只能包住 addEventListener；onclick 是**赋值**，天然幂等，必须放在
    // 守卫外面。它曾经写在守卫里面，于是 React 复用同一个 #pywebio-scope-menu
    // 节点跨路由时（hash 路由切换不重载页面），callback ref 再被调用一次就被
    // 直接 return 掉，onclick 永远停在**首次渲染那份闭包**上 —— 里面捕获的
    // instanceName 跟着过期。以前看不出来是因为 instanceName 靠
    // instNames[0] ?? 'alas' 兜底、等于常量；2026-11-25 改成读页面实例名后
    // 立刻暴露：在 alas 的配置页点「委托」会跳到小号去。
    if (!(el as any).__alasToggleBound) {
      ;(el as any).__alasToggleBound = true
      el.addEventListener('toggle', (ev: Event) => {
        const d = ev.target as HTMLDetailsElement
        const gk = d?.dataset?.group
        if (gk && d.tagName === 'DETAILS') {
          setOpenGroups(v => ({ ...v, [gk]: d.open }))
        }
      }, true)
    }
    el.onclick = (ev) => {
      // 分组标题（summary）：只切换展开态，不跳转、不收起其它分组
      const summary = (ev.target as HTMLElement).closest('summary')
      if (summary) {
        const details = summary.parentElement as HTMLDetailsElement | null
        // 展开态的同步交给下面的 toggle 监听（原生 toggle 在 click 之后才发生，
        // 这里读 details.open 只会读到旧值）。这里只负责「不要当成按钮跳转」。
        void details
        return
      }
      const btnEl = (ev.target as HTMLElement).closest('button')
      if (!btnEl) return
      const holder = btnEl.closest('[style*="--menu-"]') as HTMLElement | null
      const mark = holder?.getAttribute('style') ?? ''
      const m = mark.match(/--menu-(.+?)--/)
      if (!m) return
      const key = m[1]
      // ⚠️ 点二级菜单项时，必须先把它所属分组标记为展开再跳转。
      // 根因（长卿 2026-11-19）：「点一个主线图它会瞬间缩回去，第二次点才好」——
      // 菜单是 dangerouslySetInnerHTML 注入的字符串，跳转后 active 变化触发
      // React 重渲染 → innerHTML 整体重建 → <details> 回到 openGroups 记录的状态。
      // 而 openGroups 只有点 summary 时才更新，点菜单项时是空的 → 分组收起。
      // 这里把当前按钮所在的 details 分组写进 openGroups，重建后就保持展开。
      const holderGroup = btnEl.closest('details') as HTMLDetailsElement | null
      const holderKey = holderGroup?.dataset.group
      if (holderKey) setOpenGroups(v => (v[holderKey] ? v : { ...v, [holderKey]: true }))

      // 二级菜单项：先看 submenu 里有没有注册回调
      const sub = submenu?.find(it => it.key === key)
      if (sub?.onClick) { sub.onClick(); return }
      if (key === 'Overview') { navigate('/overview/' + encodeURIComponent(instanceName)); return }
      // 活动计算器是**纯入口项**（后端 get_menu 插在「活动通用设置」之后），
      // 不是配置任务 —— 它跳独立页 /event-calc/:name，不进配置页。
      // 长卿 2026-11-20：放主页层侧栏是错的位置，用户根本不会往那儿找；
      // 正确位置是配置树「活动」分组里、紧跟「活动通用设置」。
      if (key === 'EventCalcEntry') { navigate('/event-calc/' + encodeURIComponent(instanceName)); return }
      // 集合页入口：把多个占位置的菜单项收成一个页签页
      // （其他配置 / 主线图 / 紧急委托 / 活动图 / 活动每日）
      if (COLLECTION_ROUTES[key]) {
        navigate('/' + COLLECTION_ROUTES[key] + '/' + encodeURIComponent(instanceName))
        return
      }
      if (key === 'HomePage') {
        // 清掉「上次页面」记录：否则下次从 /new/ 进来又被带回子页面，像回不到主页
        try { sessionStorage.removeItem('alas_newui_last_route') } catch { /* 隐私模式 */ }
        navigate('/')
        return
      }
      if (key === 'Update') { navigate('/update'); return }
      if (key === 'Remote') { navigate('/remote'); return }
      if (key === 'Setting') { navigate('/settings/' + encodeURIComponent(instanceName)); return }
      if (key === 'Announcement') { navigate('/announcement'); return }
      if (key === 'Utils') { navigate('/tool/' + encodeURIComponent(instanceName)); return }
      // page=tool 的任务（半自动点击/性能测试/活动剧情…）在老 UI 里走
      // alas_daemon_overview——配置表单 + 调度器启停 + 日志，不是开发者工具页。
      // 它们必须带上任务名进 /tool/:name/:task，否则只能看到调试按钮。
      const toolGroup = menu.find(g => g.page === 'tool' && g.tasks.some(t => t.key === key))
      if (toolGroup) {
        navigate(`/tool/${encodeURIComponent(instanceName)}/${encodeURIComponent(key)}`)
        return
      }
      navigate(`/config/${encodeURIComponent(instanceName)}/${encodeURIComponent(key)}`)
    }
  }

  // 侧栏实例图标随运行状态切换 —— 与老 UI utils.py:804 Icon 类同四种状态：
  //   state 1 = 运行中（绿圈旋转）/ 3 = 异常（播放键+红角标）/ 4 = 更新中（蓝角标）/
  //   其他 = 空闲（纯播放键）。第一版只判断 state===1，错误与更新中全落到空闲。
  const instanceIcon = (state: number) =>
    state === 1 ? ICON.running : state === 3 ? ICON.error : state === 4 ? ICON.update : ICON.run

  // 主页与老 UI 一致：菜单收起（base.py:540 对 HomePage 加 container-menu-collapsed）。
  // 注意老 UI 只是加类，菜单 DOM 仍在（移动端 CSS 把它 display:none）。
  // 桌面端：老 UI 实测主页与总览页都带 container-menu-collapsed，
  // 但 alas-mobile.css 才把它 display:none，桌面端无影响。
  // 移动端：这个 class 就是「菜单是否收起」的真开关（见 alas-mobile.css:1-7），
  // 由侧栏顶部的按钮切换（老 UI base.py:540-551 的 init_menu/expand_menu 同款语义）。
  // 移动端：菜单与内容**互斥**显示（老 UI base.py:init_aside 的语义）：
  //   展开菜单 → $('.container-menu-collapsed').removeClass(...)
  //             + $('#pywebio-scope-content').addClass('container-content-collapsed')
  //   收起菜单 → 反过来
  // 桌面端：两者同时存在（aside/menu/content 三栏），menuClass 永远是 collapsed
  //        这个类只在 alas-mobile.css 里是 display:none，桌面无影响。
  const [menuOpen, setMenuOpen] = useState(false)  // 移动端专用：菜单是否展开
  // 桌面端菜单伸缩（D4a 定稿：默认展开；点把手收起 → 菜单列宽度归零，
  // aside 下方出现当前入口的任务缩写片；悬停 0.2s 弹层预览二级，点击确认）。
  // 长卿 2026-11-21：「旁边这个长条不要加」——收起态不留窄条，宽度直接为 0。
  const [menuCollapsed, setMenuCollapsed] = useState(persistedMenuCollapsed)
  // 弹层：当前悬停/点击的片（组 key 或固定入口 key），null = 关闭
  const [chipPop, setChipPop] = useState<{ key: string; name: string; top: number; left: number } | null>(null)
  const hoverTimerRef = useRef<number | null>(null)
  // asideRef 的事件监听只绑一次，闭包里引用最新数据用 ref 中转
  const chipItemsRef = useRef<ChipItem[]>([])
  const navigateMenuKeyRef = useRef<((key: string) => void) | null>(null)
  const toggleMenuCollapsed = () => {
    setMenuCollapsed(v => {
      const next = !v
      persistedMenuCollapsed = next
      try { localStorage.setItem('alas_newui_menu_collapsed', next ? '1' : '0') } catch { /* 隐私模式 */ }
      return next
    })
    setChipPop(null)
  }
  // 菜单折叠状态：**必须由 React 管理**。
  // 坑：原来直接输出 `<details>` 字符串，React 每次重渲染都会重建这些 DOM，
  // 浏览器的 open 属性随之丢失 —— 表现就是「点了一个二级菜单项，
  // 其他展开的分组全被收起了」（长卿反馈）。改成受控状态后，重渲染不影响展开态。
  //
  // ⚠️ 2026-11-20 第二次踩：这个状态只活在本组件实例里。每个页面（总览/配置/
  // 集合页/统计…）都各自挂一个 <Shell>，路由切换 = 组件卸载重建 = openGroups
  // 归零。表现就是长卿说的「点二级菜单（比如出击→主线图）整组缩回去」，
  // 而且因为菜单区 DOM 被重建，右侧内容高度突变，页面会大幅跳动一下。
  // 实测复现：从 #/overview/alas 展开「出击」点「主线图」→ Farm 从 open 变全收起。
  // 修法：用模块级变量做跨实例持久化（比 localStorage 快、无序列化开销，
  // 刷新页面按「默认全收起」处理，与老 UI 行为一致）。
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(
    () => ({ ...persistedOpenGroups }),
  )
  // 每次变化都同步回模块级变量（同一次会话内跨路由保留）
  useEffect(() => { persistedOpenGroups = openGroups }, [openGroups])
  const menuClass = isMobile
    ? (menuOpen ? '' : 'container-menu-collapsed')
    : 'container-menu-collapsed'
  // 桌面端伸缩：alas-menu-x 收起（宽度归零、无长条——长卿 2026-11-21）。
  // 展开态不加类，菜单列照常显示。
  const menuXClass = !isMobile && menuCollapsed ? ' alas-menu-x' : ''


  // ===== 菜单收起态（D4a 定稿）=====
  // 收起态任务片数据：与 menuHtml 同一套三分支数据源。
  // subs 非空 = 有二级（片上加红点 + 悬停弹层预览）。

  // 菜单树：老 UI 有两套，必须区分（实测 22267 真实 DOM）。
  //   ① 主页（HomePagePage）：6 个固定入口，无搜索框，--menu-HomePage/Update/Remote/Setting/Announcement/Utils
  //   ② 实例页：配置搜索框 + 完整配置树，分组用 <details><summary>，首项 --menu-Overview
  // 文案来自后端 /newapi/menu/fixed（按当前界面语言翻译）。
  // 兜底数组仅在接口不可用时使用（老后端）。
  const HOME_MENU: { key: string; name: string }[] =
    fixedMenu.items.length > 0
      ? fixedMenu.items
      : [
          { key: 'HomePage', name: '主页' },
          { key: 'Update', name: '更新器' },
          { key: 'Remote', name: '远程控制' },
          { key: 'Setting', name: '设置' },
          { key: 'Announcement', name: '公告' },
          { key: 'Utils', name: '开发者工具' },
        ]

  // 「主页层」页面集合：这些页面共用外侧那 6 个固定入口菜单
  const FIXED_PAGE_KEYS = new Set(['Home', 'Update', 'Remote', 'Setting', 'Announcement', 'Utils'])

  // 集合页入口 key → 路由段（长卿 2026-11-20：把占位置的菜单项收成页签页）
  const COLLECTION_ROUTES: Record<string, string> = {
    OtherConfigEntry: 'config-other',
    MainChaptersEntry: 'main-chapters',
    GemsFarmingEntry: 'gems-farming',
    EventStagesEntry: 'event-stages',
    EventDailyEntry: 'event-daily',
  }

  const menuItem = (key: string, name: string) =>
    `<div style=";;--menu-${key}--;"><button class="btn btn-menu${
      (active === key || (active === 'Home' && key === 'HomePage')) ? ' btn-menu-active' : ''
    }">${name}</button></div>`

  // 是否渲染侧栏搜索框：与 menuHtml 的「实例页」分支同条件
  // （二级菜单页 / 固定入口页都不显示，与老 UI 一致）。
  const showMenuSearch = !(submenu && submenu.length) && !FIXED_PAGE_KEYS.has(active)

  const menuItemsHtml =
    submenu && submenu.length
      ? // 二级菜单（管理/开发者工具等）：老 UI 用 dev_set_menu 往菜单区放按钮，
        // 不显示配置树。高亮仍走 div[style*="--menu-{key}--"] > button 约定。
        submenu.map(it => menuItem(it.key, it.name)).join('')
    : FIXED_PAGE_KEYS.has(active)
      ? // 固定入口页（主页/更新器/远程控制/设置/公告/开发者工具）都属于「主页层」，
        // 菜单区**始终**是这 6 个入口 —— 老 UI 就是这样（固定菜单一棵树，切页只换
        // 内容区）。之前的 bug：条件写成 active === 'Home'，进更新器后 active='Update'
        // → 走 else 渲染**实例配置树**（总览/智慧港区/出击…），看起来像「点什么都跳到同
        // 一个页面」（长卿 2026-11-18 报告）。
        HOME_MENU.map(it => menuItem(it.key, it.name)).join('')
      : // 实例页：搜索框（MenuSearch 单独渲染）+ 配置树
        menuItem('Overview', '总览') +
        menu
          .map(g => {
            const btn = menuItem(g.key, g.name)
            if (!g.tasks.length) return btn
            const children = g.tasks.map(t => menuItem(t.key, t.name)).join('')
            const open = openGroups[g.key] ? ' open' : ''
            // data-group 供事件委托识别展开/收起
            return `<details data-group="${g.key}"${open}><summary>${g.name}</summary>${children}</details>`
          })
          .join('')

  const chipItems = (): ChipItem[] => {
    const shortOf = (key: string, name: string) => {
      const zh = MENU_SHORT_ZH[key]
      // 中文映射优先；非中文文案退化为前两字符
      if (zh && /[\u4e00-\u9fa5]/.test(name)) return zh
      return name.slice(0, 2)
    }
    if (submenu && submenu.length) {
      return submenu.map(it => ({ key: it.key, name: it.name, short: shortOf(it.key, it.name) }))
    }
    if (FIXED_PAGE_KEYS.has(active)) {
      return HOME_MENU.map(it => ({ key: it.key, name: it.name, short: shortOf(it.key, it.name) }))
    }
    const items: ChipItem[] = []
    items.push({ key: 'Overview', name: '总览', short: shortOf('Overview', '总览') })
    for (const g of menu) {
      if (!g.tasks.length) {
        items.push({ key: g.key, name: g.name, short: shortOf(g.key, g.name) })
      } else {
        items.push({ key: g.key, name: g.name, short: shortOf(g.key, g.name), subs: g.tasks })
      }
    }
    return items
  }
  const chipItemsList = chipItems()

  // 与 menuRef 委托同一套路由表（抽函数避免两处维护）
  const navigateMenuKey = (key: string) => {
    const sub = submenu?.find(it => it.key === key)
    if (sub?.onClick) { sub.onClick(); return }
    if (key === 'Overview') { navigate('/overview/' + encodeURIComponent(instanceName)); return }
    if (key === 'EventCalcEntry') { navigate('/event-calc/' + encodeURIComponent(instanceName)); return }
    if (COLLECTION_ROUTES[key]) {
      navigate('/' + COLLECTION_ROUTES[key] + '/' + encodeURIComponent(instanceName))
      return
    }
    if (key === 'HomePage') {
      try { sessionStorage.removeItem('alas_newui_last_route') } catch { /* 隐私模式 */ }
      navigate('/')
      return
    }
    if (key === 'Update') { navigate('/update'); return }
    if (key === 'Remote') { navigate('/remote'); return }
    if (key === 'Setting') { navigate('/settings/' + encodeURIComponent(instanceName)); return }
    if (key === 'Announcement') { navigate('/announcement'); return }
    if (key === 'Utils') { navigate('/tool/' + encodeURIComponent(instanceName)); return }
    const toolGroup = menu.find(g => g.page === 'tool' && g.tasks.some(t => t.key === key))
    if (toolGroup) {
      navigate(`/tool/${encodeURIComponent(instanceName)}/${encodeURIComponent(key)}`)
      return
    }
    navigate(`/config/${encodeURIComponent(instanceName)}/${encodeURIComponent(key)}`)
  }
  // 事件闭包引用最新值（监听只绑一次）
  chipItemsRef.current = chipItemsList
  navigateMenuKeyRef.current = navigateMenuKey

  // 弹层内容：该入口的二级任务列表（点击直达子项）
  const chipPopHtml = (() => {
    if (!chipPop) return ''
    const item = chipItemsList.find(x => x.key === chipPop.key)
    const subs = item?.subs ?? []
    const rows = subs.length
      ? subs.map(t => `<button class="alas-chip-pop-item" data-key="${t.key}">${t.name}</button>`).join('')
      : `<div class="alas-chip-pop-empty">（无二级）</div>`
    // 标题行右侧加 ×：点它关掉弹层（长卿 2026-11-22 要求）。
    // 事件在容器 onClick 里按 .alas-chip-pop-close 分派（与 pop-item 同套委托）。
    return (
      `<div class="alas-chip-pop-head">` +
      `<span class="alas-chip-pop-title">${chipPop.name}</span>` +
      `<button type="button" class="alas-chip-pop-close" title="关闭">×</button>` +
      `</div>` +
      rows
    )
  })()

  const asideHtml =
    `<div id="pywebio-scope-aside_home">` +
    iconButton(ICON.develop, fixedMenu.aside_home, 'Home', active === 'Home') +
    `</div>` +
    `<div id="pywebio-scope-aside_instance">` +
    instances
      .map((inst, i) =>
        `<div id="pywebio-scope-alas-instance-${i}">` +
        iconButton(instanceIcon(inst.state), inst.name, inst.name, active === inst.name) +
        `</div>`,
      )
      .join('') +
    `</div>` +
    `<div id="pywebio-scope-aside_manage">` +
    iconButton(ICON.setting, fixedMenu.aside_manage, 'Manage', active === 'Manage') +
    `</div>` +
    // 收起态任务片区（桌面端）：divider + 任务片滚动区 + 展开钮固定底部。
    // 展开态整体隐藏（CSS 控制），不干扰原侧栏。
    `<div class="alas-chip-zone${menuCollapsed ? ' alas-chip-zone-open' : ''}">` +
    `<div class="alas-chip-divider"></div>` +
    `<div class="alas-chip-list">` +
    chipItemsList
      .map(it => {
        const on = active === it.key || (active === 'Home' && it.key === 'HomePage')
          || (() => {
            // 实例页：当前任务 key 命中组内任务时高亮该组片
            if (!it.subs) return false
            return it.subs.some(t => t.key === active)
          })()
        return `<button class="alas-menu-chip${on ? ' alas-menu-chip-on' : ''}${it.subs?.length ? ' alas-menu-chip-has-sub' : ''}"` +
          ` data-key="${it.key}" title="${it.name}">${it.short}</button>`
      })
      .join('') +
    `</div>` +
    `<button class="alas-chip-expand" title="展开菜单" aria-label="展开菜单">›</button>` +
    `</div>`

  // 页眉标题：老 UI 是 set_title(t(f"Task.{task}.name")) —— 显示**当前任务的中文名**
  // （主线图 / 紧急委托 / 大世界…），不是笼统的「配置」。
  // 之前配置页传了固定的 title="配置"，所有任务页眉都一样，分不清在看哪个
  // （长卿 2026-11-19：「除了总览和统计，其他的都是配置这东西根本分不清楚」）。
  const resolvedTitle = (() => {
    if (title && title !== '配置') return title
    for (const g of menu) {
      const hit = g.tasks.find(x => x.key === active)
      if (hit) return hit.name
    }
    return title
  })()

  return (
    <div className="pywebio">
      <div id="output-container" className="container no-fix-height">
        <div id="markdown-body" className="markdown-body">
          <div id="pywebio-scope-ROOT">
            <div id="pywebio-scope-header">
              <img className="alas-icon" src={header.icon} alt="AzurPilot" width={42} height={42} style={{ ['--header-icon--' as string]: '1' } as React.CSSProperties} />
              <p style={{ whiteSpace: 'pre-wrap', ['--header-text--' as string]: '1' } as React.CSSProperties}>{header.prefix}</p>
              <div
                id="pywebio-scope-header_status"
                dangerouslySetInnerHTML={{ __html: headerStatusHtml }}
              />
              <div id="pywebio-scope-header_title"><p style={{ whiteSpace: 'pre-wrap' }}>{resolvedTitle}</p></div>
            </div>
            <div id="pywebio-scope-contents">
              <div id="pywebio-scope-aside" ref={asideRef} dangerouslySetInnerHTML={{ __html: asideHtml }} />
              <div id="pywebio-scope-menu" ref={menuRef} className={menuClass + menuXClass}>
                {/* 配置搜索：菜单树仍用旧的字符串注入（一行没动，避免碰菜单渲染路径），
                    只把搜索框这一块换成真组件（长卿 2026-11-28：原来完全没反应）。 */}
                {showMenuSearch && <MenuSearch instance={instanceName} />}
                <div id="pywebio-scope-task_config_menu_items" dangerouslySetInnerHTML={{ __html: menuItemsHtml }} />
              </div>
              {!isMobile &&
                <button
                  type="button"
                  className={'alas-menu-handle' + (menuCollapsed ? ' alas-menu-handle-x' : '')}
                  aria-label={menuCollapsed ? '展开菜单' : '收起菜单'}
                  title={menuCollapsed ? '展开菜单' : '收起菜单'}
                  onClick={toggleMenuCollapsed}
                >{menuCollapsed ? '›' : '‹'}</button>
              }
              {!isMobile && chipPop &&
                <div className="alas-chip-pop" style={{ top: chipPop.top, left: chipPop.left }}
                  ref={(el) => {
                    // 防截断：弹层底部超出视口时上移（长卿 2026-11-21：底部显示一半）。
                    // 弹层切换目标（key 变）时重新校正。
                    if (!el) return
                    const curKey = chipPop.key
                    if ((el as any).__alasPopKey === curKey) return
                    ;(el as any).__alasPopKey = curKey
                    const r = el.getBoundingClientRect()
                    const vh = window.innerHeight
                    if (r.bottom > vh - 8) {
                      const shifted = Math.max(8, vh - r.height - 8)
                      setChipPop(cur => (cur ? { ...cur, top: shifted } : cur))
                    }
                  }}
                  onClick={(ev) => {
                    // × 关闭按钮：先于条目分派（长卿 2026-11-22 要求弹层可关）
                    if ((ev.target as HTMLElement).closest('.alas-chip-pop-close')) {
                      ev.stopPropagation()
                      setChipPop(null)
                      return
                    }
                    const item = (ev.target as HTMLElement).closest('.alas-chip-pop-item') as HTMLElement | null
                    if (!item) return
                    ev.stopPropagation()
                    setChipPop(null)
                    navigateMenuKey(item.dataset.key ?? '')
                  }}
                  dangerouslySetInnerHTML={{ __html: chipPopHtml }} />
              }
              {children}
              {/* 调度器快启悬浮球：总览页不显示（长卿：总览那块不要显示，
                  切到别的页面才显示）；开关在总览页调度器卡片上。
                  ⚠️ 不能用 active 判断：Overview 传的 active 是实例名不是 'Overview'，
                  这里直接看路由路径（/overview/:name 才是总览页）。 */}
                <SchedulerFab instances={instances} visible={schedFabOn && !isOverviewRoute} />
              <div id="pywebio-scope-statistics-content" style={{ display: 'none' }}></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}