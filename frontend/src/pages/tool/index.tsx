// 开发者工具页：与老 UI（module/webui/app_developer_tools.py:dev_utils）同构。
//
// 老 UI 实测 DOM（.workbuddy/perf/out/oldui-ref/oldui_devutils.html）：
//   #pywebio-scope-content > #pywebio-scope-develop_detail
//     > 一排排 put_buttons（无额外容器，按钮直接平铺）
//     > .log-export-panel（日志导出面板，独立样式类）
//
// 页面分两块：
//   ① 调试动作（抛异常/预览提示/维护横幅/图标模拟/跨月预演/重启/推送测试）
//   ② 日志导出（选实例+日期 → 导出运行日志 / 错误日志目录 / 仅日志文本）
//
// 老 UI 的调试动作全部走 PyWebIO WS 私有回调，新 UI 没有对应 REST。
// 这里只落地「有 REST 支撑」的部分（日志导出，走现成的 /api/log/*），
// 其余调试按钮保留入口并明确提示需要回老 UI —— 不假装能用。
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Shell } from '../../components/Shell'
import { apiGet } from '../../api/client'

type Instance = { name: string; state: number }
type DateInfo = { dates?: string[] }

/** 调试动作清单（与老 UI dev_utils 的按钮一一对应，含颜色）。
 *  没有 REST 支撑的项标 enabled:false，点击时明确告知去老 UI，不静默失败。 */
const DEBUG_ACTIONS: { label: string; color: string; enabled: boolean }[][] = [
  [
    { label: 'GUI测试 抛出异常事件', color: 'primary', enabled: false },
    { label: '预览更新提示', color: 'primary', enabled: false },
  ],
  [
    { label: '显示维护横幅（测试）', color: 'warning', enabled: false },
    { label: '隐藏维护横幅（测试）', color: 'secondary', enabled: false },
  ],
  [
    { label: '模拟运行图标(10s)', color: 'success', enabled: false },
    { label: '模拟错误图标(10s)', color: 'danger', enabled: false },
    { label: '模拟更新图标(10s)', color: 'warning', enabled: false },
  ],
  [{ label: '清除图标模拟状态', color: 'secondary', enabled: false }],
  [{ label: '模拟错误弹窗', color: 'danger', enabled: false }],
  [
    { label: '跨月预演(仅清理)', color: 'primary', enabled: false },
    { label: '跨月预演(全流程)', color: 'primary', enabled: false },
    { label: '取消跨月预演', color: 'secondary', enabled: false },
  ],
  [{ label: '重启Alas', color: 'primary', enabled: false }],
  [
    { label: '测试更新推送 (updata=True)', color: 'danger', enabled: false },
    { label: '测试公告推送 (updata=False)', color: 'info', enabled: false },
    { label: '测试错误推送', color: 'danger', enabled: false },
  ],
]

export function ToolPage() {
  const navigate = useNavigate()
  const { name = 'alas' } = useParams()
  const [instances, setInstances] = useState<string[]>([])
  const [instance, setInstance] = useState(name)
  const [scope, setScope] = useState('all')
  const [dates, setDates] = useState<string[]>([])
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    apiGet<{ instances: Instance[] }>('/newapi/instances')
      .then(r => setInstances(r.instances.map(i => i.name)))
      .catch(() => {})
  }, [])

  // 有日志的日期列表（/api/log/runtime/dates?instance=）
  useEffect(() => {
    apiGet<DateInfo>(`/api/log/runtime/dates?instance=${encodeURIComponent(instance)}`)
      .then(r => setDates(r.dates ?? []))
      .catch(() => setDates([]))
  }, [instance])

  const download = async (url: string, label: string) => {
    setBusy(true)
    setStatus(`正在生成${label}…`)
    try {
      const r = await fetch(url)
      if (!r.ok) throw new Error('HTTP ' + r.status)
      const blob = await r.blob()
      const cd = r.headers.get('content-disposition') || ''
      const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
      const filename = m ? decodeURIComponent(m[1]) : `${label}.zip`
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = filename
      a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 10000)
      setStatus(`${label}已下载（${Math.round(blob.size / 1024)} KB）`)
    } catch (e) {
      setStatus(`${label}失败：${String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const exportRuntime = () =>
    download(
      `/api/log/runtime?instance=${encodeURIComponent(instance)}&scope=${encodeURIComponent(scope)}`,
      '运行日志',
    )
  const exportErrorFull = () => download('/api/log/error?scope=full', '错误日志目录')
  const exportErrorText = () => download('/api/log/error?scope=text', '错误日志文本')

  const onDebugClick = (label: string) => {
    setStatus(`「${label}」是 PyWebIO 会话内的调试动作，新 UI 尚未提供等价接口；请切回老 UI 使用。`)
  }

  return (
    <Shell active="Utils" title="开发者工具">
      <div id="pywebio-scope-content">
        <div id="pywebio-scope-develop_detail">
          {DEBUG_ACTIONS.map((row, i) => (
            <div key={i} style={{ display: 'flex', flexWrap: 'wrap', gap: 4, margin: '2px 0' }}>
              {row.map(a => (
                <button
                  key={a.label}
                  className={'btn btn-' + a.color}
                  onClick={() => onDebugClick(a.label)}
                  title={a.enabled ? '' : '需老 UI 的 PyWebIO 会话'}
                >
                  {a.label}
                </button>
              ))}
            </div>
          ))}

          <div className="log-export-panel">
            <h2 className="alas-develop-section-title">日志导出</h2>
            <div className="log-export-row">
              <label className="log-export-instance">
                实例
                <select
                  id="log-export-instance"
                  className="deploy-setting-select"
                  style={{ width: 'auto', minWidth: 140 }}
                  value={instance}
                  onChange={e => setInstance(e.target.value)}
                >
                  {(instances.length ? instances : [name]).map(n => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </label>
              <label className="log-export-instance">
                日期
                <select
                  id="log-export-runtime-scope"
                  className="deploy-setting-select"
                  style={{ width: 'auto', minWidth: 200 }}
                  value={scope}
                  onChange={e => setScope(e.target.value)}
                >
                  <option value="all">全部（含历史，合并）</option>
                  {dates.map(d => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </label>
              <button
                id="log-export-runtime"
                className="deploy-setting-button"
                type="button"
                disabled={busy}
                onClick={exportRuntime}
              >
                导出运行日志
              </button>
              <button
                id="log-export-error"
                className="deploy-setting-button primary"
                type="button"
                disabled={busy}
                onClick={exportErrorFull}
              >
                导出错误日志目录（含截图）
              </button>
              <button
                id="log-export-error-text"
                className="deploy-setting-button"
                type="button"
                disabled={busy}
                onClick={exportErrorText}
              >
                仅导出日志文本（不含截图）
              </button>
            </div>
            <div id="log-export-status" className="deploy-setting-status">{status}</div>
          </div>

          <div style={{ marginTop: 16 }}>
            <button className="btn btn-secondary" onClick={() => navigate('/')}>← 返回主页</button>
          </div>
        </div>
      </div>
    </Shell>
  )
}
