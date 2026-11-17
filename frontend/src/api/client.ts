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