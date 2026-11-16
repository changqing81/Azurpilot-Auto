// PyWebIO 同构 DOM 输出器。
//
// 为什么需要它：老 UI 主题 CSS 的高亮/尺寸规则全部建立在**内联 style 字符串的
// 子串匹配**上，例如 `*[style*="--arg-title--"]`、`*[style*="--dashboard-value--"]`、
// `div[style*="--menu-{name}--"]>button`、`#pywebio-scope-log-container::before{content:var(--version)}`。
// React 的 style={{...}} 只能输出合法 CSS 声明，写不出 `--aside-Home--` 这种
// 非声明式标记（也没有结尾的 `--`），因此必须绕过 React 属性系统直接产出
// 与 PyWebIO 逐字节一致的 HTML 字符串。
//
// 约定：所有带 --marker-- 的元素都通过 dangerouslySetInnerHTML 挂载；
// 纯文本一律先做 HTML 转义。

/** HTML 转义：文本节点与属性值共用（PyWebIO 也对 put_text 内容做转义）。 */
export function esc(text: string): string {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * put_text 同构：PyWebIO 的文本节点固定输出为
 *   <p style="white-space: pre-wrap;">文本</p>
 * extra 原样拼在 white-space 之后（PyWebIO 用 `;;` 分隔多个 style 片段）。
 */
export function putText(text: string, extra = ''): string {
  return `<p style="white-space: pre-wrap;${extra}">${esc(text)}</p>`
}

/** put_scope 同构：`<div id="pywebio-scope-{name}" style="…">…</div>`。 */
export function putScope(name: string, children: string, style = ''): string {
  return `<div id="pywebio-scope-${name}" style="${style}">${children}</div>`
}

/**
 * put_buttons 同构。PyWebIO 每个按钮的包裹结构固定为：
 *   <div style=";;{style}"> <button class="btn {color-类}">{label}</button> </div>
 * 颜色映射见 module/webui/widgets.py:put_buttons。
 */
const COLORS: Record<string, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  success: 'btn-success',
  danger: 'btn-danger',
  warning: 'btn-warning',
  info: 'btn-info',
  light: 'btn-light',
  dark: 'btn-dark',
  on: 'btn-on',
  off: 'btn-off',
  aside: 'btn-aside',
  menu: 'btn-menu',
}

export function btn(label: string, color: string, active = false, style = ''): string {
  const cls = ['btn', COLORS[color] ?? 'btn-' + color]
  if (active) cls.push(color === 'menu' ? 'btn-menu-active' : 'btn-aside-active')
  const styleAttr = style ? ` style="${style}"` : ''
  return `<button class="${cls.join(' ')}"${styleAttr}>${esc(label)}</button>`
}

/** put_buttons 容器：`<div style=";;{style}">…</div>`。 */
export function btnGroup(buttons: string, style = ''): string {
  return `<div style=";;${style}">${buttons}</div>`
}

/** put_row 同构。 */
export function putRow(items: string[], size = '1fr', style = ''): string {
  return (
    `<div style="display: grid; grid-auto-flow: column; grid-template-columns: ${size};;;${style}">` +
    items.join('') +
    '</div>'
  )
}

/** put_column 同构。 */
export function putColumn(items: string[], size = 'auto', style = ''): string {
  return (
    `<div style="display: grid; grid-auto-flow: row; grid-template-rows: ${size};;;${style}">` +
    items.join('') +
    '</div>'
  )
}

/** put_loading 同构（Bootstrap spinner + get_loading_style 的标记）。 */
export function putLoading(shape: 'border' | 'grow' = 'border', color = 'success'): string {
  const mark = shape === 'grow' ? '--loading-grow--' : '--loading-border--'
  return (
    `<div style="display: grid; grid-auto-flow: column; grid-template-columns: auto;">` +
    `<div class="spinner-${shape} text-${color}" role="status" style=";;${mark}">` +
    '<span class="sr-only">Loading...</span></div></div>'
  )
}

/** scope id 助手：与老 UI 的 pywebio-scope-{name} 完全一致。 */
export const S = (name: string) => `pywebio-scope-${name}`
