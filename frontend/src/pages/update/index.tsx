// 更新器页（对照 app_developer_update.py：updater_info + 提交表 + 详细历史 + 更新日志）
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../../api/client'
import { Shell } from '../../components/Shell'

type Commit = { sha: string; author: string; time: string; message: string }
type UpdateResp = { branch: string; local: Commit; upstream: Commit[] }

export function UpdatePage() {
  const navigate = useNavigate()
  const [data, setData] = useState<UpdateResp | null>(null)
  const [changelog, setChangelog] = useState<any>(null)
  useEffect(() => {
    apiGet<UpdateResp>('/newapi/update/info').then(setData).catch(() => {})
    fetch('/changelog.json').then(r => r.json()).then(setChangelog).catch(() => {})
  }, [])
  return (
    <Shell active="Update" title="更新器">
    <div id="pywebio-scope-content" className="config-page">
      <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', margin: '.5rem 0' }}>
        <button className="log-btn" onClick={() => navigate('/')}>← 返回总览</button>
        <h2>更新器</h2>
      </div>

      <div id="pywebio-scope-group_ui1" className="cfg-group">
        <div style={{ fontSize: '1.05rem', fontWeight: 600 }}>版本对照（分支 {data?.branch ?? '-'}）</div>
        <table className="research-table">
          <thead><tr><th></th><th>SHA1</th><th>作者</th><th>时间</th><th>说明</th></tr></thead>
          <tbody>
            <tr>
              <td>本地</td>
              <td>{data?.local.sha}</td><td>{data?.local.author}</td><td>{data?.local.time}</td><td>{data?.local.message}</td>
            </tr>
            <tr>
              <td>上游</td>
              <td>{data?.upstream[0]?.sha ?? '-'}</td><td>{data?.upstream[0]?.author ?? ''}</td><td>{data?.upstream[0]?.time ?? ''}</td><td>{data?.upstream[0]?.message ?? ''}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div id="pywebio-scope-group_ui2" className="cfg-group">
        <div style={{ fontSize: '1.05rem', fontWeight: 600 }}>详细提交历史</div>
        <table className="research-table">
          <thead><tr><th>SHA1</th><th>作者</th><th>时间</th><th>说明</th></tr></thead>
          <tbody>
            {(data?.upstream ?? []).map(c => (
              <tr key={c.sha}><td>{c.sha}</td><td>{c.author}</td><td>{c.time}</td><td>{c.message}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      {changelog && (
        <div id="pywebio-scope-group_ui3" className="cfg-group">
          <div style={{ fontSize: '1.05rem', fontWeight: 600 }}>更新日志</div>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, padding: '8px 4px' }}>
            {typeof changelog === 'string' ? changelog : JSON.stringify(changelog, null, 2).slice(0, 2000)}
          </pre>
        </div>
      )}
    </div>
    </Shell>
  )
}
