# 老 UI 主页与页面壳解剖（复刻对照表）

> 来源：侦查 agent 报告（2026-11-15，分支 new-ui，行号为当时工作树）。
> 用途：新前端 1:1 复刻的结构依据。改动老 UI 后行号会漂移，以函数名/约定为准。

## 0. 架构总述
"主页"分两层：HomePage(app_home.py，壁纸/公告/语言主题) 与 实例总览(app_overview+app_dashboard，任务卡片/仪表盘/日志)。壳层 = base.py Frame + app_shell.py(侧栏) + app_instances.py(mount_shell)。数据全部走 PyWebIO WebSocket(run_js/task_handler)，REST API 只有约 15 个（api.py:2143-2192）——新前端复刻需补 REST/SSE 端点。

## 1. HomePage 区块清单（app_home.py）
- 背景墙纸: show_home:395-404 注入 --alas-apple-bg-image CSS 变量；init_wallpaper:521-563 后台线程不阻塞首渲；自定义背景 wallpapers/custom_background.* 经 _encode_custom_background:171-187 转 data URI（进程级缓存 mtime+size）；视频>8MB 走 /api/custom_background_video(api.py:79-96, FileResponse 带 Range)
- 图源竞赛: _WALLPAPER_RACE_JS:255-354，服务端收集候选 URL→run_js 交浏览器并发下载，最快者胜(window.__alasWallpaperWinner)，30s 超时；候选来自 wallpapers/wallpaper_sources.json(_load_sources:820-846，内置 4 源 _BUILTIN_SOURCES:88-125)；媒体偏好 _load_media_preference:1342
- 纯背景模式圆点: _WALLPAPER_TOGGLE_JS:199-245，#alas-wallpaper-toggle 挂 body 顶层；body.alas-wallpaper-mode 隐藏 content/header/aside/menu/statistics-content
- 语言选择 4 按钮: 407-418（lang.set_language 后整页重渲 show_home）
- 主题选择 5 按钮: 420-441（set_theme 就地换肤 swap_theme_css，app_shell.py:63-86；切主题顺带重抽背景 _reroll_wallpaper_on_theme_change:1598）
- 下载当前背景图: 442-453 / download_wallpaper:1640
- 背景管理 4 按钮: 455-484（上传/随机/管理图源/媒体类型，弹窗 938-1315）
- alas-home-marker 占位 div: 485；免责声明 markdown: 489-494
- 公告弹窗: 非主页 DOM，由 run() 的 announcement_checker(2078-2124, delay=5, group=slow)驱动；数据 ApiClient.get_announcement(module/base/api_client.py:58，jsdelivr→GitHub 三源回退:24-37，周期 300s:44)；配图本地化后走 /api/announcement/image/{name}(api.py:2145)；推送 window.alasShowAnnouncement:1838-1840（实现在 assets/gui/js/alas-utils.js:1520，localStorage 去重:1515）
- Clarity 提示 toast: 2148-2154；延迟资源加载 _load_deferred_client_assets:1878-1907（requestIdleCallback 注入 Clarity+manifest+alas-utils.js）
- OOBE 分流: run:1922-1927 is_oobe_needed→OOBEWizard
主页无周期刷新任务；会话级任务注册在 run:2072-2075（state_switch 2s / set_aside_status 2s / visibility 15s / update_switch 1s）。

## 1b. 总览页区块（任务卡片/仪表盘实际所在，app_overview.py:220-460）
- 维护横幅: put_html 静态占位:227，后台 run_js 增量更新(app_dashboard.py:106-140，数据 log/<name>.maintenance.json)
- 任务队列: scope schedulers > scheduler-bar/stat-bar/running/pending/waiting(app_overview.py:231-285)；数据 alas_config.get_next_task()，15s 周期 alas_update_overview_task(app_dashboard.py:169-218，mtime 短路+快照 diff)；每条任务 scope=overview-task_{command}(app_dashboard.py:222)
- 仪表盘: scope dashboard(app_overview.py:410)；数据 LogRes(module/log_res/log_res.py:36) 读 dashboard.json，资源清单=dashboard.yaml:8-20(Oil/Coin/Gem/Pt/Cube/ActionPoint/YellowCoin/PurpleCoin/Core/Medal/Merit/GuildCoin)；15s 周期 alas_update_dashboard(app_dashboard.py:592-612)；默认只显 4 项 Oil/Coin/Gem/Pt:608；卡片 scope=get_dashboard_scope_id/get_group_scope_id(webui/dashboard_utils.py:66,86,142,155)；增量策略 last_display_time diff:523-530
- 日志: RichLog scope log-container>log(app_overview.py:417-419)，0.5s 周期 put_log:460；页脚版本 --version CSS var:415-418
- 截图预览按钮: window.alasToggleLivePreview:399-404(alas-utils.js:1404)→/ws/live_screenshot
- 启停/滚动/仪表盘开关: BinarySwitchButton 1s 轮询:443-446

