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
  const order = localOrder ?? items

  /** 指针 Y → 落点行号（几何命中，不依赖 elementFromPoint，兼容远控）。 */
  const rowAt = (clientY: number): number | null => {
    const list = listRef.current
    if (!list) return null
    for (let k = 0; k < list.children.length; k++) {
      const r = list.children[k].getBoundingClientRect()
      if (clientY >= r.top && clientY <= r.bottom) return k
    }
    // 拖到列表外：按上下边界归位，避免卡住不动
    if (list.children.length) {
      const first = list.children[0].getBoundingClientRect()
      const last = list.children[list.children.length - 1].getBoundingClientRect()
      if (clientY < first.top) return 0
      if (clientY > last.bottom) return list.children.length - 1
    }
    return null
  }

  const onPointerDown = (e: React.PointerEvent, i: number) => {
    if (e.button !== 0) return
    dragFromRef.current = i
    setDragOver(i)
    setLocalOrder(items.slice())
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const from = dragFromRef.current
    if (from === null) return
    const to = rowAt(e.clientY)
    if (to === null) return
    if (to === dragOver) return
    setDragOver(to)
    // 实时重排：把正在拖的项移到目标位置
    setLocalOrder(prev => {
      const base = prev ?? items.slice()
      const cur = base.indexOf(items[from])
      if (cur < 0 || cur === to) return base
      const next = base.slice()
      const [moved] = next.splice(cur, 1)
      next.splice(to, 0, moved)
      return next
    })
  }

  const endDrag = () => {
    const next = localOrder
    dragFromRef.current = null
    setDragOver(null)
    setLocalOrder(null)
    // 松手才落盘（live 预览期间不动父组件状态，避免每帧触发保存 debounce）
    if (next && next.join(">") !== items.join(">")) write(next)
  }

  const onPointerUp = (e: React.PointerEvent) => {
    try { (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId) } catch { /* 忽略 */ }
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
            className={'task-priority-item priority-filter-item' + (dragFromRef.current !== null && code === items[dragFromRef.current] ? ' task-priority-floating' : '')}
          >
            <span className="task-priority-rank">{i + 1}</span>
            <span
              className="task-priority-handle"
              aria-hidden="true"
              onPointerDown={e => onPointerDown(e, i)}
            >≡</span>
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
