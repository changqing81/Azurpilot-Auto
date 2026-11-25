// 主页 HomePage：与老 UI（app_home.py:show_home:395-494）1:1 同构。
//
// 实测老 UI DOM（.workbuddy/perf/out/oldui-ref/oldui_home_content.html）：
//   #pywebio-scope-content > p(text-align:center;font-weight:600) + div(text-align:center)>button.btn.btn-primary …
// 语言按钮 = btn-primary；主题按钮按主题色：Light→btn-light / Dark→btn-dark /
// 高级材质→btn-primary / 高级材质（暗色）→btn-dark / 透明→btn-light；
// 背景管理 4 按钮 = btn-light / btn-dark / btn-light / btn-light。
import { useEffect, useState } from 'react'
import { Shell } from '../../components/Shell'
import { WallpaperSourcesModal } from './WallpaperSourcesModal'
import { apiGet, withPrefix } from '../../api/client'
import { btn, btnGroup, putText } from '../../lib/pywebio'

type RaceCandidate = { url: string; video?: boolean }

// 壁纸竞赛（app_home.py:_WALLPAPER_RACE_JS 的 TS 移植）：候选并发下载，最快者胜。
//
// ⚠️ 2026-11-25 由「<img> 比加载速度 + CSS 引用原 URL」改成「fetch 比拿到 blob」。
//
// 旧实现有个致命缺陷：CSS 变量设的是 winner.url，浏览器为渲染背景**会再请求一次**。
// 对随机直链图源（api.yppp.net 之类，每次请求返回不同图）来说，竞技阶段下载到的是
// A 图，「下载当前背景图」拿到的也是 A 图，但屏幕上显示的其实是第二次请求的 B 图。
// 长卿 2026-11-25 实测截图：屏幕上白毛短发角色，下载下来是格子裙角色 —— 两张都不是
// 同一个文件，改下载方式（canvas / 重新打开）都治不了根。
//
// 现在改成：每个候选 fetch 成 Blob，最快拿到 blob 的胜出；显示与下载共用
// 同一个 blob 生成的 objectURL —— 浏览器不会再发任何远程请求，屏幕上是什么、
// 存下来就是什么，逐字节相同。
//
// 代价（已与长卿确认）：不给 Access-Control-Allow-Origin 的图源 fetch 会失败，
// 在竞技阶段直接被淘汰。同源的 /api/wallpapers/custom（自定义背景）不受影响。
let raceDone = false
let raceTimer: ReturnType<typeof setTimeout> | null = null
let raceAbort: AbortController | null = null
// 胜者的 blob 与其 objectURL：下载直接用这份数据，不重新请求
let raceWinnerBlob: Blob | null = null
let raceWinnerObjectUrl: string | null = null

/** 从 blob 的 MIME 推断扩展名；未知时回退 .jpg。 */
function extOfBlob(blob: Blob): string {
  const sub = (blob.type || '').split('/')[1] || ''
  const map: Record<string, string> = {
    jpeg: 'jpg', jpg: 'jpg', png: 'png', webp: 'webp', gif: 'gif',
    mp4: 'mp4', webm: 'webm', quicktime: 'mov',
  }
  return map[sub.toLowerCase()] || (blob.type.startsWith('video/') ? 'mp4' : 'jpg')
}

/** 释放上一轮的 objectURL，避免内存泄漏（一张 8K 图就有几十 MB）。 */
function revokeWinnerUrl(): void {
  if (raceWinnerObjectUrl) {
    try { URL.revokeObjectURL(raceWinnerObjectUrl) } catch { /* 忽略 */ }
    raceWinnerObjectUrl = null
  }
}

