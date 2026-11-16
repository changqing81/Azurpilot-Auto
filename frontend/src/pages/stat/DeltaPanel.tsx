// 资源面板：挂载老 UI 渲染器产出的 HTML（app_stat_delta.py）。
//
// 老 UI 的资源变动页含：汇总条（各资源净变化）、时间线图（echarts）、
// 排行表。这里先挂其中纯 HTML 的部分（stats/legend/summary）；
// 时间线图需要老 UI 的 ap_chart.js 配合，后续单独接。
import { LegacyHtmlPanel } from './LegacyHtmlPanel'

export function DeltaSummary({ name }: { name: string }) {
  return (
    <LegacyHtmlPanel
      name={name}
      panel="delta"
      fields={['stats_html', 'legend_html', 'summary_html']}
      errorText="暂无资源变动数据（实例运行后出现）"
    />
  )
}
