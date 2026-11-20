// 日志面板（从总览页抽出的可复用部分）。
//
// 老 UI 的日志是 RichLog：服务端只推增量 HTML 片段，前端按游标追加。
// 这里逐条对齐那套语义（见 module/webapi/data.py:get_log_tail 的说明）：
//
//   1. 宽度必须与老 UI RichLog.get_width() 同口径，否则 rich 换行位置不同，
//      日志看起来「提前折行」。算法：(容器宽-16)/font-size*16/单字宽，钳 80..128。
//   2. full=true 表示游标失效/环形回绕，必须整块重建，否则旧内容残留。
//   3. 分块插入 + 200 块上限（RichLog.dom_max_chunks），逐节点删会残留裸文本节点。
//   4. 页面隐藏时暂停轮询（visibilitychange）。
import { useEffect, useRef } from 'react'
import { instSeg } from '../../api/client'
import { downloadRuntimeLog } from '../../api/logExport'

export function LogPanel({ instance, autoScroll = true }: { instance: string; autoScroll?: boolean }) {
  // 自动滚动开关由父组件持有（它在 log-bar 里，日志区自己看不到那个按钮）
  const autoScrollRef = useRef(true)
  autoScrollRef.current = autoScroll

  // 与老 UI widgets.py:get_width 同口径量宽
  const measureWidth = (scope: HTMLElement): number => {
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    if (!ctx) return 80
    ctx.font = '16px Menlo, consolas, DejaVu Sans Mono, Courier New, monospace'
    const charWidth = ctx.measureText('0').width || 8.8
    const fontSize = parseFloat(getComputedStyle(scope).fontSize) || 13.6
    const raw = ((scope.clientWidth - 16) / fontSize) * (16 / charWidth)
    if (!isFinite(raw) || raw <= 0) return 80
    return raw > 128 ? 128 : raw < 80 ? 80 : Math.floor(raw)
  }

  useEffect(() => {
    let cursor = -1
    let width = 80
    let measured = false
    let stopped = false
    let visible = !document.hidden
    const onVis = () => { visible = !document.hidden }
    document.addEventListener('visibilitychange', onVis)

    const tick = () => {
      if (stopped || !visible) return
      const scope = document.getElementById('pywebio-scope-log') as HTMLElement | null
      if (!scope) return
      if (!measured) { width = measureWidth(scope); measured = true }
      fetch(`/newapi/log/${instSeg(instance)}/tail?since=${cursor}&width=${width}`)
        .then(r => (r.ok ? r.json() : null))
        .then((j: { html: string; cursor: number; full: boolean } | null) => {
          if (!j || stopped) return
          cursor = j.cursor
          if (!j.html) return
          let box = scope.querySelector(':scope>div') as HTMLElement | null
          if (!box) {
            box = document.createElement('div')
            scope.innerHTML = ''
            scope.appendChild(box)
          }
          const chunk = document.createElement('div')
          chunk.className = 'alas-log-chunk'
          chunk.style.display = 'contents'
          chunk.innerHTML = j.html
          if (j.full) { box.innerHTML = ''; box.appendChild(chunk) }
          else {
            box.appendChild(chunk)
            while (box.children.length > 200) box.removeChild(box.firstElementChild as Node)
          }
          if (autoScrollRef.current) {
            requestAnimationFrame(() => { scope.scrollTop = scope.scrollHeight })
          }
        })
        .catch(() => {})
    }
    const timer = setInterval(tick, 2000)
    const first = setTimeout(tick, 400)
    return () => {
      stopped = true
      clearInterval(timer)
      clearTimeout(first)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [instance])

  // 结构照抄老 UI alas_daemon_overview：
  //   #pywebio-scope-_daemon > #pywebio-scope-log[style="grid-row:-2/-1"] > <div/>
  // 注意**没有** #pywebio-scope-log-container：那个（带 --version 水印）是「总览」页
  // 的写法，工具页用的是裸 #pywebio-scope-log + 显式 grid-row（多一个包裹层会
  // 让 alas-pc.css 的 _daemon 三行 grid 算错行号，日志把配置卡压扁）。
  return (
    <div id="pywebio-scope-log" style={{ gridRow: '-2 / -1' }}>
      <div />
    </div>
  )
}

/** 导出今日日志：走老 UI 现成的 /api/log/runtime（today 范围）。
 *
 * ⚠️ 这里原来是 window.open + query 参数，两个远控坑同时踩中：
 * - window.open 打开的是裸 URL，不带 /{peer} 前缀 → 中继 502（2026-11-18 实测）；
 * - ?instance=&scope= 会被中继整段剥掉 → HTTP 400「缺少实例名」（2026-11-20 实测）。
 * 现在改成 fetch + blob + path 形式的候选 URL（见 api/logExport.ts）。
 */
export function exportTodayLog(instance: string) {
  downloadRuntimeLog(instance, 'today').catch(e => {
    // 工具页没有状态行，反馈落在按钮 title 上（与老 UI fail() 一致）
    if (window.console) console.warn('[log-export] ' + String(e))
    window.alert('导出失败：' + String(e))
  })
}