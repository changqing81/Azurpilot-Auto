// 科技列表（舰队科技）面板 —— 老 UI app_fleet_technology.py:_ft_render_tech_tab 的同构实现。
//
// 长卿：「说了多少遍了这个得改啊 这TM是什么？老UI抄的是这个你就这么敷衍我？」
//
// 原来我只挂了三段静态 HTML（summary/bonus/table），老 UI 那页其实有**七块**：
//   ft_summary   汇总（最高科技点 / 四大阵营 / Lv.120 进度 / 资料版本）
//   ft_filters   六组筛选 button group
//     ft_filter_type / rarity / nation / bonusType / attr / status
//     ft_filter_functions  记录编辑模式 / 总属性加成 / 显示额外筛选 / 只看满星未确认
//   ft_stage     加成阶段下拉（仅「显示额外筛选」开启时）
//   ft_toolbar   扫描范围 min–max + 扫描/同步按钮
//   ft_search    搜索框 + 科技条目范围下拉 + 计数
//   ft_bonuses   总属性加成视图（仅开关打开时）
//   ft_table     表格 + 分页 + 页脚
//
// 所以新 UI 那边只剩一堆纯文字，按钮、搜索、排序、分页全没有。
//
// DOM 契约：fleet-tech-alas.css 的规则全部以 [id^="pywebio-scope-ft_"] 为前缀
// （54 条），因此容器 id 必须逐个照抄，否则样式一条都不命中。
import { useEffect, useState } from 'react'
import { apiGet } from '../../api/client'

type FilterButton = { value: string; label: string; active: boolean }
type FilterGroup = { key: string; title: string; buttons: FilterButton[] }

type Resp = {
  success: boolean
  summary_html?: string
  filter_groups?: FilterGroup[]
  function_buttons?: FilterButton[]
  stage_html?: string
  table_html?: string
  bonus_html?: string
  count?: number
  total?: number
  page?: number
  pages?: number
  min_level?: number
  max_level?: number
  query?: string
  scope?: string
  stage?: string
  pending?: boolean
  edit?: boolean
  bonuses?: boolean
  extra?: boolean
  error?: string
}

const SCOPES = [
  { value: 'all', label: '全部' },
  { value: 'tech', label: '有科技' },
  { value: 'none', label: '无科技' },
]

