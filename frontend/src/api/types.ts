export type Scalar = string | number | boolean | null
export type Value = Scalar | Value[] | {[key: string]: Value}
export type Values = Record<string, Record<string, Record<string, Value>>>
export type Status = 'running' | 'stopped' | 'error' | 'updating'
export interface Instance { name: string; status: Status; serial: string; server: string; currentTask?: string | null }
export interface UpdateStatus {
  state: string; localHead: string | null; upstreamHead: string | null; branch: string
  ahead: number; behind: number; available: boolean; busy: boolean; canApply: boolean; canCancel: boolean; error: string
}
export interface Commit { sha: string; author: string; date: string; message: string }
export interface CommitHistory { entries: Commit[]; total: number; hasMore: boolean; localHead: string | null; upstreamHead: string | null }
export interface Field { type: string; value: Value; mode?: string; display?: string; option?: Value[]; validate?: string | number[] }
export interface Schema {
  menu: Record<string, { menu: string; page: string; tasks: string[] }>
  args: Record<string, Record<string, Record<string, Field>>>
  translations: Record<string, unknown>
}
export interface Config { instance: string; revision: string; values: Values }
export interface ScheduledTask { name: string; nextRun: string; pending: boolean; state: 'running' | 'pending' | 'waiting' }
export interface Resource { name: string; label: string; value: number | null; limit?: number; total?: number | null; record?: string }
export interface Overview {
  instance: string; revision: string; status: Status; tasks: ScheduledTask[]
  resources: Resource[]; emulator: Record<string, Value>
}
export interface LogEntry { id: number; level: string; text: string }
export interface Logs { instance: string; cursor: number; reset: boolean; entries: LogEntry[] }
export interface LogsExportBundle { url: string; filename: string; expiresIn: number; scope: string; files?: number; bytes?: number; human_bytes?: string; human_estimate?: string; estimate_bytes?: number }
export interface Preview { instance: string; image: string | null; capturedAt: string | null }
export interface Statistics { instance: string; resource: string; points: {time: string; value: number}[]; truncated: boolean }
export interface StatPoint {time: string; value: number; source?: string}
export interface StatSeries {key: string; label: string; points: StatPoint[]}
export interface StatTable {title: string; columns: string[]; rows: Scalar[][]; note?: string; defaultSort?: TableSort}
export interface TableSort {index: number; descending: boolean}
export interface StatisticsReport {
  instance: string; category: string; month: string
  metrics: {label: string; value: number | null; unit: string}[]
  series: StatSeries[]; tables: StatTable[]; notes: string[]
}
export interface DeployField { key: string; type: string; label: string; help: string; value: Value; options: Value[] }
export interface Settings { groups: {key: string; label: string; fields: DeployField[]}[]; notice: string; demo: boolean }
export interface ApiEvent { v: 1; type: 'event'; topic: string; seq: number; data: unknown }
export interface ApiResponse { v: 1; type: 'response'; id: string; ok: boolean; result?: unknown; error?: {code: string; message: string; details?: unknown} }
export interface ScriptDiagnostic { code?: string; message: string; line?: number | null; column?: number | null; severity?: 'error' | 'warning' }
export interface ShopStrategyValidation { valid: boolean; diagnostics: ScriptDiagnostic[]; summary?: string }
export type ShopStrategyTask = 'EventShop' | 'ShopFrequent' | 'ShopOnce' | 'PrivateQuarters' | 'OpsiShop' | 'OpsiVoucher'
export interface Results {
  'updater.status': UpdateStatus
  'updater.commits': CommitHistory
  'updater.fetch': {accepted: boolean}
  'updater.apply': {accepted: boolean}
  'updater.cancel': {accepted: boolean}
  'system.ping': {pong: boolean}
  'auth.login': {authenticated: boolean}
  'events.subscribe': {topics: string[]; instance: string | null}
  'schema.get': Schema
  'instances.list': Instance[]
  'instances.create': Config
  'instances.delete': {deleted: string}
  'config.get': Config
  'config.patch': Config
  'shop_strategy.validate': ShopStrategyValidation
  'overview.get': Overview
  'scheduler.start': Overview
  'scheduler.stop': Overview
  'tasks.run': Overview
  'logs.get': Logs
  'logs.requestExport': LogsExportBundle
  'preview.capture': Preview
  'statistics.resources': Statistics
  'statistics.report': StatisticsReport
  'statistics.refreshLoot': {refreshed: boolean}
  'settings.get': Settings
  'settings.patch': {updated: string[]}
  'startup.get': {enabled: boolean}
  'startup.set': {enabled: boolean}
}
