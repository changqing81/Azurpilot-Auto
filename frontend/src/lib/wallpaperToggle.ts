// 纯背景模式（app_home.py:_WALLPAPER_TOGGLE_JS 的 TS 移植）。
// 右下角圆点：点击进入/退出「只显示背景、隐藏所有 UI」模式。
// DOM 契约与老 UI 完全一致（#pywebio-scope-content / -header / -aside / -menu /
// -statistics-content 全部隐藏，退出圆点常驻防锁死）。远控下也能用。
//
// 为什么不用老 UI 的 run_js 注入：新 UI 是 React SPA，路由切换不重载页面，
// 一次性注入即可常驻；用幂等函数保证 StrictMode 双挂载不重复创建。

const STYLE_ID = 'alas-wallpaper-style'
const TOGGLE_ID = 'alas-wallpaper-toggle'

const CSS = [
  '#alas-wallpaper-toggle{position:fixed;bottom:10px;right:10px;z-index:99999;width:24px;height:24px;border-radius:50%;background:rgba(255,255,255,0.6);backdrop-filter:blur(6px);border:1px solid rgba(0,0,0,0.12);display:none;align-items:center;justify-content:center;cursor:pointer;font-size:11px;line-height:1;box-shadow:0 1px 4px rgba(0,0,0,0.2);user-select:none;transition:background 0.15s;}',
  '#alas-wallpaper-toggle:hover{background:rgba(255,255,255,0.9);}',
  'body.alas-wallpaper-toggle-visible #alas-wallpaper-toggle{display:flex;}',
  'body.alas-wallpaper-mode #pywebio-scope-content,body.alas-wallpaper-mode #pywebio-scope-header,body.alas-wallpaper-mode #pywebio-scope-aside,body.alas-wallpaper-mode #pywebio-scope-menu,body.alas-wallpaper-mode #pywebio-scope-statistics-content{display:none !important;}',
  'body.alas-wallpaper-mode #alas-wallpaper-toggle{display:flex !important;}',
].join('\n')

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.appendChild(document.createTextNode(CSS))
  document.head.appendChild(style)
}

function ensureToggle() {
  let el = document.getElementById(TOGGLE_ID) as HTMLDivElement | null
  if (el) return el
  el = document.createElement('div')
  el.id = TOGGLE_ID
  el.title = '纯背景模式'
  el.textContent = '◉'
  el.onclick = () => document.body.classList.toggle('alas-wallpaper-mode')
  document.body.appendChild(el)
  return el
}

/** 注入纯背景模式圆点（幂等）。主页挂载时调用，圆点显示。 */
export function injectWallpaperToggle(): void {
  ensureStyle()
  ensureToggle()
  document.body.classList.add('alas-wallpaper-toggle-visible')
}

/** 离开主页：隐藏圆点并退出纯背景模式（否则切页后看不到任何 UI）。 */
export function hideWallpaperToggle(): void {
  document.body.classList.remove('alas-wallpaper-toggle-visible')
  document.body.classList.remove('alas-wallpaper-mode')
}
