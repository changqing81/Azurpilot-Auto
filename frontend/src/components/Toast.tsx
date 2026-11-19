// 全局轻量 toast（老 UI 的 PyWebIO toast 等价物）。
//
// 长卿 2026-11-19：「其他的那个配置都是一样的，没有一个反馈来告诉你这个配置
// 已经保存了…老 UI 都有一个小绿色弹窗弹出来说你保存成功了」。
// 老 UI 用 toast(t("Gui.Toast.ConfigSaved"), color="success", position="right")。
//
// 用法：
//   const toast = useToast()
//   toast.ok('设置已保存')
//   toast.error('3 项校验失败')
//
// 也提供模块级的 `pushToast()`（不经 hook），方便在非组件代码里调用。
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

export type ToastKind = 'success' | 'error' | 'info'

type ToastItem = { id: number; text: string; kind: ToastKind }

type ToastApi = {
  show: (text: string, kind?: ToastKind, ms?: number) => void
  ok: (text: string, ms?: number) => void
  error: (text: string, ms?: number) => void
}

const ToastContext = createContext<ToastApi | null>(null)

/** 模块级兜底通道：没有 Provider 时也能弹（用于事件回调等场景）。 */
let externalPush: ((text: string, kind: ToastKind, ms: number) => void) | null = null
export function pushToast(text: string, kind: ToastKind = 'info', ms = 2400) {
  externalPush?.(text, kind, ms)
}

const COLORS: Record<ToastKind, { bg: string; fg: string; border: string }> = {
  success: { bg: 'rgba(22, 163, 74, .96)', fg: '#fff', border: 'rgba(22, 163, 74, 1)' },
  error: { bg: 'rgba(190, 40, 40, .96)', fg: '#fff', border: 'rgba(190, 40, 40, 1)' },
  info: { bg: 'rgba(40, 44, 52, .94)', fg: '#fff', border: 'rgba(255, 255, 255, .18)' },
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const seq = useRef(0)
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  const remove = useCallback((id: number) => {
    setItems(list => list.filter(it => it.id !== id))
    const t = timers.current.get(id)
    if (t) { clearTimeout(t); timers.current.delete(id) }
  }, [])

  const show = useCallback((text: string, kind: ToastKind = 'info', ms = 2400) => {
    if (!text) return
    const id = ++seq.current
    setItems(list => [...list.slice(-3), { id, text, kind }])
    const timer = setTimeout(() => remove(id), ms)
    timers.current.set(id, timer)
  }, [remove])

  useEffect(() => {
    externalPush = show
    return () => { externalPush = null }
  }, [show])

  useEffect(() => () => { timers.current.forEach(t => clearTimeout(t)); timers.current.clear() }, [])

  const api: ToastApi = {
    show,
    ok: (text, ms) => show(text, 'success', ms ?? 2000),
    error: (text, ms) => show(text, 'error', ms ?? 3200),
  }

  return (
    <ToastContext.Provider value={api}>
      {children}
      {items.length > 0 && (
        <div className="alas-toast-stack" aria-live="polite">
          {items.map(it => {
            const c = COLORS[it.kind]
            return (
              <div
                key={it.id}
                className="alas-toast"
                role="status"
                style={{ background: c.bg, color: c.fg, borderColor: c.border }}
                onClick={() => remove(it.id)}
              >
                {it.text}
              </div>
            )
          })}
        </div>
      )}
    </ToastContext.Provider>
  )
}

/** 取 toast API。没有 Provider 时退化成 no-op（不崩，只是不弹）。 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (ctx) return ctx
  return {
    show: (text, kind, ms) => pushToast(text, kind, ms ?? 2400),
    ok: (text, ms) => pushToast(text, 'success', ms ?? 2000),
    error: (text, ms) => pushToast(text, 'error', ms ?? 3200),
  }
}
