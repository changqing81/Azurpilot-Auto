# wess09/dev React 前端工程解剖（可借鉴/不可借鉴清单）

> 来源：侦查 agent 报告（2026-11-15）。基线 wess09/dev = b9294e7fe（2026-11-12）。
> 用途：module/webapi 与 frontend 的工程参考。**只借鉴工程层，视觉/产品风格一律不用。**

## 0. 总体架构一句话
wess09 把 PyWebIO 整体替换为：React 19 + Vite(rolldown) 前端（frontend/ 约 260 文件）+ Starlette 纯 WS-RPC 后端（module/api/ 恰 33 文件）。gui.py 不再挂 PyWebIO，直接 uvicorn 拉起 `module.api.app:create_app`（wess09/dev gui.py:468,488），启动前 ensure_frontend() 自动构建前端（gui.py:376-377、1019-1033，含 EXIT_FRONTEND_BUILD_FAILURE=73）。

## 1. 目录组织与路由
- 分层：src/pages/（25 个页面）、src/app/（外壳与全局态：App/context/theme/layout + ~30 个 prefs 小模块）、src/components/（通用 + ui/ shadcn 风格基件 + official-ui//task-ui//settings-ui/ 域内变体）、src/api/（客户端层）；另有按特性聚类目录：scheduler/（@xyflow）、mind/、fleet-tech/、remote/、resources/、config/（EditQueue）。
- 路由：react-router-dom 7 的 createHashRouter（frontend/src/main.tsx）——hash 路由配合 base:'./' 相对构建，适配 /<peer_id>/ 隧道反代。两级壳：/ 全局壳与 /i/:instance 实例壳，页面全部 lazy(() => import()) 代码分割。
- 亮点：路由表内大量 <Navigate> 重定向把老 URL 映射到新页面——保书签保习惯，逐页替换值得照抄此手法。
- 双级错误兜底：class ErrorBoundary + router errorElement 指向同一 ErrorPage。

## 2. API 客户端层（frontend/src/api/）
- 全站唯一 API 通道是 WebSocket /api/v1/ws 的 JSON-RPC 风格信封 {v:1,type:'request',id,method,params}（api/socket.ts request()）；REST 只剩 /healthz 与静态资源。
- 双通道：control + data 两条 WS，用 ?channel= 区分（api/client.ts）。22 个大流量只读方法走 data 连接，写操作走 control；数据连接断开独立恢复、读请求自动重试一次。
- RpcSocket 重连：指数退避 800ms×2^n 封顶 15s + 0–400ms 随机抖动、15s 心跳 ping、45s 无报文判死、请求 45s 超时（api/socket.ts）。
- 分片：>64KiB 的 JSON 自动改二进制分帧（每帧 1 字节头=是否末片），客户端重组（api/socket.ts；服务端 ws_transport.py:4-7：FRAME 64KiB / REQ 8MiB / RESP 64MiB）。
- 类型定义：generated.ts 由 dev_tools/export_api_schema.py 生成（头注"请勿手动编辑"）；client.ts request<M>() 泛型分发；契约快照 contract.json + ajv 供测试比对；types.ts 手写业务模型。

