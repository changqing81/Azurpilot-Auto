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

// 移动端布局层：alas-pc.css ↔ alas-mobile.css（老 UI utils.py:webui_style_names 同口径）。
// 必须在**样式表插入顺序不变**的前提下换 href：alas-mobile.css 里是
// 「菜单折叠 / 日志小字号 / 总览改上下两行 / 仪表盘 6rem 栅格 / 触控 44px」整组窄屏规则。
// 注意：这两份文件的相对位置在 <link> 链里固定（紧随 alas.css），
// 直接改 href 不改变顺序，层叠优先级不受影响。
const MOBILE_UA_TOKENS = ['mobile', 'android', 'iphone', 'ipod', 'ipad', 'windows phone', 'blackberry']
function isMobileUA(): boolean {
  const ua = (navigator.userAgent || '').toLowerCase()
  return MOBILE_UA_TOKENS.some(t => ua.includes(t))
}
function applyLayoutLayer() {
  const link = document.getElementById('alas-layout-css') as HTMLLinkElement | null
  if (!link) return
  const want = isMobileUA() ? 'alas-mobile' : 'alas-pc'
  if (!link.href.includes('/' + want + '.css')) {
    link.href = '/static/assets/gui/css/' + want + '.css'
  }
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

applyLayoutLayer()

fetch('/newapi/theme-css')
  .then(r => (r.ok ? r.json() : null))
  .then(applyTheme)
  .catch(() => {
    // 端点不可用（老后端）：退回浅色主题层，保证不是裸样式
    applyTheme({ theme: 'default', links: ['/static/assets/gui/css/light-alas.css'] })
  })

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { useEffect, useRef } from 'react'
import { HashRouter, Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
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



// ───────────────────── 记住上次页面 ─────────────────────
// 长卿反馈：「记不住上次选项」。
// 老 UI 的做法（module/webui/base.py:LAST_PAGE_KEY）：
//   init_menu 里 localStorage.setItem('alas_last_page', <页签名>)
//   下次进页面时若 hash 为空，恢复到上次那页。
//
// 新 UI 是 hash 路由，天然有 URL 记忆（刷新后 hash 还在），
// 但两个场景会丢：
//   1. 直接访问根路径 /（没有 hash）→ 落到主页
//   2. 从老 UI 切到新 UI（UI=new）→ URL 从 / 重新开始，没有 hash
// 补一层 localStorage 记忆：路由变化时写入，hash 为空时恢复。
// 用 sessionStorage 而不是 localStorage：
//   - localStorage 会跨会话保留，导致「第二天打开 /new/ 被带到昨天的某个子页」，
//     看起来像「主页进不去」。长卿实测反馈过。
//   - sessionStorage 只在当前标签页有效，刷新能保持，关掉就清空 —— 更符合直觉。
const LAST_ROUTE_KEY = 'alas_newui_last_route'
const routeStore = {
  get(): string {
    try { return sessionStorage.getItem(LAST_ROUTE_KEY) || '' } catch { return '' }
  },
  set(v: string) {
    try { sessionStorage.setItem(LAST_ROUTE_KEY, v) } catch { /* 隐私模式 */ }
  },
  clear() {
    try { sessionStorage.removeItem(LAST_ROUTE_KEY) } catch { /* 隐私模式 */ }
  },
}

function RestoreLastRoute() {
  const location = useLocation()
  const navigate = useNavigate()
  const restored = useRef(false)

  useEffect(() => {
    if (restored.current) return
    restored.current = true
    // 只在「真正的根路径」上恢复。
    // 坑：HashRouter 下 location.pathname 是 # 之后的部分，
    // location.hash 是路由内部的二级 hash（如 #/a#b 的 "#b"）。
    // 所以访问 #/remote 时 pathname='/remote' 而 hash='' ——
    // 早先写成 if (location.hash) return 会导致**任何页面都被重定向**
    // 到上次访问的页（实测：请求 #/remote 却显示 #/manage）。
    if (location.pathname && location.pathname !== '/') return
    const saved = routeStore.get()
    // 只恢复白名单，避免历史脏数据把用户带到 404
    const ok = /^\/(overview|stat|config|settings|tool)\/[^/]+(\/[^/]+)?$/.test(saved)
      || /^\/(update|remote|announcement|manage)$/.test(saved)
    if (ok) navigate(saved, { replace: true })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 注：用户主动点「主页」时由 Shell 清除 LAST_ROUTE_KEY（见 components/Shell.tsx），
  // 否则下次从 /new/ 进来还会被带到上次的子页面，看起来像「回不到主页」。

  useEffect(() => {
    const cur = location.pathname
    if (!cur || cur === '/') return  // 主页是默认落点，不记
    routeStore.set(cur)
  }, [location.pathname])

  return null
}

// ───────────────────────── 关于骨架屏 ─────────────────────────
// 长卿反馈「切换界面的时候它都会闪一下，闪一下白光」。
//
// 原因：我之前照搬了老 UI 的切页骨架屏（.alas-skeleton 系列）。
// 但那套是 PyWebIO 的妥协 —— 老 UI 换页要走服务端往返（几百毫秒），
// 期间白屏，所以铺灰条占位。**React 换页是同步渲染，几十毫秒内就完成**，
// 铺骨架反而制造了一次「灰条闪一下」的视觉抖动。
//
// 结论：新 UI 不需要骨架屏。删掉，换页直接由 React 渲染。
// （老 UI 的骨架 CSS 还在 alas.css 里，但没人用，无副作用。）

// 新 UI 入口。路由用 hash 模式：避免与老 UI 的服务端路由 / scope 约定冲突。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <RestoreLastRoute />
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
