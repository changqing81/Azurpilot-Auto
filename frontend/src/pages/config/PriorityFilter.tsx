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
  const dragIndexRef = useRef<number | null>(null)
  // 触屏拖拽（手机/远控 WebView 不派发 HTML5 drag 事件）：记录起点项，
  // move 时按手指命中的行落位。桌面鼠标路径不受影响。
  const touchFromRef = useRef<number | null>(null)
  const [touchCurrent, setTouchCurrent] = useState<number | null>(null)
  const [dragging, setDragging] = useState<number | null>(null)

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
        className="task-priority-list priority-filter-list"
        onTouchMove={e => {
          const from = touchFromRef.current
          if (from === null) return
          const tY = e.touches[0].clientY
          // 手指当前命中的行：按几何位置计算（不用 elementFromPoint，兼容远控）
          const list = e.currentTarget
          for (let k = 0; k < list.children.length; k++) {
            const r = list.children[k].getBoundingClientRect()
            if (tY >= r.top && tY <= r.bottom) {
              if (k !== touchCurrent) setTouchCurrent(k)
              break
            }
          }
          e.preventDefault()
        }}
        onTouchEnd={() => {
          const from = touchFromRef.current
          const to = touchCurrent
          if (from !== null && to !== null) move(from, to)
          touchFromRef.current = null
          setTouchCurrent(null)
        }}
      >
        {items.map((code, i) => (
          <li
            key={code}
            className={'task-priority-item priority-filter-item' + (dragging === i || touchCurrent === i ? ' task-priority-floating' : '')}
            draggable
            onDragStart={() => { dragIndexRef.current = i; setTimeout(() => setDragging(i), 0) }}
            onDragOver={e => e.preventDefault()}
            onDrop={e => {
              e.preventDefault()
              const from = dragIndexRef.current
              if (from !== null) move(from, i)
              dragIndexRef.current = null
              setDragging(null)
            }}
            onDragEnd={() => { dragIndexRef.current = null; setDragging(null) }}
          >
            <span className="task-priority-rank">{i + 1}</span>
            <span
              className="task-priority-handle" aria-hidden="true"
              onTouchStart={() => {
                touchFromRef.current = i
                setTouchCurrent(i)
              }}
            >≡</span>
            {/* 双列：左中文名 / 右英文代号 —— 与老 UI widgets.py:658-663 的
                task_priority 完全同构（.task-priority-name + .task-priority-code）。 */}
            <span className="task-priority-name">{labelOf ? labelOf(code) : code}</span>
            <span className="task-priority-code">{labelOf ? code : ""}</span>
            <span className="priority-filter-actions">
              <button type="button" className="btn btn-sm btn-secondary" title="上移"
                disabled={i === 0} onClick={() => move(i, i - 1)}>↑</button>
              <button type="button" className="btn btn-sm btn-secondary" title="下移"
                disabled={i === items.length - 1} onClick={() => move(i, i + 1)}>↓</button>
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
