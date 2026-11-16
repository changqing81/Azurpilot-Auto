# 新 UI 功能覆盖验收表

> 更新时间：2026-11-16（new-ui 分支 669f1c20d）
> 对照基准：老 UI（PyWebIO）主页菜单 6 项 + 实例内页

## 一、页面覆盖（老 UI 主页菜单 → 新 UI 路由）

| 老 UI 菜单项 | 新 UI 路由 | 状态 | 说明 |
|---|---|---|---|
| 主页 HomePage | `#/` | ✅ | 语言/主题/背景管理，壁纸竞赛 TS 移植 |
| 更新器 Update | `#/update` | ✅ | 本地/上游提交对照 + 详细提交历史 + 更新日志 |
| 远程控制 Remote | `#/remote` | ✅ | 状态机 + 开关（实测显示「运行中 · 信令中」+ 入口 URL） |
| 设置 Setting | `#/settings/:name` | ✅ | 启动器自启动 + deploy.yaml 全量可视化（含界面版本切换） |
| 公告 Announcement | `#/announcement` | ✅ | 本轮新增。正文 + 配图 + 更新日志 |
| 开发者工具 Utils | `#/tool/:name` | ✅ | 本轮新增。调试按钮 + **可用的日志导出** |

## 二、实例内页覆盖

| 页面 | 新 UI 路由 | 状态 | 说明 |
|---|---|---|---|
| 总览 | `#/overview/:name` | ✅ | 任务队列 + 仪表盘 13 项 + 日志面板，布局逐项对齐老 UI |
| 统计 | `#/stat/:name` | ✅ | 6 页签（大世界/委托/科研规划中/资源/科技列表/心智计算器） |
| 配置 | `#/config/:name/:task` | ✅ | 分组表单 + 控件类型映射 + 保存 + 联动回调 |
| OOBE | `#/oobe` | ✅ | 五步向导 |

## 三、本轮新增/修复明细

### 3.1 公告页（新增）

- 端点 `GET /newapi/announcement[?force=1]`
  → `ApiClient.get_announcement()`（jsdelivr → gitcode → GitHub 三源回退）
  → `localize_images()` 把配图落本地（手机/远控直连 jsdelivr 基本不通）
  → 5 分钟进程级缓存（三源全超时要 3 秒以上，不能每次打开都重拉）
- 端点 `GET /newapi/update-log` → `ApiClient.get_changelog()`
  → 服务端复用 `module/webui/update_log.py:render_entries_html` 渲染 HTML
  → 与老 UI 更新器页显示**完全一致**（含图片安全性过滤）
- 前端 `pages/announcement/index.tsx`：公告正文 + 更新日志两张卡片
- **失败降级明确**：公告源不可达时显示「公告拉取失败：…」+ 一句原因说明，
  不假装空白页

### 3.2 开发者工具页（新增）

老 UI 该页有两类内容，新 UI 按「能真用的做真、不能的明说」处理：

**① 日志导出（真能用）** —— 走老 UI 现成的 REST：
- `GET /api/log/runtime?instance=&scope=` → 运行日志 zip
- `GET /api/log/error?scope=full` → 错误日志目录（含截图）
- `GET /api/log/error?scope=text` → 仅日志文本
- 实例下拉 + 日期下拉（日期来自 `/api/log/runtime/dates`）
- 前端用 fetch + Blob 下载，解析 `Content-Disposition` 取真实文件名

**② 调试动作（不能直接用，已明说）**
这 20 个按钮（抛异常/预览提示/维护横幅/图标模拟/跨月预演/重启 Alas/推送测试）
全部是 PyWebIO 会话内的回调（`put_button(onclick=...)`），没有 REST 等价物。
新 UI 保留按钮入口，点击时明确提示：

> 「XXX」是 PyWebIO 会话内的调试动作，新 UI 尚未提供等价接口；请切回老 UI 使用。

**不静默失败、不假装能用**——这是刻意的选择。

### 3.3 修复：下拉框被压成 0 宽

`entry-alas.css:446` 的 `.deploy-setting-select { width: 100% }` 在
`.log-export-instance`（inline-flex）里没有可用宽度参照，实测被压成 0。
修法：给两个 select 加内联 `width:auto; minWidth` —— 不改老 CSS（避免影响老 UI）。

## 四、回归测试结果（9 页全绿）

用 CDP 逐页导航 + 等 9 秒 + 采集 DOM/控制台错误：

```
主页        root✓ title=主页         menu=6    errors=[]
总览        root✓ title=总览         menu=100  errors=[]
统计        root✓ title=统计         menu=100  errors=[]   （6 页签，面板有数据）
配置        root✓ title=配置         menu=100  errors=[]
设置        root✓ title=设置         menu=100  errors=[]
更新器      root✓ title=更新器       menu=100  errors=[]
远程控制    root✓ title=远程控制     menu=100  errors=[]   （运行中 · 信令中）
公告        root✓ title=公告         menu=100  errors=[]
开发者工具  root✓ title=开发者工具   menu=100  errors=[]
```

**0 个 JS 异常、0 个 console.error。**

## 五、仍未实现（明确记录，不含糊）

| 项 | 原因 | 影响 |
|---|---|---|
| 开发者工具页的 20 个调试按钮 | 全是 PyWebIO 会话内回调，无 REST | 需要调试时切回老 UI |
| 公告弹窗（首页自动弹出） | 新 UI 是路由页，弹窗会打断导航 | 改为菜单入口进公告页 |
| 截图预览（`window.alasToggleLivePreview`） | 依赖 `alas-utils.js` 全局函数与 `/ws/live_screenshot` | 按钮在，点击无响应（待补） |
| 导出今日日志（总览页按钮） | 同上，未接 `/api/log/runtime` | 可在开发者工具页导出 |
| 维护横幅 | 依赖老 UI 的 run_js 增量更新 | 未移植 |
| 页面骨架屏（切页时） | 老 UI 的 `#alas-page-skeleton` | 新 UI 只有首屏骨架，切页无骨架 |
| 移动端布局 | `alas-mobile.css` 未纳入 bundle | 窄屏未适配 |
| 主题热切换 | 老 UI `swap_theme_css` 只换主题层 | 新 UI 改了要刷新 |

## 六、下一步

1. 补齐上表里「有 REST 支撑」的项（截图预览、导出今日日志、切页骨架）
2. 移动端布局（alas-mobile.css + 响应式）
3. 收尾：热切换 + `/` 与 `/new/` 并存（长卿已定"最后做"）
