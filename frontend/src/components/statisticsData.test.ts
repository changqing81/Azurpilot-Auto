import { describe, expect, it } from 'vitest'
import { aggregatePoints } from './statisticsData'

describe('统计时间聚合', () => {
  it('K 线保留开高低收以及零值，不用平均值代替收盘', () => {
    const points = [10, 30, 0, 20].map((value, index) => ({time: `2026-09-13 10:0${index}:00`, value}))
    expect(aggregatePoints(points, 60)).toEqual([{time: '2026-09-13 10:00:00', open: 10, close: 20, low: 0, high: 30}])
  })
  it('日聚合按本地自然日分桶，原始视图保留同一时刻的多次记录', () => {
    const points = [{time: '2026-09-13 23:59:00', value: 1}, {time: '2026-09-14 00:01:00', value: 2}, {time: '2026-09-14 00:01:00', value: 3}]
    expect(aggregatePoints(points, 1440)).toHaveLength(2)
    expect(aggregatePoints(points, 0)).toHaveLength(3)
  })
})
