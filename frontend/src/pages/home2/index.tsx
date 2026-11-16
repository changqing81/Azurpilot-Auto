// 主页 HomePage：与老 UI（app_home.py:show_home:395-494）1:1 同构。
//
// 实测老 UI DOM（.workbuddy/perf/out/oldui-ref/oldui_home_content.html）：
//   #pywebio-scope-content > p(text-align:center;font-weight:600) + div(text-align:center)>button.btn.btn-primary …
// 语言按钮 = btn-primary；主题按钮按主题色：Light→btn-light / Dark→btn-dark /
// 高级材质→btn-primary / 高级材质（暗色）→btn-dark / 透明→btn-light；
// 背景管理 4 按钮 = btn-light / btn-dark / btn-light / btn-light。
import { useEffect } from 'react'
import { Shell } from '../../components/Shell'
import { apiGet } from '../../api/client'
import { btn, btnGroup, putText } from '../../lib/pywebio'

type RaceCandidate = { url: string; video?: boolean }

// 壁纸竞赛（app_home.py:_WALLPAPER_RACE_JS 的 TS 移植）：候选并发下载，最快者胜。
let raceDone = false
let raceTimer: ReturnType<typeof setTimeout> | null = null
let raceImages: (HTMLImageElement | HTMLVideoElement)[] = []

function finishRace(winner: RaceCandidate | null, el?: HTMLImageElement | HTMLVideoElement) {
  if (raceDone) return
  raceDone = true
  if (raceTimer) clearTimeout(raceTimer)
  ;(window as any).__alasWallpaperWinner = winner ?? null
  raceImages.forEach(item => { if (item !== el) { try { (item as any).src = '' } catch { /* 忽略 */ } } })
  const existing = document.getElementById('alas-custom-bg-style')
  if (winner?.video && el) {
    document.getElementById('alas-bg-video')?.remove()
    let st = document.getElementById('alas-custom-bg-style')
    if (!st) { st = document.createElement('style'); st.id = 'alas-custom-bg-style'; document.head.appendChild(st) }
    st.textContent = 'body{background:transparent !important;}'
      + 'html{background:var(--alas-apple-bg,#f5f5f7) !important;}'
      + '#alas-bg-video{position:fixed;inset:0;width:100%;height:100%;object-fit:cover;z-index:-1;pointer-events:none;}'
    el.id = 'alas-bg-video'
    ;(el as HTMLVideoElement).loop = true
    el.setAttribute('playsinline', '')
    document.body.appendChild(el)
    const p = (el as HTMLVideoElement).play()
    if (p && p.catch) p.catch(() => { /* 自动播放被拦，保留静帧 */ })
  } else if (winner?.url) {
    existing?.remove()
    document.documentElement.style.setProperty('--alas-apple-bg-image', `url("${winner.url}")`)
  }
}

function startOne(c: RaceCandidate) {
  let el: HTMLImageElement | HTMLVideoElement
  if (c.video) {
    const v = document.createElement('video')
    v.muted = true
    v.preload = 'auto'
    v.setAttribute('playsinline', '')
    v.oncanplay = () => finishRace(c, v)
    v.onerror = () => { /* 该候选失败，等其它候选 */ }
    v.src = c.url
    el = v
  } else {
    const img = new Image()
    img.onload = () => finishRace(c, img)
    img.onerror = () => { /* 该候选失败，等其它候选 */ }
    img.src = c.url
    el = img
  }
  raceImages.push(el)
}

function wallpaperRace(candidates: RaceCandidate[]) {
  raceDone = false
  raceImages = []
  if (raceTimer) clearTimeout(raceTimer)
  ;(window as any).__alasWallpaperWinner = null
  raceTimer = setTimeout(() => finishRace(null), 30000)
  candidates.forEach(startOne)
}

const LANGS = [
  { label: '简体中文', value: 'zh-CN' },
  { label: '繁體中文', value: 'zh-TW' },
  { label: 'English', value: 'en-US' },
  { label: '日本語', value: 'ja-JP' },
]
// 「主题按钮颜色」与 app_home.py:420-441 的 put_buttons color 一一对应
const THEMES = [
  // 老 UI app_home.py:422 的值是 "default"，不是 "light" ——
  // 写成 light 虽然 THEME_OPTIONS 也接受，但 normalize 后落到别的层，
  // 与老 UI 点 Light 的效果不一致。
  { name: 'Light', value: 'default', color: 'light' },
  { name: 'Dark', value: 'dark', color: 'dark' },
  { name: '高级材质', value: 'advanced_material', color: 'primary' },
  { name: '高级材质（暗色）', value: 'dark_advanced_material', color: 'dark' },
  { name: '透明', value: 'transparent', color: 'light' },
]