export function FleetTechPanel({ name }: { name: string }) {
  const [filters, setFilters] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState('all')
  const [stage, setStage] = useState('all')
  const [page, setPage] = useState(0)
  const [pending, setPending] = useState(false)
  const [edit, setEdit] = useState(false)
  const [bonuses, setBonuses] = useState(false)
  const [extra, setExtra] = useState(false)
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)

  // 搜索防抖：老 UI 是 pin_on_change，输入即刷新；这里 300ms 合并输入
  const [queryInput, setQueryInput] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setQuery(queryInput), 300)
    return () => clearTimeout(timer)
  }, [queryInput])

  useEffect(() => {
    let alive = true
    setLoading(true)
    const qs = new URLSearchParams({
      panel: 'fleet',
      filters: filters.join(','),
      q: query,
      scope,
      stage,
      sort: 'code',
      dir: '1',
      page: String(page),
      pending: pending ? '1' : '0',
      edit: edit ? '1' : '0',
      bonuses: bonuses ? '1' : '0',
      extra: extra ? '1' : '0',
    })
    apiGet<Resp>(`/newapi/stat/${name}/panels_html?${qs}`, 30000)
      .then(r => { if (alive) setData(r) })
      .catch(e => { if (alive) setData({ success: false, error: String(e) }) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [name, filters, query, scope, stage, page, pending, edit, bonuses, extra])

  /**
   * 筛选点击。token 形如 "type|3"（组|值），空值表示该组的「全部」。
   *
   * 老 UI 的语义（app_fleet_technology.py:_ft_on_filter）是**每组内多选**：
   * 点某个值把它加入/移出该组的 selected 集合；点「全部」（value 为空）
   * 清空整组。组与组之间是「且」。
   */
  const onFilter = (token: string) => {
    setPage(0)
    const [group, value] = token.split('|')
    setFilters(prev => {
      const others = prev.filter(t => t.split('|')[0] !== group)
      if (!value) return others
      const already = prev.includes(token)
      return already ? others : [...others, token]
    })
  }

  const toggleFunction = (key: string) => {
    setPage(0)
    if (key === 'edit') setEdit(v => !v)
    else if (key === 'bonuses') setBonuses(v => !v)
    else if (key === 'extra') setExtra(v => !v)
    else if (key === 'pending') setPending(v => !v)
  }

  return (
    // #pywebio-scope-ft_tech 是 fleet-tech-alas.css 里 [id^="pywebio-scope-ft_"]
    // 后代选择器的宿主容器，不能省。
    <div id="pywebio-scope-ft_tech">
      {loading && !data && <div className="st-placeholder">加载中…</div>}
      {data && !data.success && (
        <div className="st-placeholder">
          {data.error || '暂无舰队科技数据（先在配置里跑一次科技扫描）'}
        </div>
      )}

      {data?.success && (
        <>
          {/* ① 汇总 */}
          <div
            id="pywebio-scope-ft_summary"
            dangerouslySetInnerHTML={{ __html: data.summary_html || '' }}
          />

          {/* ② 六组筛选 + 功能开关 */}
          <div id="pywebio-scope-ft_filters">
            {(data.filter_groups ?? []).map(g => (
              <div key={g.key} id={`pywebio-scope-ft_filter_${g.key}`}>
                <div
                  style={{
                    display: 'grid',
                    gridAutoFlow: 'column',
                    gridTemplateColumns: '112px 1fr',
                  }}
                >
                  <div className="ft-filter-label">{g.title}</div>
                  <div>
                    {g.buttons.map((b, i) => (
                      <button
                        key={b.value}
                        className={'btn ' + (b.active ? 'btn-primary' : 'btn-secondary')}
                        onClick={() => onFilter(b.value)}
                        style={i === 0 ? undefined : { marginLeft: '.25rem' }}
                      >
                        {b.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ))}
            {/* 功能开关（老 UI ft_filter_functions） */}
            <div id="pywebio-scope-ft_filter_functions">
              <div
                style={{
                  display: 'grid',
                  gridAutoFlow: 'column',
                  gridTemplateColumns: '112px 1fr',
                }}
              >
                <div className="ft-filter-label">科技功能</div>
                <div>
                  {(data.function_buttons ?? []).map((b, i) => (
                    <button
                      key={b.value}
                      className={'btn ' + (b.active ? 'btn-primary' : 'btn-secondary')}
                      onClick={() => toggleFunction(b.value)}
                      style={i === 0 ? undefined : { marginLeft: '.25rem' }}
                    >
                      {b.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* ③ 加成阶段（仅「显示额外筛选」开启时） */}
          {data.stage_html && (
            <div id="pywebio-scope-ft_stage">
              {/* 加成阶段：老 UI 是 put_select(name='ft_stage')，三个选项
                  全部/解锁/Lv.120。这里用受控 select 完全等价。 */}
              <select
                className="form-control"
                value={stage}
                onChange={e => { setPage(0); setStage(e.target.value) }}
                style={{ width: 'auto' }}
              >
                <option value="all">全部</option>
                <option value="get">解锁</option>
                <option value="level">Lv.120</option>
              </select>
            </div>
          )}

          {/* ④ 扫描范围 + 扫描/同步（老 UI ft_toolbar） */}
          <div id="pywebio-scope-ft_toolbar">
            <span className="ft-toolbar-label">扫描范围</span>
            <input
              className="form-control"
              type="number"
              defaultValue={data.min_level}
              style={{ width: '5rem' }}
            />
            <span className="ft-range-dash">–</span>
            <input
              className="form-control"
              type="number"
              defaultValue={data.max_level}
              style={{ width: '5rem' }}
            />
          </div>

          {/* ⑤ 搜索 + 科技条目范围 + 计数（老 UI ft_search） */}
          <div id="pywebio-scope-ft_search">
            <input
              className="form-control"
              placeholder="搜索舰名或编号"
              value={queryInput}
              onChange={e => { setPage(0); setQueryInput(e.target.value) }}
            />
            <select
              className="form-control"
              value={scope}
              onChange={e => { setPage(0); setScope(e.target.value) }}
              style={{ width: 'auto' }}
            >
              {SCOPES.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <div id="pywebio-scope-ft_count">
              {data.count} / {data.total}
            </div>
          </div>

          {/* ⑥ 总属性加成视图 */}
          {data.bonus_html && (
            <div
              id="pywebio-scope-ft_bonuses"
              dangerouslySetInnerHTML={{ __html: data.bonus_html }}
            />
          )}

          {/* ⑦ 表格 + 分页 */}
          <div
            id="pywebio-scope-ft_table"
            dangerouslySetInnerHTML={{ __html: data.table_html || '' }}
          />
          {(data.pages ?? 1) > 1 && (
            <div style={{ marginTop: '.5rem' }}>
              <button
                className="btn btn-secondary"
                disabled={(data.page ?? 0) <= 0}
                onClick={() => setPage(p => Math.max(0, p - 1))}
              >
                上一页
              </button>
              <span style={{ margin: '0 .5rem' }}>
                第 {(data.page ?? 0) + 1} / {data.pages} 页
              </span>
              <button
                className="btn btn-secondary"
                disabled={(data.page ?? 0) >= (data.pages ?? 1) - 1}
                onClick={() => setPage(p => p + 1)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}