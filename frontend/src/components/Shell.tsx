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
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../api/client'
import { injectWallpaperToggle, hideWallpaperToggle } from '../lib/wallpaperToggle'
import { applyLayoutLayer } from '../lib/layoutApply'
import { putLoading } from '../lib/pywebio'

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

export function Shell({
  active,
  title,
  children,
  submenu,
}: {
  active: string
  title: string
  children: React.ReactNode
  /** 传了就用它替换配置树菜单（老 UI 的 init_menu(name="ManageXxx") 同语义） */
  submenu?: SubMenuItem[]
}) {
  const navigate = useNavigate()
  const [instances, setInstances] = useState<Instance[]>([])
  const [menu, setMenu] = useState<MenuGroup[]>([])
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
  // 固定菜单文案（主页 6 个入口 + 侧栏「主页/管理」）由后端按当前语言返回。
  // 以前这些是**前端硬编码中文**，导致切英文后配置页已经是 "For example,
  // 7-2..." 了，侧栏还写着「主页/更新器/远程控制」。
  const [fixedMenu, setFixedMenu] = useState<FixedMenuResp>({
    items: [],
    aside_home: '主页',
    aside_manage: '管理',
    search_placeholder: '搜索配置',
  })
  const [header, setHeader] = useState<HeaderResp>({
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
    apiGet<MenuResp>('/newapi/menu').then(r => setMenu(r.groups)).catch(() => {})
    apiGet<HeaderResp>('/newapi/header').then(setHeader).catch(() => {})
    apiGet<FixedMenuResp>('/newapi/menu/fixed').then(setFixedMenu).catch(() => {})
  }, [langTick])

  // 当前实例名（菜单跳转用）：优先当前页 active，回退第一个实例
  const instanceName = instNames.includes(active) ? active : instNames[0] ?? 'alas'

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
    // 防止 callback ref 重复调用时叠加监听（同一元素只绑一次）
    if ((el as any).__alasToggleBound) return
    ;(el as any).__alasToggleBound = true
    el.addEventListener('toggle', (ev: Event) => {
      const d = ev.target as HTMLDetailsElement
      const gk = d?.dataset?.group
      if (gk && d.tagName === 'DETAILS') {
        setOpenGroups(v => ({ ...v, [gk]: d.open }))
      }
    }, true)
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
  // 菜单折叠状态：**必须由 React 管理**。
  // 坑：原来直接输出 `<details>` 字符串，React 每次重渲染都会重建这些 DOM，
  // 浏览器的 open 属性随之丢失 —— 表现就是「点了一个二级菜单项，
  // 其他展开的分组全被收起了」（长卿反馈）。改成受控状态后，重渲染不影响展开态。
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({})
  const menuClass = isMobile
    ? (menuOpen ? '' : 'container-menu-collapsed')
    : 'container-menu-collapsed'

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

  const menuHtml =
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
      : // 实例页：搜索框 + 配置树
        `<div id="pywebio-scope-task_config_search">` +
        `<div class="form-group" style=";;--task-config-search-input--">` +
        `<input type="text" class="form-control" name="config_search_keyword" value="" placeholder="${fixedMenu.search_placeholder}">` +
        `<div class="invalid-feedback"></div><div class="valid-feedback"></div>` +
        `<small class="form-text text-muted"></small></div></div>` +
        `<div id="pywebio-scope-task_config_search_results"></div>` +
        `<div id="pywebio-scope-task_config_menu_items">` +
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
          .join('') +
        `</div>`

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
              <div id="pywebio-scope-menu" ref={menuRef} className={menuClass} dangerouslySetInnerHTML={{ __html: menuHtml }} />
              {children}
              <div id="pywebio-scope-statistics-content" style={{ display: 'none' }}></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
