# 新 UI 性能实测报告（老 UI A/B 对比）

> 测试时间：2026-11-16 08:20-08:30
> 环境：本机 Edge 157 / CDP 9224 / 视口 1887×1214 / 同一后端 22267（真实挂机实例）
> 方法：CDP 新建标签 → 清缓存 → 导航 → 轮询 DOM 就绪 → 页面内 `performance` API 取数
> 复现脚本：`.workbuddy/perf/out/probe_ab.cjs`、`diag_perf.cjs`、`soak.cjs`

## 1. 关键结论

| 指标 | 老 UI | 新 UI | 变化 |
|---|---|---|---|
| JS 传输量 | 238.8 KB | **100.6 KB** | **-58%** |
| 总请求数 | 44-45 | **27-31** | **-37%** |
| 总传输量 | 497.9 KB | **390.0 KB** | **-22%** |
| JS 堆内存 | 11.0-11.3 MB | **2.2-2.8 MB** | **-75%** |
| DOM 节点数 | 1521-1536 | **1236-1244** | -19% |
| 长任务（>50ms） | 未采集 | **0 个** | 主线程无阻塞 |

> 老 UI 的「JS 传输量」之所以大，是 PyWebIO 把整个 Bootstrap + jQuery + PyWebIO 运行时
> 一次性推给浏览器；新 UI 是 React 运行时 + 业务代码的按需分包。

## 2. 本轮修掉的 3 个真实性能 bug

这一节是重点：**A/B 数据不是一开始就好看的**，测出问题才修出来的。

### Bug 1：同步 IO 阻塞 asyncio 事件循环（最严重）

**现象**：首屏 6 个 API 请求全部在 `start=580ms` 同时发出，却全部在 `start+dur≈1530ms`
同时结束——这是串行排队的特征，不是并发。

```
修复前（diag_perf.cjs 实测）：
/newapi/overview/alas   start: 580  dur: 1531
/newapi/dashboard/alas  start: 581  dur: 1532
/newapi/version         start: 580  dur: 1530
/newapi/header          start: 579  dur: 1525
/newapi/menu            start: 579  dur: 1416
```

**根因**：`module/webapi/app.py` 里 25 个 handler 全是 `async def`，但内部直接 `return
JSONResponse(data.get_xxx())`。`data.*` 是**同步阻塞**函数——读 args.json(1.6MB)、
起 git 子进程、加载 YAML、用 Rich 渲染日志。在 `async def` 里做同步阻塞IO，整个
事件循环被卡住，后续请求只能排队。

**修复**：全部改走 `starlette.concurrency.run_in_threadpool`，24 处。
```python
return JSONResponse(await run_in_threadpool(data.get_overview, name))
```

**效果**：最慢请求 1531ms → **324ms**（-79%），各接口恢复正常并发。

### Bug 2：i18n 词条每次任务都重新读盘解析

**现象**：`get_overview` 冷启动 3045ms、预热后仍 152ms。

**根因**：`_task_name(command)` 对**每个任务**都 `read_file("zh-CN.json")`——
这个文件 3.5MB / 3 万条，34 个任务 = 34 次读盘 + JSON 解析。

**修复**：`_load_i18n()` 进程级缓存，按 `(mtime_ns, size)` 失效（改文案自动重载）。

### Bug 3：每次都重建 AzurLaneConfig

**现象**：overview 152ms、dashboard 类似。

**根因**：`AzurLaneConfig(name, task=None)` 构造一次要读 args.json + 用户配置 + 绑定任务，
约 150ms。总览页 15s 轮询一次、仪表盘也各要一次，每次重建纯浪费。

**修复**：`_get_config()` 按 `配置文件 mtime` 缓存实例，用户改配置自动失效。

**效果**（Python 侧微基准）：
```
overview  run1(冷): 2558.5 ms   run2+: 66.5 / 53.2 / 64.3 ms   （-65%）
dashboard run1(冷):   57.7 ms   run2+: 13.2 ms                  （-77%）
```

### 修复后最终数据

```
/newapi/version            start: 557  dur: 324
/newapi/menu               start: 556  dur: 158
/newapi/overview/alas      start: 557  dur: 156
/newapi/dashboard/alas     start: 558  dur: 154
/newapi/instances          start: 556  dur: 147
/newapi/header             start: 556  dur: 146
```
对比修复前：`1530/1532/1530/1525/1416` → 全部降到 **150-324ms**。

## 3. 首屏时间（FCP）

| | 老 UI | 新 UI |
|---|---|---|
| TTFB | 51-57ms | 39ms |
| DOMInteractive | 95-114ms | 64ms |
| FCP | 140-172ms | 528-652ms |
| DOMComplete | 426-446ms | 374-446ms |

**新 UI 的 FCP 更慢 400ms 左右，原因已定位**：老 UI 是服务端直出 HTML，浏览器拿到就画；
新 UI 是 SPA，必须等 JS 下载 + 解析 + React 首次挂载才能画。这是 SPA 的固有代价。

**缓解方向**（未做，列为待办）：
1. `index.html` 里内联骨架屏（老 UI 是 `#alas-page-skeleton` 同一思路），FCP 可降到 ~100ms
2. `/newapi/header`、`/newapi/menu` 等首屏关键数据改为内联进 index.html，省 2 个 RTT
3. echarts（1.13MB）已经是动态 import、不在首屏关键路径上

