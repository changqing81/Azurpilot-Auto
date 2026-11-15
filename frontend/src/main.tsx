
// 主题层动态注入（基础层已在 index.html 静态引入；这里只补充当前主题的额外样式）
// 端点不可用（老版本后端）时静默跳过，页面仍有基础层样式，不会裸奔。
fetch('/newapi/theme-css')
  .then(r => (r.ok ? r.json() : null))
  .then(d => {
    const base = ['alas.css', 'alas-pc.css', 'light-alas.css']
    ;(d?.links ?? []).forEach((href: string) => {
      if (base.some(b => href.endsWith(b))) return
      const link = document.createElement('link')
      link.rel = 'stylesheet'
      link.href = href + '?v=newui'
      document.head.appendChild(link)
    })
  })
  .catch(() => {})

// 与老 UI 同构：body 需要 PyWebIO 的主题类名，否则主题 CSS 的 body.* 规则不生效
document.body.className = 'webio-theme-' + (document.body.className || 'default').trim() || 'webio-theme-default'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Overview } from './pages/home'
import { HomePageMain } from './pages/home2'
import { ConfigPage } from './pages/config'
import { StatPage } from './pages/stat'
import { SettingsPage } from './pages/settings'
import { UpdatePage } from './pages/update'
import { RemotePage } from './pages/remote'
import { OobePage } from './pages/oobe'
import { ToolPage } from './pages/tool'
import './styles/shell.css'

// 新 UI 入口。路由用 hash 模式：避免与老 UI 的服务端路由 / scope 约定冲突。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <Routes>
        <Route path="/" element={<HomePageMain />} />
        <Route path="/overview/:name" element={<Overview />} />
        <Route path="/config/:name/:task" element={<ConfigPage />} />
        <Route path="/stat/:name" element={<StatPage />} />
        <Route path="/settings/:name" element={<SettingsPage />} />
        <Route path="/update" element={<UpdatePage />} />
        <Route path="/remote" element={<RemotePage />} />
        <Route path="/oobe" element={<OobePage />} />
        <Route path="/tool/:name/:task" element={<ToolPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  </StrictMode>
)
