// 总览页 1:1 复刻（对照 oldui-ref/oldui_overview_alas.png + scope-map-overview.txt）
// DOM 契约：scope id / class 与老 UI 相同，主题 CSS（assets/gui/css）直接生效。
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

type VersionResp = { version: string }
type Instance = { name: string; state: number }
type InstancesResp = { instances: Instance[] }
type TaskRow = { command: string; name: string; next_run: string }
type OverviewResp = { running: TaskRow[]; pending: TaskRow[]; waiting: TaskRow[]; alive: boolean }
type DashItem = { name: string; value: string; limit: string; total: string; color: string; record: string }
type DashResp = { items: DashItem[] }

// 老 UI 资源名 i18n（Gui.Dashboard.*，与 zh-CN.json 同步）
const DASH_LABEL: Record<string, string> = {
  Oil: '石油', Coin: '物资', Gem: '钻石', Pt: '活动PT', Cube: '魔方',
  ActionPoint: '行动点', YellowCoin: '大世界黄币', PurpleCoin: '大世界紫币',
  Core: '核心数据', Medal: '勋章', Merit: '功勋', GuildCoin: '舰队币',
}

// 相对时间（Gui.Overview.* 口径：X分钟前/小时前…）
function relTime(record: string): string {
  if (!record || record.startsWith('2023-01-01')) return '无数据'
  const then = new Date(record.replace(' ', 'T'))
  const s = Math.floor((Date.now() - then.getTime()) / 1000)
  if (isNaN(s) || s < 0) return '无数据'
  if (s < 60) return s + '秒前'
  if (s < 3600) return Math.floor(s / 60) + '分钟前'
  if (s < 86400) return Math.floor(s / 3600) + '小时前'
  const d = Math.floor(s / 86400)
  if (d < 30) return d + '天前'
  if (d < 365) return Math.floor(d / 30) + '月前'
  return Math.floor(d / 365) + '年前'
}

function asideMark(name: string): React.CSSProperties {
  return { ['--aside-' + name]: '1' } as React.CSSProperties
}

function menuMark(name: string): React.CSSProperties {
  return { ['--menu-' + name]: '1' } as React.CSSProperties
}

// 任务卡（scope=overview-task_{command}，结构 put_column: 名称+时间+设置按钮）
function TaskCard({ task }: { task: TaskRow }) {
  return (
    <div id={'pywebio-scope-overview-task_' + task.command} className="task-card">
      <div className="task-info">
        <div className="arg-title">{task.name}</div>
        <div className="arg-help">{task.next_run}</div>
      </div>
      <button className="task-setting">设置</button>
    </div>
  )
}

// 仪表盘条目（scope=dashboard_{Item} > {Item}_group；status-point 彩点 + value + help）
function DashEntry({ item }: { item: DashItem }) {
  const second = item.limit ? ' / ' + item.limit : item.total ? ' (' + item.total + ')' : ''
  return (
    <div id={'pywebio-scope-dashboard_' + item.name} className="dashboard-entry">
      <div className="status-point" style={{ backgroundColor: item.color }} />
      <div id={'pywebio-scope-' + item.name + '_group'} className="dashboard-group">
        <div className="dashboard-value-row">
          <span className="dashboard-value">{item.value === 'None' ? '无' : item.value}</span>
          {second && item.value !== 'None' && <span className="dashboard-limit">{second}</span>}
        </div>
        <div className="dashboard-help">
          {DASH_LABEL[item.name] ?? item.name} - {relTime(item.record)}
        </div>
      </div>
    </div>
  )
}

