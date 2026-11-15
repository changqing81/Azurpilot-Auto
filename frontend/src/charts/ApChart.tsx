// 行力图表：消费老 UI 现成端点 /api/ap_timeline（同源数据）。
// 数据形状：{success, data: [...]}，data 空时显示空态。
import { useEffect, useRef } from 'react'
import type * as EChartsType from 'echarts'
import { apiGet } from '../api/client'

type ApPoint = { time: string | number; value: number; type?: string }
type ApResp = { success: boolean; data: ApPoint[] }

export function ApChart({ name }: { name: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let chart: EChartsType.ECharts | null = null
    let cancelled = false
    Promise.all([
      import('echarts'),
      apiGet<ApResp>('/api/ap_timeline'),
    ]).then(([echarts, resp]) => {
      if (cancelled || !ref.current) return
      chart = echarts.init(ref.current)
      const data = resp.data ?? []
      const sorted = [...data].sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime())
      chart.setOption({
        grid: { left: 50, right: 20, top: 30, bottom: 30 },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'time',
          axisLabel: { formatter: '{MM}-{dd} {HH}:{mm}' },
        },
        yAxis: { type: 'value', name: '行动力' },
        series: [{
          type: 'line',
          data: sorted.map(p => [p.time, p.value]),
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#4e4c97', width: 2 },
          areaStyle: { color: 'rgba(78,76,151,.12)' },
        }],
      })
      const resize = () => chart?.resize()
      window.addEventListener('resize', resize)
      return () => window.removeEventListener('resize', resize)
    }).catch(() => {})
    return () => { cancelled = true; chart?.dispose() }
  }, [name])
  return <div ref={ref} style={{ width: '100%', height: 320 }} />
}
