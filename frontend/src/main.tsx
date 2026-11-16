// 主题注入：与老 UI（module/webui/utils.py:webui_style_names +
// module/webui/app_shell.py:pywebio_theme_for）完全同口径。
//
// 老 UI 的样式分三层，主题只影响「主题层」与 body 的 Bootstrap 主题类：
//   ① 基础层 + 9 个 alas 层  → /newapi/bundle.css（index.html 固定引入）
//   ② 主题层                → 由 /newapi/theme-css 返回，**必须只注入这几个**
//   ③ body 的 webio-theme-* → dark 主题要用 webio-theme-dark（Bootstrap 深色）
//
// 反面教材（本文件此前的写法）：index.html 硬编码 light-alas.css，
// 这里再把 dark-alas.css 追加到后面 → 两个主题层同时存在，
// 深色下被条目顺序靠后的浅色层部分覆盖，出现「深底浅块」的脏配色。
// 正确做法：主题层完全由本模块按当前主题注入，index.html 不放任何主题层。

// 基础/组件层（bundle.css 已包含，theme-css 里也有这些条目，必须跳过）。
// ⚠️ 必须用「文件名精确相等」判断，不能用 endsWith：
//    'advanced-material-alas.css'.endsWith('alas.css') === true
//    —— 用后缀匹配会把高级材质主题层误当成基础层过滤掉，
//    结果是主题配置成高级材质却完全没有主题样式（按钮透明、卡片无背景）。
const BASE_CSS_NAMES = new Set([
  'alas.css',
  'alas-pc.css',
  'alas-mobile.css',
  'entry-alas.css',
  'statistics-alas.css',
  'stat-delta-alas.css',
  'meowfficer-score-alas.css',
  'meow-loot-alas.css',
  'fleet-tech-alas.css',
  'fleet-management-alas.css',
])

function basenameOf(href: string): string {
  const clean = href.split('?')[0].split('#')[0]
  return clean.substring(clean.lastIndexOf('/') + 1)
}

function applyTheme(payload: { theme?: string; links?: string[] } | null) {
  if (!payload) return
  const theme = payload.theme || 'default'
  const links = (payload.links || []).filter(href => !BASE_CSS_NAMES.has(basenameOf(href)))
  // 1) 清掉上一次注入的主题层，保证同一时刻只有一套（对应老 UI _reload_theme_css
  //    的「删除 + 注入同一条消息」约束，避免切主题闪白/脏配色）
  document.querySelectorAll('link[data-alas-theme-css]').forEach(el => el.remove())
  // 2) 按顺序注入当前主题层
  links.forEach(href => {
    const link = document.createElement('link')
    link.rel = 'stylesheet'
    link.href = href + '?v=newui'
    link.setAttribute('data-alas-theme-css', '')
    document.head.appendChild(link)
  })
  // 3) Bootstrap 主题层随主题切换（老 UI 的 pywebio_theme_for：只有 dark 用 dark）
  const bs = document.getElementById('alas-bs-theme') as HTMLLinkElement | null
  if (bs) {
    const want = '/pywebio_static/css/bs-theme/' + (theme === 'dark' ? 'dark' : 'default') + '.min.css'
    if (!bs.href.includes(want)) bs.href = want
  }
  // 4) body 的 Bootstrap 主题类（老 UI pywebio_theme_for：只有 dark 用 dark，其余 default）
  const pywebioTheme = theme === 'dark' ? 'dark' : 'default'
  document.body.className =
    document.body.className.replace(/webio-theme-\S+/g, '').trim() + ' webio-theme-' + pywebioTheme
  // 4) 通知别的模块（统计页图表等）主题已变
  window.dispatchEvent(new CustomEvent('alas-theme-change', { detail: { theme } }))
}

fetch('/newapi/theme-css')
  .then(r => (r.ok ? r.json() : null))
  .then(applyTheme)
  .catch(() => {
    // 端点不可用（老后端）：退回浅色主题层，保证不是裸样式
    applyTheme({ theme: 'default', links: ['/static/assets/gui/css/light-alas.css'] })
  })

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { useEffect, useRef, useState } from 'react'
import { HashRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Overview } from './pages/home'
import { useParams as useParamsForOverview } from 'react-router-dom'
import { HomePageMain } from './pages/home2'
import { ConfigPage } from './pages/config'
import { StatPage } from './pages/stat'
import { SettingsPage } from './pages/settings'
import { UpdatePage } from './pages/update'
import { RemotePage } from './pages/remote'
import { OobePage } from './pages/oobe'
import { ToolPage } from './pages/tool'
import { AnnouncementPage } from './pages/announcement'
import { ManagePage } from './pages/manage'
import './styles/shell.css'

// 总览页需要实例名（=ProcessManager 的 config_name），从路由参数取后透传。
function OverviewRoute() {
  const { name = 'alas' } = useParamsForOverview()
  return <Overview instance={name} />
}


