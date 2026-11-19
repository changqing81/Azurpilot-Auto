# 新 UI 全页走查验收记录（2026-11-17）

> 方式：CDP 驱动 Edge 真实打开每个页面，截图用眼睛核对 + 工具真实交互（点击/输入/翻页）。
> 长卿：「你可以开一个edge啊，用你的眼睛看，用工具去交互，看看合理不合理」
> 用完关闭：CDP 进程树已 kill，确认 9224 关闭、无残留 msedge。

## 一、走查范围与结果

| 页面 | 路由 | 渲染 | 交互 | 发现的问题 |
|---|---|---|---|---|
| 主页 | `#/ ` | ✅ | ✅ 语言/主题/背景按钮可用 | 状态圈误显示（已修 65b5d23a7） |
| 总览 | `#/overview/alas` | ✅ | ✅ 调度器启停/统计/日志 | 日志区空白（已修 4b52d9951） |
| 统计-大世界 | `#/stat/alas` | ✅ 图表+KPI | ✅ 粒度/视图切换 | — |
| 统计-委托 | `?tab=commission` | ✅ | ✅ | — |
| 统计-科研 | `?tab=research` | ✅ | ✅ 三行控件 | — |
| 统计-资源 | `?tab=resource` | ✅ | ✅ 粒度切换 | — |
| 统计-科技列表 | `?tab=fleet` | ✅ | ✅ 六组筛选 | — |
| 统计-心智 | `?tab=mind` | ✅ | — | — |
| 配置-智能调度 | `#/config/alas/OpsiScheduling` | ✅ | ✅ 开关翻转两次 SAVE 200 | — |
| 配置-模拟器设置 | `EmulatorSettings` | ✅ 五分组 | — | — |
| 配置-主线图 | `Main` | ✅ 六分组 | — | — |
| 工具页 | `#/tool/alas` | ✅ | ✅ 全部按钮真实生效 | — |
| 管理页 | `#/manage` | ✅ | — | — |
| 设置页 | `#/settings/alas` | ✅ | ✅ UI 切换/保存 | — |
| 更新器 | `#/update` | ✅ 版本对照+历史 | — | — |
| 远程控制 | `#/remote` | ✅ 状态/URL/关闭 | — | — |
| 公告 | `#/announcement` | ✅（失败降级明确） | ✅ 刷新 | — |

## 二、本轮走查发现并修复的问题（3 个 commit）

1. `4b52d9951` 主页状态圈误显示 + 总览日志空白（document.hidden 守卫）+ 统计 `?tab=` 不跟随
2. `65b5d23a7` 状态圈只在实例页显示（与老 UI set_status(0) 语义对齐）
3. `0d4c67fe1` 保存失败显示真实 HTTP 原因（配合 60afae379 的 parse_pin_value 修复）

## 三、已知非缺陷

- 公告页「三个源都不可达」：外网（jsdelivr/GitCode/GitHub）不可达时的正确降级，页面本身行为正确
- 远控手机打不开：官方中转服务器 remote.cn.shiyan.nanoda.work 故障（信令 443 拒绝、SSH 连上即被踢），
  与本地代码无关；自检脚本 .workbuddy/perf/out/remote_probe.cjs
- 主页无状态圈：老 UI 行为（set_status(0)）

## 四、2026-11-17 深夜专项自查（长卿清单：主题/背景/远控/配置）

### 主题矩阵（6 主题 × 主页/总览，真实用户路径点击切换）
| 主题 | pywebio class | 主题层 CSS | 视觉 |
|---|---|---|---|
| default | webio-theme-default | light-alas.css | 浅色 OK |
| dark | webio-theme-dark | dark-alas.css | body 13,17,23 / content 54,57,63 OK |
| advanced_material | webio-theme-default | advanced-material-alas.css | 壁纸+半透明材质 OK |
| transparent | webio-theme-default | advanced-material + transparent 两层 | 全透明壁纸透出 OK |
| dark_advanced_material | webio-theme-dark | advanced-material + overrides 两层 | 结构对齐 webui_style_names |
| light | webio-theme-default | light-alas.css | OK |

教训：测试脚本用 hash navigate 不会重载文档，主题自然不变 —— 必须走
真实用户路径（点击 → location.replace 文档重载）才算数。

### 自定义背景
- custom_bg.png（1.3MB）服务 200 image/png
- 高级材质下随机壁纸加载、卡片材质、文字可读
- /newapi/background 返回 mode/media/has_custom

### 配置保持/更新/写入 —— 发现并修复重大缺陷
Log.LogKeepCount（group 与 task 不同名案例）实测暴露：两段键保存静默丢值
（alas.json 顶层坏键 + 真实值不变）。修复见 51a340960：后端两段键补全 +
modified.copy() 迭代 + 前端改发三段键；alas.json 坏键已清理，
保存-落盘-读回-恢复四步闭环实测通过。

### 远控
- 代理协议覆盖 http.request / ws.open / sse.open（remote_access.py:626-645），
  新 UI 的 REST 全走同源相对路径，理论兼容
- log tail 无 query 时优雅降级（200 cursor=0，远控丢 query 不致 500）
- 端到端受阻：官方中转服务器故障（信令 443 拒绝 + SSH 连上即踢，见 remote_probe.cjs），
  服务器恢复后需真机补测：页面加载 / 配置保存 / 日志流

