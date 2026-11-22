// 调度器快启悬浮球（V3 · 球面即主实例状态）
//
// 长卿 2026-11-22：「调度器不是就启动吗？在其他任务配置页它是不会显示的……
// 我想做一个单独的启动悬浮……平常不用它就自动缩放成侧边（5 秒左右），
// 鼠标拖过去跟悬浮球一样自动展开，自己点击调度器直接就启动了，
// 单个实例分单个实例的」。
//
// 定位方案（第三版，前两版像素锚点反复出界，教训见 .workbuddy/memory/2026-11-22.md）：
//   只存两个语义值 —— side（'left'|'right'）+ topPercent（0~100，球心垂直位置百分比）。
//   渲染纯 CSS：贴边 = left:0 或 right:0，垂直 = top:percent% + translateY(-50%)。
//   没有任何像素换算，窗口缩放/旋转/跨设备永远不出界。
//   拖动：pointermove 实时把 clientY 换算成 topPercent，X 只判断「中心在左半屏还是右半屏」，
//   松手落 side。位置语义简单到不可能出界。
//
// 行为：
//   - 全局一颗球，所有页面显示（挂 Shell 内，总览页不显示）；
//   - 球面颜色 = 主实例状态色（运行=绿呼吸圈 / 空闲=灰 / 异常=红 / 更新=蓝），
//     角标 = 运行数/总数；instances 空时球保留（灰球 + …），面板显示空态提示；
//   - 悬停或点击展开实例列表（触屏 tap 也要能开，2026-11-22 教训）；点行设主实例，
//     点按钮直接启停；移开 5 秒收回贴边半透明；
//   - 数据复用 Shell 的 instances 轮询，本组件零额外请求。
import { useEffect, useRef, useState } from 'react'
import { apiPost, instSeg } from '../api/client'

/** 与 Shell.tsx:94 的 Instance 同构（那边未导出，这里本地声明保持零侵入）。 */
type Instance = { name: string; state: number }

// 状态码语义与侧栏图标一致（data.py get_instances 注释）：
// 1 running / 2 inactive / 3 warning / 4 updating
const STATE_COLOR: Record<number, string> = {
  1: '#34b46b',
  2: '#8a93a6',
  3: '#e05252',
  4: '#3d7fe0',
}
const STATE_TEXT: Record<number, string> = {
  1: '运行中',
  2: '已停止',
  3: '异常停止',
  4: '更新中',
}

const LS_PRIMARY = 'alas_sched_fab_primary'
const LS_POS = 'alas_sched_fab_side'

type Side = 'left' | 'right'

/** 位置持久化：{side, topPercent}。读不到/非法 → 默认右缘 50%。 */
function loadSidePos(): { side: Side; topPercent: number } {
  try {
    const raw = localStorage.getItem(LS_POS)
    if (raw) {
      const p = JSON.parse(raw)
      const side: Side = p.side === 'left' ? 'left' : 'right'
      const tp = Number(p.topPercent)
      if (Number.isFinite(tp)) {
        return { side, topPercent: Math.min(95, Math.max(5, tp)) }
      }
    }
  } catch { /* 隐私模式 */ }
  return { side: 'right', topPercent: 50 }
}

