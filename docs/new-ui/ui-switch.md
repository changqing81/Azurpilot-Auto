# 新 UI 界面版本切换（legacy / new）实现说明

> 状态：已实现并端到端实测通过（2026-11-16，new-ui 分支 b2b373342）
> 触发需求：长卿提问「老UI和新UI可以切换？可以让用户自己切换新的UI还是老UI？」
> 后续需求：长卿「老UI这里也得搞一个像新UI的切换入口 点击就切换」

## 1. 用户视角怎么切

**两边入口一模一样，都是「点一下就完成」**：

- 新 UI：`#/settings/{实例}` → 顶部「界面版本」卡片
- 老 UI：侧栏「设置」→ 页面最上方「界面版本」

```
界面版本
当前界面版本
点一下即切换并自动重启 AzurPilot（实例会自动保存并恢复）。
                                          [切换到老 UI] [切换到新 UI] [重启 AzurPilot]
新 UI（React）
```

**流程（一次点击，不用点第二次）**：
1. 点「切换到老 UI / 切换到新 UI」
2. 写 `deploy.yaml` 的 `Webui.UI`
3. **自动** `POST /newapi/restart` 下发重启
4. 前端每 1.2s 轮询 `GET /newapi/ui`，直到 `ui` 变成目标值（超时 180s）
5. `location.href = '/'` 落地到对应界面

不重启不生效（路由在 Starlette 应用创建时固定）。

## 2. 两种模式的实际差异

| 项目 | `legacy` | `new` |
|---|---|---|
| `/` | 老 UI（PyWebIO） | **新 UI（React）** |
| `/` 的老 UI 页面路由 | 全部注册 | **不注册（老 UI 关闭）** |
| `/new/` | 新 UI（并存，可对照） | 新 UI |
| `/api/*` | 保留 | 保留（新 UI 数据源） |
| `/newapi/*` | 挂载 | 挂载 |
| `/pywebio_static`、`/static` | 保留 | 保留（新 UI 靠这些样式表还原外观） |

## 3. 实现链路

### 配置层
`deploy/config.py`：`ConfigModel.UI: str = "legacy"`
`deploy/template`：`Webui.UI: legacy`（带双语文档注释）
→ `config/deploy.template*.yaml` 全部由 `config_updater.generate_deploy_template()`
从 `deploy/template` 生成，**只改 config/ 下的文件会被覆盖掉**。

### 运行时状态（关键）
`module/webapi/app.py`：

```python
_RUNTIME_UI = {"mounted": "legacy", "dist": False}   # mount_webapi 时写入

GET /newapi/ui →
{
  "ui": "new",              # **进程实际在跑**的（重启才会变）
  "configured": "new",      # deploy.yaml 里写的
  "restart_required": false,# 两者不一致 = 已切换但还没重启
  "dist": true
}
```

**为什么不直接读 deploy.yaml**：切换流程是「写配置 → 重启 → 轮询直到新版本起来」。
如果 `ui` 读配置文件，写配置那一刻它就变成目标值，轮询第一次就"成功"并跳转，
此时服务还在重启——用户看到的仍是旧界面或连接被拒，判定为「点了没反应」。
实测踩过这个坑，所以运行时值和配置值必须分开。

### 旧后端兼容（降级不猜）
老版本后端的 `/newapi/ui` 只返回 `{"ui": ...}`，且那个值就是配置值。
新前端检测到缺 `configured` / `restart_required` 字段时，
当前界面显示「重启后可见（当前后端版本较旧）」，不把配置值当运行时值展示。

### 挂载层
`module/webui/app.py`：
```python
_ui_mode = str(getattr(State.deploy_config, "UI", None) or "legacy").strip().lower()
_mount_root = _ui_mode == "new" and os.path.isdir(_FRONTEND_DIST)
if _mount_root:
    # 移除老 UI 的 "/" 入口，保留 /api 等数据接口
    application.routes[:] = [r for r in application.routes
                             if getattr(r, "path", None) != "/"]
mount_webapi(application, mount_root=_mount_root)
```

### 表单层
`module/webui/deploy_settings.py`：**UI 故意不放进 DEPLOY_FIELDS**。
它是「点一下就切换」的操作，不是普通下拉框；留在表单里会出现
"改了下拉没点保存 = 什么都没发生"的死角，而且改完还要用户自己再点重启。
两边设置页各有专用面板。

### 重启接口
`module/webapi/data.py:restart_webui()` + `POST /newapi/restart`：
复用 `module/webui/app_developer_tools.py:request_webui_restart()` ——
先把运行中实例名写入 `config/reloadalas`，再置 `State.restart_event` 通知父进程重启并恢复实例。
**与老 UI 的「重启」按钮是完全同一条链路**，不存在两套重启逻辑。

### 前端
- 新 UI `frontend/src/pages/settings/index.tsx`：`#pywebio-scope-group_ui1`
  → `.launcher-setting-panel > .launcher-setting-row`（老 UI 真实 DOM 契约；
  用普通 `cfg-group` 的 `1fr 13rem` 会把三个按钮挤成三行）
- 老 UI `module/webui/app_developer_settings.py`：`#ui-switch-panel`
  → 与老 UI 启动器面板同款结构，`run_js` 里做切换 + 轮询

## 4. 实测证据（2026-11-16）

| 步骤 | 结果 |
|---|---|
| `GET /newapi/ui`（22267 旧后端） | `{"ui":"legacy"}` —— 前端降级显示，不猜 |
| `GET /newapi/ui`（22480 新后端） | `{"ui":"new","configured":"new","restart_required":false,"dist":true}` |
| `POST /newapi/ui/switch {ui:legacy}`（不重启） | `{"ui":"new","configured":"legacy","restart_required":true}` ✅ 分开报告 |
| 新 UI 点「切换到老 UI」 | 6.2s 后自动落到老 UI（`oldUI=true`） |
| 老 UI 点「切换到新 UI」 | 7.7s 后自动落到新 UI（`newUI=true`） |
| 老 UI 面板状态显示 | 「当前界面：老 UI（PyWebIO）」+ 按钮禁用态正确 |
| 反向切换后再切回 | ✅ 双向都通 |

截图：
- `.workbuddy/perf/out/newui/settings_final.png`（新 UI 设置页「界面版本」卡片）
- `.workbuddy/perf/out/newui/settings_old_panel.png`（老 UI 设置页同一功能）

## 5. 踩过的坑（给后续维护者）

1. **`t()` 内部走 `str.format()`**
   翻译文案里出现任何花括号（例如想用 `{ui}` 占位）都会 `KeyError`。
   多语言文案一律不带占位符，用字符串拼接。

2. **`poor_yaml_write` 用正则 `f'{key}:.*?\n'` 替换**
   模板里没有的键写不进 deploy.yaml；模板里有的键每次写都会被模板值覆盖。
   UI 字段必须加到 `deploy/template` 源头。

3. **`run_js` 里的 f-string 双花括号**
   `f"""...{{...}}..."""` 才能输出 JS 的 `{...}`。漏一层就会变成 Python 求值，
   报 `NameError: name 'ui' is not defined`。

4. **HashRouter 路由带 #**
   CDP 探针要访问 `http://host/#/settings/alas`，不是 `/settings/alas`。

## 6. 已知限制

1. **必须重启才生效**——路由表在应用创建时固定。做热切换需要在运行中替换 ASGI app，
   风险远大于收益，不做。
2. **远控场景**下切换界面版本会断开当前远控会话，重连后进入新界面（与老 UI 重启行为一致）。
