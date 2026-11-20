// 活动计算器 —— 方案3（长卿 2026-11-20 选定）。
//
// 布局：左 270px（输入+日常） | 中 flex（商店，最宽） | 右 250px（出击+写回）
// KPI 顶栏 5 格 + 进度条；商店每行带「数字框 + 滑块」双向联动。
//
// 滑块语义来自老 UI（module/webui/event_calculator.py:642-650）：
//   每行 = 商品 | 单价 | [数字框][range][小计] | ×库存上限
//   拖动滑块 → 数字跟着变；改数字 → 滑块跟着变；总价实时汇总。
//   上限是该商品库存（quantity），不是随便填。
//
// 位置修正：计算器在 #pywebio-scope-_groups 里（grid-auto-flow:column），
// 每个直接子元素各占一列 → 只分到 400px。CSS 用 grid-column 跨满 +
// grid-row 另起一行解决（只写 grid-column 会和第一行重叠）。
//
// 数据流不变：
//   GET  /newapi/event-calculator/{name}[?refresh=1]
//   POST /newapi/config/{name}/task/EventGeneral
import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiGet, instSeg } from '../../api/client'
import { useToast } from '../../components/Toast'

type ShopItem = { name: string; price: number; quantity: number }
type Stage = { name: string; points: number }
type Flag = { never: boolean; already: boolean }

type CalcData = {
  success: boolean
  error?: string
  event_name: string
  end_date: string
  shop_items: ShopItem[]
  shop_total: number
  stages: Stage[]
  daily: Record<string, Flag>
  extra: Record<string, Flag>
  wiki_daily?: { name: string; points: number }[]
  source_url: string
  updated_at: string
  from_cache: boolean
  target: number
  owned: number
}

/** 商店某一行：库存上限 + 当前选择数量 */
type ShopRow = ShopItem & { value: number }

