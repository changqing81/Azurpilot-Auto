# 新 UI 界面版本切换（legacy / new）实现说明

> 状态：已实现并端到端实测通过（2026-11-16，new-ui 分支 ba6058aed）
> 触发需求：长卿提问「老UI和新UI可以切换？可以让用户自己切换新的UI还是老UI？」

## 1. 用户视角怎么切

**位置**：设置页 → WebUI 配置 → **界面版本**
（老 UI：设置页 → 部署设置 → WebUI 配置 → 界面版本；新 UI：#/settings/{实例} 同一位置）

```
界面版本   [ 老 UI ▾ ]
           老 UI      ← PyWebIO 原界面
           新 UI      ← React 界面（更快、占用更低）
```

**流程**：
1. 下拉改成「新 UI」→ 点「保存」→ 提示「已保存」
2. 页面顶部出现提示卡：「界面版本已改为「新 UI」，界面路由在启动时固定，需要重启 WebUI 才生效」
3. 点「**立即重启生效**」→ 8 秒后页面自动刷新到新界面

**注意**：不重启不生效（路由在 Starlette 应用创建时固定，热切换需要重建整个 ASGI 应用，成本与风险都不划算）。

## 2. 两种模式的实际差异

| 项目 | `legacy` | `new` |
|---|---|---|
| `/` | 老 UI（PyWebIO） | **新 UI（React）** |
| `/` 的老 UI 页面路由 | 全部注册 | **不注册（老 UI 关闭）** |
| `/new/` | 新 UI（并存，可对照） | 新 UI |
| `/manage` | 老 UI | 404 |
| `/api/*` | 保留 | 保留（新 UI 数据源） |
| `/newapi/*` | 挂载 | 挂载 |
| `/pywebio_static`、`/static` | 保留 | 保留（新 UI 靠这些样式表还原外观） |

## 3. 实现链路

### 配置层
`deploy/config.py`：`ConfigModel.UI: str = "legacy"`
`config/deploy.template.yaml`：`Webui.UI: legacy`（带双语文档注释）

### 表单层
`module/webui/deploy_settings.py`：
- `DEPLOY_GROUPS` 的 **Webui** 分组新增
  `DeployField("UI", "select", (("legacy", "老 UI"), ("new", "新 UI")))`
- `_parse_value` 的 select 分支支持「(值, 显示名) 二元组」写法
  （**这是关键修复**：原来 `value not in field.options` 拿字符串与元组比较永远失败，
   导致界面版本永远保存失败）

### 挂载层
`module/webui/app.py:677-706`：
```python
_ui_mode = str(getattr(State.deploy_config, "UI", None) or "legacy").strip().lower()
_mount_root = _ui_mode == "new" and os.path.isdir(_FRONTEND_DIST)
if _mount_root:
    # 移除老 UI 的 "/" 与 "/manage" 页面路由，保留 /api 等数据接口
    application.routes[:] = [r for r in application.routes
                             if getattr(r, "path", None) not in ("/", "/manage")]
mount_webapi(application, mount_root=_mount_root)
```

### 静态资源
`frontend/vite.config.ts`：`base: './'`
（原为 `'/new/'`，独占 `/` 时所有资源 404 白屏。改相对路径后同一份 dist
既能挂 `/new/` 也能挂 `/`）

### 重启接口
`module/webapi/data.py:restart_webui()` + `app.py` 的 `POST /newapi/restart`：
复用 `module/webui/app_developer_tools.py:request_webui_restart()` ——
先把运行中实例名写入 `config/reloadalas`，再置 `State.restart_event` 通知父进程重启并恢复实例。
**与老 UI 的「重启」按钮是完全同一条链路**，不存在两套重启逻辑。

### 前端
`frontend/src/pages/settings/index.tsx`：
- `optionOf()` 兼容三种 options 形态：`[value,label]` 二元组 / `{value,label}` 对象 / 纯字符串
- 保存成功后若检测到 `dirty.UI`，渲染顶部提示卡 + 「立即重启生效」按钮
- 点按钮 → `POST /newapi/restart` → `success=true` 则 8s 后 `location.reload()`

## 4. 实测证据（2026-11-16）

| 步骤 | 结果 |
|---|---|
| `GET /api/deploy/settings` 含 UI 字段 | ✅ `{"key":"UI","type":"select","value":"legacy","options":[["legacy","老 UI"],["new","新 UI"]]}` |
| 新 UI 设置页渲染下拉 | ✅ `SELECT value="legacy" options=["legacy=老 UI","new=新 UI"]` |
| 改成 new → 保存 | ✅ 提示「已保存」，deploy.yaml 实际写入 `UI: new` |
| 保存后出现重启引导 | ✅ 「界面版本已改为「新 UI」」+「立即重启生效」按钮 |
| `POST /newapi/restart` | ✅ `{"success":true,"message":"正在重启 WebUI"}` |
| 重启后自动恢复 | ✅ 进程重建，`/newapi/healthz` 返回 `{"ok":true}` |
| `UI: new` 时 `/` 返回新 UI | ✅ 含 `#pywebio-scope-ROOT` 的完整 DOM 链 |
| `UI: new` 时 `/manage` | ✅ 404（老 UI 已关闭） |
| `UI: new` 时静态资源 | ✅ `./assets/index-*.js` → 200 `text/javascript` |
| `UI: legacy`（默认） | ✅ `/` 仍是老 UI，`/new/` 可访问新 UI |

截图：`.workbuddy/perf/out/newui/settings_ui_v2.png`（设置页界面版本下拉）、
`switch_test.png`（保存后重启引导卡）

## 5. 已知限制

1. **必须重启才生效**——路由表在应用创建时固定。做热切换需要在运行中替换 ASGI app，
   风险远大于收益，不做。
2. **老 UI 关闭后无法从新 UI 内部返回老 UI 的调试工具页**——新 UI 尚未实现「开发者工具」页
   （`/tool/:name/:task` 有骨架但未对齐老 UI）。这是新 UI 功能补齐的待办项，与切换机制无关。
3. **远控场景**下切换界面版本会断开当前远控会话，重连后进入新界面（与老 UI 重启行为一致）。
