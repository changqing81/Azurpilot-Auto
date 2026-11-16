# 新 UI 主题适配验收报告（5 个主题全测）

> 时间：2026-11-16（new-ui 分支 7bd15bcc8）
> 触发：长卿指出「不止文字，图标，表格都要符合主题，这都是老 UI 的坑」
> 方法：CDP 逐主题实测 computed style，与老 UI（22267）同项对照

## 一、上一版的严重问题（已修）

上一版「9 页全绿」的验收**是不完整的** —— 只在 default 与 advanced_material 下看过。
这次把 5 个主题全部实测后，发现 dark 主题**大面积深底深字**：

| 元素 | 上一版（dark） | 老 UI（dark） | 修复后 |
|---|---|---|---|
| body color | `rgb(33,37,41)` 深黑 ✗ | `rgb(211,211,211)` | `rgb(211,211,211)` ✓ |
| 「调度器」标题 | `rgb(36,41,46)` 深黑 ✗ | `rgb(211,211,211)` | `rgb(211,211,211)` ✓ |
| 任务名 `--arg-title--` | `rgb(36,41,46)` 深黑 ✗ | `rgb(211,211,211)` | `rgb(211,211,211)` ✓ |
| 页眉标题 | 深黑 ✗ | — | `rgb(211,211,211)` ✓ |

## 二、根因：CSS 加载顺序错了，且少了 4 个文件

对照老 UI 实测的 `document.styleSheets`（20 个），老 UI 的顺序是：

```
 1. markdown.min.css
 2. codemirror.min.css      ← 新 UI 之前漏了
 3. toastify.min.css        ← 漏了
 4. bs-theme/dark.min.css   ← 关键：会随主题在 default/dark 间切换
 5. bootstrap-select.min.css ← 漏了
 6. app.css
 7-15. alas / alas-pc / entry-alas / statistics / stat-delta /
       meowfficer-score / meow-loot / fleet-tech / fleet-management
 16. 主题层 dark-alas.css
```

而新 UI 之前是：

```
1. bs-theme/default.min.css  ← 固定在第一位且永远是 default
2. app.css
3. markdown.min.css
4-12. alas 9 层
13. 主题层
```

### 为什么顺序会决定文字颜色

`bs-theme/dark.min.css` 里有一条 `body{ color:#d3d3d3 !important }`，
而 `markdown.min.css` 里有 `.markdown-body{ color:#24292e }`。

新 UI 的 DOM 是 `body > … > #markdown-body.markdown-body > #pywebio-scope-ROOT`，
所有文字都继承自 `#markdown-body`。若 `.markdown-body{color:#24292e}` 最后生效，
整页文字就是深黑，压在 dark 主题的深色卡片（`#2b2d31`）上 → 看不清。

老 UI 之所以正常，是 `bs-theme/dark.min.css` 排在 markdown 之后并能改写它，
且后续 `app.css` 的 `.markdown-body{color:inherit}` 让它继承 body 的 `#d3d3d3`。

### 修法

拆回 **16 个独立 `<link>`**（顺序照抄老 UI），
`bs-theme` 的 href 由 `main.tsx` 按 `/newapi/theme-css` 返回的 theme 切换：

```ts
const bs = document.getElementById('alas-bs-theme')
if (bs) bs.href = '/pywebio_static/css/bs-theme/' + (theme === 'dark' ? 'dark' : 'default') + '.min.css'
```

> 注：之前的 `/newapi/bundle.css` 合并方案（为了减少首屏请求）已撤销 ——
> 用一个文件时无法让 bs-theme 参与「按主题切换 + 保持顺序」，
> 省下的几十毫秒远不值得外观出错。首屏优化改由内联骨架承担。

## 三、第二个根因：统计/配置页写死了颜色

新 UI 自研页面的 CSS 里写死了 6 个颜色值（`#4e4c97` 紫、`#eceefc`、`#d9d9e3` 等），
换主题后完全不变 → 深色主题下白底白字、浅色文字压浅底。

改为消费老 UI **现成的主题变量**：