function finishRace(winner: RaceCandidate | null, blob?: Blob) {
  if (raceDone) return
  raceDone = true
  if (raceTimer) clearTimeout(raceTimer)
  if (raceAbort) { try { raceAbort.abort() } catch { /* 忽略 */ } raceAbort = null }
  ;(window as any).__alasWallpaperWinner = winner ?? null
  // 拿到胜者：永久停止自动重试（否则会在后台一直重抽）
  if (winner?.url) settleWallpaper()

  if (winner?.url && blob) {
    // 先释放旧 URL 再建新的
    revokeWinnerUrl()
    raceWinnerBlob = blob
    raceWinnerObjectUrl = URL.createObjectURL(blob)
  }

  const existing = document.getElementById('alas-custom-bg-style')
  if (winner?.video && raceWinnerObjectUrl) {
    document.getElementById('alas-bg-video')?.remove()
    let st = document.getElementById('alas-custom-bg-style')
    if (!st) { st = document.createElement('style'); st.id = 'alas-custom-bg-style'; document.head.appendChild(st) }
    st.textContent = 'body{background:transparent !important;}'
      + 'html{background:var(--alas-apple-bg,#f5f5f7) !important;}'
      + '#alas-bg-video{position:fixed;inset:0;width:100%;height:100%;object-fit:cover;z-index:-1;pointer-events:none;}'
    const v = document.createElement('video')
    v.id = 'alas-bg-video'
    v.loop = true
    // 自动播放策略：**必须有 muted**，否则浏览器直接拒绝 play()（背景视频不需要声音）
    v.muted = true
    v.autoplay = true
    v.setAttribute('muted', '')
    v.setAttribute('playsinline', '')
    v.setAttribute('disablepictureinpicture', '')
    v.src = raceWinnerObjectUrl
    document.body.appendChild(v)
    const p = v.play()
    if (p && p.catch) p.catch(() => { /* 自动播放被拦，保留静帧 */ })
  } else if (winner?.url && raceWinnerObjectUrl) {
    existing?.remove()
    // 用 objectURL 而不是远程 URL：浏览器不会再发请求，显示的必然是竞技时那一份。
    // 这也是本例修掉「屏幕上一张、下载另一张」的关键——旧实现把 winner.url 交给 CSS，
    // 浏览器按它重新请求，随机图源于是又给了一张新图。
    document.documentElement.style.setProperty(
      '--alas-apple-bg-image', `url("${raceWinnerObjectUrl}")`)
  }
}

/** 壁纸 URL 解析：外链（http/https）原样；站内路径（/api/wallpapers/custom）补中继前缀。
 *  不能直接用 withPrefix：它会把外链当站内路径拼成 /https://… 彻底打不开
 *  （2026-11-18 实测：主页背景全黑，竞速外链候选全部 404）。 */
function bgUrl(url: string): string {
  return /^https?:\/\//i.test(url) ? url : withPrefix(url)
}

/** 下载单个候选为 blob；超时或跨域被拒时抛错，由调用方静默淘汰。 */
async function fetchCandidate(c: RaceCandidate, signal: AbortSignal): Promise<Blob> {
  const res = await fetch(bgUrl(c.url), { mode: 'cors', signal })
  if (!res.ok) throw new Error('HTTP ' + res.status)
  const blob = await res.blob()
  if (!blob.size) throw new Error('空响应')
  // 视频候选的 blob 类型可能是 application/octet-stream，按候选标记补正
  if (c.video && !blob.type.startsWith('video/')) {
    return new Blob([blob], { type: 'video/mp4' })
  }
  // 图片候选同理：部分 CDN 不给 content-type
  if (!c.video && !blob.type.startsWith('image/')) {
    return new Blob([blob], { type: 'image/jpeg' })
  }
  return blob
}

/** 竞速：谁先拿到 blob 谁赢。 */
function startOne(c: RaceCandidate, signal: AbortSignal) {
  fetchCandidate(c, signal)
    .then(blob => finishRace(c, blob))
    .catch(() => { /* 该候选失败（网络 / 跨域被拒 / 超时），等其它候选 */ })
}


/** 竞速失败：把「无胜者」也交给 finishRace 收尾，并通知重试调度。 */
function finishRaceFailed() {
  if (raceDone) return
  finishRace(null)
}

function wallpaperRace(candidates: RaceCandidate[]) {
  raceDone = false
  if (raceTimer) clearTimeout(raceTimer)
  if (raceAbort) { try { raceAbort.abort() } catch { /* 忽略 */ } }
  ;(window as any).__alasWallpaperWinner = null
  const controller = new AbortController()
  raceAbort = controller
  // 30s 全员失败兜底：主动中止所有还在下载的候选，避免大图把带宽占满
  raceTimer = setTimeout(() => {
    try { controller.abort() } catch { /* 忽略 */ }
    finishRaceFailed()
  }, 30000)
  candidates.forEach(c => startOne(c, controller.signal))
}

