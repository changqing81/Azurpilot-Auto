# AzurPilot 新 UI（new-ui 分支）开发文档

> 最后更新：2026-11-15 · 维护者：长卿 + AI 协作组
> **后续接手的人（人或 AI）必读这份文档再动手。**

## 1. 项目目标（长卿原话提炼）

用 React 新前端**逐页替换** PyWebIO 老 UI。三条硬标准：

| 标准 | 含义 | 验收方式 |
|---|---|---|
| **功能/显示一模一样** | 与老 UI 对应页面逐项一致，视觉逐位对齐（四套主题全保真） | 截图对照 + 功能清单逐项核对 |
| **加载更快** | 老 UI 首屏内容稳定 2.0s（探针实测） | 新 UI 首屏 < 300ms |
| **占用更低** | 静态资源长缓存 + 无服务端逐会话渲染 | 探针帧数/字节数对比 |

## 2. 背景与决策记录（为什么这么做）

- 老 UI 的卡顿是 **PyWebIO 协议层天花板**：一命令一帧、服务端渲染 DOM、每会话经 WS 全量重发 ~100KB CSS。2026-09-27 已做框架内优化（懒渲染/骨架屏/渲染锁），榨干了，框架内无解 → 换前端是唯一出路。
- **放弃全量 Rust 迁移**（2026-11-15 决策）：侦查结论是收益不够，不重复展开，详见 `.workbuddy/memory/2026-11-15.md`。
- **不用 wess09 上游的后端**（module/api）与视觉（玻璃卡片风格）；它的 frontend 工程配置（Vite/API 客户端/i18n 组织）**只作参考**。
- **本地登录砍掉**：本地访问不要求密码；远控（remote_access）保留密码。登录页本身是废弃设计。
- 老 UI（PyWebIO, 22267）**全程不删不动**，双轨并行，直到新 UI 全页替换完成再谈下线。

## 3. 架构

```
┌─ 同一 Starlette 应用（gui.py 现有进程，端口不变）─┐
│  /            PyWebIO 老 UI（不动）              │
│  /new/        React 新 UI（静态产物，immutable 长缓存）│
│  /api/…       module/webapi/ 薄 API 层（新写）    │
│  /ws/…        新 WS（状态推送，参照老 /ws 协议裁剪）│
└──────────────────────────────────────────────┘
frontend/（Vite+React+TS）→ pnpm build → 产物挂 /new/
```

- **主题 CSS 直接复用** `assets/gui/css/*.css` 原文件 `<link>` 引用，不重写不复制，命中 fastapi.py 的 immutable 缓存中间件。DOM 结构必须对齐老 UI 的 `#pywebio-scope-*` 约定，主题样式才能直接生效（对照表由主页解剖 agent 产出，落在本目录）。
- 后端薄层 `module/webapi/`：只做"把现成业务函数包成 REST/WS"，**不复制业务逻辑**；挂载方式参考 wess09 module/api 的 app 挂载姿势。

## 4. 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 分支 new-ui（基点 e2258246f）+ 文档 + 侦查 | ✅ 进行中 |
| P1 | frontend 脚手架 + module/webapi 骨架 + 壳/主页复刻 | ✅ 完成 |
| P2 | 逐页迁移：总览页✅ 配置页✅ 统计页六面板全完成（大世界/委托/科研/资源/科技列表 904 项/心智计算器 560 舰）✅ → 设置页/更新器排队 | 🔄 进行中 |
| P3 | 全页完成后：deploy.yaml 开关切默认入口；老 UI 保留一个版本周期 | ⬜ |

## 5. 工作规矩（接手必读）

1. **改 WebUI 相关代码前先读** `.agent/GUI-PERFORMANCE.md` 与 azurpilot-gui-impact-guard 技能的 11 条不变量——老 UI 的红线对新 UI 的"显示一致"同样适用。
2. 每页迁移必须跑探针对比（工具与基线：`.workbuddy/perf/webui_probe.mjs`，基线数据见 azurpilot-webui-perf-probe 技能）。
3. 本分支**只推 GitHub**：sync-gitcode.yml 只触发 [master, dev]，new-ui 自动不走 GitCode——别把分支改名。
4. 主分支基线漂移：dev 有并发提交，**定期 rebase**（本仓库规矩：冲突以 dev 为准；不确定先问长卿）。
5. 性能探针脚本在 `.workbuddy/perf/`（不进 git），用法见技能文档。

## 6. 侦查产物索引

- wess09 前端工程解剖报告：本目录 `wess09-frontend-notes.md` ✅
- 老 UI 主页结构解剖（scope 对照表 + 端点清单）：本目录 `old-ui-home-anatomy.md` ✅
- 迁移验收记录：本目录 `milestones.md`（待补）

## 7. 已知坑（踩过的记录在这里）

- （待记录）