async function writeDeploySetting(patch: Record<string, string>) {
  const r = await fetch('/api/deploy/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) throw new Error('HTTP ' + r.status)
}

export function HomePageMain() {
  useEffect(() => {
    apiGet<{ candidates: RaceCandidate[] }>('/newapi/wallpaper/candidates')
      .then(r => { if (r.candidates?.length) wallpaperRace(r.candidates) })
      .catch(() => { /* 图源不可用：保持纯色背景 */ })

    // 公告自动弹窗：与老 UI app_home.py:1811-1840 完全同一条链路——
    // 拉 /newapi/announcement → 用 alas-utils.js 的 window.alasShowAnnouncement 渲染，
    // alasHasBeenShown 做 localStorage 去重（看过就不再弹）。
    apiGet<{ title: string; content: string; id: number | null; url: string; error: string }>(
      '/newapi/announcement',
      20000,
    )
      .then(ann => {
        if (!ann || ann.error || !ann.content) return
        if (ann.id != null && window.alasHasBeenShown?.(ann.id)) return
        window.alasShowAnnouncement?.(ann.title, ann.content, ann.id, ann.url, false)
      })
      .catch(() => { /* 公告源不可达：静默，不打扰用户 */ })
  }, [])

  // 切换语言/主题后：**先回主页再重载**。
  // 坑：直接 location.reload() 会保留当前 hash —— 用户如果在 #/stat/alas
  // 点了语言，刷新后仍停在统计页，看起来像「点语言跳到了统计页」。
  // 老 UI 是整页重定向到 /，这里保持一致。
  const onClick = (patch: Record<string, string>) => async () => {
    try {
      await writeDeploySetting(patch)
      try { sessionStorage.removeItem('alas_newui_last_route') } catch { /* 隐私模式 */ }
      location.hash = ''
      location.replace(location.pathname)
    } catch { /* 写盘失败：不刷新，避免误导 */ }
  }

  const downloadWallpaper = () => {
    const winner = (window as any).__alasWallpaperWinner as RaceCandidate | null
    if (winner?.url) window.open(winner.url, '_blank')
  }

  // 容器 style 必须是 ";;text-align: center"（与老 UI put_buttons 的 .style 原样一致），
  // 否则按钮组靠左，与老 UI 的居中排布不一致。
  const center = 'text-align: center'
  const html =
    putText('Select your language / 选择语言', ';;text-align: center; font-weight: 600') +
    btnGroup(LANGS.map(l => btn(l.label, 'primary')).join(''), center) +
    putText('Change theme / 更改主题', ';;text-align: center') +
    btnGroup(THEMES.map(t => btn(t.name, t.color)).join(''), center) +
    btnGroup(btn('下载当前背景图', 'light'), center) +
    putText('Background / 背景', ';;text-align: center; font-weight: 600') +
    btnGroup(
      btn('上传自定义背景', 'light') + btn('随机背景', 'dark') + btn('管理图源', 'light') + btn('媒体类型', 'light'),
      center,
    ) +
    '<div class="alas-home-marker" aria-hidden="true"></div>' +
    putText(
      'AzurPilot 是基于上游项目 Alas (AzurLaneAutoScript) 的修改版本，采用 GPL-3.0 许可证，免费开源。'
        + '如果你在任何渠道付费购买，那你一定是个大傻逼，请申请退款。',
      ';;text-align: center',
    )

  // 按钮点击用事件委托（HTML 是字符串注入的，没法挂 React onClick）
  const ref = (el: HTMLDivElement | null) => {
    if (!el) return
    el.onclick = (ev) => {
      const target = (ev.target as HTMLElement).closest('button')
      if (!target) return
      const label = target.textContent ?? ''
      const lang = LANGS.find(l => l.label === label)
      if (lang) { void onClick({ Language: lang.value })(); return }
      const theme = THEMES.find(t => t.name === label)
      if (theme) { void onClick({ Theme: theme.value })(); return }
      if (label === '下载当前背景图') downloadWallpaper()
    }
  }

  return (
    <Shell active="Home" title="主页">
      <div id="pywebio-scope-content" ref={ref} dangerouslySetInnerHTML={{ __html: html }} />
    </Shell>
  )
}