// ── 竞速失败后的自动重试 ──────────────────────────────────────────
//
// 长卿 2026-11-25：「为什么有时候高级材质和暗色会没有背景图片」。
//
// 根因不是网络本身，而是**一次失败就永久放弃**：原实现在 .then() 里只判
// candidates 非空就开赛，失败分支是空注释；而高级材质/暗色主题的玻璃感完全
// 依赖 --alas-apple-bg-image，变量停在 none 时半透明卡叠在纯色底上，
// 看起来就是「没有背景图」。浅色主题底够亮，同样失败却看不出来，
// 所以用户只会觉得「只有高级材质和暗色有问题」。
//
// 现在：拿不到候选 / 30s 内全员加载失败 → 指数退避重抽，拿到胜者即永久停。
// 退避 2s→4s→8s→15s→30s（封顶），页面隐藏时暂停（回来立刻补一次），
// 避免后台标签页白烧请求与流量。
const RETRY_DELAYS = [2000, 4000, 8000, 15000, 30000]
let retryTimer: ReturnType<typeof setTimeout> | null = null
// 竞速结果的独立探测定时器（与 retryTimer 分开，见 armRaceFailureProbe）
let probeTimer: ReturnType<typeof setTimeout> | null = null
let retryCount = 0
/** 已经有胜者（或用户已拿到图）就不再重试。 */
let wallpaperSettled = false

function hasWinner(): boolean {
  return Boolean((window as any).__alasWallpaperWinner?.url)
}

/** 停掉重试并标记已收尾（拿到胜者时调用）。 */
function settleWallpaper(): void {
  wallpaperSettled = true
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  if (probeTimer) { clearTimeout(probeTimer); probeTimer = null }
}

/** 清掉重试状态，让下一轮可以重新开始（切主题/显式重抽时用）。 */
export function resetWallpaperRetry(): void {
  wallpaperSettled = false
  retryCount = 0
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  if (probeTimer) { clearTimeout(probeTimer); probeTimer = null }
  // 重抽会换掉胜者：旧 blob 的 objectURL 必须释放，否则每次重抽泄漏一张大图
  revokeWinnerUrl()
  raceWinnerBlob = null
}

/** 把竞速胜者存成文件，保证「存下来的就是屏幕上那张」。
 *
 *  长卿 2026-11-25：「有些图源点击下载背景图显示完全不一样的图片」。
 *
 *  根因不只是下载方式：旧实现把 winner.url 交给 CSS 变量，浏览器为渲染背景**又请求
 *  一次**，随机图源于是给出第二张图 —— 屏幕上是 B、下载的是 A，两者都不是同一个文件。
 *  现在竞技阶段就把候选 fetch 成 blob，显示用 blob 的 objectURL、下载也用同一个 blob，
 *  逐字节相同，浏览器不再发任何远程请求。
 *
 *  Returns:
 *    'saved'  已作为文件保存
 *    'none'   还没有胜者（竞技未完成或全部失败）
 */
