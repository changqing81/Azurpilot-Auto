import { createContext, useCallback, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react'
import { api } from '../api/client'
import type { Instance, Schema } from '../api/types'
import type { Parameters } from '../api/generated'
import { resumeEditors } from '../config/editors'
import { detectLanguage, isLanguage, languages, localeForLanguage, translateUi, type Language, type UiTranslator } from '../i18n'
import { readDevMode, writeDevMode } from './devMode'

export { languages }

type SchemaLanguage = NonNullable<Parameters['schema.get']['language']>
const _languageCompatibility: Record<Language, SchemaLanguage> = {
  'zh-CN': 'zh-CN', 'zh-TW': 'zh-TW', 'en-US': 'en-US', 'ja-JP': 'ja-JP', 'zh-MIAO': 'zh-MIAO',
}
void _languageCompatibility

export interface AppContextValue {
  instancesLoaded: boolean; instances: Instance[]; schema?: Schema; refresh: () => Promise<void>; t: (key: string) => string; ui: UiTranslator
  notify: (message: string, error?: boolean) => void
  previewEnabled: boolean; setPreviewEnabled: (enabled: boolean) => void
  devMode: boolean; setDevMode: (enabled: boolean) => void
  theme: 'light' | 'dark'; setTheme: (theme: 'light' | 'dark') => void
  language: Language; setLanguage: (language: Language) => void
}
export const AppContext = createContext<AppContextValue | null>(null)
const Context = AppContext
export const useConnection = () => useSyncExternalStore(api.subscribe, api.getSnapshot)
export const useApp = () => useContext(AppContext)!

function initialLanguage(): Language {
  try {
    const saved = localStorage.getItem('azurpilot.language')
    if (isLanguage(saved)) return saved
  } catch { /* 浏览器禁用存储时使用浏览器语言。 */ }
  return detectLanguage()
}

export function AppProvider({children}: {children: ReactNode}) {
  const connection = useConnection()
  const [instances, setInstances] = useState<Instance[]>([])
  const [instancesLoaded, setInstancesLoaded] = useState(false)
  const [schema, setSchema] = useState<Schema>()
  const [previewEnabled, setPreviewEnabled] = useState(false)
  const [devMode, setDevMode] = useState(readDevMode)
  const [theme, setTheme] = useState<'light' | 'dark'>(() => localStorage.getItem('azurpilot.theme') === 'dark' ? 'dark' : 'light')
  const [language, setLanguage] = useState<Language>(initialLanguage)
  const [toast, setToast] = useState<{message: string; error: boolean}>()
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('azurpilot.theme', theme)
  }, [theme])
  useEffect(() => { writeDevMode(devMode) }, [devMode])
  useEffect(() => {
    document.documentElement.lang = localeForLanguage(language)
    try { localStorage.setItem('azurpilot.language', language) } catch { /* 存储不可用时仅当前会话生效。 */ }
  }, [language])
  useEffect(() => { api.connect(); return () => api.disconnect() }, [])
  useEffect(() => { if (connection === 'ready') resumeEditors() }, [connection])
  const notify = useCallback((message: string, error = false) => setToast({message, error}), [])
  const refresh = useCallback(async () => setInstances(await api.request('instances.list', {})), [])
  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    void api.request('instances.list', {}).then(instances => {
      if (active) {setInstances(instances); setInstancesLoaded(true)}
    }).catch(error => notify(error.message, true))
    return () => { active = false }
  }, [connection, notify])
  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    void api.request('schema.get', {language}).then(schema => {
      if (active) {
        setSchema(schema)
      }
    }).catch(error => {if (active) notify(error.message, true)})
    return () => {active = false}
  }, [connection, language, notify])
  useEffect(() => api.onEvent(event => {
    if (event.topic === 'instances') setInstances(event.data as Instance[])
  }), [])
  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(undefined), 6000)
    return () => clearTimeout(timer)
  }, [toast])
  const t = useCallback((key: string) => {
    let value: unknown = schema?.translations
    for (const part of key.split('.')) value = value && typeof value === 'object' ? (value as Record<string, unknown>)[part] : undefined
    return typeof value === 'string' && value !== key ? value : key.split('.').filter(item => item !== 'name' && item !== '_info').at(-1) ?? key
  }, [schema])
  const ui = useCallback<UiTranslator>((key, params) => translateUi(language, key, params), [language])
  return <Context.Provider value={{instancesLoaded, instances, schema, refresh, t, ui, notify, previewEnabled, setPreviewEnabled, devMode, setDevMode, theme, setTheme, language, setLanguage}}>
    {children}
    {toast && <div role={toast.error ? 'alert' : 'status'} className={`toast ${toast.error ? 'error' : ''}`} onClick={() => setToast(undefined)}>{toast.message}</div>}
  </Context.Provider>
}
