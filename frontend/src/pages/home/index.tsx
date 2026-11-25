// 总览页：与老 UI（app_overview.py:218-460 + app_dashboard.py）1:1 同构。
//
// DOM 契约（实测老 UI 真实节点，见 .workbuddy/perf/out/oldui-ref/）：
//   #pywebio-scope-content
//     > #pywebio-scope-overview               ← alas-pc.css: grid 2 列 16-20rem / 1fr
//       > #pywebio-scope-schedulers          ← grid 5 行（bar/bar/运行中/队列中/等待中）
//         > #pywebio-scope-scheduler-bar     ← 白卡 + 「调度器」+ 启停按钮
//         > #pywebio-scope-stat-bar          ← 白卡 + 「统计」+ 打开按钮
//         > #pywebio-scope-running           ← 标题 + hr + running_tasks
//         > #pywebio-scope-pending           ← 标题(带计数徽标) + hr + pending_tasks
//         > #pywebio-scope-waiting           ← 标题(带计数徽标) + hr + waiting_tasks
//       > #pywebio-scope-logs
//         > #pywebio-scope-log-bar           ← 白卡 + 「日志」+ 4 个按钮 + hr + dashboard
//         > #pywebio-scope-log-container     ← style 带 --version 变量 + ::before 水印
//           > #pywebio-scope-log             ← 日志正文（Rich HTML）
//
// 关键点：CSS 全部靠内联 style 的子串匹配选中，因此这里不用 React style 对象，
// 一律通过 lib/pywebio.ts 输出与 PyWebIO 逐字节一致的 HTML 字符串。
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, instSeg, withPrefix } from '../../api/client'
import { downloadRuntimeLog } from '../../api/logExport'
import { Shell, toggleSchedFab } from '../../components/Shell'
import { esc, putText } from '../../lib/pywebio'
import { MaintenanceBanner } from './MaintenanceBanner'

type TaskRow = { command: string; name: string; next_run: string }
type OverviewResp = { running: TaskRow[]; pending: TaskRow[]; waiting: TaskRow[]; alive: boolean }
type DashItem = { name: string; label?: string; value: string; limit: string; total: string; color: string; record: string }
type DashResp = { items: DashItem[] }

// 老 UI 资源名（config/argument/dashboard.yaml + i18n Gui.Dashboard.*）
const DASH_LABEL: Record<string, string> = {
  Oil: '石油',
  Coin: '物资',
  Gem: '钻石',
  Pt: '活动PT',
  Cube: '魔方',
  ActionPoint: '行动力',
  YellowCoin: '大世界黄币',
  PurpleCoin: '大世界紫币',
  Core: '核心数据',
  Medal: '勋章',
  Merit: '功勋',
  GuildCoin: '舰队币',
}

// 相对时间（app_dashboard.py 口径：秒前/分钟前/小时前/天前/月前/年前）
function relTime(record: string): string {
  if (!record || record.startsWith('2023-01-01')) return '无数据'
  const then = new Date(record.replace(' ', 'T'))
  const s = Math.floor((Date.now() - then.getTime()) / 1000)
  if (isNaN(s) || s < 0) return '无记录'
  if (s < 60) return s + '秒前'
  if (s < 3600) return Math.floor(s / 60) + '分钟前'
  if (s < 86400) return Math.floor(s / 3600) + '小时前'
  const d = Math.floor(s / 86400)
  if (d < 30) return d + '天前'
  if (d < 365) return Math.floor(d / 30) + '月前'
  return Math.floor(d / 365) + '年前'
}

/** 任务卡（app_dashboard.py:222-250 _overview_task 同构）。
 *  id=pywebio-scope-overview-task_{command}；内层 put_column(auto auto) 放
 *  标题(--arg-title--) 与时间(--arg-help--)，右侧「设置」按钮。 */
function taskCard(task: TaskRow, settingLabel: string): string {
  return (
    `<div id="pywebio-scope-overview-task_${esc(task.command)}">` +
    `<div style="display: grid; grid-auto-flow: row; grid-template-rows: auto auto;">` +
    putText(task.name, ';;--arg-title--') +
    putText(task.next_run, ';;--arg-help--') +
    `</div>` +
    `<div><button class="btn btn-off">${settingLabel}</button></div>` +
    `</div>`
  )
}