export async function downloadCurrentWallpaper(): Promise<'saved' | 'none'> {
  const winner = (window as any).__alasWallpaperWinner as RaceCandidate | null
  if (!winner?.url || !raceWinnerBlob) return 'none'

  const url = URL.createObjectURL(raceWinnerBlob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'wallpaper_' + new Date()
    .toISOString().replace(/[:.]/g, '-').slice(0, 19) + '.' + extOfBlob(raceWinnerBlob)
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 立刻 revoke 会让部分浏览器来不及取数据，延后释放
  setTimeout(() => URL.revokeObjectURL(url), 10000)
  return 'saved'
}

/** 拉一次候选并发起竞速；拿到胜者即停，否则排下一次重试。 */
function loadCandidatesAndRace(): void {
  apiGet<{ candidates: RaceCandidate[] }>('/newapi/wallpaper/candidates', 30000)
    .then(r => {
      const candidates = r?.candidates ?? []
      if (!candidates.length) {
        // 全部图源都没返回直链（网络抖动 / 图源临时不可用）
        scheduleRetry()
        return
      }
      wallpaperRace(candidates)
      // 竞速本身可能全员失败，30s 兜底结束后会再走一次 scheduleRetry
      armRaceFailureProbe()
    })
    .catch(() => { scheduleRetry() })
}

/** 竞速开赛后盯着结果：一旦有胜者就收尾，否则退避重试。 */
function armRaceFailureProbe(): void {
  const startedAt = Date.now()
  // probe 用独立定时器：与 retryTimer（重抽调度）分开，否则两边互相 clearTimeout，
  // 会出现「探测被取消就再也不重试」的静默卡死。
  if (probeTimer) clearTimeout(probeTimer)
  const probe = () => {
    probeTimer = null
    if (wallpaperSettled) return
    if (hasWinner()) { settleWallpaper(); return }
    // raceDone 且仍无胜者 = 本轮全员失败；或超过竞速 30s 兜底 + 余量
    if (raceDone || Date.now() - startedAt > 32000) {
      scheduleRetry()
      return
    }
    probeTimer = setTimeout(probe, 500)
  }
  probeTimer = setTimeout(probe, 500)
}

function scheduleRetry(): void {
  if (wallpaperSettled || hasWinner()) { settleWallpaper(); return }
  if (typeof document !== 'undefined' && document.hidden) {
    // 页面在后台：不烧请求，等可见性回调补
    return
  }
  const delay = RETRY_DELAYS[Math.min(retryCount, RETRY_DELAYS.length - 1)]
  retryCount += 1
  if (retryTimer) clearTimeout(retryTimer)
  retryTimer = setTimeout(() => {
    retryTimer = null
    if (wallpaperSettled || hasWinner()) { settleWallpaper(); return }
    if (typeof document !== 'undefined' && document.hidden) return
    loadCandidatesAndRace()
  }, delay)
}

// 页面回到前台时：还没胜者就立刻补一次（幂等，不叠加定时器）
function onVisibilityChange(): void {
  if (document.hidden || wallpaperSettled || hasWinner()) return
  if (retryTimer) clearTimeout(retryTimer)
  retryTimer = setTimeout(() => { retryTimer = null; loadCandidatesAndRace() }, 300)
}
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', onVisibilityChange)
}

const LANGS = [
  { label: '简体中文', value: 'zh-CN' },
  { label: '繁體中文', value: 'zh-TW' },
  { label: 'English', value: 'en-US' },
  { label: '日本語', value: 'ja-JP' },
]

/**
 * 切换界面语言。
 *
 * **不能走 /api/deploy/settings**：那条链路只写 deploy.yaml，而真正决定
 * t() 返回什么的 module.webui.lang.LANG 是进程启动时设一次、之后再没人改，
 * 所以以前点「English / 日本語」界面文字一字不动 —— 这就是用户说的
 * 「切换语言是没有用的」。
 *
 * /newapi/language 会同时做三件事（与老 UI 的 set_language 对齐）：
 * 写 deploy.yaml、改 lang.LANG、预加载该语言词条。
 * 成功后派发 alas-language-change 让 Shell 重拉菜单/页眉（它们是后端翻译的）。
 */
async function applyLanguage(language: string) {
  const r = await fetch('/newapi/language', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ language }),
  })
  const j = await r.json().catch(() => ({}))
  if (!r.ok || j.success === false) {
    throw new Error(j.error || 'HTTP ' + r.status)
  }
  window.dispatchEvent(new CustomEvent('alas-language-change', { detail: { language } }))
  return j
}
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
  // ⚠️ 不能走 /api/deploy/settings：那个端点带 is_local_request 门禁
  // （launcher.py:24，要求 client.host 与 Host 头都是 127.0.0.1），
  // 远控经中继转发时 Host 是 remurl 域名 → 直接 403「部署设置只允许从
  // 本机 WebUI 读取/保存」。实测手机端经 remurl 点主题完全没反应
  // （长卿 2026-11-17：「切换主题也切不动」）。
  // /newapi/theme 是远控可用的等价端点（只改 Theme 一个字段）。
  const r = await fetch('/newapi/theme', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) throw new Error('HTTP ' + r.status)
}

/**
 * 启动壁纸竞速（应用级，任意页面都能看到背景）。
 *
 * 老 UI 的语义：每次 WebSocket 会话建立就 init_wallpaper()
 * （module/webui/app_home.py:1916），壁纸是**整个应用**的背景，不是主页专属。
 * 新 UI 原来只在 HomePageMain 挂载时跑竞速 —— 手机远控恢复时直达
 * 统计/配置页（RestoreLastRoute），壁纸竞速从未运行，
 * --alas-apple-bg-image 停在 none：半透明卡叠在纯深色底上，
 * 整页糊成「灰的一坨」，半透明材质完全看不出来（长卿 2026-11-19：
 * 「新UI有好多地方是双面底…灰的一坨」的真实原因之一）。
 *
 * 竞速本身有 raceDone 守卫（跑过就不再跑），这里加一层模块级
 * started 标记，主页挂载与 App 启动两处调用也只会跑一次。
 */