## 五、2026-11-17 凌晨第二轮（长卿十项清单）

| 清单项 | 结论 |
|---|---|
| Edge 测试 | ✅ 全程真实浏览器点击交互，截图 20+ 张 |
| 配置写入/加载 | ✅ 修复两段键静默丢值（51a340960），保存-落盘-读回-恢复闭环实测 |
| 与老 UI 一致 | ✅ 文案逐键核对（71+13 键）、控件契约（--arg-title--/btn-group/form-control） |
| 交互合理 | ✅ 行编辑/删除/批量/分页/导出全链路 |
| 远控 | ⚠️ 代码层兼容（http/ws/sse 三通道 + query 降级实测），端到端等中转服务器恢复 |
| 主题显示 | ✅ 6 主题×2 页面矩阵，色值与老 UI 基准一致 |
| 语言 | ✅ 统计页 77 键 + 菜单/头部，en/zh 来回切实测 |
| 自定义背景 | ✅ custom_bg.png 服务 + 高级材质下壁纸竞赛 + 上传/随机/下载 |
| 图源管理 | ✅ c27669b63 从 prompt 升级为正式弹窗（列表/启停/添加/删除/恢复默认 + 内置源回落） |
| 舰队科技 | ✅ 完整（筛选/排序/分页/编辑/批量工具栏/Wiki 出处）；批量编辑弹窗待做 |
| 心智计算器 | ✅ 75b96c010 交互化（行编辑/删除/批量开关/导出/分页 + revision 乐观锁） |

## 六、2026-11-17 凌晨第三轮（老 UI 交互链路补完）

- 科技列表**批量编辑弹窗**：9410f2981 —— 后端 POST /newapi/fleet/{name}/batch
  （gids + 显式 changes + revision 乐观锁 + 事务写盘）；前端弹窗
  （三下拉/等级/三快捷预设/恢复自动记录/保存）+ data-ft-batch 委托 +
  勾选联动（计数/disabled）。Edge 实测：1 艘 level=99 落盘 → restore 还原。
- 心智计算器**扫描触发**：64d656792 —— dev_action action=mind_scan
  （等级范围校验 + 写配置 + ProcessManager 两段启动）。实测 state=1 → 停止。
- 心智计算器**清单导入**：64d656792 —— POST /newapi/mind/{name}/import
  （同名合并取最高、目录补全、review=true/来源=手动导入）。实测 added=1。

至此老 UI 的统计页交互链路（科技列表 + 心智计算器 + 图源管理）全部迁移完成。
唯一遗留：远控端到端真机补测 —— 等官方中转服务器恢复
（remote_probe.cjs 可自查：信令 443 恢复 + SSH 不再即踢即可测）。

## 七、2026-11-17 第四轮（半成品清理 + 目标要求逐条验证）

### 清掉的最后半成品
- 统计页页头「数据截至 … / 数据源正常」曾是硬编码/本地时间占位，
  新增 GET /newapi/stat/{name}/state（老 UI _get_statistics_data_state 同口径，
  四候选源含 -wal），实测显示真实时间 2026-11-17 07:52 + is-ok。

### objective 要求逐条验证（本次给出证据）
| 要求 | 证据 |
|---|---|
| 老 UI 改「切换+重启」而非后台常驻 | hot_switch.py 已删；启动日志「UI=new：新 UI 占据 /，老 UI 页面路由已移除（2 条）」；老 UI 会话建立日志 0 条（PyWebIO 会话从未创建） |
| 切换流程文案准确 | b2c62b751 修正 switch_ui docstring/日志（原写「热切换…不需要重启」「刷新页面生效」，实际必须重启） |
| DOM 用老 UI 真实 ID/class 契约 | #pywebio-scope-* / --arg-title-- / .form-control / .btn-group 等，统计/配置/总览各页持续沿用 |
| 零自造样式 | 主题层走老 UI 的 12 张 CSS；6 主题矩阵实测色值与老 UI 基准一致 |
| 只抄样式不抄懒加载/骨架 | 新 UI 的 index.html 内联骨架仅是首屏 FCP 优化（不在 React 组件里），面板全部即时渲染，无老 UI 的分批 put_scope 懒加载 |
| 逐页功能可用 | 配置/统计/管理/设置/更新器/远程/公告/开发者工具/总览 全部走查通过（见前六节） |

### 工作区清理
- 删除 4 个误产物：$null（PowerShell 错误重定向）、2026-11-15_alas.txt（误拷日志 16MB）、
  advanced_material / transparent（主题测试截图写错文件名）
- .gitignore 修复：lib/ → /lib/ /lib64/（原规则会忽略 frontend/src/lib/，
  新 UI 的 pywebio.ts 曾因此进不了版本库）
- 舰船科技数据同步至 2026-11-15

## 八、遗留（低优先级）

- 科技列表批量编辑**弹窗**（三下拉+快捷预设+保存）未迁移：工具栏入口已补
  （479805759），但 data-ft-batch 点击桥接与 _ft_batch_save 的 REST 化未做
- 心智计算器的编辑/删除/导入导出按钮未迁移
- 统计页视图切换「详情图」按钮（老 UI ToggleDetailChart）未迁移