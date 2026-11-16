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

type Instance = { name: string; state: number }
type InstancesResp = { instances: Instance[] }
type MenuGroup = { key: string; name: string; page: string; tasks: { key: string; name: string }[] }
type MenuResp = { groups: MenuGroup[] }
type HeaderResp = { icon: string; prefix: string; title: string }

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

  useEffect(() => {
    apiGet<InstancesResp>('/newapi/instances').then(r => { setInstances(r.instances); setInstNames(r.instances.map(i => i.name)) }).catch(() => {})
    apiGet<MenuResp>('/newapi/menu').then(r => setMenu(r.groups)).catch(() => {})
    apiGet<HeaderResp>('/newapi/header').then(setHeader).catch(() => {})
  }, [langTick])

  // 当前实例名（菜单跳转用）：优先当前页 active，回退第一个实例
  const instanceName = instNames.includes(active) ? active : instNames[0] ?? 'alas'

  // 侧栏/菜单由 HTML 字符串注入，导航靠事件委托（与老 UI onclick 语义一致）。
  const asideRef = (el: HTMLDivElement | null) => {
    if (!el) return
    el.onclick = (ev) => {
      const btnEl = (ev.target as HTMLElement).closest('button')
      if (!btnEl) return
      const label = (btnEl.textContent ?? '').trim()
      // 移动端：侧栏顶部的「主页」图标在 alas-mobile.css 下退化为「菜单开关」
      // （老 UI app_developer_menu.py:78-84 的 ui_develop 二次点击语义：
      //  点一次展开菜单/内容，再点一次收起）。
      if (isMobile && label === '主页') {
        setMenuOpen(v => !v)
        return
      }
      if (label === '主页') { navigate('/'); return }
      if (label === '管理') { navigate('/manage'); return }
      // 实例按钮：进对应实例的总览页
      const holder = btnEl.closest('[style*="--aside-"]') as HTMLElement | null
      const mark = holder?.getAttribute('style') ?? ''
      const m = mark.match(/--aside-(.+?)--/)
      if (m && m[1] !== 'Home' && m[1] !== 'Manage') navigate('/overview/' + encodeURIComponent(m[1]))
    }
  }
  const menuRef = (el: HTMLDivElement | null) => {
    if (!el) return
    el.onclick = (ev) => {
      // 分组标题（summary）：只切换展开态，不跳转、不收起其它分组
      const summary = (ev.target as HTMLElement).closest('summary')
      if (summary) {
        const details = summary.parentElement as HTMLDetailsElement | null
        const groupKey = details?.dataset.group
        if (groupKey) {
          // 用 React 状态驱动，避免直接改 DOM 被下次渲染覆盖
          setOpenGroups(v => ({ ...v, [groupKey]: !v[groupKey] }))
        }
        return
      }
      const btnEl = (ev.target as HTMLElement).closest('button')
      if (!btnEl) return
      const holder = btnEl.closest('[style*="--menu-"]') as HTMLElement | null
      const mark = holder?.getAttribute('style') ?? ''
      const m = mark.match(/--menu-(.+?)--/)
      if (!m) return
      const key = m[1]
      // 二级菜单项：先看 submenu 里有没有注册回调
      const sub = submenu?.find(it => it.key === key)
      if (sub?.onClick) { sub.onClick(); return }
      if (key === 'Overview') { navigate('/overview/' + encodeURIComponent(instanceName)); return }
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
    iconButton(ICON.develop, '主页', 'Home', active === 'Home') +
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
    iconButton(ICON.setting, '管理', 'Manage', active === 'Manage') +
    `</div>`

  // 菜单树：老 UI 有两套，必须区分（实测 22267 真实 DOM）。
  //   ① 主页（HomePagePage）：6 个固定入口，无搜索框，--menu-HomePage/Update/Remote/Setting/Announcement/Utils
  //   ② 实例页：配置搜索框 + 完整配置树，分组用 <details><summary>，首项 --menu-Overview
  const HOME_MENU: { key: string; name: string }[] = [
    { key: 'HomePage', name: '主页' },
    { key: 'Update', name: '更新器' },
    { key: 'Remote', name: '远程控制' },
    { key: 'Setting', name: '设置' },
    { key: 'Announcement', name: '公告' },
    { key: 'Utils', name: '开发者工具' },
  ]

  const menuItem = (key: string, name: string) =>
    `<div style=";;--menu-${key}--;"><button class="btn btn-menu${
      (active === key || (active === 'Home' && key === 'HomePage')) ? ' btn-menu-active' : ''
    }">${name}</button></div>`

  const menuHtml =
    submenu && submenu.length
      ? // 二级菜单（管理/开发者工具等）：老 UI 用 dev_set_menu 往菜单区放按钮，
        // 不显示配置树。高亮仍走 div[style*="--menu-{key}--"] > button 约定。
        submenu.map(it => menuItem(it.key, it.name)).join('')
    : active === 'Home'
      ? HOME_MENU.map(it => menuItem(it.key, it.name)).join('')
      : // 实例页：搜索框 + 配置树
        `<div id="pywebio-scope-task_config_search">` +
        `<div class="form-group" style=";;--task-config-search-input--">` +
        `<input type="text" class="form-control" name="config_search_keyword" value="" placeholder="搜索配置">` +
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
              <div id="pywebio-scope-header_status"></div>
              <div id="pywebio-scope-header_title"><p style={{ whiteSpace: 'pre-wrap' }}>{title}</p></div>
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
