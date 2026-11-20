// 活动计算器（老 UI app_event_tools.py + event_calculator.py 的 REST 化复刻）。
//
// 数据流：
//   GET  /newapi/event-calculator/{name}           → Wiki 数据 + 本实例初始值
//   GET  /newapi/event-calculator/{name}?refresh=1 → 强制重拉 Wiki
//   POST /newapi/config/{name}/task/EventGeneral   → 写回（复用普通配置保存，
//        modified: {EventGeneral.EventGeneral.PtLimit / TimeLimit} 与
//        {EventShop.EventShop.PresetFilter/CustomFilter}）
//
// 界面对齐老 UI _render_event_calculator：徽章行（活动名/来源/总价）+
// 目标与每日收益（日常/额外表）+ 兑换商店（勾选算价）+ 出击数计算 +
// 五个写回按钮。样式用主题变量（--st-*），四套主题自动跟随。
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

const PANEL: React.CSSProperties = {
  border: '1px solid var(--st-border, rgba(128,128,128,.25))',
  borderRadius: 8,
  padding: '10px 12px',
  background: 'var(--st-surface-muted, rgba(128,128,128,.04))',
}

const TABLE: React.CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  fontSize: '12px',
}

const CELL: React.CSSProperties = {
  border: '1px solid var(--st-border, rgba(128,128,128,.25))',
  padding: '4px 6px',
  verticalAlign: 'middle',
}

const BADGE: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  minHeight: 26,
  padding: '3px 8px',
  border: '1px solid var(--st-border, rgba(128,128,128,.28))',
  borderRadius: 4,
  fontSize: 12,
  opacity: 0.85,
}