// ───────────────────────── 切页骨架屏 ─────────────────────────
// 老 UI 的做法（module/webui/base.py:318-389）：点菜单的瞬间往
// #pywebio-scope-content 里 append 一个 #alas-page-skeleton，
// 真实内容渲染出来后被 MutationObserver 撤掉。
//
// 新 UI 没有「服务端往返」这一步（数据走 REST 并行拉取），但切页时
// 组件挂载 → 首次 fetch 返回之间仍有 100~800ms 的空白，
// 所以同样值得铺骨架。样式直接复用 alas.css:1408-1464 的
// .alas-skeleton*（颜色走 --alas-entry-* 令牌，五个主题自适应），
// 只需按老 UI 的结构生成 DOM。
//
// 与老 UI 的差异（有意为之）：
//   - 用 React 的 useLocation + effect 控制，不用 MutationObserver
//     （React 什么时候渲染完是确定的，观察 DOM 反而多一层开销）
//   - 骨架 800ms 后强制移除：即使某页数据拉取失败也不能留一堆灰条
function PageSkeleton({ visible }: { visible: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    // 挂载点用常驻的 #pywebio-scope-contents（壳层容器）而不是 #pywebio-scope-content：
    // 后者只有总览/配置页渲染，统计页用的是 #pywebio-scope-statistics-content，
    // 切到统计页时 #pywebio-scope-content 根本不存在 → 骨架永远挂不上。
    // 挂到 contents 上，然后在视觉上覆盖内容区（position:absolute）。
    let raf = 0
    let tries = 0
    const mount = () => {
      const host = document.getElementById('pywebio-scope-contents')
      if (!host && ++tries < 20) { raf = requestAnimationFrame(mount); return }
      if (!host) return
      const existing = document.getElementById('alas-page-skeleton')
      if (visible && !existing) {
      const sk = document.createElement('div')
      sk.id = 'alas-page-skeleton'
      const lines = Array.from({ length: 6 }, (_, i) =>
        '<div class="alas-skeleton-line" style="width:' + (88 - i * 11) + '%"></div>',
      ).join('')
        // 覆盖内容区：起点取「内容区元素」的实际左边界，而不是硬算 aside+menu 宽度
        // （菜单折叠、实例名长短、移动端分支都会让它变化，硬算必错位）。
        // 找不到内容区时退回 contents 的 padding-left。
        const anchor =
          document.getElementById('pywebio-scope-content') ||
          document.getElementById('pywebio-scope-statistics-content')
        const hostRect = host.getBoundingClientRect()
        const anchorRect = anchor ? anchor.getBoundingClientRect() : null
        const left = anchorRect ? Math.max(0, Math.round(anchorRect.left - hostRect.left)) : 0
        const width = anchorRect ? Math.round(anchorRect.width) : hostRect.width - left
        // 必须给骨架自己一块不透明底：否则右侧露出页面底色，看起来像"内容缺失"
        const bg = getComputedStyle(document.body).backgroundColor
        sk.style.cssText =
          'position:absolute;top:0;height:100%;z-index:1;overflow:hidden;' +
          'left:' + left + 'px;width:' + width + 'px;' +
          'background:' + (bg && bg !== 'rgba(0, 0, 0, 0)' ? bg : 'var(--alas-entry-page, #f9f9f9)') + ';'
        sk.innerHTML = '<div class="alas-skeleton"><div class="alas-skeleton-title"></div>' + lines + '</div>'
        if (getComputedStyle(host).position === 'static') host.style.position = 'relative'
        host.appendChild(sk)
      } else if (!visible && existing) {
        existing.remove()
      }
    }
    mount()
    return () => cancelAnimationFrame(raf)
  }, [visible])
  return <div ref={ref} style={{ display: 'none' }} aria-hidden="true" />
}

// 路由变化时铺骨架，内容挂载后 400ms 撤掉（给首次数据拉取留时间），
// 最长 2500ms 强制撤（数据失败也不能留灰条）。
function SkeletonOnRouteChange() {
  const location = useLocation()
  const [visible, setVisible] = useState(false)
  // 监听 HashRouter 的 location（含 hash 变化）。用整个 location.key 而不是
  // pathname：同一个 pathname 换实例名（#/overview/alas → #/overview/小号）也算切页。
  const routeKey = location.pathname + location.search
  useEffect(() => {
    // 主页不铺骨架（老 UI 的 init_menu 同样跳过 HomePage：主页是轻量静态内容）
    if (location.pathname === '/') { setVisible(false); return }
    setVisible(true)
    // 400ms 后撤：给首次数据拉取留出时间，之后即使还没回来也直接显示真实容器
    const hide = setTimeout(() => setVisible(false), 400)
    // 2.5s 强制撤：数据失败也不能一直挂灰条
    const force = setTimeout(() => setVisible(false), 2500)
    return () => { clearTimeout(hide); clearTimeout(force) }
  }, [routeKey])
  return <PageSkeleton visible={visible} />
}

// 新 UI 入口。路由用 hash 模式：避免与老 UI 的服务端路由 / scope 约定冲突。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <SkeletonOnRouteChange />
      <Routes>
        <Route path="/" element={<HomePageMain />} />
        <Route path="/overview/:name" element={<OverviewRoute />} />
        <Route path="/config/:name/:task" element={<ConfigPage />} />
        <Route path="/stat/:name" element={<StatPage />} />
        <Route path="/settings/:name" element={<SettingsPage />} />
        <Route path="/update" element={<UpdatePage />} />
        <Route path="/remote" element={<RemotePage />} />
        <Route path="/oobe" element={<OobePage />} />
        <Route path="/announcement" element={<AnnouncementPage />} />
        <Route path="/manage" element={<ManagePage />} />
        <Route path="/tool/:name" element={<ToolPage />} />
        <Route path="/tool/:name/:task" element={<ToolPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  </StrictMode>
)