let wallpaperStarted = false
export function startWallpaperRace(): void {
  if (wallpaperStarted) return
  wallpaperStarted = true
  loadCandidatesAndRace()
}

export function HomePageMain() {
  // 页面标题（老 UI set_title(t("Gui.MenuDevelop.HomePage"))）
  const [pageTitle, setPageTitle] = useState('主页')
  const [showSources, setShowSources] = useState(false)
  useEffect(() => {
    apiGet<{ aside_home: string }>('/newapi/menu/fixed', 8000)
      .then(r => { if (r?.aside_home) setPageTitle(r.aside_home) })
      .catch(() => { /* 老后端没有该端点：保持中文兜底 */ })
  }, [])

  useEffect(() => {
    startWallpaperRace()

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

  // 切换主题后：**先回主页再重载**。
  // 坑：直接 location.reload() 会保留当前 hash —— 用户如果在 #/stat/alas
  // 点了主题，刷新后仍停在统计页，看起来像「点主题跳到了统计页」。
  // 老 UI 是整页重定向到 /，这里保持一致。
  const onClick = (patch: Record<string, string>) => async () => {
    try {
      await writeDeploySetting(patch)
      try { sessionStorage.removeItem('alas_newui_last_route') } catch { /* 隐私模式 */ }
      location.hash = ''
      location.replace(location.pathname)
    } catch (e) {
      // ⚠️ 不能静默吞掉：原来 catch 是空的，后端返回 400 时界面毫无反应，
      // 用户看到的就是「点主题完全没用」（长卿 2026-11-17 二次反馈）。
      // 现在把真实原因弹出来，至少能立刻知道是失败而不是卡住。
      window.alert('切换主题失败：' + String(e))
    }
  }

  // 切换语言：走专用端点（同时改配置 + 运行时 LANG），成功后**整页重载**。
  // 为什么不就地切：语言影响的不只是这几个按钮，还有配置页的参数名/帮助、
  // 统计页表头、菜单名——那些是各页面自己 fetch 的，重载一次最稳，
  // 与老 UI show_home()+refresh_aside_labels() 的最终效果一致。
  const onClickLanguage = (language: string) => async () => {
    try {
      await applyLanguage(language)
      try { sessionStorage.removeItem('alas_newui_last_route') } catch { /* 隐私模式 */ }
      location.hash = ''
      location.replace(location.pathname)
    } catch (e) {
      window.alert('切换语言失败：' + String(e))
    }
  }

  // ── 背景 4 个按钮（长卿反馈「点了没有任何反应」）─────────────────
  // 老 UI 用 PyWebIO 的 input_group 弹窗；新 UI 用 REST + 原生控件实现同样功能。
  const [bg, setBg] = useState<{ mode: string; media: string; has_custom: boolean } | null>(null)
  useEffect(() => {
    apiGet<{ mode: string; media: string; has_custom: boolean }>('/newapi/background', 8000)
      .then(setBg).catch(() => { /* 读不到就用默认值 */ })
  }, [])

  /** 上传自定义背景：**分片上传**（远控中继 body 上限 ~1MB，整文件必然 413）。
 *  实测：remurl 中继 1000KB 通过、1024KB 返回 413 HTML 错误页，前端把 HTML
 *  当 JSON 解析就报 "Unexpected token '<'"（长卿 2026-11-18 截图）。
 *  这里按 512KB 切片，逐片 POST /newapi/background/upload-chunk，最后一片
 *  到达时后端合并落盘。手机照片/视频都能传。
 */
  /** 上传自定义背景：优先分片上传（绕开远控中继 1MB 限制），不支持时回退整文件。
 *  · 分片端点 /newapi/background/upload-chunk 是后加的；旧后端没有它（会 404/405）。
 *  · 电脑端直连没有 1MB 限制，整文件端点一直可用 —— 所以先探测分片端点，
 *    不可用就走整文件，保证「不重启后端也能上传」；重启后手机远控也能传大文件。
 *  · 中继 1MB 限制实测：1000KB 通过、1024KB 返回 413 HTML（长卿 2026-11-18）。
 */
  const uploadBackground = () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/*,video/mp4,video/webm,video/quicktime'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      if (file.size > 200 * 1024 * 1024) { window.alert('文件超过 200MB 限制'); return }

      const tip = document.createElement('div')
      tip.id = 'alas-upload-tip'
      tip.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);'
        + 'z-index:99999;padding:10px 18px;border-radius:999px;background:rgba(0,0,0,.78);'
        + 'color:#fff;font-size:13px;font-family:system-ui;pointer-events:none;'
        + 'box-shadow:0 4px 16px rgba(0,0,0,.3)'
      document.body.appendChild(tip)
      const done = (msg: string) => { tip.textContent = msg }

      try {
        // 探测分片端点是否可用（旧后端没有该路由）
        let chunkOk = false
        try {
          const probe = await fetch('/newapi/background/upload-chunk', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ upload_id: 'probe', index: 0, total: 0, filename: 'probe', data: '' }),
          })
          // total=0 的分片请求：端点存在时返回 JSON，不存在时是 404/405
          chunkOk = probe.status !== 404 && probe.status !== 405
        } catch { chunkOk = false }

        if (chunkOk) {
          const CHUNK = 512 * 1024
          const total = Math.max(1, Math.ceil(file.size / CHUNK))
          // 断点续传：upload_id 由「文件名+大小+最后修改时间」派生，同一文件重传
          // 会复用同一 id，服务端据此返回已收到的分片，只补缺失的。
          // 「服务器中断了不白搭」—— 中断后重新选同一个文件即可续传。
          const sig = `${file.name}:${file.size}:${file.lastModified}`
          let h = 0
          for (let k = 0; k < sig.length; k++) h = ((h << 5) - h + sig.charCodeAt(k)) | 0
          const uploadId = 'u' + Math.abs(h).toString(36) + file.size.toString(36)
          let already: number[] = []
          try {
            const st = await fetch('/newapi/background/upload-status?upload_id=' + encodeURIComponent(uploadId))
            if (st.ok) {
              const sj = await st.json()
              if (sj && Array.isArray(sj.received)) already = sj.received
            }
          } catch { /* 旧后端没有该端点：从零开始 */ }
          const skip = new Set(already)
          for (let i = 0; i < total; i++) {
            if (skip.has(i)) { done(`续传：跳过已传 ${skip.size}/${total}`); continue }
            done(`正在上传 ${i + 1}/${total}（${Math.round((i / total) * 100)}%）`)
            const slice = file.slice(i * CHUNK, (i + 1) * CHUNK)
            const b64 = await new Promise<string>((resolve, reject) => {
              const fr = new FileReader()
              fr.onload = () => resolve(String(fr.result).split(',')[1] || '')
              fr.onerror = () => reject(new Error('读取分片失败'))
              fr.readAsDataURL(slice)
            })
            const resp = await fetch('/newapi/background/upload-chunk', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ upload_id: uploadId, index: i, total, filename: file.name, data: b64 }),
            })
            const text = await resp.text()
            let jr: any = null
            try { jr = JSON.parse(text) } catch {
              tip.remove()
              window.alert('上传失败：服务端返回非 JSON 响应（HTTP ' + resp.status + '）'
                + (resp.status === 413 ? '\n\n文件分片仍超网关限制。' : ''))
              return
            }
            if (!jr || !jr.success) {
              tip.remove()
              window.alert('上传失败：' + ((jr && jr.error) || 'HTTP ' + resp.status))
              return
            }
          }
        } else {
          // 回退：整文件上传（电脑端/直连没有中继限制）
          done('正在上传…')
          const fd = new FormData()
          fd.append('file', file)
          const resp = await fetch('/newapi/background/upload', { method: 'POST', body: fd })
          const text = await resp.text()
          let jr: any = null
          try { jr = JSON.parse(text) } catch {
            tip.remove()
            window.alert('上传失败：服务端返回非 JSON（HTTP ' + resp.status + '）'
              + (resp.status === 413 ? '\n\n文件超过网关限制，请压小图片或等后端支持分片。' : ''))
            return
          }
          if (!jr || !jr.success) {
            tip.remove()
            window.alert('上传失败：' + ((jr && jr.error) || 'HTTP ' + resp.status))
            return
          }
        }
        tip.textContent = '上传完成，正在应用…'
        location.reload()
      } catch (err) {
        tip.remove()
        window.alert('上传失败：' + String(err))
      } finally {
        setTimeout(() => tip.remove(), 3000)
      }
    }
    input.click()
  }

