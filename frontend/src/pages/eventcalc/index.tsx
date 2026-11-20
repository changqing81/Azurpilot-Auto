// 活动计算器独立页（长卿 2026-11-20 定：从活动通用设置页移出来）。
//
// 为什么独立成页：
//   原来它挂在 EventGeneral 配置页的 #pywebio-scope-group_EventCalculator 里，
//   而那个容器在 #pywebio-scope-_groups（grid + grid-auto-flow: column）中。
//   计算器有 KPI 五格 + 三栏 + 31 项商店，塞进去会把「活动通用设置」
//   挤到第一行只剩 227px（CDP 实测 gridTemplateRows "226.969px 897.031px"）。
//   独立成页后两边都不受挤压。
//
// 数据与交互完全复用 EventCalculator 组件（滑块/写回/进度条都不变），
// 只是外壳换成 Shell 的整页形态。
import { useParams } from 'react-router-dom'
import { Shell } from '../../components/Shell'
import { EventCalculator } from '../config/EventCalculator'

export function EventCalcPage() {
  const { name = 'alas' } = useParams()
  return (
    <Shell active="EventCalcEntry" title="活动计算器">
      <div id="pywebio-scope-content" className="config-page">
        {/* 标题与说明放进底板内 —— 否则它们浮在壁纸上，浅色壁纸时看不清
            （长卿 2026-11-20：「上面的文字在一些壁纸下看不清」）。 */}
        <div className="ec-page-wrap">
          <h2 className="ec-page-title">活动计算器</h2>
          <p className="ec-page-desc">
            从碧蓝航线 Wiki 自动读取活动商店、结束日期和各图 PT，
            计算后可写回活动通用设置（目标 PT / 结束时间 / 商店过滤器）。
          </p>
          <EventCalculator name={name} />
        </div>
      </div>
    </Shell>
  )
}