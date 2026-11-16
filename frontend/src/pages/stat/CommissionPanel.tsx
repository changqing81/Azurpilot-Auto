// 委托面板：直接挂载老 UI 渲染器产出的 HTML（app_stat_commission.py）。
//
// 长卿反馈「委托统计也是半成品」—— 我原来只算了一个月总次数和 5 个数字。
// 老 UI 实际有：汇总条（钻石/魔方/芯片/石油/物资 各自的次数与均值）、
// 完整明细表、最近记录分页。这些口径都在 Python 侧，重写必然对不上，
// 所以直接复用渲染器。
import { LegacyHtmlPanel } from './LegacyHtmlPanel'

export function CommissionIncome({ name }: { name: string }) {
  return (
    <LegacyHtmlPanel
      name={name}
      panel="commission"
      fields={['summary_html', 'table_html', 'recent_html']}
      errorText="暂无委托收入数据（实例运行后出现）"
    />
  )
}