## 2. 页面壳 DOM/scope 对照（新前端需同构输出）
base.py:_show:506-529，固定树：ROOT > #pywebio-scope-header（Icon.ALAS html --header-icon-- + Gui.Status.Prefix 文本 --header-text-- + scope header_status + scope header_title）> #pywebio-scope-contents > aside / menu / content / statistics-content(初始 display:none)
侧栏: set_aside(app_shell.py:216-228) 在 aside 内建 aside_home/aside_instance/aside_manage 三 scope；实例按钮 scope alas-instance-{index}(app_instances.py:300-302)，图标随 ProcessManager.state 切换(273-286)
导航高亮约定(关键！): 按钮外层 div 的 style 属性含 --menu-{name}-- / --aside-{name}-- 标记（app_developer_menu.py:26 等），active_button 用 div[style*=...] > button 选择器加 .btn-menu-active/.btn-aside-active(base.py:556-562)
折叠约定: #pywebio-scope-menu 加 .container-menu-collapsed，content 加 .container-content-collapsed(base.py:285-289,537-553)
骨架屏: #alas-page-skeleton 由 init_menu JS 注入(base.py:318-389)，HomePage 与 Stat 不注入(base.py:322)；MutationObserver 撤骨架:364-387；统计页另有 st-skeleton
页面持久化: localStorage alas_last_page(base.py:190,403-411) + aside 键；page_renderer 恢复映射(base.py:433-471)
页眉状态: set_status(app_shell.py:318-359) 渲染 put_loading 到 header_status，1=running/2=inactive/3=warning/4=updating
渲染锁: 所有渲染入口 @render_locked 串行(base.py:118-152)，交互优先于后台刷新——新前端若保留轮询需等效节流。

## 3. 主题加载（新前端 <link> 按同规则选文件）
- HTML 预载(首屏骨架着色): app.py:337-343 _initial_style_names = (alas, entry-alas, *主题层)；app.py:535-541 initial_css_files = INITIAL_WEBUI_CSS(alas.css, app.py:124) + 其余按 _versioned_static_asset(sha256 前 12 位作 ?v=, app.py:118-121)
- WS 全量注入(最终保障): utils.py:745-783 webui_style_names = [alas, alas-mobile|alas-pc, entry-alas, statistics-alas, stat-delta-alas, meowfficer-score-alas, meow-loot-alas, fleet-tech-alas, fleet-management-alas] + 主题层
- 主题层映射(utils.py:725-737): default/light→light-alas; dark→dark-alas; advanced_material→advanced-material-alas; dark_advanced_material→advanced-material-alas+dark-advanced-material-overrides-alas; transparent→advanced-material-alas+transparent-alas
- 全部 15 个 CSS 在 assets/gui/css/（清单已实测核对，与 webui_style_names 一致）
- 主题切换: swap_theme_css(utils.py:589) 只换主题层，删除+注入同一条消息，#alas-custom-bg-style 保序 head 末尾(app_shell.py:63-86 三条硬约束注释)；切主题后派发 alas-theme-change 事件(app_shell.py:397-399)
- CSS 热更新: /api/css-fingerprint(api.py:2185) + _inject_css_watcher 轮询(utils.py:690-720)

## 4. 主页 API 面需求（现成=api.py:2143-2192 已有）
现成: /api/css-fingerprint:2185、/api/announcement/image/{name}:2145、/api/custom_background_video:2146、static/assets 挂载(app.py:568-572)、/api/cl1_stats:2144、/api/ap_timeline:2147、/ws/live_screenshot:2190、/api/log/*:2166-2177、/api/data/export*:2180-2183、/api/notify_stream:2149、/api/launcher/*:2150-2153
需新增(目前仅走 WS 私有通道): ①公告数据端点(等价 ApiClient.get_announcement+announce push) ②壁纸候选列表+背景模式/图源/媒体偏好读写 ③上传自定义背景 ④下载当前背景 ⑤实例列表+运行状态轮询(等价 set_aside_status/state_switch) ⑥任务队列+仪表盘数据(等价 alas_update_overview_task/alas_update_dashboard) ⑦日志实时流(REST 只有导出，RichLog 靠 WS) ⑧语言/主题设置写入(现写 deploy config) ⑨更新检查(updater.state) ⑩可见性上报(可前端本地化) ⑪登录校验(app.py:626-629)

## 5. is_mobile 差异点
- 判定: base.py:165 info.user_agent.is_mobile（会话级一次定死）
- CSS: utils.py:765 alas-mobile.css vs alas-pc.css；app.py:613-616 会话建立时按 UA 注入
- 手机主页无菜单入口: alas-mobile.css 把折叠类渲染为 display:none，ui_develop:78-84 二次点击侧栏图标才 expand_menu
- 总览/工具页布局: app_overview.py:478-501 手机=平铺 scopes(scheduler-bar/stat-bar/groups/log-bar/log)，桌面=_daemon grid(_daemon_upper+groups+log+两侧 put_none)；手机专属 stat-bar 占位:519-528；无日志时手机改 grid-template-rows:608-614
- 仪表盘宽度分支已废弃: app_dashboard.py:534-538 仅注释

## 6. 复刻注意（给新前端的红线）
① style 标记选择器是主题 CSS 的高亮依赖，React 组件必须输出同构 style 属性+按钮类名；② statistics-content 是常驻隐藏 scope（统计页复用它切换显示，base.py:473-503），新前端要么保留同 id 要么改造统计页；③ 透明主题依赖 html 背景兜底+body 透明(app_home.py:284-293)，视频背景 z-index:-1；④ 公告/更新通知强依赖 alas-utils.js 的 window.alas* 全局函数，新前端可继续引用该文件或等价实现 5 个入口(alasShowAnnouncement/alasHasBeenShown/alasToggleLivePreview/alasToggleWallpaper/alasWallpaperRace)；⑤ 竞赛壁纸逻辑整体在前端 JS(_WALLPAPER_RACE_JS)，可直接移植为 TS 模块。
