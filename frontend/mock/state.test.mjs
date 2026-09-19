import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { createMockState } from './state.mjs'

// 本地配置树是否包含 ShopAdvanced 商店策略组（上游功能，本地默认不带）
const args = JSON.parse(readFileSync(new URL('../../module/config/argument/args.json', import.meta.url), 'utf8'))
const hasShopAdvanced = Boolean(args.EventShop?.ShopAdvanced)

describe('前端模拟服务', () => {
  it('配置按实例隔离，合并过期快照的字段修改并拒绝非原子保存', () => {
    const {dispatch} = createMockState()
    const initial = dispatch('config.get', {instance: 'demo-main'})
    const params = {instance: 'demo-main', revision: initial.revision, changes: [{path: 'Alas.Emulator.Serial', value: 'mock-serial'}]}
    const saved = dispatch('config.patch', params)
    expect(saved.values.Alas.Emulator.Serial).toBe('mock-serial')
    expect(dispatch('config.get', {instance: 'demo-alt'}).values.Alas.Emulator.Serial).toBe('127.0.0.1:5557')
    expect(dispatch('config.patch', params)).toEqual(saved)
    expect(() => dispatch('config.patch', {...params, revision: saved.revision, changes: [{path: 'Alas.Emulator.Serial', value: '不应部分保存'}, {path: 'Main.Scheduler.Enable', value: 'false'}]})).toThrow()
    expect(dispatch('config.get', {instance: 'demo-main'})).toEqual(saved)
  })
  it('空场景支持创建、复制和删除，重启后恢复初始数据', () => {
    const {dispatch} = createMockState({empty: true})
    expect(dispatch('instances.list')).toEqual([])
    dispatch('instances.create', {name: 'first'})
    expect(() => dispatch('instances.create', {name: 'first'})).toThrow(/同名/)
    dispatch('instances.create', {name: 'second', source: 'first'})
    dispatch('scheduler.start', {instance: 'second'})
    const config = dispatch('config.get', {instance: 'second'})
    expect(() => dispatch('instances.delete', {instance: 'second', revision: config.revision})).toThrow(/停止/)
    dispatch('scheduler.stop', {instance: 'second'})
    dispatch('instances.delete', {instance: 'second', revision: config.revision})
    expect(dispatch('instances.list').map(item => item.name)).toEqual(['first'])
    expect(createMockState({empty: true}).dispatch('instances.list')).toEqual([])
  })
  it('总览投影保留行动力总值', () => {
    const {dispatch} = createMockState()
    const actionPoint = dispatch('overview.get', {instance: 'demo-main'}).resources.find(resource => resource.name === 'ActionPoint')

    expect(actionPoint).toMatchObject({value: 101, total: 1301})
  })
  it('契约参数、只读字段、语言、日志游标和被动预览可验证', () => {
    const {dispatch, tick} = createMockState()
    expect(() => dispatch('schema.get', {language: '../deploy'})).toThrow(/契约/)
    expect(dispatch('schema.get', {language: 'en-US'}).translations.Emulator.Serial.name).toMatch(/serial/i)
    const config = dispatch('config.get', {instance: 'demo-main'})
    expect(() => dispatch('config.patch', {...config, values: undefined, changes: []})).toThrow(/契约/)
    expect(() => dispatch('config.patch', {instance: config.instance, revision: config.revision, changes: [{path: 'Main.Scheduler.Command', value: 'Main'}]})).toThrow(/不可修改/)
    dispatch('scheduler.start', {instance: 'demo-main'})
    const before = dispatch('logs.get', {instance: 'demo-main'})
    tick()
    expect(dispatch('logs.get', {instance: 'demo-main', after: before.cursor}).entries).toHaveLength(1)
    expect(dispatch('preview.capture', {instance: 'demo-error'}).image).toBeNull()
    const frame = dispatch('preview.capture', {instance: 'demo-main'})
    expect(frame.image).toMatch(/^data:image/)
    expect(dispatch('preview.capture', {instance: 'demo-main'})).toEqual(frame)
  })

  it('高级商店策略校验不写配置', () => {
    const {dispatch} = createMockState()
    const initial = dispatch('config.get', {instance: 'demo-main'})
    const invalid = dispatch('shop_strategy.validate', {
      instance: 'demo-main', task: 'EventShop', script: 'os.execute("bad")',
    })
    expect(invalid).toMatchObject({valid: false, diagnostics: [{code: 'forbidden_call', message: '不允许调用 os.execute', line: 1, column: 1}]})
    expect(dispatch('shop_strategy.validate', {instance: 'demo-main', task: 'EventShop', script: ''})).toEqual({valid: true, diagnostics: []})
    expect(dispatch('config.get', {instance: 'demo-main'})).toEqual(initial)
  })

  it.skipIf(!hasShopAdvanced)('最终高级模式必须保留有效脚本', () => {
    const {dispatch} = createMockState()
    const initial = dispatch('config.get', {instance: 'demo-main'})
    expect(() => dispatch('config.patch', {
      instance: 'demo-main', changes: [{path: 'EventShop.ShopAdvanced.Script', value: 'os.execute("bad")'}],
    })).toThrow(/不允许调用 os.execute/)
    expect(dispatch('config.get', {instance: 'demo-main'})).toEqual(initial)

    expect(() => dispatch('config.patch', {
      instance: 'demo-main', changes: [{path: 'EventShop.ShopAdvanced.Mode', value: 'advanced'}],
    })).toThrow(/需要先保存非空且有效的策略脚本/)
    expect(dispatch('config.get', {instance: 'demo-main'})).toEqual(initial)

    const script = 'return shop.plan { candidates = candidates:take(0) }'
    const saved = dispatch('config.patch', {
      instance: 'demo-main',
      changes: [
        {path: 'EventShop.ShopAdvanced.Mode', value: 'advanced'},
        {path: 'EventShop.ShopAdvanced.Script', value: script},
      ],
    })
    expect(saved.values.EventShop.ShopAdvanced).toEqual({Mode: 'advanced', Script: script})
    expect(() => dispatch('config.patch', {
      instance: 'demo-main', changes: [{path: 'EventShop.ShopAdvanced.Script', value: ''}],
    })).toThrow(/需要先保存非空且有效的策略脚本/)
    expect(dispatch('config.get', {instance: 'demo-main'})).toEqual(saved)
  })

  it('高级策略 mock 接受复杂分支模板并拒绝明显的非白名单调用', () => {
    const {dispatch} = createMockState()
    const valid = `local pool = candidates:where(function(item)
  return item.tier == 't4' and item.available
end):score(function(item)
  return 100 - item.price
end)
if context.domain == 'event' then
  return shop.plan { reserve = { Pt = 2 }, candidates = pool:cap('key', 'Cube', 1):take(20) }
else
  return shop.plan { candidates = candidates:take(0) }
end`
    expect(dispatch('shop_strategy.validate', {instance: 'demo-main', task: 'EventShop', script: valid})).toEqual({valid: true, diagnostics: []})
    expect(dispatch('shop_strategy.validate', {
      instance: 'demo-main', task: 'EventShop',
      script: 'return shop.plan { candidates = candidates:where(function(item) return math.abs(item.price) > 0 end):take(1) }',
    })).toMatchObject({valid: false, diagnostics: [{code: 'forbidden_call', message: '不允许调用 math.abs'}]})
  })
})
