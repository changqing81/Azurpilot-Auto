// 优先级过滤器编辑器：把「a > b > c」这种字符串渲染成可拖拽排序的列表。
//
// 长卿 2026-11-17：这种过滤器能不能改成老版那样子？
// 老 UI 的 task_priority 控件（widgets.py:639 put_arg_task_priority）就是把
// 优先级字符串渲染成可拖拽的 <ol>，拖一下即可调顺序，不用手写 > 分隔符。
// 但 task_priority 只用于任务名（会套 Task.xxx.name 翻译），而这里的元素是
// 舰船代号（bogue / ranger / langley / hermes），不能复用那个控件 ——
// 所以单独做一个同交互范式的 PriorityFilter。
//
// 认不出的值（含换行、非 > 分隔、空）一律退回普通 textarea，避免把不该用
// 这个编辑器的参数（如 EquipmentCode 的 YAML）也套进来。
import { useEffect, useRef, useState } from 'react'
import './priority-filter.css'

/** 值是否为「a > b > c」形式（至少一个 >，元素是字母数字下划线点横线）。 */
export function isPriorityList(value: string): boolean {
  const v = String(value ?? '').trim()
  if (!v || v.indexOf('\n') >= 0) return false
  const parts = v.split('>').map(s => s.trim())
  if (parts.length < 2) return false
  return parts.every(p => /^[A-Za-z0-9_.-]+$/.test(p))
}