export function SchedulerFab({ instances, visible }: { instances: Instance[]; visible: boolean }) {
  const [open, setOpen] = useState(false)
  const [dim, setDim] = useState(true)
  const [primary, setPrimary] = useState<string>(() => '')
  const [busy, setBusy] = useState<string>('')
  const [notice, setNotice] = useState('')
  const [sidePos, setSidePos] = useState<{ side: Side; topPercent: number }>(() => loadSidePos())
  const [dragging, setDragging] = useState(false)
  // 拖动来源：'ball' = 拖球（收起面板、只显示球）；'head' = 拖面板标题条（面板保持打开跟随移动）
  const [dragSource, setDragSource] = useState<'ball' | 'head'>('ball')
  const rootRef = useRef<HTMLDivElement | null>(null)
  const closeTimer = useRef<number | null>(null)
  const noticeTimer = useRef<number | null>(null)
  // hover 展开延时（长卿 2026-11-22：「鼠标悬浮上去 0.几秒就打开了 我根本拖动不了」
  // → 先改 450ms，仍嫌易误触 → 最终定 2 秒）。
  // 球悬停瞬间就展开会让球 opacity:0 消失，想拖也抓不到；2 秒门槛下
  // 扫过、按下拖动都不会误触发，只有「真的停在那儿」才展开。
  const openTimer = useRef<number | null>(null)
  const drag = useRef<{ startX: number; startY: number; startTopPercent: number; moved: boolean } | null>(null)
  // 拖动刚结束的标记：pointerup 后浏览器还会补一个 click，不能让它误展开面板
  const wasDragged = useRef(false)

  // 主实例：首次拿到实例列表后初始化（localStorage 优先，失效回退第一个）
  useEffect(() => {
    if (!instances.length) return
    setPrimary(prev => {
      if (prev) {
        return instances.some(i => i.name === prev) ? prev : instances[0].name
      }
      try {
        const v = localStorage.getItem(LS_PRIMARY)
        if (v) return v
      } catch { /* 隐私模式 */ }
      return instances[0].name
    })
  }, [instances])

  // 2.5 秒无操作自动收回（悬停期间不计时；长卿 2026-11-22：5 秒太黏，半天不消失）
  const armClose = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => {
      setOpen(false)
      setDim(true)
    }, 2500)
  }
  const disarmClose = () => {
    if (closeTimer.current) { window.clearTimeout(closeTimer.current); closeTimer.current = null }
  }
  /** hover 延时展开：2 秒内移走或按下拖动则取消。 */
  const armOpen = () => {
    disarmClose()
    if (openTimer.current) window.clearTimeout(openTimer.current)
    openTimer.current = window.setTimeout(() => {
      openTimer.current = null
      setOpen(true)
      setDim(false)
    }, 2000) // 悬停 2 秒才展开（长卿 2026-11-22：450ms 还是太容易误触）
  }
  const disarmOpen = () => {
    if (openTimer.current) { window.clearTimeout(openTimer.current); openTimer.current = null }
  }
  useEffect(() => () => { disarmClose(); if (noticeTimer.current) window.clearTimeout(noticeTimer.current) }, [])

  const flashNotice = (msg: string) => {
    setNotice(msg)
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => setNotice(''), 2200)
  }

  const toggleScheduler = async (name: string, running: boolean) => {
    if (busy) return
    setBusy(name)
    try {
      const r = await apiPost<{ success: boolean; error?: string; alive?: boolean }>(
        `/newapi/scheduler/${instSeg(name)}/${running ? 'stop' : 'start'}`)
      if (r?.success) {
        flashNotice(`${name} ${running ? '已停止' : '已启动'}`)
      } else {
        flashNotice(`${name} 操作失败：${r?.error || '未知错误'}`)
      }
    } catch {
      flashNotice(`${name} 请求失败（服务未响应）`)
    } finally {
      setBusy('')
      armClose()
    }
  }

  const setPrimaryInst = (name: string) => {
    setPrimary(name)
    try { localStorage.setItem(LS_PRIMARY, name) } catch { /* 隐私模式 */ }
  }

  // ---- 拖动：跟手模式（长卿 2026-11-22：「完全不跟着鼠标走，直接硬切」）----
  // 拖动中用自由像素坐标（球心=鼠标），一比一跟手；松手才吸附 side + topPercent。
  const [freePos, setFreePos] = useState<{ x: number; y: number } | null>(null)
  useEffect(() => {
    const onMove = (ev: PointerEvent) => {
      const d = drag.current
      if (!d) return
      if (!d.moved && Math.abs(ev.clientX - d.startX) + Math.abs(ev.clientY - d.startY) < 4) return
      d.moved = true
      setDragging(true)
      // 一比一跟手：球心 = 鼠标位置（clamp 在视口内防拖丢）
      const half = 23
      const x = Math.max(half, Math.min(window.innerWidth - half, ev.clientX))
      const y = Math.max(half, Math.min(window.innerHeight - half, ev.clientY))
      setFreePos({ x, y })
    }
    const onUp = () => {
      const d = drag.current
      if (!d) return
      drag.current = null
      setDragging(false)
      if (!d.moved) { setFreePos(null); return } // 纯点击：不留自由位
      wasDragged.current = true // 标记本次是拖动，吞掉随后的 click
      // 松手吸附：中心在左半屏 → side-left，否则 side-right；top 转百分比落盘
      setFreePos(p => {
        if (p) {
          const side: Side = p.x < window.innerWidth / 2 ? 'left' : 'right'
          const tp = Math.min(95, Math.max(5, p.y / window.innerHeight * 100))
          const next = { side, topPercent: tp }
          setSidePos(next)
          try { localStorage.setItem(LS_POS, JSON.stringify(next)) } catch { /* 隐私模式 */ }
        }
        return null // 回到 side+percent 渲染（吸附由 CSS transition 平滑过渡）
      })
      // 拖面板头：松手后面板继续开着（用户正在里面操作）；拖球：保持收起
      if (dragSource === 'head') { setOpen(true); setDim(false) }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])

  const onFabPointerDown = (ev: React.PointerEvent, source: 'ball' | 'head') => {
    if (ev.button !== 0 && ev.pointerType === 'mouse') return
    // 按下 = 要拖动：取消待展开计时（长卿 2026-11-22：拖动时不能弹开面板）
    disarmOpen()
    disarmClose()
    setDragSource(source)
    // 拖球：收起面板让球单独跟手；拖面板头：面板保持打开一起移动
    if (source === 'ball') setOpen(false)
    const el = rootRef.current
    const r = el?.getBoundingClientRect()
    drag.current = {
      startX: ev.clientX,
      startY: ev.clientY,
      startTopPercent: r && el ? ((r.top + el.offsetHeight / 2) / window.innerHeight * 100) : sidePos.topPercent,
      moved: false,
    }
  }

  // 开关关闭时不渲染。instances 为空保留球（远控端轮询失败时球会闪现即消失的教训）。
  if (!visible) return null

  const runningCount = instances.filter(i => i.state === 1).length
  const main = instances.find(i => i.name === primary) ?? instances[0]
  const mainColor = main ? (STATE_COLOR[main.state] ?? '#8a93a6') : '#8a93a6'
  const mainInitial = main ? main.name.slice(0, 1) : '?'
  const hasInstances = instances.length > 0
  // 展开方向：球贴右缘 → 面板朝左；贴左缘 → 朝右。
  const side = sidePos.side

  return (
    <div
      ref={rootRef}
      className={'alas-sched-fab' + (open ? ' alas-sched-fab-open' : '') + (dim ? ' alas-sched-fab-dim' : '')
        + (dragging ? ' alas-sched-fab-dragging' : '')
        + ` alas-sched-fab-side-${side}`}
      data-drag-src={dragSource}
      style={dragging && freePos
        // 拖动中：球心=鼠标。⚠️ 不能用 translate(-50%)：容器含面板（宽 236），
        // -50% 是容器的一半不是球的一半，会偏移一个面板宽（长卿实测球心偏 100px）。
        ? { left: `${freePos.x - 23}px`, right: 'auto', top: `${freePos.y - 23}px`, transform: 'none' }
        : { top: `${sidePos.topPercent}%` }}
      onMouseEnter={() => { armOpen() }}
      onMouseLeave={() => { disarmOpen(); armClose(); }}
    >
      {/* 球面：主实例状态色 + 首字 + 运行角标；按住拖动，单击/触屏 tap 展开 */}
      <div className="alas-sched-fab-ball" style={{ ['--fab-c' as string]: mainColor }}
        onPointerDown={(ev) => onFabPointerDown(ev, 'ball')}
        onClick={() => {
          // 拖动松手后浏览器会补 click：吞掉，别误展开（长卿 2026-11-22 拖不动根因之一）
          if (wasDragged.current) { wasDragged.current = false; return }
          armClose()
          setOpen(true)
          setDim(false)
        }}
        title="按住拖动 · 单击展开">
        <span className="alas-sched-fab-initial">{mainInitial}</span>
        {main?.state === 1 && <i className="alas-sched-fab-pulse" />}
        <span className="alas-sched-fab-badge">{hasInstances ? `${runningCount}/${instances.length}` : '…'}</span>
      </div>
      {/* 展开面板（头部=拖动把手） */}
      <div className="alas-sched-fab-panel">
        <div className="alas-sched-fab-head" onPointerDown={(ev) => onFabPointerDown(ev, 'head')}
          onClick={() => { if (wasDragged.current) { wasDragged.current = false; return } setOpen(true); setDim(false) }}>
          <span className="alas-sched-fab-head-title" title="点实例名可设为主实例（球面显示它的状态）">≡ 调度器快启</span>
          {/* 关闭按钮：立即收起回到贴边小球（长卿 2026-11-22 要求） */}
          <button
            type="button"
            className="alas-sched-fab-close"
            title="关闭"
            onPointerDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => { ev.stopPropagation(); disarmClose(); setOpen(false); setDim(true) }}
          >×</button>
        </div>
        {!hasInstances && (
          <div className="alas-sched-fab-empty">实例列表获取中…（若长时间空白，请检查远控连接）</div>
        )}
        {instances.map(inst => {
          const running = inst.state === 1
          const c = STATE_COLOR[inst.state] ?? '#8a93a6'
          const isMain = inst.name === main?.name
          return (
            <div
              key={inst.name}
              className={'alas-sched-fab-row' + (isMain ? ' alas-sched-fab-row-main' : '')}
              onClick={() => setPrimaryInst(inst.name)}
              title={isMain ? '当前主实例（球面显示它的状态）' : '点击设为主实例'}
            >
              <span className="alas-sched-fab-dot" style={{ ['--fab-c' as string]: c }} />
              <span className="alas-sched-fab-name">{inst.name}</span>
              <em className="alas-sched-fab-state">{STATE_TEXT[inst.state] ?? ''}</em>
              <button
                type="button"
                className={'alas-sched-fab-btn' + (running ? ' is-stop' : ' is-start')}
                disabled={busy === inst.name}
                onClick={(ev) => { ev.stopPropagation(); toggleScheduler(inst.name, running) }}
              >{busy === inst.name ? '…' : running ? '停止' : '启动'}</button>
            </div>
          )
        })}
        {notice && <div className="alas-sched-fab-notice">{notice}</div>}
      </div>
    </div>
  )
}
