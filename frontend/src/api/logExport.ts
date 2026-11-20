// 日志导出的下载通道：**关键参数一律走 path，不用 query**。
//
// 为什么（2026-11-20 实测 remurl 远控中继 /89982203008c27b2）：
//   GET /api/log/runtime/alas/today                 → 200，14MB 日志正常
//   GET /api/log/runtime?instance=alas&scope=today  → 400 {"error":"缺少实例名"}
// 中继会把 query string 整段剥掉，后端拿不到 instance 就直接 400。
// 同一个坑 2026-09-15 就在老 UI 踩过（技能里记作"坑 2"），当时的修法是
// 「path 形式排第一 + 前端多候选逐个试」；新 UI 这三处又退回了只传 query，于是复发。
//
// 所以这里照抄老 UI 的既有实现作为标准：
//   app_overview.py:104        runtime today → [/{inst}/today, ?instance=&scope=, /{inst}?scope=]
//   app_developer_tools.py:566 runtime       → [/{inst}/{scope}, ?instance=&scope=, /{inst}?scope=]
//   app_developer_tools.py:588 error         → [/{scope}, ?scope=]
// query 形式只保留在最后，作为"绕过中继直连"时的兜底 —— 它永远排在 path 之后，
// 否则会以 HTTP 200 返回默认范围而被当成成功（用户拿到错内容却毫无提示）。
import { withPrefix } from './client'

/** 依次尝试多个接口地址，返回第一个 2xx 响应。
 *
 * 全部失败时返回**最后一次**响应（而不是抛错），让调用方有机会读出后端给的
 * 具体原因（如「缺少实例名」「实例 alas 在 2026-11-20 没有运行日志」），
 * 而不是只丢一个裸的 HTTP 状态码给用户。
 */
export async function fetchFirst(paths: string[]): Promise<Response> {
  let last: Response | null = null
  let lastErr: unknown = null
  for (const p of paths) {
    try {
      const r = await fetch(withPrefix(p), { cache: 'no-store' })
      if (r.ok) return r
      last = r
    } catch (e) {
      lastErr = e
    }
  }
  if (last) return last
  throw lastErr instanceof Error ? lastErr : new Error('network error')
}

/** 从 Content-Disposition 解析文件名。
 *
 * Starlette 对非 ASCII 文件名（如实例「小号」）用 RFC 5987 的
 * `filename*=utf-8''<pct>` 形式，普通 ASCII 才用 `filename="x"`，两式都要认。
 */
export function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null
  const star = header.match(/filename\*=(?:utf-8|UTF-8)''([^;]+)/)
  if (star) {
    try {
      return decodeURIComponent(star[1])
    } catch {
      return star[1]
    }
  }
  const plain = header.match(/filename="?([^";]+)"?/)
  return plain ? plain[1] : null
}

/** 读出接口失败时后端给的中文原因，拿不到就退回 HTTP 状态码。
 *
 * 老 UI 的按钮会把这段文案放进 title 里，新 UI 之前只显示裸 `HTTP 400`，
 * 用户和排查的人都看不出到底是谁在 400 —— 所以这里必须把它取出来。
 */
export async function responseErrorDetail(resp: Response): Promise<string> {
  const fallback = 'HTTP ' + resp.status
  try {
    const text = await resp.text()
    if (!text) return fallback
    try {
      const j = JSON.parse(text) as { error?: unknown }
      if (j && typeof j.error === 'string' && j.error) return 'HTTP ' + resp.status + ': ' + j.error
    } catch {
      // 不是 JSON，退回原文片段
    }
    const brief = text.replace(/\s+/g, ' ').trim().slice(0, 200)
    return brief ? 'HTTP ' + resp.status + ': ' + brief : fallback
  } catch {
    return fallback
  }
}

/** 把响应落成浏览器下载，返回最终文件名。
 *
 * 必须走 HTTP + Blob：pywebio 的 download() 把字节塞进自己的 WebSocket，
 * 十几 MB 会撞上 2048 条积压上限而被主动断连。
 */
export async function saveResponse(resp: Response, fallback: string): Promise<string> {
  const blob = await resp.blob()
  const name = filenameFromDisposition(resp.headers.get('content-disposition')) || fallback
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60000)
  return name
}

/** 实例的运行日志，scope 为 all / today / YYYY-MM-DD。
 *
 * `today` 由**服务端**解析成真实日期，不要用客户端 new Date() 算：
 * 远控时手机（或异地浏览器）的时区/时钟可能和服务器不一致，会算成别的天，
 * 表现为 404 或导错那一天。
 */
export async function downloadRuntimeLog(instance: string, scope: string): Promise<string> {
  const inst = encodeURIComponent(instance)
  const sc = encodeURIComponent(scope)
  const resp = await fetchFirst([
    `/api/log/runtime/${inst}/${sc}`,
    `/api/log/runtime?instance=${inst}&scope=${sc}`,
    `/api/log/runtime/${inst}?scope=${sc}`,
  ])
  if (!resp.ok) throw new Error(await responseErrorDetail(resp))
  return saveResponse(resp, `${instance}_runtime_log_${scope}.txt`)
}

/** log/error 目录打包，scope 为 full（含截图）/ text（仅文本）。 */
export async function downloadErrorLog(scope: 'full' | 'text'): Promise<string> {
  const sc = encodeURIComponent(scope)
  const resp = await fetchFirst([
    `/api/log/error/${sc}`,
    `/api/log/error?scope=${sc}`,
  ])
  if (!resp.ok) throw new Error(await responseErrorDetail(resp))
  return saveResponse(resp, `error_log_${scope}.zip`)
}
