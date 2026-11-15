// 工具页（对照 alas_daemon_overview：daemon-overview = scheduler-bar + log-bar + groups + log）。
// 工具任务参数表单复用配置页逻辑；调度器/日志流与总览同款。
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { Shell } from '../../components/Shell'

type TaskRow = { command: string; name: string; next_run: string }
type OverviewResp = { running: TaskRow[]; pending: TaskRow[]; waiting: TaskRow[]; alive: boolean }
type Group = { group: string; title: string; args: { name: string; title: string; help: string; type: string; value: any; option: { label: string; value: string }[] }[] }
type ConfigResp = { task: string; groups: Group[] }

export function ToolPage() {
  const { name = 'alas', task = 'Daemon' } = useParams()
  const navigate = useNavigate()
  const [ov, setOv] = useState<OverviewResp | null>(null)
  const [groups, setGroups] = useState<Group[]>([])
  const [logHtml, setLogHtml] = useState('')

  useEffect(() => {
    apiGet<OverviewResp>('/newapi/overview/' + name).then(setOv).catch(() => {})
    apiGet<ConfigResp>('/newapi/config/' + name + '/task/' + task).then(r => setGroups(r.groups)).catch(() => {})
    const timer = setInterval(() => apiGet<OverviewResp>('/newapi/overview/' + name).then(setOv).catch(() => {}), 15000)
    const logTimer = setInterval(() => fetch('/newapi/log/' + name + '/tail').then(r => r.ok ? r.text() : '').then(setLogHtml).catch(() => {}), 2000)
    return () => { clearInterval(timer); clearInterval(logTimer) }
  }, [name, task])

  const alive = ov?.alive ?? false
  return (
    <Shell active={task} title="工具">
      <main id="pywebio-scope-content">
        <div id="pywebio-scope-daemon-overview">
          <div id="pywebio-scope-_daemon">
            <div id="pywebio-scope-_daemon_upper">
              <div id="pywebio-scope-scheduler-bar">
                <p>调度器</p>
                <div id="pywebio-scope-scheduler_btn">
                  <button className="pywebio-btn">{alive ? '停止' : '启动'}</button>
                </div>
              </div>
              <div id="pywebio-scope-log-bar">
                <p>日志</p>
                <div id="pywebio-scope-log-bar-btns" className="log-bar-btns">
                  <button className="log-btn-primary">自动滚动 开</button>
                  <button className="log-btn">截图预览</button>
                  <button className="log-btn">导出今日日志</button>
                </div>
              </div>
            </div>
            <div id="pywebio-scope-groups">
              {groups.map(g => (
                <section key={g.group} id={'pywebio-scope-config_' + g.group} className="ov-card cfg-group">
                  <div className="cfg-group-title">{g.title || g.group}</div>
                  {g.args.map(a => (
                    <div key={a.name} className="cfg-row">
                      <div className="cfg-title-wrap">
                        <div className="arg-title">{a.title}</div>
                        {a.help && <div className="arg-help">{a.help}</div>}
                      </div>
                      <div className="cfg-control">
                        {a.type === 'select' && a.option?.length ? (
                          <select className="cfg-input" defaultValue={a.value}>
                            {a.option.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                          </select>
                        ) : typeof a.value === 'boolean' ? (
                          <input type="checkbox" defaultChecked={!!a.value} />
                        ) : (
                          <input className="cfg-input" defaultValue={a.value ?? ''} />
                        )}
                      </div>
                    </div>
                  ))}
                </section>
              ))}
            </div>
            <div id="pywebio-scope-log-container" className="log-container">
              <code id="pywebio-scope-log" className="log-body" dangerouslySetInnerHTML={{ __html: logHtml }} />
            </div>
          </div>
        </div>
        <button className="log-btn" style={{ marginTop: 10 }} onClick={() => navigate('/overview/' + name)}>← 返回总览</button>
      </main>
    </Shell>
  )
}
