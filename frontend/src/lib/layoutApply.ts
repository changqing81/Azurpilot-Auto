// 移动端布局层切换（alas-pc.css ↔ alas-mobile.css）。
// 老 UI utils.py:webui_style_names(theme, is_mobile) 同口径：UA 含移动端 token 时用 alas-mobile。
//
// ⚠️ 为什么从 main.tsx 顶层的一次性调用改成导出函数：2026-11-18 实测手机远控
// 下 layout 仍是 alas-pc.css（menu 192px 把内容挤到 148px，标题竖排）——
// 顶层调用只跑一次，theme-css 异步返回后 applyTheme 重建/覆盖 link 的时序
// 竞争会让结果回退。这里导出函数，由 Shell 在挂载与主题应用后**重复调用**，
// 幂等（href 已正确就不再写）。
import { withPrefix } from '../api/client'

const MOBILE_UA_TOKENS = ['mobile', 'android', 'iphone', 'ipod', 'ipad', 'windows phone', 'blackberry']

export function isMobileUA(): boolean {
  const ua = (navigator.userAgent || '').toLowerCase()
  return MOBILE_UA_TOKENS.some(t => ua.includes(t))
}

/** 幂等应用移动端/桌面布局层；返回实际生效的布局名。 */
export function applyLayoutLayer(): string {
  const link = document.getElementById('alas-layout-css') as HTMLLinkElement | null
  if (!link) return 'none'
  const want = isMobileUA() ? 'alas-mobile' : 'alas-pc'
  if (!link.href.includes('/' + want + '.css')) {
    link.href = withPrefix('/static/assets/gui/css/' + want + '.css')
  }
  return want
}
