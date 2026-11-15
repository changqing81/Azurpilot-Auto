// 主页 1:1 复刻（P1 骨架）：侧栏 + 仪表盘真数据。
// DOM 约定对照 docs/new-ui/old-ui-home-anatomy.md §2：
// - scope id 与老 UI 一致（#pywebio-scope-aside / #pywebio-scope-content / #pywebio-scope-dashboard）
// - 侧栏按钮外层 div 的 style 含 --aside-{name}-- 标记（老 UI 主题 CSS 的 active_button 选择器依赖）
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

type VersionResp = { version: string }
type Instance = { name: string; state: number }
type InstancesResp = { instances: Instance[] }
type DashItem = { name: string; value: number; record: string }
type DashResp = { items: DashItem[] }

// 老 UI 仪表盘资源图标语义（先文字兜底，图标资源对齐在 P2 做）
const DASH_LABEL: Record<string, string> = {
  Oil: '石油', Coin: '金币', Gem: '钻石', Pt: '点数', Cube: ' cubes',
  ActionPoint: '行动点', YellowCoin: '黄币', PurpleCoin: '紫币',
  Core: '核心', Medal: '勋章', Merit: '功勋', GuildCoin: '舰队币',
}

// aside 按钮样式标记：老 UI active_button 用 div[style*=--aside-{name}--] > button
function asideMark(name: string): React.CSSProperties {
  return { ['--aside-' + name]: '1' } as React.CSSProperties
}

export function Home() {
  const [version, setVersion] = useState('')
  const [instances, setInstances] = useState<Instance[]>([])
  const [dash, setDash] = useState<DashItem[]>([])
  const [active, setActive] = useState<string | null>(null)

  useEffect(() => {
    apiGet<VersionResp>('/newapi/version').then(r => setVersion(r.version)).catch(() => {})
    apiGet<InstancesResp>('/newapi/instances').then(r => setInstances(r.instances)).catch(() => {})
    apiGet<DashResp>('/newapi/dashboard/alas').then(r => setDash(r.items)).catch(() => {})
  }, [])

  return (
    <div id="pywebio-scope-root">
      <header id="pywebio-scope-header">
        <div id="pywebio-scope-header_title">AzurPilot {version && <small>Ver.{version}</small>}</div>
      </header>
      <div id="pywebio-scope-contents">
        <aside id="pywebio-scope-aside">
          <div id="pywebio-scope-aside_home" style={asideMark('home')}>
            <button className={active === 'home' ? 'btn-aside-active' : ''} onClick={() => setActive('home')}>主页</button>
          </div>
          {instances.map(inst => (
            <div key={inst.name} id={'pywebio-scope-alas-instance-' + inst.name} style={asideMark(inst.name)}>
              <button className={active === inst.name ? 'btn-aside-active' : ''} onClick={() => setActive(inst.name)}>
                {inst.name}
              </button>
            </div>
          ))}
        </aside>
        <main id="pywebio-scope-content">
          <div id="pywebio-scope-dashboard">
            {dash.map(item => (
              <div key={item.name} className="dashboard-item" title={'更新于 ' + item.record}>
                <span className="dashboard-name">{DASH_LABEL[item.name] ?? item.name}</span>
                <span className="dashboard-value">{item.value}</span>
              </div>
            ))}
          </div>
        </main>
      </div>
      <footer data-version={version} />
    </div>
  )
}