| 用途 | 变量 |
|---|---|
| 主文字 | `--alas-entry-text` |
| 次要文字 | `--alas-entry-muted` |
| 边框 / 分隔线 | `--alas-entry-border` / `-divider` |
| 强调色 | `--alas-entry-accent` / `-accent-soft` / `-on-accent` |
| 表面 | `--alas-entry-surface` / `-surface-muted` |
| 状态色 | `--alas-entry-success` / `-badge-updating` / `-badge-error` |
| 统计页封装 | `--st-surface` / `-text` / `-muted` / `-border` / `-divider` / `-accent` |

每个 `var()` 都带浅色回退值（如 `var(--alas-entry-text, #202124)`），变量缺失也不会崩。

**表格表头**特意用 `--st-table-head`（灰色）而非 `--st-accent`（紫色）：
深色主题下 accent 是深紫，压在深底上同样不可读。

## 四、5 个主题实测结果

| 主题 | body 色 / 底 | 卡片底 | 标题/正文 | 图标 | 结论 |
|---|---|---|---|---|
| **default**（亮色） | `#212529` / `#fff` | `#fff` | `#212529` | stroke `#212529` / fill `#2c2c2c` | ✓ |
| **dark** | `#d3d3d3` / `#0d1117` | `#2b2d31` | `#d3d3d3` | stroke `#d3d3d3` | ✓ 与老 UI 一致 |
| **advanced_material**（高级材质） | `#1d1d1f` / `#f5f5f7` | `rgba(255,255,255,.93)` | `#1d1d1f` | stroke `#1d1d1f` | ✓ |
| **dark_advanced_material** | `#f4f8ff` / `#0b1220` | `rgba(15,23,42,.93)` | `#f4f8ff` | stroke `#f4f8ff` | ✓ |
| **transparent**（透明） | `#1d1d1f` / 壁纸 | 全透明 | `#1d1d1f` | stroke `#1d1d1f` | ✓ |

### 图标逐项对照（dark 主题）

| 属性 | 老 UI | 新 UI | |
|---|---|---|---|
| `svg.aside-icon` color | `rgb(211,211,211)` | `rgb(211,211,211)` | ✓ |
| `path` fill | `rgb(201,209,217)` | `rgb(201,209,217)` | ✓ |
| `path` stroke | `rgb(211,211,211)` | `rgb(211,211,211)` | ✓ |
| `rect` stroke | `rgb(211,211,211)` | `rgb(211,211,211)` | ✓ |
| aside 按钮文字 | `rgb(223,220,251)` | `rgb(223,220,251)` | ✓ |

**5 项完全一致** —— 因为图标用的是同一份 `assets/gui/icon/*.svg` + 同一套 CSS。

### 表格（统计页 科技列表 / 心智计算器 页签）

| 属性 | dark 值 | 可读性 |
|---|---|---|
| `td` color | `rgb(211,211,211)` | ✓ 浅字压深底 |
| `tr` background | `rgb(13,17,23)` | ✓ |
| `th` color | `var(--st-table-head)` → 灰 | ✓ 不再是浅色主题的紫 |

## 五、移动端（顺带完成）

| 项 | 实现 |
|---|---|
| CSS 分支 | 端点按 UA 判定，`alas-pc.css` → `alas-mobile.css`（同老 UI `is_mobile` 分支） |
| 菜单折叠 | `alas-mobile.css` 的 `.container-menu-collapsed{display:none}` 是真开关 |
| 切换入口 | 点侧栏「主页」图标 → 切换菜单/内容（对应老 UI `ui_develop` 语义） |
| 触控目标 | `alas-mobile.css` 自带 44px 最小高度 |

## 六、踩坑记录（给后来人）

1. **`'advanced-material-alas.css'.endsWith('alas.css') === true`**
   —— 用 `endsWith` 过滤基础层会把高级材质主题层一起过滤掉，导致主题完全失效。
   必须按**文件名精确相等**判断。
2. **内联 `<style>` 里的 `html,body{background:#f9f9f9}` 会盖掉主题层的 body 底色**
   —— 骨架样式必须自带背景，不要给 body 设 background。
3. **FCP 只统计文本/图像** —— 纯色块骨架不算 contentful paint，必须放一行真实文字。
4. **`bundle.css` 合并方案与「按主题切 Bootstrap」互斥** —— 已撤销合并。

## 七、仍未验证

- 移动端**真机**（目前只在桌面 UA 下验证过 CSS 分支逻辑，未用手机 UA 实测布局）
- 统计页 6 个页签在 dark 下的完整视觉（只测了有表格的两个页签）
