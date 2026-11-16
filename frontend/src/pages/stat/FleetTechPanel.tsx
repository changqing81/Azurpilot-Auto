// 科技列表（舰队科技）面板：挂载老 UI 渲染器产出的 HTML。
//
// 注意归属：这个页签**不是**舰船经验（那属于「大世界」面板的 ship_exp_table），
// 而是舰队科技模块（app_fleet_technology.py:_ft_render_tech_tab +
// fleet_tech_view.py:summary_html / table_html / bonus_grid_html）。
//
// 内容：汇总条（最高科技点 / 四大阵营 / Lv.120 进度）
//       + 加成格 + 904 艘舰船完整表格（可按列排序，由后端 table_html 输出）。
import { LegacyHtmlPanel } from './LegacyHtmlPanel'

export function FleetTechPanel({ name }: { name: string }) {
  return (
    // 同 MindPanel：#pywebio-scope-ft_tech 是 fleet-tech-alas.css 里
    // [id^="pywebio-scope-ft_"] 后代选择器的宿主容器，不能省。
    <div id="pywebio-scope-ft_tech">
      <LegacyHtmlPanel
        name={name}
        panel="fleet"
        fields={['summary_html', 'bonus_html', 'table_html']}
        errorText="暂无舰队科技数据（先在配置里跑一次科技扫描）"
      />
    </div>
  )
}