## 3. 构建配置
- vite.config.ts：base:'./'（远程反代下绝对 /assets 会 404）；dev 代理 /api(ws:true) → AZURPILOT_BACKEND（默认 127.0.0.1:22267）；minify oxc + cssMinify lightningcss；react-runtime 单组代码分割。
- package.json：react 19.3、vite 8.3.6（rolldown）、typescript 7.0.2、tailwindcss 4.3.5、react-router-dom 7、echarts 6.1、@codemirror/*、marked+dompurify+katex+highlight.js、lottie-web、radix-ui 全家桶、lucide-react；engines node>=22.12。测试：vitest + testing-library + Playwright（30+ e2e）+ 自研 mock/server.mjs。
- tsconfig：strict + noUnusedLocals/Parameters + moduleResolution Bundler + ES2022；build = tsc -b && vite build。
- 产物：frontend/dist；后端 Mount('/assets') + SPA 回退 index.html 且 no-cache。

## 4. 状态管理与 i18n
- 无 Redux/Zustand。全局态 = 单个 AppContext：instances/schema/language/theme/toast；连接状态用 useSyncExternalStore 订阅。
- 防重渲染纪律：provider value useMemo 固定身份；instances 事件先 JSON.stringify 比对、没变留旧引用。
- i18n 自研零依赖：4 语言，i18n.ts 主包 + 分域文件 spread 合并；{param} 插值；i18n.test.ts 强制四语键完备。
- 任务/参数文案不进前端包：schema.get 按 language 返回 menu+args+translations，前端 t() 按点分路径查。

## 5. module/api/ 后端——挂载姿势供 module/webapi 参考
- 应用工厂：app.py create_app() 返回纯 Starlette 路由表：Route('/healthz')、WebSocketRoute('/api/v1/ws')、launcher/notify 子路由、Mount('/assets')、Mount('/mcp')、最后 Mount('/', FrontendFiles(dist)) SPA 回退；lifespan 管启停。
- uvicorn 以字符串工厂拉起：uvicorn.Config("module.api.app:create_app")——gui.py 照此姿势可与 PyWebIO 并存或切换。
- 路由表：Router.methods 显式 dict（约 90 个 RPC 方法），Method(params, handler, mutates, local_only, data_channel) dataclass；dispatch 统一处理 METHOD_NOT_FOUND/参数校验/DEMO 只读/local_only。
- 鉴权链：WS 升级前校验 Origin==Host；连接限流 32/16；本机直连免密；auth.login 用 secrets.compare_digest + 失败锁定；Method.local_only 标记；背景上传独立 token。
- 分片协议：FRAME_BYTES=64KiB、REQ 8MiB、RESP 64MiB；每 64KiB 帧前置 1 字节末片标志；拒混合类型/不完整分片（close 1002）；首 event('session') 下发 {authRequired, channel, protocolVersion}。
- 会话工程：reader/writer 双协程 + 按 channel 启动 producer（1–2s 定时采样 + fingerprint 去重推送；log/preview 事件驱动）；发送队列 maxsize=32 满即 1013 断开慢客户端（背压）；请求限速 30/s；request_id 防重放；业务 asyncio.to_thread + Semaphore(8/4)；pydantic strict + extra='forbid'。
- 静态服务：static.py FrontendFiles（SPA 回退 + 点开头文件 404 + no-cache）；WEB_MIME_TYPES 写死 .js/.css MIME 防 Windows 注册表把 .js 关联成 text/plain 导致整页白屏——Windows 部署必踩坑，直接照抄。

## 6. 清单
### A. 值得直接借鉴（工程层）
1. 双通道 WS RPC + 生成式 TS 类型三件套（generated.ts + export_api_schema.py + contract.json/ajv 测试）。
2. RpcSocket 全套传输细节：退避+抖动重连、心跳/判死、请求超时、64KiB 分帧与会话协商。
3. Vite 策略：base:'./'、dev 代理 ws:true、压缩、单组代码分割。
4. createHashRouter + lazy 分割 + 老路径 Navigate 映射表。
5. 轻状态组合：Context + useSyncExternalStore + useMemo 固定身份 + 快照比对抑制重渲染；偏好散落 localStorage 小模块。
6. i18n 分域文件 + 键完备性测试 + schema.translations 承载任务文案。
7. 后端骨架：create_app 工厂 + 显式 Method 注册表 + pydantic strict + to_thread + Semaphore + 背压队列 + Origin 校验/限流/防重放。
8. 静态服务三细节：SPA 回退、WEB_MIME_TYPES 防 Windows 注册表污染、物品图标静态 Mount。
9. QA 工程：mock server + Playwright e2e + vitest 单测 + 契约测试。
10. 错误纪律：错误码枚举、敏感输入不回显、双级 ErrorPage 兜底。

### B. 不借鉴（视觉/交互/产品风格）
1. 「旧版外壳」复刻皮肤、玻璃卡片+错峰入场动效、MiSans/JetBrains Mono 字体。
2. 它的主题系统（minimal/terminal/自定义调色板/动效强度旋钮）——我们用 assets/gui 原主题。
3. 产品扩展页：StockExchange、MindCalculator、Fleet* 全家桶、MaterialInspector。
4. scheduler/（@xyflow 节点图编程器）。
5. index.html 内嵌 Clarity 遥测脚本——隐私口径不放。
6. 官网链接/robots/Demo 模式等品牌化内容。

### C. 风险提示
- 版本激进：Vite 8(rolldown) + TS 7 + Node>=22.12；本项目选型保守一档（Vite 7 + TS 5.x，已按此落地脚手架）。
- 依赖冗余：radix-ui 聚合包与细包并存，借鉴时择一。
- 同源 GPL-3.0：借鉴工程模式无碍；若直接复制实现文件，保留来源与许可声明。
