// 维护等待横幅：老 UI 概览页那条「服务器维护中 · cn_android-12 · 模拟器保持运行，等待开服」。
//
// 为什么直接复用老 UI 的 class 而不是重写样式：
//   alas.css:1473-1570 与 dark-alas.css:378-403 已经把这套横幅（含暗色/开服态）
//   写全了，DOM 契约是 .alas-maint-banner__{dot,body,state,server,desc,meta,since,next}。
//   逐字节复刻 DOM，样式自动一致，也不违背「不改共享 CSS」的红线。
//
// 数据与文案全部来自后端 /newapi/maintenance/{name}（与老 UI 同源：
// log/<name>.maintenance.json，normalize 逻辑也复用 maintenance_banner.normalize），
// 前端只负责每秒走表 + 到点隐藏。
import { useEffect, useRef, useState } from 'react'
import { apiGet } from '../../api/client'

type MaintState = {
  opened: boolean
  emulator_closed: boolean
  server: string
  since: number
  last_poll: number
  poll_seconds: number
}
type Labels = {
  state?: string; state_open?: string; desc?: string
  desc_running?: string; desc_open?: string
  elapsed?: string; next?: string
}

/** 秒数 → mm:ss / h:mm:ss（与老 UI build_js 的 fmt 同款）。 */
function fmt(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) seconds = 0
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = s % 60
  const pad = (n: number) => (n < 10 ? '0' + n : '' + n)
  return (h ? h + ':' : '') + pad(m) + ':' + pad(ss)
}

/** 「服务器已开服」横幅的停留时长，与 maintenance_banner.OPENED_TTL 一致。 */
const OPENED_TTL = 8

export function MaintenanceBanner({ name }: { name: string }) {
  const [state, setState] = useState<MaintState | null>(null)
  const [labels, setLabels] = useState<Labels>({})
  const [, force] = useState(0)
  const openedAtRef = useRef(0)

  useEffect(() => {
    let alive = true
    const load = () => {
      apiGet<{ show: boolean; state: MaintState | null; labels: Labels }>(
        '/newapi/maintenance/' + encodeURIComponent(name), 8000,
      )
        .then(r => {
          if (!alive) return
          setLabels(r.labels || {})
          setState(prev => {
            const next = r.show ? r.state : null
            // 开服态出现的瞬间记下时间，供 TTL 收起
            if (next?.opened && !prev?.opened) openedAtRef.current = Date.now()
            return next
          })
        })
        .catch(() => { /* 端点不可用：不显示横幅，不影响总览 */ })
    }
    load()
    // 老 UI 是跟着总览后台任务（约 2s）刷新的；这里 3s 轮询，
    // 维护期间实例每 60 秒才重写一次状态文件，3s 足够及时。
    const timer = setInterval(load, 3000)
    return () => { alive = false; clearInterval(timer) }
  }, [name])

  // 每秒走表：已等待 / 下次查询（服务端不必高频轮询）
  useEffect(() => {
    if (!state) return
    const timer = setInterval(() => force(v => v + 1), 1000)
    return () => clearInterval(timer)
  }, [state])

  if (!state) return null

  const opened = !!state.opened
  const desc = opened
    ? labels.desc_open
    : state.emulator_closed ? labels.desc : labels.desc_running
  const nowSec = Date.now() / 1000
  const elapsed = nowSec - (state.since || 0)
  const nextLeft = (state.last_poll || 0) + (state.poll_seconds || 60) - nowSec

  // 开服态停留 OPENED_TTL 秒后自行收起（老 UI 同款，由 JS 端负责）
  if (opened && openedAtRef.current && Date.now() - openedAtRef.current > OPENED_TTL * 1000) {
    return null
  }

  return (
    <div className={'alas-maint-banner' + (opened ? ' is-opened' : '')}>
      <span className="alas-maint-banner__dot"></span>
      <span className="alas-maint-banner__body">
        <b className="alas-maint-banner__state">{opened ? labels.state_open : labels.state}</b>
        {/* 服务器名空配置时不显示（老 UI build_js 的同款处理），避免「· ·」 */}
        {state.server ? <span className="alas-maint-banner__server">{'· ' + state.server + ' ·'}</span> : null}
        <span className="alas-maint-banner__desc">{desc}</span>
      </span>
      <span className="alas-maint-banner__meta">
        <span>{labels.elapsed} <b className="alas-maint-banner__since">{fmt(elapsed)}</b></span>
        <span>{labels.next}{' '}
          <b className="alas-maint-banner__next">
            {opened ? fmt(elapsed) : (nextLeft > 0 ? fmt(nextLeft) : '...')}
          </b>
        </span>
      </span>
    </div>
  )
}