export function EventCalculator({ name }: { name: string }) {
  const toast = useToast()
  const [data, setData] = useState<CalcData | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [saving, setSaving] = useState('')

  // 目标/已有/结束日期（可编辑，写回配置）
  const [target, setTarget] = useState(0)
  const [owned, setOwned] = useState(0)
  const [endDate, setEndDate] = useState('')

  // 商店勾选（不落盘，仅算价；「导入商店总价」把结果填进目标框）
  const [shopSel, setShopSel] = useState<Record<string, boolean>>({})
  // 日常/额外勾选（计算用）
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
        // 商店默认全不勾（老 UI 也是空的，用户自己挑要买的）
        const sel: Record<string, boolean> = {}
        for (const it of r.shop_items || []) sel[it.name] = false
        setShopSel(sel)
      })
      .catch(e => setErr(String(e)))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [name])

  useEffect(() => { void load() }, [load])

  /** 商店勾选总价（勾选项 price×quantity 之和） */
  const shopSelTotal = useMemo(() => {
    if (!data) return 0
    return (data.shop_items || [])
      .filter(it => shopSel[it.name])
      .reduce((sum, it) => sum + it.price * it.quantity, 0)
  }, [data, shopSel])

  /** 剩余天数（结束日期 − 今天） */
  const remainingDays = useMemo(() => {
    if (!endDate) return null
    const end = new Date(endDate + 'T23:59:59')
    const now = new Date()
    const days = Math.ceil((end.getTime() - now.getTime()) / 86400000)
    return Number.isNaN(days) ? null : days
  }, [endDate])

  /** 日常任务每日 PT 合计（勾选「会做」的） */
  const dailyTotal = useMemo(() => {
    if (!data) return 0
    return (data.wiki_daily || [])
      .filter(it => it.name && daily[it.name] && !daily[it.name].never)
      .reduce((s, it) => s + (Number(it.points) || 0), 0)
  }, [data, daily])

  /** 每日总收益 = 日常勾选（每日额外的点数 Wiki ECALCExtra 未在接口暴露，按 0 计） */
  const perDay = dailyTotal

  /** 还需 PT = 目标 − 已有 */
  const remain = Math.max(0, target - owned)

  /** 写回配置（复用普通配置保存通道） */
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

  const writeTarget = async () => {
    await savePatch({ 'EventGeneral.EventGeneral.PtLimit': Number(target) || 0 }, '写入目标 PT')
  }
  const writeTime = async () => {
    if (!endDate) { toast.error('结束日期为空'); return }
    await savePatch({ 'EventGeneral.EventGeneral.TimeLimit': endDate + ' 23:59:59' }, '写入结束时间')
  }
  const writeBoth = async () => {
    if (!endDate) { toast.error('结束日期为空'); return }
    await savePatch({
      'EventGeneral.EventGeneral.PtLimit': Number(target) || 0,
      'EventGeneral.EventGeneral.TimeLimit': endDate + ' 23:59:59',
    }, '写入目标 PT 和结束时间')
  }
  const writeShopFilter = async () => {
    const names = (data?.shop_items || []).filter(it => shopSel[it.name]).map(it => it.name)
    if (!names.length) { toast.error('请先勾选要兑换的商品'); return }
    await savePatch({
      'EventShop.EventShop.PresetFilter': 'custom',
      'EventShop.EventShop.CustomFilter': names.join(' > '),
    }, '写入商店过滤器')
  }

  if (loading) return <div style={{ opacity: .7, margin: '10px 0' }}>活动计算器加载中…</div>
  if (err) {
    return (
      <div style={PANEL}>
        <div style={{ color: 'var(--alas-entry-badge-error, #c0392b)', marginBottom: 8 }}>
          活动计算器：{err}
        </div>
        <button className="btn btn-sm btn-warning" disabled={refreshing} onClick={() => void load(true)}>
          {refreshing ? '拉取中…' : '重新从 Wiki 拉取'}
        </button>
      </div>
    )
  }
  if (!data) return null

  const th: React.CSSProperties = { ...CELL, fontWeight: 600, background: 'var(--st-table-head, rgba(128,128,128,.12))' }

  return (
    <div style={{ marginTop: 14 }}>
      {/* 徽章行 */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 10 }}>
        <span style={BADGE}>{data.event_name || '活动'}</span>
        <span style={BADGE}>总价 <b style={{ color: '#e53935', marginLeft: 4 }}>{(data.shop_total || 0).toLocaleString()}</b></span>
        <span style={BADGE}>{data.from_cache ? '缓存数据' : 'Wiki 实时'} · {data.updated_at}</span>
        <label style={BADGE}>
          <input type="checkbox" checked={shopSelTotal > 0 && shopSelTotal === target} readOnly style={{ marginRight: 6 }} />
          勾选总价 <b style={{ marginLeft: 4 }}>{shopSelTotal.toLocaleString()}</b>
        </label>
        <button className="btn btn-sm btn-secondary" onClick={() => setTarget(shopSelTotal)} disabled={shopSelTotal <= 0}>
          导入商店总价
        </button>
        <button className="btn btn-sm btn-secondary" onClick={() => { const sel: Record<string, boolean> = {}; for (const it of data.shop_items) sel[it.name] = false; setShopSel(sel) }}>
          清空兑换
        </button>
        <button className="btn btn-sm btn-secondary" onClick={() => { const sel: Record<string, boolean> = {}; for (const it of data.shop_items) sel[it.name] = true; setShopSel(sel) }}>
          重置兑换
        </button>
        <button className="btn btn-sm btn-secondary" disabled={refreshing} onClick={() => void load(true)}>
          {refreshing ? '刷新中…' : '刷新 Wiki 数据'}
        </button>
      </div>

      {/* 三栏面板 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 380px) minmax(300px, 1fr)', gap: 14, gridAutoRows: 'min-content' }}>
        {/* 目标与每日收益 */}
        <div style={{ ...PANEL, gridColumn: '1 / -1' }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>目标与每日收益</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8, marginBottom: 10 }}>
            <label style={{ fontSize: 12 }}>目标点数
              <input className="form-control form-control-sm" type="number" min={0} step={1} value={target} onChange={e => setTarget(Number(e.target.value) || 0)} />
            </label>
            <label style={{ fontSize: 12 }}>已有点数
              <input className="form-control form-control-sm" type="number" min={0} step={1} value={owned} onChange={e => setOwned(Number(e.target.value) || 0)} />
            </label>
            <label style={{ fontSize: 12 }}>结束日期
              <input className="form-control form-control-sm" type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
            </label>
            <label style={{ fontSize: 12 }}>剩余天数
              <input className="form-control form-control-sm" type="number" value={remainingDays ?? ''} readOnly />
            </label>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10 }}>
            <div>
              <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 12, opacity: .85 }}>日常任务（勾选=会做）</div>
              <table style={TABLE}>
                <tbody>
                  {(data.wiki_daily || []).map(it => (
                    <tr key={it.name}>
                      <td style={CELL}>{it.name}</td>
                      <td style={CELL}>{it.points} PT</td>
                      <td style={CELL}>
                        <input type="checkbox" checked={!!daily[it.name] && !daily[it.name].never}
                          onChange={e => setDaily(m => ({ ...m, [it.name]: { never: !e.target.checked, already: !!daily[it.name]?.already } }))} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 12, opacity: .85 }}>每日额外（勾选=会做）</div>
              <table style={TABLE}>
                <tbody>
                  {Object.entries(extra).map(([nm, f]) => (
                    <tr key={nm}>
                      <td style={CELL}>{nm}</td>
                      <td style={CELL}>
                        <input type="checkbox" checked={!!f && !f.never}
                          onChange={e => setExtra(m => ({ ...m, [nm]: { never: !e.target.checked, already: !!f?.already } }))} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div style={{ marginTop: 8, fontSize: 13 }}>
            每日收益 ≈ <b>{perDay}</b> PT；还需 <b>{remain.toLocaleString()}</b> PT
            {perDay > 0 && <>，约需 <b>{Math.ceil(remain / Math.max(1, perDay))}</b> 天</>}
          </div>
        </div>

        {/* 兑换商店 */}
        <div style={PANEL}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>兑换商店（勾选要买的）</div>
          <div style={{ maxHeight: 320, overflow: 'auto' }}>
            <table style={TABLE}>
              <thead>
                <tr><th style={{ ...th, width: 34 }}></th><th style={th}>商品</th><th style={{ ...th, width: 70 }}>单价</th><th style={{ ...th, width: 60 }}>数量</th><th style={{ ...th, width: 80 }}>小计</th></tr>
              </thead>
              <tbody>
                {(data.shop_items || []).map(it => (
                  <tr key={it.name + it.price}>
                    <td style={CELL}><input type="checkbox" checked={!!shopSel[it.name]} onChange={e => setShopSel(m => ({ ...m, [it.name]: e.target.checked }))} /></td>
                    <td style={CELL}>{it.name}</td>
                    <td style={CELL}>{it.price.toLocaleString()}</td>
                    <td style={CELL}>{it.quantity}</td>
                    <td style={CELL}>{(it.price * it.quantity).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* 出击数计算 */}
        <div style={PANEL}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>出击数计算</div>
          <table style={TABLE}>
            <thead>
              <tr><th style={th}>关卡</th><th style={th}>PT/场</th><th style={th}>还需场次</th></tr>
            </thead>
            <tbody>
              {(data.stages || []).map(s => {
                const pts = Number(s.points) || 0
                const count = pts > 0 ? Math.ceil(remain / pts) : null
                return (
                  <tr key={s.name}>
                    <td style={CELL}>{s.name}</td>
                    <td style={CELL}>{pts || '—'}</td>
                    <td style={CELL}>{count === null ? '—' : count.toLocaleString() + ' 场'}</td>
                  </tr>
                )
              })}
              {!data.stages?.length && <tr><td style={CELL} colSpan={3}>Wiki 未给出关卡数据</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* 写回按钮（老 UI 五连，同款语义） */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
        <button className="btn btn-sm btn-secondary" disabled={!!saving} onClick={() => void writeTarget()}>{saving === '写入目标 PT' ? '写入中…' : '写入目标 PT'}</button>
        <button className="btn btn-sm btn-secondary" disabled={!!saving} onClick={() => void writeTime()}>{saving === '写入结束时间' ? '写入中…' : '写入结束时间'}</button>
        <button className="btn btn-sm btn-secondary" disabled={!!saving} onClick={() => void writeBoth()}>{saving === '写入目标 PT 和结束时间' ? '写入中…' : '写入目标 PT 和结束时间'}</button>
        <button className="btn btn-sm btn-secondary" disabled={!!saving} onClick={() => void writeShopFilter()}>{saving === '写入商店过滤器' ? '写入中…' : '写入商店过滤器'}</button>
      </div>
      <small style={{ opacity: .65, marginTop: 6, display: 'block' }}>
        写回即保存到配置（活动通用设置 / 活动商店），调度器按此执行。数据来自碧蓝航线 Wiki。
      </small>
    </div>
  )
}
