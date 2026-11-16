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

export function Shell({
  active,
  title,
  children,
}: {
  active: string
  title: string
  children: React.ReactNode
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

  useEffect(() => {
    apiGet<InstancesResp>('/newapi/instances').then(r => { setInstances(r.instances); setInstNames(r.instances.map(i => i.name)) }).catch(() => {})
    apiGet<MenuResp>('/newapi/menu').then(r => setMenu(r.groups)).catch(() => {})
    apiGet<HeaderResp>('/newapi/header').then(setHeader).catch(() => {})
  }, [])

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
      const btnEl = (ev.target as HTMLElement).closest('button')
      if (!btnEl) return
      const holder = btnEl.closest('[style*="--menu-"]') as HTMLElement | null
      const mark = holder?.getAttribute('style') ?? ''
      const m = mark.match(/--menu-(.+?)--/)
      if (!m) return
      const key = m[1]
      if (key === 'Overview') { navigate('/overview/' + encodeURIComponent(instanceName)); return }
      if (key === 'HomePage') { navigate('/'); return }
      if (key === 'Update') { navigate('/update'); return }
      if (key === 'Remote') { navigate('/remote'); return }
      if (key === 'Setting') { navigate('/settings/' + encodeURIComponent(instanceName)); return }
      if (key === 'Announcement') { navigate('/announcement'); return }
      if (key === 'Utils') { navigate('/tool/' + encodeURIComponent(instanceName)); return }
      navigate(`/config/${encodeURIComponent(instanceName)}/${encodeURIComponent(key)}`)
    }
  }

  // 侧栏实例图标随运行状态切换（ProcessManager.state: 1 运行中 / 3 异常 / 4 更新中 / 其他 空闲）
  const instanceIcon = (state: number) => (state === 1 ? ICON.running : ICON.run)

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
    active === 'Home'
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
            return `<details><summary>${g.name}</summary>${children}</details>`
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