/** 数值转数字；None/非数字返回 null。 */
function dashNum(v: string | null | undefined): number | null {
  if (v == null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** 资源名 → 图标文件（frontend/public/icons/*.webp）。
 *  图标来自上游 AzurPilot 的 frontend/public（webp 透明背景），已按实际观感核对映射：
 *  行动力 = 金锚（上游文件名 guild_coin）、舰队币 = 银币（上游文件名 stamina）——
 *  上游的文件名与我们的资源名并不一一对应，这里按「资源名」重新命名落地，避免误导。
 *  缺失时回退成 color 色点（后端 group.Color）。 */
const DASH_ICON: Record<string, string> = {
  Oil: 'Oil',
  Coin: 'Coin',
  Gem: 'Gem',
  Cube: 'Cube',
  Pt: 'Pt',
  ActionPoint: 'ActionPoint',
  YellowCoin: 'YellowCoin',
  PurpleCoin: 'PurpleCoin',
  Core: 'Core',
  Medal: 'Medal',
  Merit: 'Merit',
  GuildCoin: 'GuildCoin',
}

/** 仪表盘单条（C4 版式，长卿 2026-11-25 选定）：
 *     [24px 图标] 资源名 ........ 数值(右对齐)
 *                  上限 —        更新时间
 *                  ─── 2px 进度线（仅有上限时显示，溢出转红）
 *  结构说明：
 *    - 用 grid（icon / body 两列），数字统一右对齐形成整齐的数字列；
 *    - 名称 flex:1 + ellipsis，"大世界黄币"这类长名不推挤数值；
 *    - 上限与时间同一行，时间 margin-left:auto 贴右；
 *    - 进度线只在 Limit 有效时出现；Total（行动力累计）不算上限，不画线。 */
function dashEntry(item: DashItem): string {
  const hasValue = item.value !== 'None' && !!item.value
  const value = hasValue ? item.value : '无'
  const limitNum = dashNum(item.limit)
  const valueNum = dashNum(item.value)
  const hasLimit = limitNum != null && limitNum > 0 && valueNum != null
  const pct = hasLimit ? (valueNum! / limitNum!) * 100 : 0
  const shown = Math.min(Math.max(pct, 0), 100)
  const over = hasLimit && pct >= 100

  // 第二行：上限 / 累计 / 占位破折号
  const sub = item.limit
    ? putText('/ ' + item.limit, over ? ';;--dashboard-limit--;;is-over' : ';;--dashboard-limit--')
    : item.total
      ? putText('累计 ' + item.total, ';;--dashboard-total--')
      : putText('—', ';;--dashboard-none--')

  const iconName = DASH_ICON[item.name]
  const iconHtml = iconName
    ? `<img class="dash-ico" src="${withPrefix('/icons/' + iconName + '.webp')}" alt="" width="24" height="24">`
    : (() => {
        // 无图标时回退成后端给的资源色点
        const raw = (item.color || '').trim()
        const c = /^#?[0-9A-Fa-f]{6}$/.test(raw) ? (raw.startsWith('#') ? raw : '#' + raw) : '#8a93a6'
        return `<span class="dash-ico dash-ico-dot" style="background:${c}"></span>`
      })()

  return (
    `<div class="dash-item" id="pywebio-scope-dashboard_${esc(item.name)}">` +
    iconHtml +
    `<div class="dash-body" id="pywebio-scope-${esc(item.name)}_group">` +
    `<div class="dash-row1">` +
    putText(item.label || DASH_LABEL[item.name] || item.name, ';;--dashboard-name--') +
    putText(value, ';;--dashboard-value--') +
    `</div>` +
    `<div class="dash-row2">` +
    sub +
    putText(relTime(item.record), ';;--dashboard-help--') +
    `</div>` +
    (hasLimit
      ? `<div class="dash-bar"><i class="dash-bar-fill${over ? ' is-over' : ''}" style="width:${shown}%"></i></div>`
      : `<div class="dash-bar dash-bar-none"></div>`) +
    `</div></div>`
  )
}

export function Overview({ instance }: { instance: string }) {
  const navigate = useNavigate()
  const [ov, setOv] = useState<OverviewResp | null>(null)
  const [dash, setDash] = useState<DashItem[]>([])
  const [version, setVersion] = useState('')
  // 总览页的**固定文案**（调度器/统计/运行中/队列中/等待中/日志/按钮）
  // 必须走后端 t()，不能在 JSX 里硬编码中文 —— 否则切到日文后这些位置
  // 仍是中文（长卿 2026-11-17 截图：侧栏变成 ホームページ，但「调度器」「统计」
  // 「运行中」「队列中」「等待中」还是中文）。老 UI 这些全是
  // t("Gui.Overview.X")，新 UI 曾经写死字面量漏了翻译。
  const [i18n, setI18n] = useState<Record<string, string>>({})
  useEffect(() => {
    // ⚠️ 用**无参数**接口 /newapi/ui-labels，不要用 /newapi/i18n?keys=…：
    // 远控中继转发会剥掉 query string，参数一丢就只剩空结果、全部回退中文
    // （长卿 2026-11-17 截图：侧栏已经是 Home，页面里「调度器/统计/运行中/
    // 队列中/等待中」还是中文）。无参接口不依赖 query，中继下也拿得到。
    const load = () => {
      apiGet<{ success: boolean; items: Record<string, string> }>(
        '/newapi/ui-labels', 10000,
      ).then(r => { if (r?.items) setI18n(r.items) }).catch(() => { /* 老后端：保持中文兜底 */ })
    }
    load()
    // 切语言后 Shell 会派发该事件（见 aura-language-change 的用法）
    window.addEventListener('alas-language-change', load)
    return () => window.removeEventListener('alas-language-change', load)
  }, [])
  /** 取译文；后端没给就退回中文兜底（与老 UI t() 的失败语义一致）。 */
  const L = (key: string, fallback: string) => i18n[key] || fallback
  const [autoScroll, setAutoScroll] = useState(true)
  const [dashboardShown, setDashboardShown] = useState(true)
  // 悬浮快启开关变化 → 重渲染调度器卡片（开关 label 的 on 类由 schedFabStore 决定）。
  // 开关在总览页卡片上，球显隐在 Shell —— 两组件靠 store 事件同步。
  const [fabOn, setFabOn] = useState<boolean>(() => {
    try { return localStorage.getItem('alas_sched_fab_enabled') !== '0' } catch { return true }
  })
  useEffect(() => {
    const on = (ev: Event) => setFabOn((ev as CustomEvent<boolean>).detail)
    window.addEventListener('alas-sched-fab-change', on)
    return () => window.removeEventListener('alas-sched-fab-change', on)
  }, [])
  const autoScrollRef = useRef(true)
  autoScrollRef.current = autoScroll
  const aliveRef = useRef(false)

  useEffect(() => {
    apiGet<{ version: string }>('/newapi/version').then(r => setVersion(r.version)).catch(() => {})
  }, [])

  useEffect(() => {
    let mounted = true
    const load = () => {
      apiGet<OverviewResp>('/newapi/overview/' + instance)
        .then(next => {
          if (!mounted) return
          aliveRef.current = next.alive
          // 快照 diff（老 UI _overview_snapshot 语义）：无变化不触发重渲染
          setOv(prev => {
            const sig = JSON.stringify([next.alive, next.running, next.pending, next.waiting])
            const prevSig = prev ? JSON.stringify([prev.alive, prev.running, prev.pending, prev.waiting]) : ''
            return sig === prevSig ? prev : next
          })
        })
        .catch(() => {})
      apiGet<DashResp>('/newapi/dashboard/' + instance)
        .then(r => { if (mounted) setDash(r.items) })
        .catch(() => {})
    }
    load()
    const timer = setInterval(load, 15000) // 老 UI task_handler 15s 周期
    return () => { mounted = false; clearInterval(timer) }
  }, [instance])

  // 日志流：增量游标 + DocumentFragment 单次插入 + 分块裁剪（老 UI RichLog.extend 同款）。
  // 分块而非逐节点裁剪：rich 输出的 span 之间夹着裸文本节点，逐节点删会残留、内存无限涨。
  //
  // width 必须与老 UI RichLog.get_width() 同口径，否则 rich 的换行位置不同，
  // 日志看起来"提前折行"。算法见 module/webui/widgets.py:get_width：
  //   (容器宽-16) / 容器 font-size * 16 / '0' 字宽，钳在 80..128
  const measureLogWidth = (scope: HTMLElement): number => {
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    if (!ctx) return 80
    ctx.font = '16px Menlo, consolas, DejaVu Sans Mono, Courier New, monospace'
    const charWidth = ctx.measureText('0').width || 8.8
    const fontSize = parseFloat(getComputedStyle(scope).fontSize) || 13.6
    const raw = ((scope.clientWidth - 16) / fontSize) * (16 / charWidth)
    if (!isFinite(raw) || raw <= 0) return 80
    return raw > 128 ? 128 : raw < 80 ? 80 : Math.floor(raw)
  }

  useEffect(() => {
    let cursor = -1
    let width = 80
    let widthMeasured = false
    let stopped = false
    // ⚠️ 不要按 document.hidden 暂停：无头浏览器/后台标签页 hidden 恒为 true，
    // 初始化的 visible=false 会让 tick 永远 return —— 实测总览页日志区整块空白
    // （/tail 一次请求都没发出，CDP 网络面板零记录）。
    // 2 秒一次的本地请求开销可忽略；老 UI 的 put_log 循环同样无条件跑。
    const tick = () => {
      if (stopped) return
      const scope = document.getElementById('pywebio-scope-log') as HTMLElement | null
      if (!scope) return
      if (!widthMeasured) {
        width = measureLogWidth(scope)
        widthMeasured = true
      }
      fetch(
        `/newapi/log/${instSeg(instance)}/tail?since=${cursor}&width=${width}`,
      )
        .then(r => (r.ok ? r.json() : null))
        .then((j: { html: string; cursor: number; full: boolean } | null) => {
          if (!j || stopped || !j.html) { if (j) cursor = j.cursor; return }
          cursor = j.cursor
          let box = scope.querySelector(':scope>div') as HTMLElement | null
          if (!box) {
            // 首帧：容纳块还不存在（React 还没画完），先补容器再按增量写
            box = document.createElement('div')
            scope.innerHTML = ''
            scope.appendChild(box)
          }
          if (j.full) {
            // 全量重建（游标失效/环形回绕）：只保留一个块，避免旧内容残留
            const chunk = document.createElement('div')
            chunk.className = 'alas-log-chunk'
            chunk.style.display = 'contents'
            chunk.innerHTML = j.html
            box.innerHTML = ''
            box.appendChild(chunk)
          } else {
            const chunk = document.createElement('div')
            chunk.className = 'alas-log-chunk'
            chunk.style.display = 'contents'
            chunk.innerHTML = j.html
            box.appendChild(chunk)
            // 200 块上限（老 UI RichLog.dom_max_chunks）
            while (box.children.length > 200) box.removeChild(box.firstElementChild as Node)
          }
          if (autoScrollRef.current) {
            requestAnimationFrame(() => { scope.scrollTop = scope.scrollHeight })
          }
        })
        .catch(() => {})
    }
    const timer = setInterval(tick, 2000)
    const first = setTimeout(tick, 400)
    return () => {
      stopped = true
      clearInterval(timer)
      clearTimeout(first)
    }
  }, [instance])

  // 事件委托：「调度器启停 / 统计打开 / 自动滚动 / 折叠 / 任务设置」都渲染成 HTML 字符串，
  // 无法挂 React 事件，统一在容器上按文本分派（与老 UI onclick 语义一致）。
  const onSchedulersClick = async (ev: React.MouseEvent<HTMLDivElement>) => {
    // 悬浮快启开关（label，非 button）：先于按钮分派处理
    const fabSwitch = (ev.target as HTMLElement).closest('.alas-fab-switch') as HTMLElement | null
    if (fabSwitch) {
      toggleSchedFab()
      return
    }
    const target = (ev.target as HTMLElement).closest('button')
    if (!target) return
    const label = (target.textContent ?? '').trim()
    if (label === L('Gui.Button.Start', '启动') || label === L('Gui.Button.Stop', '停止')) {
      const action = label === L('Gui.Button.Stop', '停止') ? 'stop' : 'start'
      try {
        await fetch(`/newapi/scheduler/${instSeg(instance)}/${action}`, { method: 'POST' })
      } catch { /* 网络异常：等 15s 轮询收敛 */ }
      const next = await apiGet<OverviewResp>('/newapi/overview/' + instance).catch(() => null)
      if (next) setOv(next)
      return
    }
    if (label === L('Gui.Button.Open', '打开')) { navigate('/stat/' + encodeURIComponent(instance)); return }
    if (label === L('Gui.Button.Setting', '设置')) {
      // 找到按钮所属任务卡，取 command 后进配置页
      const card = target.closest('[id^="pywebio-scope-overview-task_"]') as HTMLElement | null
      const command = card?.id.replace('pywebio-scope-overview-task_', '') ?? ''
      if (command) navigate(`/config/${encodeURIComponent(instance)}/${encodeURIComponent(command)}`)
    }
  }

  const onLogBarClick = (ev: React.MouseEvent<HTMLDivElement>) => {
    const target = (ev.target as HTMLElement).closest('button')
    if (!target) return
    const label = (target.textContent ?? '').trim()
    if (label.startsWith('自动滚动')) {
      const next = !autoScrollRef.current
      setAutoScroll(next)
      // 开启时立即滚到底：否则「日志静止」（调度器未跑）时点了开关毫无反应，
      // 看起来像坏了。老 UI 因为有 0.5s 的 put_log 循环，天然会在下一拍滚到底，
      // 新 UI 的轮询在没有新内容时不会触发滚动，所以这里补一次。
      if (next) {
        const el = document.getElementById('pywebio-scope-log')
        if (el) requestAnimationFrame(() => { el.scrollTop = el.scrollHeight })
      }
      return
    }
    if (label === '折叠' || label === '展开') { setDashboardShown(v => !v); return }
    // 截图预览：直接调老 UI 的 alas-utils.js（已作为 public/alas-utils.js 引入，
    // 它内部走 /ws/live_screenshot 的 scrcpy H.264 链路，样式与交互与老 UI 完全一致）
    if (label === '截图预览') { window.alasToggleLivePreview?.(instance); return }
    // 导出今日日志：走老 UI 现成的 /api/log/runtime（today 范围）。
    //
    // 两个远控坑（都实测过）：
    // 1. 不能再用 window.open：远控中继下打开的是裸 URL，不带 /{peer} 前缀 →
    //    中继 502（长卿 2026-11-18 实测）。而且它走浏览器导航，
    //    main.tsx 劫持 fetch 的那套前缀逻辑管不到。
    // 2. 参数不能只放在 query 里：中继会把 query 整段剥掉 →
    //    后端拿不到 instance → HTTP 400「缺少实例名」
    //    （2026-11-20 实测 /api/log/runtime?instance=alas&scope=today 就是 400，
    //     而 path 形式 /api/log/runtime/alas/today 正常返回）。
    //    候选 URL 与失败详情统一交给 api/logExport.ts 处理。
    //
    // scope 用字面量 'today' 交给服务端解析日期，不在客户端 new Date() 算：
    // 远控时手机时区/时钟可能和服务器不一致，会算成别的天（导错或 404）。
    if (label === '导出今日日志') {
      const tip = document.createElement('div')
      tip.textContent = '正在导出日志…'
      tip.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:99999;padding:10px 18px;border-radius:999px;background:rgba(0,0,0,.78);color:#fff;font-size:13px;font-family:system-ui;pointer-events:none'
      document.body.appendChild(tip)
      downloadRuntimeLog(instance, 'today')
        .then(name => { tip.textContent = '已导出 ' + name })
        .catch(e => { tip.textContent = '导出失败：' + String(e) })
        .finally(() => { setTimeout(() => tip.remove(), 2500) })
    }
  }
  // ── 总览左右两列（全部为 HTML 字符串，保证 style 标记逐字节同构）───────────
  const alive = ov?.alive ?? false
  const schedulersHtml =
    `<div id="pywebio-scope-scheduler-bar">` +
    putText(L('Gui.Overview.Scheduler', '调度器'), ';;font-size: 1.25rem; margin: auto .5rem auto;') +
    // 悬浮快启开关（长卿 2026-11-22：放左边标题旁，启动/停止按钮保持最右老位置）。
    // 开关管「其他页面右缘的调度器快启球」，总览页本身不显示球。
    `<label class="alas-fab-switch${fabOn ? ' on' : ''}" id="alas-fab-switch" title="在其他页面右缘显示调度器快启悬浮球"><span class="alas-fab-switch-tk"></span>悬浮快启</label>` +
    `<div id="pywebio-scope-scheduler_btn"><div><button class="btn ${
      alive ? 'btn-off' : 'btn-on'
    }">${alive ? L('Gui.Button.Stop', '停止') : L('Gui.Button.Start', '启动')}</button></div></div>` +
    `</div>` +
    `<div id="pywebio-scope-stat-bar">` +
    putText(L('Gui.Overview.Stat', '统计'), ';;font-size: 1.25rem; margin: auto .5rem auto;') +
    `<div><button class="btn btn-on">${L('Gui.Button.Open', '打开')}</button></div>` +
    `</div>` +
    `<div id="pywebio-scope-running">` +
    putText(L('Gui.Overview.Running', '运行中')) +
    '<hr class="hr-group">' +
    `<div id="pywebio-scope-running_tasks">${(ov?.running ?? []).map(x => taskCard(x, L('Gui.Button.Setting', '设置'))).join('')}</div>` +
    `</div>` +
    `<div id="pywebio-scope-pending">` +
    `<p class="overview-group-title">${L('Gui.Overview.Pending', '队列中')}<span class="overview-task-count" id="overview-count-pending">${
      ov?.pending.length ?? 0
    }</span></p>` +
    '<hr class="hr-group">' +
    `<div id="pywebio-scope-pending_tasks">${(ov?.pending ?? []).map(x => taskCard(x, L('Gui.Button.Setting', '设置'))).join('')}</div>` +
    `</div>` +
    `<div id="pywebio-scope-waiting">` +
    `<p class="overview-group-title">${L('Gui.Overview.Waiting', '等待中')}<span class="overview-task-count" id="overview-count-waiting">${
      ov?.waiting.length ?? 0
    }</span></p>` +
    '<hr class="hr-group">' +
    `<div id="pywebio-scope-waiting_tasks">${(ov?.waiting ?? []).map(x => taskCard(x, L('Gui.Button.Setting', '设置'))).join('')}</div>` +
    `</div>`

  const logBarHtml =
    `<div id="pywebio-scope-log-bar">` +
    putText(L('Gui.Overview.Log', '日志'), ';;font-size: 1.25rem; margin: auto .5rem auto;') +
    `<div id="pywebio-scope-log-bar-btns">` +
    `<div id="pywebio-scope-log_scroll_btn"><div><button class="btn ${
      autoScroll ? 'btn-on' : 'btn-off'
    }">${autoScroll ? L('Gui.Button.ScrollON', '自动滚动 开') : L('Gui.Button.ScrollOFF', '自动滚动 关')}</button></div></div>` +
    `<div><button class="btn btn-off">${L('Gui.Button.Screenshot', '截图预览')}</button></div>` +
    `<div id="pywebio-scope-log_export_btn"><div><button class="btn btn-off">${L('Gui.LogExport.ExportToday', '导出今日日志')}</button></div></div>` +
    `<div id="pywebio-scope-dashboard_btn"><div><button class="btn btn-off">${
      dashboardShown ? L('Gui.Button.DashboardON', '折叠') : L('Gui.Button.DashboardOFF', '展开')
    }</button></div></div>` +
    `</div>` +
    '<hr class="hr-group">' +
    (dashboardShown ? `<div id="pywebio-scope-dashboard">${dash.map(dashEntry).join('')}</div>` : '<div id="pywebio-scope-dashboard"></div>') +
    `</div>`

  return (
    <Shell active={instance} title={L('Gui.MenuAlas.Overview', '总览')}>
      <div id="pywebio-scope-content">
        {/* 维护等待横幅：老 UI app_overview.py:227 是 put_html 在
            put_scope("overview") **之前**，也就是 content 直下、overview 网格之外。
            ⚠️ 不能放进 #pywebio-scope-overview：那是 2 列网格
            （alas-pc.css:65 minmax(16rem,20rem) + 1fr，schedulers | logs），
            横幅作为第 3 个子元素会被塞进新格子，撑成一条竖条把左栏挤走
            （实测截图：横幅占满左列，调度器被推到右边）。
            只在维护等待/刚开服时出现，其余时刻返回 null 不占位。 */}
        <MaintenanceBanner name={instance} />
        <div id="pywebio-scope-overview">
          <div
            id="pywebio-scope-schedulers"
            onClick={onSchedulersClick}
            dangerouslySetInnerHTML={{ __html: schedulersHtml }}
          />
          <div id="pywebio-scope-logs">
            <div onClick={onLogBarClick} dangerouslySetInnerHTML={{ __html: logBarHtml }} />
            {/* log-container 的 style 必须带 --version 变量：alas.css:1182 用
                ::before content: var(--version) 画右下角版本水印 */}
            {/* 日志区 DOM 契约（实测老 UI 几何值，见下方数值比对）：
                  #pywebio-scope-log-container  外层壳：**无 class、无 padding、无底色**
                                              只有一个 --version 变量供 ::before 画水印
                  #pywebio-scope-log            <pre class="container-log">：**同一元素**
                                               既是 scope 又带 class（widgets.py:60）
                                               底色 0.93 / padding 20px / 圆角 16px 来自这里
                踩过的坑：把 container-log 加在外层容器上，等于套了两层
                padding(20+20) 和两层卡片，日志区被挤窄 40px，与老 UI 明显不符。 */}
            <div
              id="pywebio-scope-log-container"
              style={{ ['--version' as string]: `'Ver.${version}'` } as React.CSSProperties}
            >
              <div id="pywebio-scope-log" className="container-log">
                <div />
              </div>
            </div>
          </div>
        </div>
      </div>
    </Shell>
  )
}
