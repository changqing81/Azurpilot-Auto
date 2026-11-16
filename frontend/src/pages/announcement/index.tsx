// 公告页：老 UI 是弹窗（window.alasShowAnnouncement），新 UI 做成独立页面。
//
// 数据源与老 UI 完全一致：
//   - 正文/标题/id/url  → ApiClient.get_announcement()（jsdelivr → GitHub 三源回退）
//   - 配图              → localize_images() 落本地后返回 /api/announcement/image/{name}
//   - 更新日志          → changelog.json，服务端复用 update_log.render_entries_html 渲染
//
// 这么做而不是移植 alas-utils.js 的弹窗：新 UI 是路由式页面，弹窗会打断导航，
// 且 alas-utils.js 依赖全局 window.alas* 系列函数（老 UI 专用）。
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shell } from '../../components/Shell'
import { apiGet } from '../../api/client'

type AnnouncementResp = { title: string; content: string; id: number | null; url: string; error: string }
type UpdateLogEntry = { id: string; date: string; title: string; content: string; sha: string }
type UpdateLogResp = { entries: UpdateLogEntry[]; html: string; error: string }

export function AnnouncementPage() {
  const navigate = useNavigate()
  const [ann, setAnn] = useState<AnnouncementResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [log, setLog] = useState<UpdateLogResp | null>(null)
  const [logLoading, setLogLoading] = useState(true)

  const load = async (force: boolean) => {
    setLoading(true)
    try {
      setAnn(await apiGet<AnnouncementResp>('/newapi/announcement' + (force ? '?force=1' : ''), 20000))
    } catch (e) {
      setAnn({ title: '', content: '', id: null, url: '', error: String(e) })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load(false)
    apiGet<UpdateLogResp>('/newapi/update-log', 20000)
      .then(setLog)
      .catch(() => setLog({ entries: [], html: '', error: '读取失败' }))
      .finally(() => setLogLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <Shell active="Announcement" title="公告">
      <div id="pywebio-scope-content">
        <div style={{ maxWidth: 900, margin: '0 auto', padding: '0 .625rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '10px 0' }}>
            <h1 style={{ fontSize: 20, margin: 0 }}>公告</h1>
            <button className="btn btn-secondary" disabled={loading} onClick={() => load(true)}>
              {loading ? '刷新中…' : '刷新'}
            </button>
            <button className="btn btn-secondary" onClick={() => navigate('/')}>← 返回主页</button>
          </div>

          <div className="ov-card apple-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
            {loading && <div className="st-placeholder">正在拉取公告…</div>}
            {!loading && ann?.error && (
              <div className="st-placeholder">
                公告拉取失败：{ann.error}
                <div style={{ marginTop: 8, fontSize: 12, opacity: .7 }}>
                  公告源在外网（jsdelivr / GitHub），国内网络或代理异常时会失败。
                </div>
              </div>
            )}
            {!loading && !ann?.error && ann && (
              <>
                <h2 style={{ fontSize: 18, margin: '0 0 10px' }}>{ann.title || '（无标题）'}</h2>
                {/* content 是服务端本地化过的 HTML（含 img），与老 UI 弹窗同一份数据 */}
                <div
                  className="announcement-content"
                  style={{ lineHeight: 1.7, fontSize: 14 }}
                  dangerouslySetInnerHTML={{ __html: ann.content || '（空公告）' }}
                />
                {ann.url && (
                  <div style={{ marginTop: 12 }}>
                    <a href={ann.url} target="_blank" rel="noreferrer" className="btn btn-primary">
                      查看详情
                    </a>
                  </div>
                )}
              </>
            )}
          </div>

          <div className="ov-card apple-card" style={{ padding: '14px 18px' }}>
            <h2 className="alas-develop-section-title" style={{ fontSize: 16, margin: '0 0 10px' }}>
              更新日志
            </h2>
            {logLoading && <div className="st-placeholder">读取更新日志…</div>}
            {!logLoading && log?.error && <div className="st-placeholder">更新日志读取失败：{log.error}</div>}
            {!logLoading && !log?.error && !log?.html && <div className="st-placeholder">暂无更新日志</div>}
            {!logLoading && log?.html && <div dangerouslySetInnerHTML={{ __html: log.html }} />}
          </div>
        </div>
      </div>
    </Shell>
  )
}
