// 总览页 1:1 复刻（对照 oldui-ref/oldui_overview_alas.png + scope-map-overview.txt）
// DOM 契约：scope id / class 与老 UI 相同，主题 CSS（assets/gui/css）直接生效。
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'

type VersionResp = { version: string }
type Instance = { name: string; state: number }
type InstancesResp = { instances: Instance[] }
type TaskRow = { command: string; name: string; next_run: string }
type OverviewResp = { running: TaskRow[]; pending: TaskRow[]; waiting: TaskRow[]; alive: boolean }
type DashItem = { name: string; value: string; limit: string; total: string; color: string; record: string }
type DashResp = { items: DashItem[] }
type HeaderResp = { icon: string; prefix: string; title: string }
type MenuGroup = { key: string; name: string; page: string; tasks: { key: string; name: string }[] }
type MenuResp = { groups: MenuGroup[] }

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
    <div id={'pywebio-scope-overview-task_' + task.command}>
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        <span style={{ ['--arg-title']: '1' } as React.CSSProperties}>{task.name}</span>
        <span style={{ ['--arg-help']: '1' } as React.CSSProperties}>{task.next_run}</span>
      </div>
      <button className="pywebio-btn">设置</button>
    </div>
  )
}

// 仪表盘条目：DOM 与老 UI put_row/put_column 同构（alas.css:399-427、1102-1135 的 style 标记选择器依赖这些属性）
function DashEntry({ item }: { item: DashItem }) {
  const second = item.limit ? ' / ' + item.limit : item.total ? ' (' + item.total + ')' : ''
  return (
    <div id={'pywebio-scope-dashboard_' + item.name}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '20px 1fr',
          alignItems: 'center',
          height: '100%',
        }}
      >
        <div className="status-point" style={{ backgroundColor: item.color }} />
        <div id={'pywebio-scope-' + item.name + '_group'}>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'min-content auto',
              alignItems: 'baseline',
            }}
          >
            <span style={{ ['--dashboard-value']: '1' } as React.CSSProperties}>{item.value === 'None' ? '无' : item.value}</span>
            {second && item.value !== 'None' && (
              <span style={{ ['--dashboard-limit']: '1' } as React.CSSProperties}>{second}</span>
            )}
          </div>
          <div style={{ ['--dashboard-help']: '1' } as React.CSSProperties}>
            {DASH_LABEL[item.name]} - {relTime(item.record)}
          </div>
        </div>
      </div>
    </div>
  )
}
export function Overview() {
  const navigate = useNavigate()
  const [version, setVersion] = useState('')
  const [instances, setInstances] = useState<Instance[]>([])
  const [active, setActive] = useState('alas')
  const [ov, setOv] = useState<OverviewResp | null>(null)
  const [dash, setDash] = useState<DashItem[]>([])
  const autoScrollRef = useRef(true)
  const [autoScroll, setAutoScrollState] = useState(true)
  const setAutoScroll = (v: boolean) => { setAutoScrollState(v); autoScrollRef.current = v }
  const [header, setHeader] = useState<HeaderResp>({ icon: '', prefix: 'AzurPilot', title: 'AzurPilot' })
  const [menu, setMenu] = useState<MenuGroup[]>([])
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  useEffect(() => {
    apiGet<VersionResp>('/newapi/version').then(r => setVersion(r.version)).catch(() => {})
    apiGet<HeaderResp>('/newapi/header').then(setHeader).catch(() => {})
    apiGet<MenuResp>('/newapi/menu').then(r => setMenu(r.groups)).catch(() => {})
    apiGet<InstancesResp>('/newapi/instances').then(r => setInstances(r.instances)).catch(() => {})
  }, [])
  useEffect(() => {
    if (!active) return
    apiGet<OverviewResp>('/newapi/overview/' + active).then(setOv).catch(() => {})
    apiGet<DashResp>('/newapi/dashboard/' + active).then(r => setDash(r.items)).catch(() => {})
    const timer = setInterval(() => {
      apiGet<OverviewResp>('/newapi/overview/' + active).then(next => {
        // 快照 diff（老 UI _overview_snapshot 语义）：没变化就不触发重渲染
        setOv(prev => {
          const sig = JSON.stringify([next.alive, next.running, next.pending, next.waiting])
          const prevSig = prev ? JSON.stringify([prev.alive, prev.running, prev.pending, prev.waiting]) : ''
          return sig === prevSig ? prev : next
        })
      }).catch(() => {})
    }, 15000)  // 老 UI 15s 周期同款
    // 日志流：增量拉取（游标 since），append 到容器尾部并裁剪旧节点（老 UI put_log 语义）
    let cursor = -1
    // 页面不可见时暂停轮询（老 UI visibility 任务同语义，省 CPU/带宽）
    let visible = !document.hidden
    const onVis = () => { visible = !document.hidden }
    document.addEventListener('visibilitychange', onVis)
    const logTimer = setInterval(() => {
      if (!visible) return
      const el = document.getElementById('pywebio-scope-log')
      if (!el) return
      fetch('/newapi/log/' + active + '/tail?since=' + cursor)
        .then(r => r.ok ? r.json() : null)
        .then(j => {
          if (!j) return
          cursor = j.cursor
          if (j.full) { el.innerHTML = j.html || '' }
          else if (j.html) {
            const frag = document.createDocumentFragment()
            const span = document.createElement('div')
            span.innerHTML = j.html
            while (span.firstChild) frag.appendChild(span.firstChild)
            el.appendChild(frag)  // 一次性插入，单次回流
            while (el.childNodes.length > 600) el.removeChild(el.firstChild)
            if (autoScrollRef.current) el.scrollTop = el.scrollHeight
          }
        }).catch(() => {})
    }, 2000)
    return () => { clearInterval(timer); clearInterval(logTimer); document.removeEventListener('visibilitychange', onVis) }
  }, [active])

  const alive = ov?.alive ?? false
  return (
    <div id="pywebio-scope-ROOT">
      <header id="pywebio-scope-header">
        <img className="alas-icon" src={header.icon} alt="AzurPilot" width={42} height={42} style={{ position: 'absolute', left: 12, borderRadius: '50%' }} />
        <span style={{ position: 'absolute', left: 62, fontSize: 14, fontWeight: 600 }}>{header.prefix}</span>
        <div id="pywebio-scope-header_title">{active ? '总览' : '主页'}</div>
        <div className="header-version">Ver.{version}</div>
      </header>
      <div id="pywebio-scope-contents">
        <aside id="pywebio-scope-aside">
          <div id="pywebio-scope-aside_home" style={asideMark('home')}>
            <button className="btn-aside" onClick={() => navigate('/')}>主页</button>
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
                      onClick={() => navigate(`/config/${active}/${t.key}`)}
                    >
                      {t.name}
                    </button>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </nav>

        <main id="pywebio-scope-content">
          <div id="pywebio-scope-overview">
            <section id="pywebio-scope-schedulers">
              <div id="pywebio-scope-scheduler-bar">
                <p>调度器</p>
                <div id="pywebio-scope-scheduler_btn">
                  <button
                    className="pywebio-btn"
                    onClick={async () => {
                      const action = alive ? 'stop' : 'start'
                      try {
                        await fetch(`/newapi/scheduler/${active}/${action}`, { method: 'POST' })
                        apiGet<OverviewResp>('/newapi/overview/' + active).then(setOv).catch(() => {})
                      } catch {}
                    }}
                  >
                    {alive ? '停止' : '启动'}
                  </button>
                </div>
              </div>
              <div id="pywebio-scope-stat-bar">
                <p>统计</p>
                <div id="pywebio-scope-stat_btn">
                  <button className="pywebio-btn" onClick={() => navigate('/stat/' + active)}>打开</button>
                </div>
              </div>
              <hr className="hr-group" />
              <div id="pywebio-scope-running">
                <p>运行中</p>
                <div id="pywebio-scope-running_tasks">
                  {ov && ov.running.length
                    ? ov.running.map(t => <TaskCard key={t.command} task={t} />)
                    : <span style={{ ['--overview-notask-text']: '1' } as React.CSSProperties}>无任务</span>}
                </div>
              </div>
              <hr className="hr-group" />
              <div id="pywebio-scope-pending">
                <p>队列中<span className="overview-task-count">{ov?.pending.length ?? 0}</span></p>
                <div id="pywebio-scope-pending_tasks">
                  {ov?.pending.map(t => <TaskCard key={t.command} task={t} />)}
                </div>
              </div>
              <hr className="hr-group" />
              <div id="pywebio-scope-waiting">
                <p>等待中<span className="overview-task-count">{ov?.waiting.length ?? 0}</span></p>
                <div id="pywebio-scope-waiting_tasks">
                  {ov?.waiting.map(t => <TaskCard key={t.command} task={t} />)}
                </div>
              </div>
            </section>

            <section id="pywebio-scope-logs">
              <div id="pywebio-scope-log-bar">
                <span className="ov-card-title">日志</span>
                <div id="pywebio-scope-log-bar-btns" className="log-bar-btns">
                  <button className="log-btn-primary" onClick={() => setAutoScroll(v => !v)}>
                    {autoScroll ? '自动滚动 开' : '自动滚动 关'}
                  </button>
                  <button className="log-btn">截图预览</button>
                  <button className="log-btn">导出今日日志</button>
                  <button className="log-btn">折叠</button>
                </div>
              </div>
              <div id="pywebio-scope-dashboard">
                {dash.map(item => <DashEntry key={item.name} item={item} />)}
              </div>
              <div id="pywebio-scope-log-container" className="log-container">
                <code
                id="pywebio-scope-log"
                className="log-body"
                ref={el => el}
                dangerouslySetInnerHTML={{ __html: logHtml }}
              />
              </div>
            </section>
          </div>
        </main>

        <div id="pywebio-scope-statistics-content" style={{ display: 'none' }} />
      </div>
    </div>
  )
}
