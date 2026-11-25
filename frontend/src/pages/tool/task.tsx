// 工具页（老 UI app_overview.py:468 alas_daemon_overview 的新 UI 等价物）。
//
// 长卿：「工具这里完全没有用 因为不能运行调度器 调度器没有添加进去，
//         老UI这里可以直接点击运行」
//
// 之前 Shell 把 page=tool 的任务全都导航到 /tool/:name，而那个路由被我写成了
// 「开发者工具」（dev_utils）页，于是「半自动点击 / 性能测试 / 活动剧情」这些
// **工具类任务**点进去看到的是调试按钮，既没有配置项也没有调度器启停按钮，
// 完全没法用。
//
// 老 UI 对 page=tool 走的其实还是 alas_daemon_overview —— 与其他任务配置页同一套，
// 区别只在标题与内容。真实 DOM（实测 22480，见 oldui_toolpage.html）：
//
//   #pywebio-scope-content
//     > #pywebio-scope-daemon-overview         ← 桌面三分行 grid
//       > #pywebio-scope-_daemon
//         > #pywebio-scope-_daemon_upper
//           > #pywebio-scope-scheduler-bar     ← 「调度器」+ 启动/停止按钮
//           > #pywebio-scope-log-bar           ← 「日志」+ 自动滚动/截图预览/导出
//         > #pywebio-scope-groups              ← 配置卡（与配置页同一套 group_ 契约）
//         > #pywebio-scope-log                 ← 日志正文
//
// 因此这里不是新造页面：配置卡复用 ConfigPage 的 DOM 契约，
// 日志复用总览页的增量拉取，调度器走现成的 /newapi/scheduler/*。
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { apiGet, instSeg } from '../../api/client'
import { Shell } from '../../components/Shell'
import { putText } from '../../lib/pywebio'
import { ConfigGroups, type Group } from '../config/groups'
import { LogPanel, exportTodayLog } from './log'

type ConfigResp = { task: string; groups: Group[]; error?: string }
type OverviewResp = { alive: boolean }