/** 随机背景：切回 random 模式并重载（壁纸竞速重跑）。 */
  const randomBackground = async () => {
    try {
      const r = await fetch('/newapi/background/mode', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'random' }),
      })
      const j = await r.json()
      if (j.success) {
        // 清掉上次壁纸变量并重载，避免浏览器缓存同一张图
        try { (window as any).__alasWallpaperWinner = null } catch { /* 忽略 */ }
        location.reload()
      } else window.alert('切换失败：' + (j.error || '未知错误'))
    } catch (e) { window.alert('切换失败：' + String(e)) }
  }

  /** 媒体类型：三轮切换 image → video → auto（老 UI 是弹窗单选，这里用 prompt 简化）。 */
  const mediaPreference = async () => {
    const cur = bg?.media ?? 'auto'
    const label = (m: string) => m === 'image' ? '仅图片' : m === 'video' ? '仅视频' : '混合'
    const ans = window.prompt(
      `背景媒体类型（当前：${label(cur)}）\n\n输入序号切换：\n1 = 仅图片\n2 = 仅视频\n3 = 混合（图片和视频一起竞赛）`,
      cur === 'image' ? '1' : cur === 'video' ? '2' : '3',
    )
    if (!ans) return
    const map: Record<string, string> = { '1': 'image', '2': 'video', '3': 'auto' }
    const media = map[ans.trim()]
    if (!media) { window.alert('请输入 1 / 2 / 3'); return }
    try {
      const r = await fetch('/newapi/background/media', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ media }),
      })
      const j = await r.json()
      if (j.success) { setBg(v => v ? { ...v, media } : v); location.reload() }
      else window.alert('设置失败：' + (j.error || '未知错误'))
    } catch (e) { window.alert('设置失败：' + String(e)) }
  }

  /** 图源管理：打开正式弹窗（对齐老 UI 四操作：添加/启停/删除/恢复默认）。 */
  const manageSources = () => { setShowSources(true) }

  const downloadWallpaper = () => {
    void downloadCurrentWallpaper().then(result => {
      if (result === 'none') window.alert('当前还没有加载出背景图，请稍候再试')
    }).catch(e => window.alert('保存失败：' + String(e)))
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

  // 按钮点击用**React 的 onClick 事件委托**（HTML 是字符串注入的，没法给每个按钮挂 onClick）。
  //
  // 坑：原来写的是 `ref={el => { el.onclick = ... }}`。ref 回调只在挂载/卸载时跑，
  // 而 dangerouslySetInnerHTML 在每次重渲染都会**替换整个 innerHTML** ——
  // 新生成的按钮上没有 onclick，点击变成彻底的空操作。
  // 实测：点「English」后 Network 面板零请求、/newapi/language 零调用、
  // deploy.yaml 与界面语言纹丝不动（长卿问「语言修改好没有？」就是这么来的）。
  //
  // 用 React 的 onClick 挂在容器上就不会有这个问题：React 的事件系统是
  // 根节点委托，DOM 被替换后依然生效。
  const onHomeClick = (ev: React.MouseEvent<HTMLDivElement>) => {
    const target = (ev.target as HTMLElement).closest('button')
    if (!target) return
    const label = (target.textContent ?? '').trim()
    const lang = LANGS.find(l => l.label === label)
    if (lang) { void onClickLanguage(lang.value)(); return }
    const theme = THEMES.find(t => t.name === label)
    if (theme) { void onClick({ Theme: theme.value })(); return }
    if (label === '下载当前背景图') { downloadWallpaper(); return }
    if (label === '上传自定义背景') { uploadBackground(); return }
    if (label === '随机背景') { void randomBackground(); return }
    if (label === '管理图源') { void manageSources(); return }
    if (label === '媒体类型') { void mediaPreference(); return }
  }

  return (
    // 标题与侧栏「主页」同一份文案（后端按当前语言翻译），
    // 否则切英文后侧栏是 Home、页面标题还是「主页」。
    <Shell active="Home" title={pageTitle}>
        {showSources && (
          <WallpaperSourcesModal
            onClose={() => setShowSources(false)}
            onChanged={() => { /* 背景由 wallpaperRace 轮询，无需即时刷新 */ }}
          />
        )}
      <div
        id="pywebio-scope-content"
        onClick={onHomeClick}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </Shell>
  )
}
