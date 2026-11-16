// 科研面板：直接挂载老 UI 渲染器产出的 HTML（app_stat_research.py）。
//
// 老 UI 的科研表含：期数、物品种类数、累计数、每件物品的稀有度/累计/次数/
// 均值/本月，以及「进行中的科研」区块。全部由 Python 侧渲染。
import { LegacyHtmlPanel } from './LegacyHtmlPanel'

export function ResearchPanel({ name }: { name: string }) {
  return (
    <LegacyHtmlPanel
      name={name}
      panel="research"
      fields={['table_html']}
      errorText="暂无科研掉落数据"
    />
  )
}
