// 配置搜索的纯数据逻辑（前端版）。
//
// 必须与 module/webui/config_search.py 的 search_config_entries **逐字同口径**：
// 老 UI（PyWebIO）与新 UI（React）搜同一个词，结果顺序要一致，否则用户
// 两边切换会觉得「搜出来的东西不一样」。
//
// 规则（照抄 Python 实现）：
//   1. 文本规范化 = casefold + 连续空白折叠（normalize）
//   2. 三组词按优先级算基础分：参数名/技术路径 = 0，任务名/组名/任务 key = 3，
//      帮助文本 = 6
//   3. 组内命中细分：完全相等 +0、前缀 +1、包含 +2
//   4. 取所有命中的最小分，再按「分数 → 原始菜单顺序」排序
//   5. 默认只回前 20 条，但总数照实返回（UI 显示「共 N 项结果」）

export type SearchEntry = {
  key: string
  task: string
  group: string
  argument: string
  taskName: string
  groupName: string
  argumentName: string
  help: string
}

/** 索引项 + 预先规范化好的搜索词（2065 条逐键规范化会卡，一次性烤好） */
export type PreparedEntry = SearchEntry & {
  primary: string[]
  context: string[]
  helpTerms: string[]
}

/** 规范化搜索文本：大小写不敏感 + 连续空白折叠。 */
export function normalizeSearchText(value: unknown): string {
  return String(value ?? '').toLowerCase().split(/\s+/).filter(Boolean).join(' ')
}

/** 把接口返回的索引烤成可快速匹配的形式。 */
export function prepareEntries(entries: SearchEntry[]): PreparedEntry[] {
  return entries.map(entry => ({
    ...entry,
    primary: [normalizeSearchText(entry.argumentName), normalizeSearchText(entry.key)]
      .filter(Boolean),
    context: [
      normalizeSearchText(entry.taskName),
      normalizeSearchText(entry.groupName),
      normalizeSearchText(entry.task),
      normalizeSearchText(entry.group),
    ].filter(Boolean),
    helpTerms: [normalizeSearchText(entry.help)].filter(Boolean),
  }))
}

function termRank(query: string, terms: string[], base: number): number | null {
  let best: number | null = null
  for (const term of terms) {
    if (!term.includes(query)) continue
    const rank = term === query ? base : term.startsWith(query) ? base + 1 : base + 2
    if (best === null || rank < best) best = rank
  }
  return best
}

/** 按相关性筛选条目，并保留原菜单顺序作为次级排序。 */
export function searchEntries(
  entries: PreparedEntry[],
  query: string,
  limit = 20,
): { results: PreparedEntry[]; total: number } {
  const normalized = normalizeSearchText(query)
  if (!normalized || limit <= 0) return { results: [], total: 0 }

  const matched: { rank: number; index: number; entry: PreparedEntry }[] = []
  entries.forEach((entry, index) => {
    const ranks = [
      termRank(normalized, entry.primary, 0),
      termRank(normalized, entry.context, 3),
      termRank(normalized, entry.helpTerms, 6),
    ].filter((value): value is number => value !== null)
    if (!ranks.length) return
    matched.push({ rank: Math.min(...ranks), index, entry })
  })

  matched.sort((a, b) => a.rank - b.rank || a.index - b.index)
  return {
    results: matched.slice(0, limit).map(item => item.entry),
    total: matched.length,
  }
}