**但请注意**：FCP 只影响「第一眼看到东西」的观感，不影响「能用」。
从「点了就响应」的角度看，新 UI 反而更好：**0 长任务**，主线程从不阻塞。

## 3b. 首屏 FCP 优化（本轮已做完）

第一版新 UI 的 FCP 是 643ms，老 UI 只有 140-172ms——这个差距会让用户第一感觉「新 UI 更卡」。

### 优化 1：内联首屏骨架（643ms → 360ms）

`frontend/index.html` 里内联 CSS + HTML 骨架，浏览器解析到文档就画，不等 JS。

**踩到的坑**：第一版骨架全是纯色块，FCP **完全没变**。
原因：FCP（First Contentful Paint）只统计「文本节点或图像」的首次绘制，
纯色 div 不算 contentful paint。加了一行真实文字 `正在加载 AzurPilot…` 后才生效。

骨架结构按真实壳层摆位（左侧 264px 窄栏 + 内容区卡片），
用 `body:has(#root > div)` 纯 CSS 判断 React 已挂载并自动退场，无需 JS。

### 优化 2：12 个渲染阻塞 CSS 合并成 1 个（360ms → 219ms）

老 UI 需要 12 个 CSS（3 个 PyWebIO 基础层 + 9 个 alas 层），全是渲染阻塞资源，
浏览器要全部下完才画第一帧。

新增 `GET /newapi/bundle.css`：服务端按原 **完全相同的顺序** 拼接成 397KB 单文件
（`module/webapi/data.py:_BUNDLE_CSS_FILES`），带 mtime 缓存。
主题层（light/dark/advanced-material/transparent）仍单独由 `/newapi/theme-css` 动态注入，
因为它要跟随 `deploy.yaml` 的 `Theme` 变化。

**验证样式没坏**（合并顺序错了会导致层叠变化）：
```
bundle.css          rules: 2458
overview grid       320px 1283.47px       ← 与合并前完全一致
dashboard columns   178.875px × 7         ← 一致
aside width         71.8646px             ← 一致
log fontFamily      "JetBrains Mono NL"   ← 一致
btn-on              rgb(78,76,151) / 100px ← 一致
```

### FCP 最终结果

| 阶段 | 新 UI FCP | 老 UI FCP |
|---|---|---|
| 初版（无骨架） | 643 ms | 140-172 ms |
| + 内联骨架 | 360 ms | — |
| + CSS 合并 | **219 ms** | 140-172 ms |

**从慢 4.6 倍收窄到慢 1.3 倍**，同一量级。剩余差距是 SPA 本质代价（必须等 JS 执行）。

## 4. 长跑稳定性（用户最关心的「运行久了会卡」）

新 UI 的日志面板做了三重防护，与老 UI 同一套语义：
1. **增量下游**：`?since=<cursor>` 只取新增条目，不重传历史
2. **分块裁剪**：`display:contents` 的 `.alas-log-chunk`，超过 200 块整块删除
   （逐节点删会残留裸文本节点，老 UI 踩过 3.3GB 内存的坑，注释记录在 `widgets.py`）
3. **可见性暂停**：`document.hidden` 时不轮询

服务端侧同样复用老 UI 的 `RichLog._html_cache`（LRU 2048，对象同一性键），
相同 renderable 不重复渲染。

### 长跑实测（5 分钟 / 每 15s 采样 / 各 20 个样本）

```
                       老 UI          新 UI
JS 堆 首 → 末        7.0 → 11.0 MB    2.1 → 2.1 MB
堆增长               +4.0 MB !!        0 MB
DOM 节点 首 → 末     1635 → 5343      1616 → 1498
DOM 增长             +3708 !!          -118
日志块数             10 → 177          1 → 19
日志节点数           899 → 4607        1158 → 1040
日志 HTML 体积       74 → 365 KB       86 → 75 KB
```

**这是最有说服力的一组数据**：

- **老 UI 在 5 分钟内 DOM 节点从 1635 涨到 5343（+227%）**，日志 HTML 从 74KB 涨到 365KB，
  堆内存从 7MB 涨到 11MB。这就是「运行久了会卡、会爆内存」的机制。
  老 UI 的 `dom_max_chunks = 200` 上限没生效到 200 块（才 177 块），但节点数已经翻三倍。
- **新 UI 5 分钟 DOM 节点 1616 → 1498（不升反降）**，堆内存 2.1 → 2.1 MB **完全没涨**，
  日志块数 1 → 19（远低于 200 上限），HTML 体积 86 → 75KB 稳定。
  说明分块裁剪 + 增量游标 + 缓存复用这套机制真正起作用了。

按这个斜率推算，老 UI 挂 7×24h 会持续膨胀，新 UI 是平台期。

## 5. 结论

| 核心诉求 | 达成情况 |
|---|---|
| 加载更快 | 传输量 -22%、JS -58%、请求数 -37%；但 FCP 慢 400ms（SPA 固有代价，有缓解方案） |
| 占用更低 | JS 堆 **-75%**（11MB → 2.4MB）、DOM 节点 -19% |
| 反应速度更快 | 接口最慢 1531ms → 324ms；0 长任务 |
| 运行久了不卡 | 三重日志防护 + 分块裁剪，与服务端缓存复用；长跑数据待补 |

**诚实的结论**：省内存和接口响应是实打实的（-75% / -79%），
「打开页面第一眼看到」新 UI 反而慢 400ms——这个必须后续用骨架屏补上，
否则用户会觉得「新 UI 更卡」。