export function Overview() {
  const [version, setVersion] = useState('')
  const [instances, setInstances] = useState<Instance[]>([])
  const [active, setActive] = useState('alas')
  const [ov, setOv] = useState<OverviewResp | null>(null)
  const [dash, setDash] = useState<DashItem[]>([])

  useEffect(() => {
    apiGet<VersionResp>('/newapi/version').then(r => setVersion(r.version)).catch(() => {})
    apiGet<InstancesResp>('/newapi/instances').then(r => setInstances(r.instances)).catch(() => {})
  }, [])
  useEffect(() => {
    if (!active) return
    apiGet<OverviewResp>('/newapi/overview/' + active).then(setOv).catch(() => {})
    apiGet<DashResp>('/newapi/dashboard/' + active).then(r => setDash(r.items)).catch(() => {})
    const timer = setInterval(() => {
      apiGet<OverviewResp>('/newapi/overview/' + active).then(setOv).catch(() => {})
    }, 15000)  // 老 UI 15s 周期同款
    return () => clearInterval(timer)
  }, [active])

  const alive = ov?.alive ?? false
  return (
    <div id="pywebio-scope-ROOT">
      <header id="pywebio-scope-header">
        <div id="pywebio-scope-header_status" className="header-status" />
        <div id="pywebio-scope-header_title">总览</div>
        <div className="header-version">Ver.{version}</div>
      </header>
      <div id="pywebio-scope-contents">
        <aside id="pywebio-scope-aside">
          <div id="pywebio-scope-aside_home" style={asideMark('home')}>
            <button className="btn-aside">主页</button>
          </div>
          {instances.map(inst => (
            <div key={inst.name} id={'pywebio-scope-alas-instance-' + inst.name} style={asideMark(inst.name)}>
              <button
                className={'btn-aside' + (active === inst.name ? ' btn-aside-active' : '')}
                onClick={() => setActive(inst.name)}
              >
                {inst.name}
              </button>
            </div>
          ))}
          <div id="pywebio-scope-aside_manage" style={asideMark('manage')}>
            <button className="btn-aside">管理</button>
          </div>
        </aside>

        <nav id="pywebio-scope-menu">
          <div id="pywebio-scope-task_config_search" className="config-search">
            <input placeholder="搜索配置" />
          </div>
          <div id="pywebio-scope-task_config_menu_items">
            <div style={menuMark('Overview')}>
              <button className="btn-menu btn-menu-active">总览</button>
            </div>
            <div style={menuMark('Campaign')}>
              <button className="btn-menu">▶ 智慧港区</button>
            </div>
            <div style={menuMark('Main')}>
              <button className="btn-menu">▶ 出击</button>
            </div>
            <div style={menuMark('Event')}>
              <button className="btn-menu">▶ 活动</button>
            </div>
            <div style={menuMark('Reward')}>
              <button className="btn-menu">▶ 自动收获</button>
            </div>
            <div style={menuMark('DailyMission')}>
              <button className="btn-menu">▶ 每日任务</button>
            </div>
            <div style={menuMark('Opsi')}>
              <button className="btn-menu">▶ 大世界</button>
            </div>
            <div style={menuMark('Island')}>
              <button className="btn-menu">▶ 岛屿计划</button>
            </div>
            <div style={menuMark('Fleet')}>
              <button className="btn-menu">▶ 舰队管理</button>
            </div>
            <div style={menuMark('Tool')}>
              <button className="btn-menu">▶ 工具</button>
            </div>
          </div>
        </nav>

        <main id="pywebio-scope-content">
          <div id="pywebio-scope-overview">
            <section id="pywebio-scope-schedulers">
              <div id="pywebio-scope-scheduler-bar" className="ov-card ov-card-bar">
                <span className="ov-card-title">调度器</span>
                <button className={'ov-btn-primary' + (alive ? '' : ' is-off')}>
                  {alive ? '停止' : '启动'}
                </button>
              </div>
              <div id="pywebio-scope-stat-bar" className="ov-card ov-card-bar">
                <span className="ov-card-title">统计</span>
                <button className="ov-btn-primary">打开</button>
              </div>
              <hr className="hr-group" />
              <div id="pywebio-scope-running" className="ov-card">
                <div className="ov-card-title">运行中</div>
                <div id="pywebio-scope-running_tasks">
                  {ov && ov.running.length
                    ? ov.running.map(t => <TaskCard key={t.command} task={t} />)
                    : <div className="overview-notask">无任务</div>}
                </div>
              </div>
              <hr className="hr-group" />
              <div id="pywebio-scope-pending" className="ov-card">
                <div className="ov-card-title">队列中 <sup className="ov-count">{ov?.pending.length ?? 0}</sup></div>
                <div id="pywebio-scope-pending_tasks">
                  {ov?.pending.map(t => <TaskCard key={t.command} task={t} />)}
                </div>
              </div>
              <hr className="hr-group" />
              <div id="pywebio-scope-waiting" className="ov-card">
                <div className="ov-card-title">等待中 <sup className="ov-count">{ov?.waiting.length ?? 0}</sup></div>
                <div id="pywebio-scope-waiting_tasks">
                  {ov?.waiting.map(t => <TaskCard key={t.command} task={t} />)}
                </div>
              </div>
            </section>

            <section id="pywebio-scope-logs">
              <div id="pywebio-scope-log-bar" className="ov-card">
                <span className="ov-card-title">日志</span>
                <div id="pywebio-scope-log-bar-btns" className="log-bar-btns">
                  <button className="log-btn-primary">自动滚动 开</button>
                  <button className="log-btn">截图预览</button>
                  <button className="log-btn">导出今日日志</button>
                  <button className="log-btn">折叠</button>
                </div>
              </div>
              <div id="pywebio-scope-dashboard" className="dashboard-grid">
                {dash.map(item => <DashEntry key={item.name} item={item} />)}
              </div>
              <div id="pywebio-scope-log-container" className="log-container">
                <div id="pywebio-scope-log" className="log-body">
                  <span className="log-placeholder">日志流接入中（P2-WS）</span>
                </div>
              </div>
            </section>
          </div>
        </main>

        <div id="pywebio-scope-statistics-content" style={{ display: 'none' }} />
      </div>
    </div>
  )
}
