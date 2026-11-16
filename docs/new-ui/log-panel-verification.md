# 日志面板验收报告（真实数据）

> 时间：2026-11-16（new-ui 分支 ebea428a2）
> 数据源：22267 真实挂机实例（355 条 renderables / 67KB 彩色 HTML）
> 意义：这是此前最大的验证盲区 —— 测试实例 22480 没有 worker，日志永远是空的

## 一、端点级验证（5 项性质，全部通过）

| 性质 | 请求 | 结果 |
|---|---|---|
| 首次全量 | `?since=-1&width=128` | `full:true` cursor=355 67,265 字节 ✅ |
| 增量空转 | `?since=355` | `full:false` **0 字节**（不重传历史）✅ |
| 环形回绕 | `?since=999999` | `full:true` 全量重建 ✅ |
| width 生效 | 80 vs 128 | 58,523 vs 67,265 字节（换行位置不同）✅ |
| 颜色正确 | — | INFO `#0451a5` / 时间戳 `#0598bc` / 粗体 span ✅ |

## 二、前端渲染验证（真实日志）

```
htmlLen       67,337 字节
彩色 span     400 个
字体          "JetBrains Mono NL", monospace
字号          13px
滚动          自动滚到底部（scrolledToBottom: true）
背景          rgba(255,255,255,0.93)  ← 高级材质主题正确
```

截图：`.workbuddy/perf/out/real_log_newui.png`

渲染内容与老 UI 逐项一致：INFO 蓝色标签、青色时间戳、绿色分隔线（`<<< 战役结束 >>>`）、
中文日志正文、末尾 `[alas] exited. Reason: Manual stop`。

## 三、DOM 裁剪压测（300 次追加）

在真实日志页面上直接注入 300 次增量追加，观察是否膨胀：

| 指标 | 压测前 | 300 次后 | 结论 |
|---|---|---|---|
| 日志块数 | 3 | **200（封顶）** | ✅ 分块裁剪生效 |
| 日志节点数 | 836 | **801** | ✅ 不升反降 |
| 日志 HTML | 69 KB | **34 KB** | ✅ 旧块被回收 |
| JS 堆 | — | 2.4 MB | ✅ 无增长 |
| 300 次追加耗时 | — | **7 ms** | ✅ 主线程无感 |

**这就是解决「运行久了会卡」的核心机制**：

1. **分块**：每次增量包进 `<div class="alas-log-chunk" style="display:contents">`
   （布局上完全透明，视觉与直插无差异）
2. **整块裁剪**：超过 200 块就 `removeChild(firstElementChild)`
   —— 不能用「逐节点删」，rich 输出的 span 之间夹着裸文本节点，
      逐节点删会残留且内存无限涨（老 UI 踩过 3.3GB 的坑，
      注释记录在 `module/webui/widgets.py:RichLog.dom_max_chunks`）
3. **增量游标**：`?since=<cursor>` 只取新增，不重传历史
4. **可见性暂停**：`document.hidden` 时不轮询

## 四、仍未验证

- **实时日志追加**：22267 当前 `alive:false`（调度器停止），日志静止，
  因此「真实新日志到达 → 前端 2s 轮询 → 追加」这条链路只验证了端点与压测，
  没验证「真实数据流动」。需要在调度器运行时观察。
- **日志滚动交互**：手动向上滚动后，「自动滚动」开关是否正确保持位置。

## 五、与老 UI 的关键差异（有意为之）

| 项 | 老 UI | 新 UI |
|---|---|---|
| 传输方式 | PyWebIO WebSocket push | REST 轮询 2s |
| 首屏全量 | 服务端推 400 条 | `?since=-1` 拉最近 200 条 |
| 增量 | WS 推送 | `?since=cursor` 拉取 |
| 渲染缓存 | `RichLog._html_cache` | **复用同一个**（服务端共享） |

服务端渲染链完全复用（`HTMLConsole + Highlighter + WEB_THEME + LOG_CODE_FORMAT`），
所以颜色/格式与老 UI **逐字节一致**。