export function PriorityFilter({
  value,
  onChange,
  labelOf,
}: {
  value: string
  onChange: (v: string) => void
  /** 代号 → 显示名（可选；没有就原样显示代号） */
  labelOf?: (code: string) => string
}) {
  const [items, setItems] = useState<string[]>([])
  const [input, setInput] = useState('')
  // ⚠️ 用 useRef 而不是 useState 存拖拽源索引。
  // HTML5 drag 的 onDragStart 里如果 setState，React 会重渲染并**替换 DOM 节点**，
  // 浏览器随即判定"被拖元素已消失"、直接取消整个拖拽 —— 表现就是
  // 「鼠标放上去根本拖不动」（长卿 2026-11-17 实测）。
  // 拖拽统一用 **Pointer Events**（鼠标/触屏/手写笔同一套 API）。
  //
  // 为什么不用 HTML5 drag：① 触屏浏览器不派发 dragstart，手机永远拖不动；
  // ② onDragStart 里若触发 React 重渲染会替换 DOM 节点，浏览器直接取消拖拽
  //    （长卿 2026-11-17 实测「鼠标放上去根本拖不动」就是这个）。
  // 为什么不用 touch 事件另搞一套：两套并存时行为不一致、easy 互相干扰；
  // pointerdown/move/up + setPointerCapture 是当前标准的做法，一套通吃。
  const dragFromRef = useRef<number | null>(null)
  const [dragOver, setDragOver] = useState<number | null>(null)
  const listRef = useRef<HTMLOListElement>(null)

  /** 按指针 Y 坐标找落点行（几何命中，不依赖 elementFromPoint）。 */
  // ── 拖拽：实时预览重排（不是松手才挪） ──────────────────────────
  //
  // 长卿 2026-11-18：「能拖拽了 但是拖拽很生硬」。原实现只在 pointerup 时
  // move()，拖动过程中只有目标行变色，行本身不动 —— 手感是「拖了个寂寞」。
  //
  // 现在改成**实时预览**：pointermove 命中新行时立刻重排 items（用 localOrder
  // 暂存），松手才 onChange 落盘。这样行跟着手指走，与老 UI / 常见拖拽列表一致。
  const [localOrder, setLocalOrder] = useState<string[] | null>(null)
  // 正在拖的项（用于高亮）：索引会随实时重排变化，用 code 追踪更稳
  const draggedCode = dragFromRef.current !== null ? items[dragFromRef.current] : null
  const order = localOrder ?? items

  /** 指针 Y → 落点行号（几何命中，不依赖 elementFromPoint，兼容远控）。 */
  // ── 拖拽：性能优先的实现 ────────────────────────────────────────
  //
  // 长卿 2026-11-18：「卡是光标追不上鼠标，会在后面追，像帧率很低」。
  //
  // 原因是每帧做了三件昂贵的事：
  //   ① 每个 pointermove 同步读 getBoundingClientRect() —— 强制同步重排；
  //   ② 每次都 setState 触发整个列表重渲染；
  //   ③ 行上有 0.12s transition，连续重排时动画队列叠加成「追赶」。
  //
  // 修法：
  //   · 行矩形只在 dragstart 量一次（布局在拖拽期间不变），缓存到 ref；
  //   · pointermove 用 rAF 节流，一帧最多处理一次；
  //   · 命中行没变就直接返回，不 setState；
  //   · 拖拽期间给 <ol> 加类，**关掉所有过渡动画**（瞬时重排，不追赶）。
  const rowRectsRef = useRef<{ top: number; bottom: number }[]>([])
  const rafRef = useRef<number | null>(null)
  const lastYRef = useRef(0)

  /** 拖拽开始时量一次所有行矩形（拖拽期间布局不变，无需重复读取）。 */
  const measureRows = () => {
    const list = listRef.current
    if (!list) return
    const rows: { top: number; bottom: number }[] = []
    for (let k = 0; k < list.children.length; k++) {
      const r = list.children[k].getBoundingClientRect()
      rows.push({ top: r.top, bottom: r.bottom })
    }
    rowRectsRef.current = rows
  }

  /** 指针 Y → 落点行号（纯算术，不碰 DOM）。 */
  const rowAt = (clientY: number): number | null => {
    const rows = rowRectsRef.current
    if (!rows.length) return null
    for (let k = 0; k < rows.length; k++) {
      if (clientY >= rows[k].top && clientY <= rows[k].bottom) return k
    }
    if (clientY < rows[0].top) return 0
    if (clientY > rows[rows.length - 1].bottom) return rows.length - 1
    return null
  }

  const onPointerDown = (e: React.PointerEvent, i: number) => {
    if (e.button !== 0) return
    dragFromRef.current = i
    setDragOver(i)
    setLocalOrder(items.slice())
    measureRows()
    // 拖拽期间禁用过渡：连续重排时 transition 会排队播放，视觉上就是「追不上」
    listRef.current?.classList.add('is-dragging')
    listRef.current?.setPointerCapture?.(e.pointerId)
  }

  const applyMove = (clientY: number) => {
    const from = dragFromRef.current
    if (from === null) return
    const to = rowAt(clientY)
    if (to === null) return
    setDragOver(prev => {
      if (prev === to) return prev
      // 命中行变了才重排 —— 同帧重复 move 直接跳过，不做无用渲染
      setLocalOrder(base => {
        const cur = (base ?? items.slice()).indexOf(items[from])
        if (cur < 0 || cur === to) return base
        const next = (base ?? items.slice()).slice()
        const [moved] = next.splice(cur, 1)
        next.splice(to, 0, moved)
        return next
      })
      return to
    })
  }

  const onPointerMove = (e: React.PointerEvent) => {
    if (dragFromRef.current === null) return
    lastYRef.current = e.clientY
    // rAF 节流：一帧最多处理一次，避免高频 pointermove 堆积
    if (rafRef.current !== null) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      applyMove(lastYRef.current)
    })
  }

  const endDrag = () => {
    const next = localOrder
    dragFromRef.current = null
    // 清理 rAF 与「拖拽中」类，恢复过渡动画
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    listRef.current?.classList.remove('is-dragging')
    rowRectsRef.current = []
    setDragOver(null)
    setLocalOrder(null)
    // 松手才落盘（live 预览期间不动父组件状态，避免每帧触发保存 debounce）
    if (next && next.join(">") !== items.join(">")) write(next)
  }

  const onPointerUp = (e: React.PointerEvent) => {
    try { listRef.current?.releasePointerCapture?.(e.pointerId) } catch { /* 忽略 */ }
    endDrag()
  }

  // 外部值变化（切任务/重新拉配置）时同步。用原串作签名比较，避免父组件
  // 每次渲染生成新数组导致用户编辑被重置。
  const sig = String(value ?? '')
  useEffect(() => {
    setItems(sig.split('>').map(s => s.trim()).filter(Boolean))
  }, [sig])

  const write = (next: string[]) => {
    setItems(next)
    onChange(next.join(' > '))
  }

  const add = () => {
    const v = input.trim()
    if (!v) return
    if (!/^[A-Za-z0-9_.-]+$/.test(v)) return
    if (items.indexOf(v) >= 0) { setInput(''); return }
    write([...items, v])
    setInput('')
  }

  const move = (from: number, to: number) => {
    if (to < 0 || to >= items.length || from === to) return
    const next = items.slice()
    const removed = next.splice(from, 1)[0]
    next.splice(to, 0, removed)
    write(next)
  }

  return (
    <div className="priority-filter">
      <ol
        ref={listRef}
        className="task-priority-list priority-filter-list"
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {order.map((code, i) => (
          <li
            key={code}
            className={'task-priority-item priority-filter-item' + (dragOver !== null && code === draggedCode ? ' task-priority-floating' : '')}
            onPointerDown={e => {
              // 整行都可拖（老 UI task_priority 同语义），手柄只是视觉提示。
              // 但点在按钮上时不能抢事件，否则 ↑/↓/× 会被拖拽吞掉。
              const el = e.target as HTMLElement
              if (el.closest('button, input, a')) return
              onPointerDown(e, i)
            }}
          >
            <span className="task-priority-rank">{i + 1}</span>
            <span className="task-priority-handle" aria-hidden="true">≡</span>
            {/* 双列：左中文名 / 右英文代号 —— 与老 UI widgets.py:658-663 的
                task_priority 完全同构（.task-priority-name + .task-priority-code）。 */}
            <span className="task-priority-name">{labelOf ? labelOf(code) : code}</span>
            <span className="task-priority-code">{labelOf ? code : ""}</span>
            <span className="priority-filter-actions">
              <button type="button" className="btn btn-sm btn-secondary" title="上移"
                disabled={i === 0} onClick={() => move(i, i - 1)}>↑</button>
              <button type="button" className="btn btn-sm btn-secondary" title="下移"
                disabled={i === order.length - 1} onClick={() => move(i, i + 1)}>↓</button>
              <button type="button" className="btn btn-sm btn-secondary" title="移除"
                onClick={() => write(items.filter((_, k) => k !== i))}>×</button>
            </span>
          </li>
        ))}
      </ol>
      <div style={{ display: 'flex', gap: '.4rem', marginTop: '.4rem' }}>
        <input
          className="form-control form-control-sm"
          value={input}
          placeholder="输入代号后回车添加（如 bogue）"
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
          style={{ maxWidth: '16rem' }}
        />
        <button type="button" className="btn btn-sm btn-secondary" onClick={add}>添加</button>
      </div>
    </div>
  )
}