export function TaskToolPage() {
  const navigate = useNavigate()
  const { name = 'alas', task = 'Daemon' } = useParams()
  const [groups, setGroups] = useState<Group[]>([])
  const [error, setError] = useState('')
  const [alive, setAlive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  // 标题 = 任务名（老 UI set_title(t(f"Task.{task}.name"))，实测顶部显示「性能测试」）
  const [title, setTitle] = useState(task)

  // 配置 schema + 当前值（与配置页同一个端点）
  useEffect(() => {
    setLoaded(false)
    // 30s：配置 schema 首次要读 args.json(1.5MB) + 实例 config + i18n(3.5MB)，
    // 默认 8s 在冷启动/CPU 忙时会被 AbortController 掐断（实测报 AbortError）。
    apiGet<ConfigResp>(`/newapi/config/${name}/task/${task}`, 30000)
      .then(r => {
        if (r.error) setError(r.error)
        setGroups(r.groups || [])
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoaded(true))
  }, [name, task])

  // 语言切换后要重取任务名（菜单名由后端按当前语言翻译）
  const [langTick, setLangTick] = useState(0)
  useEffect(() => {
    const onLang = () => setLangTick(v => v + 1)
    window.addEventListener('alas-language-change', onLang)
    return () => window.removeEventListener('alas-language-change', onLang)
  }, [])

  // 从菜单树里取任务的中文名（/newapi/menu 已按当前语言翻译）
  useEffect(() => {
    apiGet<{ groups: { tasks: { key: string; name: string }[] }[] }>('/newapi/menu', 20000)
      .then(r => {
        for (const g of r.groups || []) {
          const hit = (g.tasks || []).find(x => x.key === task)
          if (hit) { setTitle(hit.name); return }
        }
      })
      .catch(() => { /* 取不到就用任务 key 兜底 */ })
  }, [task, langTick])
  // 语言切换后重取任务名（菜单名是后端按当前语言翻译的）

  // 调度器状态：15s 轮询（与总览页同一周期）
  useEffect(() => {
    let mounted = true
    const load = () => {
      apiGet<OverviewResp>('/newapi/overview/' + instSeg(name))
        .then(r => { if (mounted) setAlive(!!r.alive) })
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 15000)
    return () => { mounted = false; clearInterval(t) }
  }, [name])

  // 启停：与总览页同一条链路（POST /newapi/scheduler/{name}/{start|stop}）
  const toggleScheduler = async () => {
    setBusy(true)
    try {
      await fetch(`/newapi/scheduler/${instSeg(name)}/${alive ? 'stop' : 'start'}`, {
        method: 'POST',
      })
      // 立刻读回状态：不要等 15s 轮询，否则按钮像没反应
      const next = await apiGet<OverviewResp>('/newapi/overview/' + instSeg(name)).catch(() => null)
      if (next) setAlive(!!next.alive)
    } catch (e) {
      setError('调度器操作失败: ' + String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell active={task} instance={name} title={title}>
      {/* 注意：Shell 的 children 直接挂在 #pywebio-scope-contents（display:flex）下。
          在这里放 content 的**兄弟**元素会被当成 flex 子项，把 content 挤窄
          （实测多一个 <p> + 一个按钮后，content 从 1336px 掉到 856px，
          整页左偏、日志压成一条）。所以所有东西必须放进 content 里面。 */}
      <div id="pywebio-scope-content">
        {error && (
          <p style={{ color: 'var(--alas-entry-badge-error, #c0392b)', margin: '.5rem .625rem' }}>{error}</p>
        )}
        <div id="pywebio-scope-daemon-overview">
          <div />
          <div id="pywebio-scope-_daemon">
            {/* 上半：调度器栏 + 日志工具栏 */}
            <div id="pywebio-scope-_daemon_upper">
              <div id="pywebio-scope-scheduler-bar">
                <span
                  dangerouslySetInnerHTML={{
                    __html: putText('调度器', ';;font-size: 1.25rem; margin: auto .5rem auto;'),
                  }}
                />
                <div id="pywebio-scope-scheduler_btn">
                  <div>
                    <button
                      className={'btn ' + (alive ? 'btn-off' : 'btn-on')}
                      disabled={busy}
                      onClick={toggleScheduler}
                    >
                      {alive ? '停止' : '启动'}
                    </button>
                  </div>
                </div>
              </div>
              <div id="pywebio-scope-log-bar">
                <span
                  dangerouslySetInnerHTML={{
                    __html: putText('日志', ';;font-size: 1.25rem; margin: auto .5rem auto;'),
                  }}
                />
                <div id="pywebio-scope-log-bar-btns">
                  <div id="pywebio-scope-log_scroll_btn">
                    <div>
                      <button
                        className={'btn ' + (autoScroll ? 'btn-on' : 'btn-off')}
                        onClick={() => setAutoScroll(v => !v)}
                      >
                        {'自动滚动 ' + (autoScroll ? '开' : '关')}
                      </button>
                    </div>
                  </div>
                  <div>
                    <button className="btn btn-off" onClick={() => window.alasToggleLivePreview?.(name)}>
                      截图预览
                    </button>
                  </div>
                  <div id="pywebio-scope-log_export_btn">
                    <div>
                      <button className="btn btn-off" onClick={() => exportTodayLog(name)}>
                        导出今日日志
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* 配置卡：与配置页完全同一套 group_ / arg_container- DOM 契约 */}
            <div id="pywebio-scope-groups">
              <ConfigGroups groups={groups} name={name} task={task} onError={setError} />
              {groups.length === 0 && loaded && (
                <p style={{ margin: '.5rem', opacity: .7 }}>该任务没有可配置项</p>
              )}
              {!loaded && <p style={{ margin: '.5rem', opacity: .7 }}>加载中…</p>}
            </div>

            <LogPanel instance={name} autoScroll={autoScroll} />
          </div>
          {/* 老 UI 的 daemon-overview 是**三列** grid（alas-pc.css:70
              grid-template-columns: 1fr minmax(25rem, 6fr) 1fr），
              _daemon 在中间，末尾还有一个 put_none() 占位。
              少了它 _daemon 会落到第 1 列（1fr），整页被拉宽并左偏。 */}
          <div />
        </div>
        <div style={{ padding: '.625rem' }}>
          <button className="btn btn-secondary" onClick={() => navigate('/')}>← 返回主页</button>
        </div>
      </div>
    </Shell>
  )
}