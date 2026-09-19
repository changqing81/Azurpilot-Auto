import { Component, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { createHashRouter, RouterProvider, Navigate } from 'react-router-dom'
import { App } from './app/App'
import { AppProvider } from './app/context'
import { Wallpaper } from './components/Wallpaper'
import { Overview } from './pages/Overview'
import { TaskConfig } from './pages/TaskConfig'
import { Statistics } from './pages/Statistics'
import { Home } from './pages/Home'
import { Updater } from './pages/Updater'
import { Settings } from './pages/Settings'
import { DevControls } from './pages/DevControls'
import { translateCurrentUi } from './i18n'
import './styles/tokens.css'
import './styles/layout.css'
import './styles/components.css'
import './styles/insights.css'
import './styles/home.css'
import './styles/apple.css'
import './styles/dev.css'
import './styles/theme-system.css'
import './styles/forms.css'

function mountUserTheme() {
  if (document.querySelector('link[data-azurpilot-theme]')) return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = `${import.meta.env.BASE_URL}theme.css`
  link.dataset.azurpilotTheme = 'user'
  document.head.appendChild(link)
}

mountUserTheme()

class ErrorBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = {failed: false}
  static getDerivedStateFromError() { return {failed: true} }
  render() {
    if (this.state.failed) return <div className="welcome"><h1>{translateCurrentUi('error.pageTitle')}</h1><p>{translateCurrentUi('error.pageHint')}</p><button className="button primary" onClick={() => location.reload()}>{translateCurrentUi('error.reload')}</button></div>
    return this.props.children
  }
}
const router = createHashRouter([
  {path: '/', element: <App/>, children: [{index: true, element: <Home/>}, {path: 'settings', element: <Settings/>}, {path: 'updater', element: <Updater/>}, {path: 'dev', element: <DevControls/>}]},
  {path: '/i/:instance', element: <App/>, children: [
    {index: true, element: <Navigate to="overview" replace/>},
    {path: 'overview', element: <Overview/>}, {path: 'task/:task', element: <TaskConfig/>},
    {path: 'logs', element: <Navigate to="../overview" replace/>}, {path: 'statistics', element: <Statistics/>}, {path: 'settings', element: <Navigate to="/settings" replace/>},
  ]},
  {path: '*', element: <Navigate to="/" replace/>},
])
createRoot(document.getElementById('root')!).render(<ErrorBoundary><AppProvider><Wallpaper/><RouterProvider router={router}/></AppProvider></ErrorBoundary>)
