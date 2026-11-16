# 移动端适配验收报告

> 时间：2026-11-16（new-ui 分支 42629e680）
> 方法：CDP 模拟 iPhone 13（UA + 390×844 + DPR3 + 触摸），逐页量 computed style

## 一、修掉的两个「写了但没生效」的 bug

### Bug 1：alas-mobile.css 根本没加载

**现象**：390px 屏上菜单占 270px，内容区只剩 **126px**，中文被挤成竖排一字一行。

**根因**：上一轮为了修正 CSS 加载顺序（修 dark 主题深底深字），
把 `/newapi/bundle.css` 拆成了 16 个静态 `<link>`。
而 UA 判定（`alas-pc.css` ↔ `alas-mobile.css`）原本在 bundle 端点里，
**拆开后整条逻辑丢了** —— `alas-pc.css` 被写死在 HTML 里。

**修法**：给该 `<link>` 加 `id="alas-layout-css"`，由 `main.tsx` 按 UA 改 `href`。
只改 href 不改变插入顺序，层叠优先级不受影响。

```ts
const want = isMobileUA() ? "alas-mobile" : "alas-pc"
link.href = "/static/assets/gui/css/" + want + ".css"
```

### Bug 2：移动端菜单与内容没有互斥

**现象**：`alas-mobile.css` 加载了，菜单仍是 `display:block`，和内容挤在一起。

**根因**：老 UI 的 `base.py:init_aside` 语义是**互斥**的：

```js
// 展开菜单
$('.container-menu-collapsed').removeClass('container-menu-collapsed')
$('#pywebio-scope-content').addClass('container-content-collapsed')
```

我第一版让菜单和内容同时存在，等于两个 100% 宽的东西并排。

**修法**：`menuOpen` 状态 + effect 同步内容区的 `container-content-collapsed`：

| 状态 | 菜单 | 内容 |
|---|---|---|
| 初始 | `none` 收起 | `block` 显示 |
| 点侧栏「主页」 | `block` 展开 | `none` 隐藏 |
| 再点 | `none` 收起 | `block` 显示 |

## 二、实测结果（iPhone 13 / 390×844 / DPR3 / 触摸）

| 页面 | 菜单 | 内容宽 | 横向溢出 |
|---|---|---|---|
| 主页 | none | 318px | 无 |
| 总览 | none | 318px | 无 |
| 统计 | none | 318px | 无 |
| 配置 | none | 318px | 无 |
| 设置 | none | 318px | 无 |
| 管理 | none | 318px | 无 |
| 开发者工具 | none | 318px | 无 |

内容区从 **126px → 318px**（+152%），`document.scrollWidth == innerWidth` 无横向滚动。

### 布局变化确认

| 项 | 桌面 | 移动端 | 依据 |
|---|---|---|---|
| 总览列数 | 2 列（320px + 1fr） | 1 列（上下两行 780px 780px） | `alas-mobile.css` `#pywebio-scope-overview{grid-auto-flow:row}` |
| 菜单 | 常驻 | 折叠可切换 | `alas-mobile.css` `.container-menu-collapsed{display:none}` |
| 日志字号 | 13.6px | 13px | 移动端 `.75rem !important` |
| 触控目标 | — | ≥44px | `alas-mobile.css` 的 `min-height:44px` |

## 三、踩坑记录

1. **拆 bundle 会连带丢掉服务端分支逻辑** —— UA 判定、主题切换这类
   「按请求/按会话变化」的逻辑，不能放在静态 HTML 里。
2. **移动端是「互斥」不是「并排」** —— 老 UI 的菜单/内容切换靠
   `container-content-collapsed` 成对使用，漏一边就会挤。
3. **只用桌面 UA 测不出移动端问题** —— 这一项拖到最后才验，
   一测就是「完全不可用」级别。以后涉及 CSS 分支的改动必须同时跑两种 UA。

## 四、仍未覆盖

- 真实 iOS Safari / Android Chrome（当前用 Chromium 模拟 UA + 视口，
  能验证 CSS 与布局，但验证不了 iOS 的 `position:fixed` 键盘顶起、
  100vh 陷阱等 Safari 特有问题）
- 真机远控链路（`remurl.*` 的 P2P 隧道 + 弱网）
