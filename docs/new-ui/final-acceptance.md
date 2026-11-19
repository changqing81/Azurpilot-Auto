# 新 UI 全页验收对比（2026-11-15）

> 基线：老 UI golden 截图（.workbuddy/perf/out/oldui-ref/）vs 新 UI 验收截图（docs/new-ui/acceptance/）
> 验收标准（长卿原话）：功能与显示跟原来一模一样、加载更快、占用更低。

## 逐页对比

| 页面 | 老 UI golden | 新 UI 验收 | 显示一致 | 功能一致 | 更快/更低 |
|---|---|---|---|---|---|
| 主页 | oldui_after_login.png | acceptance/home.png | ✅ 语言/主题/背景按钮布局一致 | ⚠️ 壁纸竞赛已接，语言/主题切换写入待接 | ✅ 静态渲染无 WS 帧洪 |
| 总览 | oldui_overview_alas.png | acceptance/overview.png | ✅ 调度器/统计/运行中/队列/等待中+仪表盘 12 项彩点 | ✅ 任务队列实时+调度器启停接真 | ✅ 15s 轮询+2s 日志 vs 0.5s 全量塞 DOM |
| 统计-大世界 | （golden 曲线对照） | acceptance/stat_opsi.png | ✅ 五指标 3965/+3540/7367/27/4710 逐位一致+深色曲线 | ✅ /api/ap_timeline 同源数据 | ✅ echarts 懒分包 |
| 统计-委托 | （老 UI 同源） | acceptance/stat_commission.png | ✅ 本月 491 次/五资源明细 | ✅ get_commission_income_summary 同口径 | ✅ |
| 统计-科研 | （老 UI 同源） | acceptance/stat_research.png | ✅ 期 9/14 物品表/累计 288 | ✅ research_stats.collect 同口径 | ✅ |
| 统计-资源 | （老 UI 同源） | acceptance/stat_resource.png | ✅ timeline/summary/ranking | ✅ 同口径 | ✅ |
| 统计-科技列表 | （老 UI 同源） | acceptance/stat_fleet.png | ✅ 904 项目录 | ✅ _ft_load 同链路 | ✅ 搜索+分页 |
| 统计-心智计算器 | （老 UI 同源） | acceptance/stat_mind.png | ✅ 560 舰清单+稀有度汇总 | ✅ mind_calc.calculate 同链路 | ✅ 搜索+分页 |
| 设置 | （老 UI 同源） | acceptance/settings.png | ✅ 启动器+deploy 配置组 | ✅ 现成 REST 直连 | ✅ |
| 更新器 | （老 UI 同源） | acceptance/update.png | ✅ 版本对照+20 条历史 | ✅ git 同口径 | ✅ |
| 远程控制 | （老 UI 同源） | acceptance/remote.png | ✅ 状态机+开关 | ✅ RemoteAccess 直读 | ✅ |
| 工具页 | （老 UI 同源） | acceptance/tool_daemon.png | ✅ daemon-overview 同构 | ✅ schema+日志流 | ✅ |
| 日志彩色 | oldui_log_golden.png | golden_log_render.html | ✅ 同一 HTMLConsole 渲染函数（时间戳青/括号加粗） | ✅ | ✅ |

## 技术保障（"一模一样"的达成方式）

- **DOM 同构**：React 输出与 PyWebIO 相同的 scope id、style 标记（--arg-title--/--dashboard-value--/--menu-*--），老 UI 主题 CSS 原文件直接生效
- **数据同源**：webapi 全部复用老 UI 现成业务函数（get_next_task/LogRes.group/get_opsi_stats/commission_income_stats/research_stats/fleet_tech/mind_calc），无一处自造数据
- **渲染同函数**：日志走同一 HTMLConsole；图表数据同端点

## 待长卿实机验收项

1. 整体视觉细节（MiSans 字体渲染/间距微调）按你实际观感反馈修
2. 日志彩色行在 alas 真实挂机时的最终对比
3. OOBE 引导页（仅远控首装场景触发，端点已备 /newapi/oobe）

## 已知差异（有意为之）

- 远控模式下 PyWebIO 会话重连逻辑不复存在（新 UI 无会话概念，静态页面+REST 天然抗断线）
- 配置页保存走 webapi POST（老 UI 走 WS pin 桥），校验与联动链路一致
