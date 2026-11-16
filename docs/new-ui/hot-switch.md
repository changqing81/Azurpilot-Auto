# 新老 UI 热切换实现与验收

> 时间：2026-11-16（new-ui 分支 8649b0e8c）
> 需求来源：长卿「加个不用重启的热切换，并让 / 和 /new/ 同时可用，随便切来切去」

## 一、实现

核心：`module/webui/hot_switch.py` —— 用**容器 ASGI app** 包住原 PyWebIO application，
只接管 `/` 这一个路径：

```
┌─────────────────────────────────────────────┐
│ HotSwitchApp（容器）                         │
│  path == "/" 且 UI=new  → 返回新 UI index.html│
│  其余一切                → 原样透传给 PyWebIO │
└─────────────────────────────────────────────┘
```

### 为什么用容器 ASGI app 而不是中间件

老 UI 的 `/` 要处理 **WebSocket 升级**（PyWebIO 全程走 WS）。
中间件只能改 http 响应，**没法把 ws 交给下游** —— 用它等于把老 UI 打死。

容器里 `self.inner(scope, receive, send)` 原样透传，
WS / lifespan / http 全都按原逻辑走，零副作用。

### 为什么现在能热切换

| | 上一版 | 现在 |
|---|---|---|
| 时机 | 启动时把老 UI 路由**摘掉** | 路由表**不动** |
| 生效 | 必须重启 GUI | 下一次请求就生效 |
| 机制 | 改 `application.routes` | dispatch 时读 `State.deploy_config.UI` |

配置读盘按 `deploy.yaml` 的 **mtime 缓存**：改完立即生效，也不浪费 IO
（首屏有 30+ 请求，每次都读盘 + 解析 YAML 太浪费）。

### 新增端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/newapi/ui` | 当前界面版本 |
| POST | `/newapi/ui/switch` | `{ui: "legacy"\|"new"}` 写入 deploy.yaml |

新 UI 设置页加了「切到老 UI / 保持新 UI」按钮，切换后 2 秒自动刷新。

## 二、验收（实测，未重启）

```
初始 /newapi/ui    -> {"ui":"legacy"}
初始 /             -> 老UI
切到 new           -> {"success":true,"ui":"new"}
切换后 /           -> 新UI        ← 未重启，700ms 后生效
/new/              -> 200 新UI   ← 同时可用
切回 legacy        -> {"success":true,"ui":"legacy"}
切回后 /           -> 老UI
```

### 老 UI 的 WebSocket 会话（最大风险点）

容器包住 PyWebIO 后，最关键的是 **PyWebIO 的 WS 会话不能被破坏**：

| 检查项 | 结果 |
|---|---|
| 登录页渲染 | ✓ 老 UI 登录页正常 |
| `#pywebio-scope-ROOT` | ✓ 存在 |
| 登录成功 | ✓ |
| 登录后侧栏按钮 | ✓ 4 个 |
| 页标题 | ✓ 「主页」 |

**WS 链路完全正常** —— 这是容器方案的直接收益（原样透传）。

## 三、顺带纠正一处错误认知

我原先以为老 UI 有 `/manage` 这个 URL，甚至在 `HotSwitchApp` 注释里写了
「`/manage` 永远老 UI」。**这是错的**：

PyWebIO 的路由表里**只有 `/`**（一条 `Route` + 一条 `WebSocketRoute`，
见 `pywebio/platform/fastapi.py:_webio_routes`）。
它用 `?app=<name>` 查询参数区分多个 application。

所以老 UI 的「管理」页**没有独立 URL** —— 它是侧栏按钮走 WS 内部回调
（`ui_manage`）进入的。`/manage` 从来就 404，与本次改动无关。

## 四、当前 URL 布局

| URL | legacy 模式 | new 模式 |
|---|---|---|
| `/` | 老 UI（PyWebIO） | 新 UI（React） |
| `/new/` | 新 UI | 新 UI |
| 其余 | 原样透传 | 原样透传 |

切换方式：
1. **新 UI 设置页** → 界面版本 → 「切到老 UI」/「保持新 UI」→ 自动刷新
2. **老 UI 设置页** → 部署设置 → WebUI 配置 → 界面版本 → 保存 → 刷新
3. **直接改 `deploy.yaml`** 的 `Webui.UI` → 刷新（mtime 感知）

三种方式都**不需要重启**。

## 五、仍需注意

- 切换只影响 `/` 的响应。已经在页面里的用户刷新才能看到新界面（这是浏览器行为，无法绕过）。
- 如果 `frontend/dist` 不存在（没 build），`/` 固定走老 UI，日志里会有 warning。
