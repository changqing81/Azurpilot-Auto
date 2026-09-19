import { ApiError } from '../api/client'
import type { Value } from '../api/types'
import { translateCurrentUi } from '../i18n'

export interface Edit {
  value: Value
  payload: Value
  sequence: number
  status: 'queued' | 'saving' | 'saved' | 'error'
  error?: string
  retryable?: boolean
}
export interface EditSnapshot { edits: Record<string, Edit>; storageError: string }
interface Transport {
  ready: () => boolean
  send: (path: string, value: Value) => Promise<unknown>
}

/** 页面之外的字段队列：先保留输入，再提交；旧响应只能确认自己的输入版本。 */
export class EditQueue {
  private state: EditSnapshot = {edits: {}, storageError: ''}
  private listeners = new Set<() => void>()
  private sequence = 0
  private running?: Promise<void>
  private retryTimer?: ReturnType<typeof setTimeout>
  private retryDelay = 1000

  constructor(private key: string, private transport: Transport, private storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>) {
    try {
      const saved = JSON.parse(storage?.getItem(key) ?? '{}') as Record<string, Edit>
      for (const [path, edit] of Object.entries(saved)) {
        if (!edit || typeof edit.sequence !== 'number' || !('value' in edit)) continue
        this.sequence = Math.max(this.sequence, edit.sequence)
        this.state.edits[path] = {...edit, status: edit.status === 'error' && !edit.retryable ? 'error' : 'queued'}
      }
    } catch { this.state.storageError = translateCurrentUi('edit.draftReadError') }
  }

  getSnapshot = () => this.state
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  confirmed() {
    return Object.fromEntries(Object.entries(this.state.edits)
      .filter(([, edit]) => edit.status === 'saved').map(([path, edit]) => [path, edit.sequence]))
  }

  /** 只清理由读取前已确认的覆盖值，读取期间的新输入和回执仍由队列保护。 */
  reconcile(confirmed: Record<string, number>) {
    const edits = {...this.state.edits}
    for (const [path, sequence] of Object.entries(confirmed)) {
      if (edits[path]?.status === 'saved' && edits[path].sequence === sequence) delete edits[path]
    }
    this.state = {...this.state, edits}
    this.publish()
  }

  private publish() {
    // 仅持久化未确认的输入；每个标签页独立，避免其他页面覆盖本页草稿。
    try {
      const pending = Object.fromEntries(Object.entries(this.state.edits).filter(([, edit]) => edit.status !== 'saved'))
      if (!this.storage) throw new Error()
      if (Object.keys(pending).length) this.storage.setItem(this.key, JSON.stringify(pending))
      else this.storage.removeItem(this.key)
      this.state = {...this.state, storageError: ''}
    } catch { this.state = {...this.state, storageError: translateCurrentUi('edit.draftPersistError')} }
    this.listeners.forEach(listener => listener())
  }

  private replace(path: string, edit: Edit) {
    this.state = {...this.state, edits: {...this.state.edits, [path]: edit}}
    this.publish()
  }

  change(path: string, value: Value, payload: Value = value, error?: string) {
    this.replace(path, {value, payload, sequence: ++this.sequence, status: error ? 'error' : 'queued', error})
    void this.flush()
  }

  retry = () => {
    clearTimeout(this.retryTimer)
    this.retryTimer = undefined
    for (const [path, edit] of Object.entries(this.state.edits)) {
      if (edit.status === 'error' && edit.retryable) this.replace(path, {...edit, status: 'queued', error: undefined})
    }
    void this.flush()
  }

  /** 运行工具前等候队列排空；格式错误保留在原字段，不允许带旧配置启动。 */
  async settled() {
    await this.flush()
    if (Object.values(this.state.edits).some(edit => edit.status !== 'saved')) {
      throw new Error(translateCurrentUi('edit.unsaved'))
    }
  }

  flush(): Promise<void> {
    if (this.running) return this.running
    this.running = this.drain().finally(() => { this.running = undefined })
    return this.running
  }

  private async drain() {
    while (this.transport.ready()) {
      const next = Object.entries(this.state.edits)
        .filter(([, edit]) => edit.status === 'queued')
        .sort((a, b) => a[1].sequence - b[1].sequence)[0]
      if (!next) return
      const [path, edit] = next
      this.replace(path, {...edit, status: 'saving'})
      try {
        await this.transport.send(path, edit.payload)
        this.retryDelay = 1000
        if (this.state.edits[path]?.sequence === edit.sequence) this.replace(path, {...edit, status: 'saved'})
      } catch (error) {
        const permanent = error instanceof ApiError && ['INVALID_PARAMS', 'READ_ONLY', 'NOT_FOUND', 'CONFIG_INVALID'].includes(error.code)
        if (this.state.edits[path]?.sequence === edit.sequence) {
          this.replace(path, {...edit, status: 'error', error: (error as Error).message, retryable: !permanent})
        }
        if (!permanent && !this.retryTimer) {
          this.retryTimer = setTimeout(this.retry, this.retryDelay)
          this.retryDelay = Math.min(15000, this.retryDelay * 2)
        }
      }
    }
  }
}
