import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import Ajv from 'ajv'

// 只读取公开的模板、元数据和翻译，绝不读取用户实例或部署文件。
const read = path => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'))
const args = read('../../module/config/argument/args.json')
const menu = read('../../module/config/argument/menu.json')
const template = read('../../config/template.json')
const contract = read('../src/api/contract.json')
const locales = Object.fromEntries(['zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'zh-MIAO'].map(lang => [lang, read(`../../module/config/i18n/${lang}.json`)]))
const ajv = new Ajv({strict: false, useDefaults: true})
const validators = Object.fromEntries(Object.entries(contract.methods).map(([method, entry]) => [method, ajv.compile(entry.params)]))
const revision = values => createHash('sha256').update(JSON.stringify(values)).digest('hex')
const timestamp = date => date.toISOString().slice(0, 19).replace('T', ' ')
const translate = key => key.split('.').reduce((value, part) => value?.[part], locales['zh-CN']) ?? key
export const fail = (code, message, details = null) => {throw Object.assign(new Error(message), {code, details})}

function strategyLocation(source, index) {
  const prefix = source.slice(0, Math.max(0, index))
  return {line: prefix.split(/\r?\n/).length, column: prefix.length - Math.max(prefix.lastIndexOf('\n'), prefix.lastIndexOf('\r'))}
}

function invalidStrategy(source, code, message, index = 0) {
  return {valid: false, diagnostics: [{code, message, ...strategyLocation(source, index)}]}
}

function maskLuaStringsAndComments(source) {
  let output = ''
  for (let index = 0; index < source.length;) {
    if (source.startsWith('--[[', index)) {
      const end = source.indexOf(']]', index + 4)
      if (end < 0) return {masked: output, error: invalidStrategy(source, 'syntax_error', '多行注释没有结束', index)}
      const comment = source.slice(index, end + 2)
      output += comment.replace(/[^\r\n]/g, ' ')
      index = end + 2
      continue
    }
    if (source.startsWith('--', index)) {
      const end = source.indexOf('\n', index)
      const comment = source.slice(index, end < 0 ? source.length : end)
      output += comment.replace(/[^\r\n]/g, ' ')
      index = end < 0 ? source.length : end
      continue
    }
    const quote = source[index]
    if (quote === '"' || quote === "'") {
      const start = index++
      output += ' '
      let closed = false
      while (index < source.length) {
        const char = source[index++]
        if (char === '\\') {
          output += ' '
          if (index < source.length) output += source[index++] === '\n' ? '\n' : ' '
          continue
        }
        output += char === '\n' || char === '\r' ? char : ' '
        if (char === quote) {closed = true; break}
      }
      if (!closed) return {masked: output, error: invalidStrategy(source, 'syntax_error', '字符串没有结束', start)}
      continue
    }
    output += source[index++]
  }
  return {masked: output, error: null}
}

function validateMockStrategy(script) {
  // Mock 不执行 Lua；这里只复现不会误伤有效分支策略的明显语法和白名单错误。
  if (!script.trim()) return {valid: true, diagnostics: []}
  const {masked, error} = maskLuaStringsAndComments(script)
  if (error) return error

  const pairs = {'(': ')', '[': ']', '{': '}'}
  const opening = []
  for (let index = 0; index < masked.length; index++) {
    const character = masked[index]
    if (character in pairs) opening.push({character, index})
    else if (Object.values(pairs).includes(character)) {
      const previous = opening.pop()
      if (!previous || pairs[previous.character] !== character) return invalidStrategy(script, 'syntax_error', '括号或花括号不匹配', index)
    }
  }
  if (opening.length) return invalidStrategy(script, 'syntax_error', '括号或花括号没有结束', opening.at(-1).index)

  const forbiddenStatement = /\b(?:while|repeat|for|goto|break)\b/.exec(masked)
  if (forbiddenStatement) return invalidStrategy(script, 'forbidden_statement', `不支持 ${forbiddenStatement[0]} 语句`, forbiddenStatement.index)
  const namedFunction = /\bfunction\s+(?!\()/.exec(masked)
  if (namedFunction) return invalidStrategy(script, 'forbidden_statement', '不支持具名 function 语句', namedFunction.index)
  const forbiddenCall = /\b((?:os|io|debug|package|math|string|table|coroutine)\s*[.:]\s*[A-Za-z_]\w*|require|load|dofile|loadfile|collectgarbage|setmetatable|getmetatable|pairs|ipairs|next|type|tonumber|tostring|error|assert|pcall|xpcall)\s*\(/.exec(masked)
  if (forbiddenCall) {
    return invalidStrategy(script, 'forbidden_call', `不允许调用 ${forbiddenCall[1].replace(/\s/g, '')}`, forbiddenCall.index)
  }
  const plan = /\breturn\s+shop\s*\.\s*plan\s*(?:\(\s*)?\{/.exec(masked)
  if (!plan) return invalidStrategy(script, 'missing_return', '必须返回 shop.plan {...}', 0)

  const unknownShopField = /\bshop\s*\.\s*(?!plan\b)([A-Za-z_]\w*)/.exec(masked)
  if (unknownShopField) return invalidStrategy(script, 'forbidden_field', `不支持 shop.${unknownShopField[1]}`, unknownShopField.index)
  const allowedContextFields = new Set(['domain', 'currency', 'spent', 'purchased'])
  for (const match of masked.matchAll(/\bcontext\s*\.\s*([A-Za-z_]\w*)/g)) {
    if (!allowedContextFields.has(match[1])) return invalidStrategy(script, 'unknown_context_field', `不支持 context.${match[1]}`, match.index)
  }
  const allowedCandidateFields = new Set(['id', 'key', 'name', 'group', 'sub_genre', 'tier', 'price', 'cost', 'stock', 'max_quantity', 'available'])
  for (const match of masked.matchAll(/\bitem\s*\.\s*([A-Za-z_]\w*)/g)) {
    if (!allowedCandidateFields.has(match[1])) return invalidStrategy(script, 'unknown_candidate_field', `不支持商品字段 ${match[1]}`, match.index)
  }
  const allowedPipelineMethods = new Set(['where', 'score', 'order_by', 'cap', 'take'])
  for (const match of masked.matchAll(/:\s*([A-Za-z_]\w*)\s*\(/g)) {
    const method = match[1]
    if (!allowedPipelineMethods.has(method)) return invalidStrategy(script, 'forbidden_call', '候选管道只允许 where、score、order_by、cap、take', match.index)
  }
  for (const match of masked.matchAll(/:\s*take\s*\(([^)]*)\)/g)) {
    const rawAmount = match[1].trim()
    if (!/^\d+$/.test(rawAmount)) return invalidStrategy(script, 'invalid_take', 'take 必须是 0 到 100 之间的整数', match.index)
    const amount = Number(rawAmount)
    if (amount > 100) return invalidStrategy(script, 'invalid_take', 'take 必须在 0 到 100 之间', match.index)
  }
  for (const match of masked.matchAll(/\b([A-Za-z_]\w*)\s*\(/g)) {
    const before = masked.slice(0, match.index).trimEnd().at(-1)
    if (before === ':' || before === '.' || ['function', 'if', 'elseif'].includes(match[1])) continue
    return invalidStrategy(script, 'forbidden_call', `不允许调用 ${match[1]}`, match.index)
  }
  return {valid: true, diagnostics: []}
}

function requireValidMockStrategy(script) {
  const result = validateMockStrategy(script)
  if (!result.valid) fail('INVALID_PARAMS', `高级商店策略脚本无效：${result.diagnostics[0].message}`, result.diagnostics)
}

function validateMockAdvancedGroups(values, tasks) {
  for (const task of tasks) {
    const group = values[task]?.ShopAdvanced
    if (group?.Mode !== 'advanced') continue
    const script = group.Script
    if (typeof script !== 'string' || !script.trim()) fail('INVALID_PARAMS', `${task} 的高级模式需要先保存非空且有效的策略脚本`)
    requireValidMockStrategy(script)
  }
}

function validateField(path, value) {
  const parts = path.split('.')
  const field = parts.length === 3 && parts.reduce((node, key) => Object.hasOwn(node ?? {}, key) ? node[key] : undefined, args)
  if (!field) fail('INVALID_PARAMS', '配置项不存在')
  if (field.type === 'storage' && field.display !== 'hide' && value !== null && typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length) return parts
  if (['hide', 'disabled', 'readonly', 'display'].includes(field.display) || ['storage', 'stored', 'state', 'lock'].includes(field.type)) fail('READ_ONLY', '此配置项不可修改')
  if (field.type === 'multiselect') {
    if (!Array.isArray(value) || value.some(item => !field.option?.includes(item)) || new Set(value).size !== value.length) fail('INVALID_PARAMS', '多选项无效')
    return parts
  }
  if (field.option?.length && !field.option.includes(value)) fail('INVALID_PARAMS', '请选择有效选项')
  const kind = typeof field.value
  const valid = field.type === 'checkbox' || kind === 'boolean' ? typeof value === 'boolean'
    : kind === 'number' ? typeof value === 'number' && Number.isFinite(value) && (!Number.isInteger(field.value) || Number.isInteger(value))
    : typeof value === 'string' || (field.value === null && value === null)
  if (!valid || (typeof value === 'string' && value.length > 20000)) fail('INVALID_PARAMS', '参数类型或长度不正确')
  if (Array.isArray(field.validate) && (typeof value !== 'number' || value < field.validate[0] || value > field.validate[1])) fail('INVALID_PARAMS', '数值超出允许范围')
  if (field.validate === 'datetime' && (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value) || Number.isNaN(Date.parse(value.replace(' ', 'T'))))) fail('INVALID_PARAMS', '日期格式不正确')
  return parts
}

export function createMockState({empty = false} = {}) {
  const instances = new Map()
  const startup = new Set()
  const commits = Array.from({length: 123}, (_, index) => ({sha: createHash('sha1').update(`mock-commit-${123 - index}`).digest('hex'), author: 'AzurPilot', date: new Date(Date.UTC(2026, 8, 14, 0, -index)).toISOString(), message: index === 0 ? 'feat(webui): 新增主页与实例状态\n\n统一全局设置和更新入口。' : `fix(runtime): 改善任务运行稳定性 ${123 - index}`}))
  let localHead = commits[3].sha
  let upstreamHead = commits[0].sha
  const updateStatus = () => ({state: localHead === upstreamHead ? 'idle' : 'available', localHead, upstreamHead, branch: 'dev', ahead: 0, behind: commits.findIndex(item => item.sha === localHead), available: localHead !== upstreamHead, busy: false, canApply: localHead !== upstreamHead, canCancel: false, error: ''})
  const settings = {groups: [{key: 'Webui', label: 'WebUI 设置', fields: [
    {key: 'WebuiHost', type: 'string', label: '监听地址', help: '模拟部署设置，仅在当前 mock 会话中保留。', value: '127.0.0.1', options: []},
    {key: 'WebuiPort', type: 'int', label: '监听端口', help: '用于验证数值输入与保存。', value: 22267, options: []},
    {key: 'Password', type: 'password', label: '访问密码', help: '留空保留原密码。', value: '', options: []},
  ]}], notice: '前端测试数据', demo: false}
  const get = name => instances.get(name) ?? fail('NOT_FOUND', '实例不存在')
  const snapshot = name => ({instance: name, revision: revision(get(name).values), values: structuredClone(get(name).values)})
  function log(name, text, level = 'INFO') {
    const instance = get(name)
    instance.logs.push({id: ++instance.cursor, level, text: `${timestamp(new Date())} [${name}] ${text}`})
    instance.logs = instance.logs.slice(-400)
  }
  function add(name, values) {
    instances.set(name, {values: structuredClone(values), status: 'stopped', logs: [], cursor: 0})
    log(name, '测试实例已就绪，所有操作均为模拟。')
  }
  if (!empty) {
    for (const [index, name] of ['demo-main', 'demo-alt', 'demo-error'].entries()) {
      const values = structuredClone(template)
      values.Main.Emotion.Fleet1Record = '2026-09-12 23:45:12.123456'
      values.Main.Scheduler.NextRun = '2099-01-01 12:00:00'
      values.Alas.Emulator.Serial = `127.0.0.1:${5555 + index * 2}`
      for (const [key, value] of Object.entries({Oil: 14200, Coin: 186420, Gem: 2468, Cube: 384})) {
        values.Dashboard[key].Value = value - index * 100
        values.Dashboard[key].Record = timestamp(new Date())
      }
      values.Dashboard.ActionPoint.Value = 101 - index * 2
      values.Dashboard.ActionPoint.Total = values.Dashboard.ActionPoint.Value + 1200
      values.Dashboard.ActionPoint.Record = timestamp(new Date())
      for (const [order, task] of ['Commission', 'Research', 'Dorm', 'Main'].entries()) {
        values[task].Scheduler.Enable = true
        values[task].Scheduler.NextRun = timestamp(new Date(Date.now() + (order - 1) * 1800000))
      }
      add(name, values)
    }
    get('demo-error').status = 'error'
    get('demo-error').values.Alas.Storage.Storage = {failureCount: 3, lastError: '模拟器连接失败', retry: {enabled: false, remaining: 0}, tasks: ['Commission', 'Research']}
    log('demo-error', '模拟器连接失败，请检查连接设置。', 'ERROR')
  }
  function overview(name) {
    const data = snapshot(name)
    return {instance: name, revision: data.revision, status: get(name).status, emulator: data.values.Alas.Emulator,
      tasks: Object.entries(data.values).filter(([, groups]) => groups.Scheduler?.Enable).map(([task, groups]) => ({
        name: task, nextRun: groups.Scheduler.NextRun,
        state: get(name).status === 'running' && task === 'Commission' ? 'running' : groups.Scheduler.NextRun <= timestamp(new Date()) ? 'pending' : 'waiting',
        pending: groups.Scheduler.NextRun <= timestamp(new Date()),
      })),
      resources: Object.entries(data.values.Dashboard).filter(([, resource]) => 'Value' in resource).map(([key, resource]) => ({
        name: key, label: translate(`${key}._info.name`), value: resource.Value, limit: resource.Limit, total: resource.Total, record: resource.Record,
      })),
    }
  }
  function dispatch(method, input = {}) {
    const params = structuredClone(input)
    if (!Object.hasOwn(validators, method)) fail('METHOD_NOT_FOUND', '未知 API 方法')
    if (!validators[method](params)) fail('INVALID_PARAMS', '请求参数不符合 API 契约')
    const name = params.instance
    if (name != null) get(name)
    switch (method) {
      case 'updater.status': return updateStatus()
      case 'updater.commits': return {entries: commits.slice(params.offset, params.offset + params.limit), total: commits.length, hasMore: params.offset + params.limit < commits.length, localHead, upstreamHead}
      case 'updater.fetch': return {accepted: true}
      case 'updater.apply': localHead = upstreamHead; return {accepted: true}
      case 'updater.cancel': return {accepted: true}
      case 'system.ping': return {pong: true}
      case 'schema.get': return {args, menu, translations: locales[params.language]}
      case 'instances.list': return [...instances].map(([name, item]) => ({name, status: item.status, currentTask: item.status === 'running' ? 'Commission' : null, serial: item.values.Alas.Emulator.Serial, server: item.values.Alas.Emulator.ServerName}))
      case 'instances.create': {
        if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(params.name) || /^(template|deploy|backup|con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(params.name)) fail('INVALID_PARAMS', '实例名称无效')
        if ([...instances.keys()].some(name => name.toLowerCase() === params.name.toLowerCase())) fail('ALREADY_EXISTS', '同名实例已存在')
        add(params.name, params.source ? get(params.source).values : template)
        return snapshot(params.name)
      }
      case 'instances.delete':
        if (get(name).status === 'running') fail('INSTANCE_RUNNING', '请先停止实例再删除')
        if (params.revision !== snapshot(name).revision) fail('CONFLICT', '配置已变化，请重新加载后删除')
        instances.delete(name); startup.delete(name)
        return {deleted: name}
      case 'config.get': return snapshot(name)
      case 'shop_strategy.validate': return validateMockStrategy(params.script)
      case 'config.patch': {
        const data = snapshot(name)
        const seen = new Set()
        const affectedShopTasks = new Set()
        for (const {path, value} of params.changes) {
          const [task, group, arg] = validateField(path, value)
          if (seen.has(path)) fail('INVALID_PARAMS', '同一次保存不能重复修改同一个参数')
          seen.add(path)
          const field = args[task][group][arg]
          if (field.mode === 'restricted_lua') requireValidMockStrategy(value)
          data.values[task] ??= {}; data.values[task][group] ??= {}
          data.values[task][group][arg] = value
          if (group === 'ShopAdvanced') affectedShopTasks.add(task)
        }
        validateMockAdvancedGroups(data.values, affectedShopTasks)
        get(name).values = data.values
        log(name, `已保存 ${params.changes.length} 项配置。`)
        return snapshot(name)
      }
      case 'overview.get': return overview(name)
      case 'scheduler.start': case 'tasks.run':
        if (get(name).status === 'running') fail('INSTANCE_RUNNING', '实例已在运行')
        if (method === 'tasks.run' && params.task !== 'FleetScan' && !Object.values(menu).some(group => group.page === 'tool' && group.tasks.includes(params.task))) fail('INVALID_PARAMS', '该任务不支持单独运行')
        get(name).status = 'running'; log(name, '模拟调度器已启动。')
        return overview(name)
      case 'scheduler.stop':
        get(name).status = 'stopped'; log(name, '模拟调度器已停止。')
        return overview(name)
      case 'logs.get': {
        const item = get(name)
        const reset = params.after > item.cursor || params.after < (item.logs[0]?.id ?? 1) - 1
        return {instance: name, cursor: item.cursor, reset, entries: item.logs.filter(entry => reset || entry.id > params.after)}
      }
      case 'preview.capture': {
        if (!get(name).previewAt) return {instance: name, image: null, capturedAt: null}
        const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><rect width="1280" height="720" fill="#142d3a"/><circle cx="640" cy="300" r="145" fill="none" stroke="#78dac4" stroke-width="3"/><path d="M640 190 720 370 640 330 560 370Z" fill="#78dac4"/><text x="640" y="530" text-anchor="middle" fill="#d5ede9" font-size="32">AzurPilot · 模拟器测试画面</text></svg>'
        return {instance: name, image: `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`, capturedAt: get(name).previewAt ?? null}
      }
      case 'statistics.refreshLoot': return {refreshed: true}
      case 'statistics.report': {
        const resourceLabels = {Oil: '石油', Coin: '物资', Gem: '钻石', Cube: '心智魔方'}
        const actionLabels = {ActionPoint: '行动力', YellowCoin: '作战补给凭证', PurpleCoin: '特别兑换凭证'}
        const activeLabels = params.category === 'action' ? actionLabels : resourceLabels
        const series = Object.entries(activeLabels).map(([key, label]) => ({
          key, label, points: dispatch('statistics.resources', {instance: name, resource: key, days: params.days}).points
        }))
        const result = {instance: name, category: params.category, month: params.month, metrics: [], series: [], tables: [], notes: []}
        if (['resources', 'action', 'ships', 'commission'].includes(params.category)) result.series = series
        if (!['resources', 'action'].includes(params.category)) {
          result.metrics = [{label: '战斗次数', value: 1234, unit: '场'}, {label: '净行动力', value: 345, unit: ''}]
          result.tables = [{title: '统计明细', columns: ['项目', '数量', '记录时间'], rows: name === 'demo-alt' ? [] : [['测试数据', 1234, timestamp(new Date())]]}]
        }
        return result
      }
      case 'statistics.resources': {
        const base = get(name).values.Dashboard[params.resource]?.Value ?? 500
        return {instance: name, resource: params.resource, truncated: false, points: name === 'demo-alt' ? [] : Array.from({length: 24}, (_, index) => ({
          time: timestamp(new Date(Date.now() - (23 - index) * params.days * 3600000)), value: Math.max(0, Math.round(base * (.8 + index / 120 + Math.sin(index) * .03))),
        }))}
      }
      case 'settings.get': return structuredClone(settings)
      case 'settings.patch': {
        const fields = settings.groups.flatMap(group => group.fields)
        for (const [key, value] of Object.entries(params.values)) {
          const field = fields.find(field => field.key === key)
          if (!field || typeof value !== typeof field.value || (key === 'WebuiPort' && (!Number.isInteger(value) || value < 1 || value > 65535))) fail('INVALID_PARAMS', '部署设置无效')
        }
        for (const field of fields) if (field.key in params.values && field.key !== 'Password') field.value = params.values[field.key]
        return {updated: Object.keys(params.values)}
      }
      case 'startup.get': return {enabled: startup.has(name)}
      case 'startup.set':
        if (params.enabled) startup.add(name); else startup.delete(name)
        return {enabled: params.enabled}
      case 'events.subscribe':
        if (params.topics.some(topic => topic !== 'instances') && !name) fail('INVALID_PARAMS', '订阅此主题需要指定实例')
        return {topics: params.topics, instance: name ?? null}
      default: fail('METHOD_NOT_FOUND', '此方法由连接层处理')
    }
  }
  function tick() {
    for (const [name, item] of instances) if (item.status === 'running') {
      item.previewAt = new Date().toISOString()
      log(name, '模拟任务正在运行，等待下一轮调度。')
    }
  }
  return {dispatch, tick}
}
