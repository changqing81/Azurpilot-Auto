// 侧栏「搜索配置」输入框 + 结果下拉。
//
// 背景（长卿 2026-11-28）：「新UI这个搜索配置没有用，完全是个装饰，搜索委托、
// 作战档案没有一点反应」。属实 —— 旧实现（Shell.tsx 里那段字符串）只输出了
// 一个 <input>，既没有事件也没有结果容器内容，后端也没有对应接口。
//
// 设计要点：
//   1. DOM 契约照抄老 UI（#pywebio-scope-task_config_search /
//      #pywebio-scope-task_config_search_results / .config-search-result*），
//      所以 assets/gui/css/** 里那套现成样式（含四种主题）一行都不用改。
//   2. 索引由 /newapi/config/{name}/search 下发（2065 项 / 约 240KB / gzip 后很小），
//      匹配在前端本地做：每敲一个字都打一趟服务端，在远控中继下体验会很差。
//   3. 排序规则与老 UI 逐字一致（lib/configSearch.ts 顶部注释）。
//   4. 命中后跳转：普通任务 → /config/:name/:task#arg=Group.Arg；
//      集合页任务（作战档案、主线图、活动图…）→ 集合页 + ?tab= 切页签 + arg=。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, instSeg } from '../api/client'
import { prepareEntries, searchEntries, type PreparedEntry, type SearchEntry } from '../lib/configSearch'
import { COLLECTIONS } from '../pages/otherconfig'

type SearchResp = { success?: boolean; entries: SearchEntry[]; error?: string }

/** 默认取回的条数，与老 UI CONFIG_SEARCH_RESULT_LIMIT 一致。 */
const RESULT_LIMIT = 20

/** 任务 key → 所属集合页 route（集合页里的任务要带页签跳转）。 */
const TASK_TO_COLLECTION: Record<string, string> = (() => {
  const map: Record<string, string> = {}
  for (const [route, collection] of Object.entries(COLLECTIONS)) {
    for (const tab of collection.tabs) map[tab.id] = route
  }
  return map
})()

/** 结果条目的落点路径。 */
export function searchResultRoute(instance: string, entry: SearchEntry): string {
  const arg = encodeURIComponent(`${entry.group}.${entry.argument}`)
  const seg = instSeg(instance)
  const route = TASK_TO_COLLECTION[entry.task]
  if (route) {
    // 集合页：页签用查询参数（与 TaskCollectionPage 的 ?tab= 约定一致）
    return `/${route}/${seg}?tab=${encodeURIComponent(entry.task)}&arg=${arg}`
  }
  // 普通任务：配置页从 hash 里读 arg（hash 路由下 HashRouter 会把 # 之后的
  // 内容也当下级路由，所以这里用查询串更稳），见 pages/config/index.tsx。
  return `/config/${seg}/${encodeURIComponent(entry.task)}?arg=${arg}`
}

export function MenuSearch({ instance }: { instance: string }) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [entries, setEntries] = useState<PreparedEntry[]>([])
  const [active, setActive] = useState(0)
  const [open, setOpen] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [placeholder, setPlaceholder] = useState('搜索配置')
  const inputRef = useRef<HTMLInputElement | null>(null)

  // 索引懒加载：侧栏搜索框每个实例页都会挂一次，缓存到模块级，
  // 路由切换不重复拉（实例切换才会重新拉）。
  useEffect(() => {
    let alive = true
    apiGet<SearchResp>(`/newapi/config/${instSeg(instance)}/search`, 15000)
      .then(res => {
        if (!alive) return
        if (res.success === false) {
          // 后端明确报错（配置读不到等）：不要装作「没有匹配」，直接说明原因
          setLoadFailed(true)
          setLoaded(true)
          return
        }
        setEntries(prepareEntries(res.entries || []))
        setLoaded(true)
      })
      .catch(() => { if (!alive) { setLoadFailed(true); setLoaded(true) } })
    return () => { alive = false }
  }, [instance])

  // 占位文案跟随后端语言（与菜单/页眉同一套 i18n），拿不到就用中文兜底
  useEffect(() => {
    apiGet<{ items?: Record<string, string> }>('/newapi/i18n?keys=Gui.TaskConfig.SearchPlaceholder')
      .then(res => {
        const text = res.items?.['Gui.TaskConfig.SearchPlaceholder']
        // t() 查不到时原样返回 key，这种情况保持兜底文案
        if (text && !text.startsWith('Gui.')) setPlaceholder(text)
      })
      .catch(() => {})
  }, [])

  const { results, total } = useMemo(() => {
    if (!query.trim()) return { results: [] as PreparedEntry[], total: 0 }
    return searchEntries(entries, query, RESULT_LIMIT)
  }, [entries, query])

  useEffect(() => { setActive(0) }, [query])

  const choose = useCallback((entry: SearchEntry) => {
    setOpen(false)
    setQuery('')
    navigate(searchResultRoute(instance, entry))
  }, [navigate, instance])

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      // 先清空再失焦：用户按 Esc 的语义就是「不搜了」
      event.preventDefault()
      setQuery('')
      setOpen(false)
      inputRef.current?.blur()
      return
    }
    if (!results.length) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive(index => (index + 1) % results.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive(index => (index - 1 + results.length) % results.length)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const hit = results[active]
      if (hit) choose(hit)
    }
  }

  const showPanel = open && !!query.trim()

  return (
    // DOM 契约：外层 scope id 与老 UI 一致，alas.css / 主题层里的规则直接命中
    <div id="pywebio-scope-task_config_search" style={{ position: 'relative' }}>
      <div className="form-group" style={{ ['--task-config-search-input--' as string]: '1' } as React.CSSProperties}>
        <input
          ref={inputRef}
          type="text"
          className="form-control"
          name="config_search_keyword"
          autoComplete="off"
          placeholder={placeholder}
          value={query}
          onChange={event => { setQuery(event.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          // 失焦延迟收起：直接 onBlur 收起会先于结果的 onClick 触发，
          // 点结果时面板已经没了（经典坑）。
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
          onKeyDown={onKeyDown}
        />
        <div className="invalid-feedback"></div>
        <div className="valid-feedback"></div>
        <small className="form-text text-muted"></small>
      </div>

      {showPanel && (
        <div id="pywebio-scope-task_config_search_results">
          {results.length === 0 ? (
            <span style={{ ['--task-config-search-empty--' as string]: '1' } as React.CSSProperties}>
              {loadFailed
                ? '配置索引加载失败，请刷新页面重试'
                : loaded
                  ? '未找到匹配的配置'
                  : '正在加载配置索引…'}
            </span>
          ) : (
            <>
              <span style={{ ['--task-config-search-count--' as string]: '1' } as React.CSSProperties}>
                共 {total} 项结果
              </span>
              {results.map((entry, index) => (
                <button
                  key={entry.key}
                  type="button"
                  className="config-search-result"
                  data-config-search-key={entry.key}
                  data-active={index === active ? '1' : undefined}
                  // 用 onMouseDown 抢在 blur 之前选中（onClick 会被延迟收起影响）
                  onMouseDown={event => { event.preventDefault(); choose(entry) }}
                  onMouseEnter={() => setActive(index)}
                  style={index === active ? { background: 'rgba(72,117,164,.1)', borderColor: 'rgba(54,112,174,.42)' } : undefined}
                >
                  <span className="config-search-result-name">{entry.argumentName}</span>
                  <span className="config-search-result-path">
                    {entry.taskName} &gt; {entry.groupName}
                  </span>
                  <span className="config-search-result-key">{entry.key}</span>
                  {entry.help && <span className="config-search-result-help">{entry.help}</span>}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
