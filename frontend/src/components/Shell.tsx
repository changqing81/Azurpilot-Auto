// 页面壳：侧栏 + 菜单树 + 内容容器。DOM 约定与老 UI 一致（asideMark/menuMark style 标记）。
// Overview/HomePage/Settings 等页面共用。
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../api/client'

type Instance = { name: string; state: number }
type InstancesResp = { instances: Instance[] }
type MenuGroup = { key: string; name: string; page: string; tasks: { key: string; name: string }[] }
type MenuResp = { groups: MenuGroup[] }

export function asideMark(name: string): React.CSSProperties {
  return { ['--aside-' + name]: '1' } as React.CSSProperties
}
export function menuMark(name: string): React.CSSProperties {
  return { ['--menu-' + name]: '1' } as React.CSSProperties
}

export function Shell({ active, children, headerTitle }: { active: string; children: React.ReactNode; headerTitle?: string }) {
  const navigate = useNavigate()
  const [instances, setInstances] = useState<Instance[]>([])
  const [menu, setMenu] = useState<MenuGroup[]>([])
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  useEffect(() => {
    apiGet<InstancesResp>('/newapi/instances').then(r => setInstances(r.instances)).catch(() => {})
    apiGet<MenuResp>('/newapi/menu').then(r => setMenu(r.groups)).catch(() => {})
  }, [])
  return (
    <div id="pywebio-scope-ROOT">
      <header id="pywebio-scope-header">
        <img className="alas-icon" src="/static/assets/spa/spa-icon-192x192.png" alt="AzurPilot" width={42} height={42} style={{ position: 'absolute', left: 12, borderRadius: '50%' }} />
        <span style={{ position: 'absolute', left: 62, fontSize: 14, fontWeight: 600 }}>待到山花烂漫时，她在丛中笑</span>
        <div id="pywebio-scope-header_title">{headerTitle ?? 'AzurPilot'}</div>
        <div className="header-version" />
      </header>
      <div id="pywebio-scope-contents">
        <aside id="pywebio-scope-aside">
          <div id="pywebio-scope-aside_home" style={asideMark('home')}>
            <button className={'btn-aside' + (active === 'home' ? ' btn-aside-active' : '')} onClick={() => navigate('/')}>主页</button>
          </div>
          {instances.map(inst => (
            <div key={inst.name} id={'pywebio-scope-alas-instance-' + inst.name} style={asideMark(inst.name)}>
              <button
                className={'btn-aside' + (active === inst.name ? ' btn-aside-active' : '')}
                onClick={() => navigate('/overview/' + inst.name)}
              >
                {inst.name}
              </button>
            </div>
          ))}
          <div id="pywebio-scope-aside_manage" style={asideMark('manage')}>
            <button className="btn-aside" onClick={() => navigate('/settings/' + (instances[0]?.name ?? 'alas'))}>管理</button>
          </div>
        </aside>
        <nav id="pywebio-scope-menu">
          <div id="pywebio-scope-task_config_search" className="config-search">
            <input placeholder="搜索配置" />
          </div>
          <div id="pywebio-scope-task_config_menu_items">
            {menu.map(g => (
              <div key={g.key}>
                <div style={menuMark(g.key)}>
                  <button
                    className={'btn-menu' + (active === g.key ? ' btn-menu-active' : '')}
                    onClick={() => setCollapsed(s => ({ ...s, [g.key]: !s[g.key] }))}
                  >
                    {g.name}
                    <span className="menu-arrow">{collapsed[g.key] ? '▶' : '▼'}</span>
                  </button>
                </div>
                {!collapsed[g.key] && g.tasks.map(t => (
                  <div key={t.key} style={{ paddingLeft: 18, ...menuMark(g.key + '.' + t.key) } as React.CSSProperties}>
                    <button
                      className={'btn-menu btn-task' + (active === g.key + '.' + t.key ? ' btn-menu-active' : '')}
                      onClick={() => navigate(`/config/${active === 'home' ? 'alas' : active}/${t.key}`)}
                    >
                      {t.name}
                    </button>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </nav>
        {children}
        <div id="pywebio-scope-statistics-content" style={{ display: 'none' }} />
      </div>
    </div>
  )
}