export function EventCalculator({ name }: { name: string }) {
  const toast = useToast()
  const [data, setData] = useState<CalcData | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState('')

  // 目标/已有/结束日期
  const [target, setTarget] = useState(0)
  const [owned, setOwned] = useState(0)
  const [endDate, setEndDate] = useState('')

  // 商店选择数量（默认全 0，用户自己拖）
  const [shop, setShop] = useState<ShopRow[]>([])
  // 商店变动同步目标（老 UI data-field="auto-target"，默认开）
  const [autoTarget, setAutoTarget] = useState(true)

  // 日常/额外勾选
  const [daily, setDaily] = useState<Record<string, Flag>>({})
  const [extra, setExtra] = useState<Record<string, Flag>>({})

  const load = useCallback((force = false) => {
    if (force) setRefreshing(true)
    apiGet<CalcData>(
      `/newapi/event-calculator/${instSeg(name)}${force ? '?refresh=1' : ''}`,
      30000,
    )
      .then(r => {
        if (r.success === false) { setErr(r.error || '加载失败'); return }
        setData(r)
        setErr('')
        setTarget(Number(r.target) || 0)
        setOwned(Number(r.owned) || 0)
        setEndDate(r.end_date || '')
        setDaily(r.daily || {})
        setExtra(r.extra || {})
        // 商店：带上库存上限，初始全选 0（与老 UI 一致，用户自己挑）
        setShop((r.shop_items || []).map(it => ({
          name: it.name,
          price: Number(it.price) || 0,
          quantity: Number(it.quantity) || 0,
          value: 0,
        })))
      })
      .catch(e => setErr(String(e)))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [name])

  useEffect(() => { void load() }, [load])

  /** 勾选总价 = Σ(单价 × 选择数量) */
  const shopSelTotal = useMemo(() => {
    return shop.reduce((sum, it) => sum + it.price * it.value, 0)
  }, [shop])

  /** 剩余天数 */
  const remainingDays = useMemo(() => {
    if (!endDate) return null
    const end = new Date(endDate + 'T23:59:59')
    const now = new Date()
    const days = Math.ceil((end.getTime() - now.getTime()) / 86400000)
    return Number.isNaN(days) ? null : days
  }, [endDate])

  /** 日常任务每日 PT 合计 */
  const perDay = useMemo(() => {
    if (!data) return 0
    return (data.wiki_daily || [])
      .filter(it => it.name && daily[it.name] && !daily[it.name].never)
      .reduce((s, it) => s + (Number(it.points) || 0), 0)
  }, [data, daily])

  const remain = Math.max(0, target - owned)
  const pct = target > 0 ? Math.min(100, (owned / target) * 100) : 0

  /** 商店某行数量变更（数字框与滑块共用）*/
  const setShopValue = useCallback((index: number, raw: number) => {
    setShop(prev => {
      const next = prev.slice()
      const row = next[index]
      if (!row) return prev
      // 上限是该商品库存，不是随便填（老 UI setShopValue 同语义）
      const v = Math.max(0, Math.min(Math.floor(Number(raw) || 0), row.quantity));
      next[index] = { ...row, value: v }
      // 商店变动同步目标
      if (autoTarget) {
        const total = next.reduce((s, it) => s + it.price * it.value, 0)
        setTarget(total)
      }
      return next
    })
  }, [autoTarget])

  const clearShop = () => setShop(prev => prev.map(it => ({ ...it, value: 0 })))
  const fillShop = () => setShop(prev => prev.map(it => ({ ...it, value: it.quantity })))

  /** 写回配置 */
  const savePatch = useCallback(async (patch: Record<string, any>, label: string) => {
    setSaving(label)
    try {
      const res = await fetch(`/newapi/config/${instSeg(name)}/task/EventGeneral`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ modified: patch }),
      })
      const j = await res.json().catch(() => null)
      if (!res.ok || !j || j.error) {
        toast.error('写入失败: ' + (j?.error || ('HTTP ' + res.status)))
        return false
      }
      toast.ok(label + '成功')
      return true
    } catch (e) {
      toast.error('写入失败: ' + String(e))
      return false
    } finally {
      setSaving('')
    }
  }, [name, toast])

  const writeTarget = () =>
    savePatch({ 'EventGeneral.EventGeneral.PtLimit': Number(target) || 0 }, '写入目标 PT')
  const writeTime = () => {
    if (!endDate) { toast.error('结束日期为空'); return Promise.resolve(false) }
    return savePatch({ 'EventGeneral.EventGeneral.TimeLimit': endDate + ' 23:59:59' }, '写入结束时间')
  }
  const writeBoth = () => {
    if (!endDate) { toast.error('结束日期为空'); return Promise.resolve(false) }
    return savePatch({
      'EventGeneral.EventGeneral.PtLimit': Number(target) || 0,
      'EventGeneral.EventGeneral.TimeLimit': endDate + ' 23:59:59',
    }, '写入目标 PT 和结束时间')
  }
  const writeShopFilter = () => {
    const names = shop.filter(it => it.value > 0).map(it => it.name)
    if (!names.length) { toast.error('请先选择要兑换的商品'); return Promise.resolve(false) }
    return savePatch({
      'EventShop.EventShop.PresetFilter': 'custom',
      'EventShop.EventShop.CustomFilter': names.join(' > '),
    }, '写入商店过滤器')
  }

  if (loading) return <div style={{ opacity: .7, margin: '10px 0' }}>活动计算器加载中…</div>
  if (err) {
    return (
      <div className="ec-root">
        <div className="ec-card">
          <div style={{ color: 'var(--alas-entry-badge-error, #c0392b)', marginBottom: 8 }}>
            活动计算器：{err}
          </div>
          <button disabled={refreshing} onClick={() => void load(true)}>
            {refreshing ? '拉取中…' : '重新从 Wiki 拉取'}
          </button>
        </div>
      </div>
    )
  }
  if (!data) return null

  // 时间不够的警告（老 UI 没这功能，方案3 新增）
  const daysNeeded = perDay > 0 ? Math.ceil(remain / perDay) : 0
  const timeShort = remainingDays !== null && remainingDays > 0 && perDay > 0 && daysNeeded > remainingDays

  return (
    <div className="ec-root">

      {/* KPI 顶栏 5 格 */}
      <div className="ec-kpis">
        <div className="ec-kpi hi">
          <div className="ec-kpi-k">活动</div>
          <div className="ec-kpi-v" title={data.event_name}>{data.event_name || '活动'}</div>
          <div className="ec-kpi-d">结束 {data.end_date || '—'}</div>
        </div>
        <div className="ec-kpi">
          <div className="ec-kpi-k">商店总价</div>
          <div className="ec-kpi-v">{(data.shop_total || 0).toLocaleString()}</div>
          <div className="ec-kpi-d">全部兑换</div>
        </div>
        <div className="ec-kpi">
          <div className="ec-kpi-k">目标</div>
          <div className="ec-kpi-v">{target.toLocaleString()}</div>
          <div className="ec-kpi-d">达成 {pct.toFixed(1)}%</div>
          <div className="ec-prog"><i style={{ width: pct + '%' }} /></div>
        </div>
        <div className="ec-kpi ok">
          <div className="ec-kpi-k">已有</div>
          <div className="ec-kpi-v">{owned.toLocaleString()}</div>
          <div className="ec-kpi-d">{owned.toLocaleString()} / {target.toLocaleString()}</div>
        </div>
        <div className="ec-kpi danger">
          <div className="ec-kpi-k">还差</div>
          <div className="ec-kpi-v">{remain.toLocaleString()}</div>
          <div className="ec-kpi-d">
            {remainingDays === null ? '—' : remainingDays > 0 ? `需 ${daysNeeded} 天` : '已结束'}
          </div>
        </div>
      </div>

      {/* 工具栏 */}
      <div className="ec-toolbar">
        <span className="ec-chip accent">勾选总价 <b>{shopSelTotal.toLocaleString()}</b></span>
        <label className="ec-chip" style={{ gap: 5 }}>
          <input type="checkbox" checked={autoTarget} onChange={e => setAutoTarget(e.target.checked)} />
          商店变动同步目标
        </label>
        <button type="button" onClick={() => setTarget(shopSelTotal)} disabled={shopSelTotal <= 0}>
          导入商店总价
        </button>
        <button type="button" onClick={clearShop}>清空兑换</button>
        <button type="button" onClick={fillShop}>重置兑换</button>
        <button type="button" disabled={refreshing} onClick={() => void load(true)}>
          {refreshing ? '刷新中…' : '刷新 Wiki 数据'}
        </button>
      </div>

      {/* 三栏主体 */}
      <div className="ec-grid3">

        {/* 左栏：目标与收益 + 日常 */}
        <div>
          <div className="ec-card">
            <h2>目标与收益</h2>
            <div className="ec-fields">
              <label>目标点数
                <input type="number" min={0} step={1} value={target}
                  onChange={e => setTarget(Number(e.target.value) || 0)} />
              </label>
              <label>已有点数
                <input type="number" min={0} step={1} value={owned}
                  onChange={e => setOwned(Number(e.target.value) || 0)} />
              </label>
              <label>结束日期
                <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
              </label>
              <label>剩余天数
                <input type="number" value={remainingDays ?? ''} readOnly />
              </label>
            </div>
            {timeShort && (
              <div className="ec-warn">
                按当前日收益还需 {daysNeeded} 天，活动结束前刷不完：
                每天需 {Math.ceil(remain / Math.max(1, remainingDays!))} PT。
              </div>
            )}
          </div>

          <div className="ec-card">
            <h2>日常任务</h2>
            <table>
              <tbody>
                {(data.wiki_daily || []).map(it => (
                  <tr key={it.name}>
                    <td>{it.name}</td>
                    <td className="ec-num">{it.points}</td>
                    <td style={{ width: 26 }}>
                      <input type="checkbox"
                        checked={!!daily[it.name] && !daily[it.name].never}
                        onChange={e => setDaily(m => ({
                          ...m,
                          [it.name]: { never: !e.target.checked, already: !!daily[it.name]?.already },
                        }))} />
                    </td>
                  </tr>
                ))}
                {Object.entries(extra).map(([nm, f]) => (
                  <tr key={nm}>
                    <td>{nm}</td>
                    <td className="ec-num">—</td>
                    <td style={{ width: 26 }}>
                      <input type="checkbox"
                        checked={!!f && !f.never}
                        onChange={e => setExtra(m => ({
                          ...m,
                          [nm]: { never: !e.target.checked, already: !!f?.already },
                        }))} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="ec-foot">每日收益 {perDay} PT</div>
          </div>
        </div>

        {/* 中栏：商店（最宽，带滑块）*/}
        <div className="ec-card" style={{ margin: 0 }}>
          <h2>
            兑换商店
            <span style={{ marginLeft: 'auto', fontWeight: 400, fontSize: 10, opacity: .7 }}>
              拖动滑块选数量
            </span>
          </h2>
          <div className="ec-shopwrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: '28%' }}>商品</th>
                  <th className="ec-num" style={{ width: '11%' }}>单价</th>
                  <th style={{ width: '47%' }}>兑换数量</th>
                  <th className="ec-num" style={{ width: '14%' }}>小计</th>
                </tr>
              </thead>
              <tbody>
                {shop.map((it, i) => {
                  const sub = it.price * it.value;
                  return (
                    <tr key={it.name + it.price} className="ec-shoprow">
                      <td>{it.name}</td>
                      <td className="ec-num">{it.price.toLocaleString()}</td>
                      <td>
                        <div className="ec-slidercell">
                          <input className="ec-qty" type="number" min={0} max={it.quantity}
                            value={it.value}
                            onChange={e => setShopValue(i, Number(e.target.value))} />
                          <input type="range" min={0} max={it.quantity}
                            value={it.value}
                            onChange={e => setShopValue(i, Number(e.target.value))} />
                          <span className={'ec-subc' + (sub ? '' : ' zero')}>
                            {sub ? sub.toLocaleString() : '—'}
                          </span>
                        </div>
                      </td>
                      <td className="ec-num" style={{ fontSize: 10, opacity: .6 }}>
                        ×{it.quantity}
                      </td>
                    </tr>
                  );
                })}
                {!shop.length && <tr><td colSpan={4}>Wiki 未给出商店数据</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        {/* 右栏：出击数 + 写回 */}
        <div>
          <div className="ec-card">
            <h2>出击数计算</h2>
            <table>
              <thead>
                <tr>
                  <th>关卡</th>
                  <th className="ec-num">PT</th>
                  <th className="ec-num">还需</th>
                </tr>
              </thead>
              <tbody>
                {(data.stages || []).map(s => {
                  const pts = Number(s.points) || 0;
                  return (
                    <tr key={s.name}>
                      <td>{s.name}</td>
                      <td className="ec-num">{pts || '—'}</td>
                      <td className="ec-num">
                        {pts > 0 ? Math.ceil(remain / pts).toLocaleString() : '—'}
                      </td>
                    </tr>
                  );
                })}
                {!data.stages?.length && <tr><td colSpan={3}>Wiki 未给出关卡数据</td></tr>}
              </tbody>
            </table>
          </div>

          <div className="ec-card">
            <h2>写回配置</h2>
            <div className="ec-acts">
              <button type="button" className="primary" disabled={!!saving}
                onClick={() => void writeTarget()}>
                {saving === '写入目标 PT' ? '写入中…' : '写入目标 PT'}
              </button>
              <button type="button" disabled={!!saving} onClick={() => void writeTime()}>
                {saving === '写入结束时间' ? '写入中…' : '写入结束时间'}
              </button>
              <button type="button" disabled={!!saving} onClick={() => void writeBoth()}>
                {saving === '写入目标 PT 和结束时间' ? '写入中…' : '写入目标和时间'}
              </button>
              <button type="button" disabled={!!saving} onClick={() => void writeShopFilter()}>
                {saving === '写入商店过滤器' ? '写入中…' : '写入商店过滤器'}
              </button>
            </div>
            <div className="ec-foot">
              写回即保存到活动通用设置 / 活动商店，调度器按此执行。
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}