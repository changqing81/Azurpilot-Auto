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
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
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
import './styles/shell.css'

// 总览页需要实例名（=ProcessManager 的 config_name），从路由参数取后透传。
function OverviewRoute() {
  const { name = 'alas' } = useParamsForOverview()
  return <Overview instance={name} />
}

// 新 UI 入口。路由用 hash 模式：避免与老 UI 的服务端路由 / scope 约定冲突。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
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
        <Route path="/tool/:name" element={<ToolPage />} />
        <Route path="/tool/:name/:task" element={<ToolPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  </StrictMode>
)
