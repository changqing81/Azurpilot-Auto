// API 客户端基座：fetch 封装（超时/错误归一）。端点随 module/webapi 落地逐个补。

// ⚠️ 远控中继的路径前缀（长卿 2026-11-17 报「远控端完全加载不出来，
// 其他人的远控也是这样」）。
//
// remurl.a1.nanoda.work 这类中继把每个客户端挂在 /{peer_id} 下，且**只认
// 带 peer 前缀的路径**：
//   /89982203008c27b2?app=index        → 200 页面
//   /newapi/ui                          → 502（不带前缀，中继不知道转发给谁）
//   /89982203008c27b2/newapi/ui         → 200
// 所以 API 请求不能写死绝对路径 /newapi/…，必须带上当前页面的路径前缀。
//
// 从 location.pathname 提取前缀：
//   '/89982203008c27b2'      → '/89982203008c27b2'
//   '/89982203008c27b2/'     → '/89982203008c27b2'
//   '/new/'                  → '/new'（旧的双前缀挂载形态）
//   '/'                      → ''（直连，无前缀）
// 用纯 16 进制 / 字母数字段判定，避免把 /new 之外的业务路由误当前缀。
function relayPrefix(): string {
  if (typeof window === 'undefined') return ''
  const segs = window.location.pathname.split('/').filter(Boolean)
  if (!segs.length) return ''
  const first = segs[0]
  // 已知的业务路径段，不是中继前缀
  const RESERVED = ['new', 'assets', 'newapi', 'static', 'pywebio_static', 'api']
  if (RESERVED.indexOf(first) >= 0) return ''
  // 中继 peer_id：16~64 位十六进制串（remurl 是 16 位纯 hex）
  if (/^[0-9a-f]{8,64}$/i.test(first)) return '/' + first
  return ''
}

/** 把 '/newapi/xxx' 变成 '<relayPrefix>/newapi/xxx'（直连时前缀为空）。 */
export function withPrefix(path: string): string {
  const p = path.startsWith('/') ? path : '/' + path
  return relayPrefix() + p
}


/** 实例名 → 路径段：
 *
 * 远控中继拒绝百分号编码的多字节（中文等）路径段（400 Invalid HTTP request），
 * 直连则无此问题。所以统一把实例名编码成 base64url 并加 b64_ 前缀 ——
 * 纯 ASCII 字符集中继必放行；后端 B64InstanceNameMiddleware 在 ASGI 层
 * 还原成原名，handler 无感知。ASCII 实例名（如 alas）不做编码，URL 保持可读。
 */
export function instSeg(name: string): string {
  if (!/^[_a-zA-Z0-9]+$/.test(name)) {
    const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(name)))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    return 'b64_' + b64
  }
  return encodeURIComponent(name)
}

export async function apiGet<T>(path: string, timeoutMs = 8000): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(withPrefix(path), {
      signal: ctrl.signal,
      headers: { Accept: 'application/json' },
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return (await res.json()) as T
  } finally {
    clearTimeout(timer)
  }
}


/** 把面板参数编码进路径段，供远控中继场景使用。
 *
 * 为什么需要（长卿 2026-11-17 远控截图：统计页出现两份「净赚体力」、
 * 没有作战次数、文字竖排）：远控中继转发时会**剥掉 query string**。
 * 实测：经 remurl 请求 ?panel=ship_exp，到达后端时 panel 为空 → 后端默认
 * 走 opsi，于是「舰船经验」位置渲染出净赚体力卡，页面上出现两份净赚体力。
 * 路径段不会被剥，所以把参数塞进路径。
 *
 * 编码约定：每个参数取 encodeURIComponent，空值用 "_" 占位
 * （空路径段会被服务端/浏览器折叠掉）。后端 _arg() 会把 "_" 还原成空串。
 */
/**
 * 全参数路径通道（远控根治方案）。
 *
 * 远控中继（remurl 等）会把 query string **整段剥掉**，所以
 *   ?period=days30&scope=series&series=8
 * 到达后端时全部为空 → 按钮点了没反应 / 点第 8 期跳回第 9 期
 * （长卿 2026-11-19：「我说的都是远控，电脑端肯定正常」）。
 *
 * 后端为此开了一条专门路由：
 *   GET /newapi/stat/{name}/panels_html/{panel}/_p/{params}
 * 参数整体编码进一个路径段，中继不剥路径。
 *
 * 编码格式：k=v&k=v（UTF-8 百分号编码整段），空值用 "_"。
 */
export function panelPathWithParams(
  name: string,
  panel: string,
  params: Record<string, string | number | undefined | null>,
): string {
  // ⚠️ 不能把整段 encodeURIComponent：那会把 & 和 = 一起编码成 %26/%3D，
  // 后端 parse_qsl 解析出的是一个完整键名（实测 'period=days30&scope=series' → 空值）。
  // 正确做法：k/v 各自编码，& 与 = 保持原样作为分隔符。
  const pairs: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    pairs.push(encodeURIComponent(k) + '=' + encodeURIComponent(String(v)))
  }
  const payload = pairs.length ? pairs.join('&') : '_'
  return `/newapi/stat/${encodeURIComponent(name)}/panels_html/${encodeURIComponent(panel)}/_p/${payload}`
}

export function panelPath(
  name: string,
  panel: string,
  args: Record<string, string> = {},
): string {
  const seg = (v: string | undefined) =>
    encodeURIComponent(v === undefined || v === '' ? '_' : v)
  // ⚠️ 顺序必须与后端路由的路径段顺序**完全一致**：
  //   /newapi/stat/{name}/panels_html/{panel}/{month}/{task}/{period}/{scope}/{year}/{yearmonth}
  // 后端 series 不在路径段里（它只在 query 里），所以这里也不放 —— 见下方说明。
  // ⚠️ 不要加 series：后端第 8 段路由是在「重启后才可能加载」的新代码，
  // 而 panelPath 是**所有面板共用**的 —— 一旦拼出 8 段，旧进程上所有面板
  // 都会落进 SPA 回退拿到 HTML，等于全部失效。
  // series（科研期数）目前只能走 query；远控中继剥 query 时它会丢，
  // 等后端第 8 段路由确认上线后再单独给科研开一条专用 path builder。
  const order = ['month', 'task', 'period', 'scope', 'year', 'yearmonth']
  const tail = order.map(k => seg(args[k])).join('/')
  return `/newapi/stat/${instSeg(name)}/panels_html/${encodeURIComponent(panel)}/${tail}`
}

/** POST 封装，同样带中继前缀。 */
export async function apiPost<T>(path: string, body?: unknown, timeoutMs = 30000): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(withPrefix(path), {
      method: 'POST',
      signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return (await res.json()) as T
  } finally {
    clearTimeout(timer)
  }
}