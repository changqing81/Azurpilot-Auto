// 心智计算器面板：挂载老 UI 渲染器产出的 HTML。
//
// 老 UI 的实现（app_mind_calculator_tab.py:_ft_render_mind_tab）用
// mind_calculator_view.py 的 intro_html / stats_html / summary_table_html /
// ships_table_html 组装，四个函数都返回字符串。
// 内容：说明条 + 四张统计卡 + 稀有度汇总表 + 船舶清单表（560 条 / 12 页）。
//
// 注：清单的编辑/增删/导入是 PyWebIO 的回调（pin_on_change + 弹窗），
// 本面板先做到「完整只读展示」；编辑能力后续按需补（需要 REST 化的
// 写接口 + 前端表格编辑）。
import { LegacyHtmlPanel } from './LegacyHtmlPanel'

export function MindPanel({ name }: { name: string }) {
  return (
    // ⚠️ 外层 id 必须是 #pywebio-scope-ft_mind：
    // fleet-tech-alas.css 里所有 .ft-table / .ft-name / .ft-num / .ft-panel
    // 规则都写成 [id^="pywebio-scope-ft_"] .ft-xxx 这种**后代选择器**。
    // 少了这层容器，表格就退回浏览器默认样式 —— 截图里「浅色文字压在
    // 壁纸上、看不清」就是这个原因（长卿：「还是这个样」）。
    // 老 UI 的容器结构见 app_mind_calculator_tab.py:_ft_render_mind_tab。
    <div id="pywebio-scope-ft_mind">
      <LegacyHtmlPanel
        name={name}
        panel="mind"
        fields={['intro_html', 'stats_html', 'summary_html', 'list_html']}
        errorText="暂无心智清单数据（先在配置里跑一次心智扫描）"
      />
    </div>
  )
}
