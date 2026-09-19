import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { translateCurrentUi } from '../i18n'
import { EditQueue } from './EditQueue'
import { prepareValue } from './editors'

function storage() {
  const values = new Map<string, string>()
  return {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => {values.set(key, value)}, removeItem: (key: string) => {values.delete(key)}}
}
function deferred() {
  let resolve!: () => void
  let reject!: (error: Error) => void
  const promise = new Promise<void>((yes, no) => {resolve = yes; reject = no})
  return {promise, resolve, reject}
}
afterEach(() => vi.useRealTimers())

describe('即时配置队列', () => {
  it('新读取只清理此前确认的输入，不能清理读取期间完成的新修改', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    const queue = new EditQueue('test', {ready: () => true, send}, storage())
    queue.change('serial', 'before-read')
    await queue.settled()
    const confirmed = queue.confirmed()
    queue.change('serial', 'during-read')
    await queue.settled()
    queue.reconcile(confirmed)
    expect(queue.getSnapshot().edits.serial.value).toBe('during-read')
    queue.reconcile(queue.confirmed())
    expect(queue.getSnapshot().edits.serial).toBeUndefined()
  })

  it('输入时立即发送，旧响应不会撤销后续输入，并串行提交最终值', async () => {
    const first = deferred()
    const send = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined)
    const queue = new EditQueue('test', {ready: () => true, send}, storage())
    queue.change('serial', 'first')
    expect(send).toHaveBeenCalledWith('serial', 'first')
    queue.change('serial', 'second')
    queue.change('serial', 'latest')
    expect(queue.getSnapshot().edits.serial.value).toBe('latest')
    expect(send).toHaveBeenCalledTimes(1)
    first.resolve()
    await queue.settled()
    expect(send.mock.calls).toEqual([['serial', 'first'], ['serial', 'latest']])
    expect(queue.getSnapshot().edits.serial).toMatchObject({value: 'latest', status: 'saved'})
  })

  it('格式错误保留原文，其他字段独立保存，修正后立即重试', async () => {
    const send = vi.fn().mockRejectedValueOnce(new ApiError('INVALID_PARAMS', '日期格式不正确')).mockResolvedValue(undefined)
    const queue = new EditQueue('test', {ready: () => true, send}, storage())
    queue.change('date', '2026-')
    queue.change('enabled', true)
    await queue.flush()
    expect(queue.getSnapshot().edits.date).toMatchObject({value: '2026-', status: 'error'})
    expect(queue.getSnapshot().edits.enabled.status).toBe('saved')
    await expect(queue.settled()).rejects.toThrow(translateCurrentUi('edit.unsaved'))
    queue.change('date', '2026-09-14 12:00:00')
    await queue.settled()
    expect(queue.getSnapshot().edits.date.status).toBe('saved')
  })

  it('旧请求失败不能将新输入标记为错误', async () => {
    const first = deferred()
    const send = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(undefined)
    const queue = new EditQueue('test', {ready: () => true, send}, storage())
    queue.change('number', '-')
    queue.change('number', '12', 12)
    first.reject(new ApiError('INVALID_PARAMS', '必须是整数'))
    await queue.settled()
    expect(queue.getSnapshot().edits.number).toMatchObject({value: '12', payload: 12, status: 'saved'})
  })

  it('离线和刷新不丢失草稿，恢复全部字段，已保存字段不再重放', async () => {
    const memory = storage()
    const send = vi.fn().mockResolvedValue(undefined)
    const first = new EditQueue('instance-a', {ready: () => false, send}, memory)
    first.change('serial', 'offline')
    first.change('number', '-', '-', '请输入数字')
    expect(send).not.toHaveBeenCalled()
    const restored = new EditQueue('instance-a', {ready: () => true, send}, memory)
    await restored.flush()
    expect(send.mock.calls).toEqual([['serial', 'offline']])
    expect(restored.getSnapshot().edits.number).toMatchObject({value: '-', status: 'error'})
    const again = new EditQueue('instance-a', {ready: () => true, send}, memory)
    expect(again.getSnapshot().edits.serial).toBeUndefined()
    expect(again.getSnapshot().edits.number.value).toBe('-')
  })

  it('断线和超时会自动重试，其他实例的队列不受影响', async () => {
    vi.useFakeTimers()
    const send = vi.fn().mockRejectedValueOnce(new ApiError('TIMEOUT', '请求超时')).mockResolvedValue(undefined)
    const memory = storage()
    const queue = new EditQueue('a', {ready: () => true, send}, memory)
    queue.change('serial', 'retained')
    await queue.flush()
    expect(queue.getSnapshot().edits.serial.value).toBe('retained')
    const other = new EditQueue('b', {ready: () => true, send}, memory)
    other.change('serial', 'other')
    await other.settled()
    await vi.advanceTimersByTimeAsync(1000)
    expect(send.mock.calls).toEqual([['serial', 'retained'], ['serial', 'other'], ['serial', 'retained']])
    expect(queue.getSnapshot().edits.serial.status).toBe('saved')
  })

  it('浏览器存储失败不打断输入和提交，并明确提示', async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    const queue = new EditQueue('test', {ready: () => true, send})
    queue.change('serial', 'retained')
    await queue.settled()
    expect(send).toHaveBeenCalledWith('serial', 'retained')
    expect(queue.getSnapshot().storageError).toBe(translateCurrentUi('edit.draftPersistError'))
  })
})

describe('保留数值输入原文', () => {
  const field = {type: 'int', value: 3, validate: [1, 10]}
  it.each(['', '-', '1e', 'abc', 'Infinity', '1.5', '11', '9007199254740993'])('拒绝不完整或不合法的数值 %s', value => {
    expect(prepareValue(value, field).error).toBeTruthy()
  })
  it('只转换提交值，不改写原始文本', () => {
    expect(prepareValue('03', field)).toEqual({payload: 3})
    expect(prepareValue('1.50', {type: 'float', value: 0.5})).toEqual({payload: 1.5})
    expect(prepareValue('1.50', {type: 'input', value: 1})).toEqual({payload: 1.5})
    expect(prepareValue('9007199254740993', {type: 'input', value: 1}).error).toBeTruthy()
  })
})
