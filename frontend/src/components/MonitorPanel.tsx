import { useEffect, useState } from 'react'
import { Download, Image, Terminal } from 'lucide-react'
import { api } from '../api/client'
import type { Preview } from '../api/types'
import { useApp } from '../app/context'
import { Empty } from './ui'
import { LogPanel } from './LogPanel'
import { SegmentedControl } from './SegmentedControl'

export function MonitorPanel({instance}: {instance: string}) {
  const [view, setView] = useState('logs')
  const [frame, setFrame] = useState<Preview>()
  const {setPreviewEnabled, ui} = useApp()
  useEffect(() => {
    setPreviewEnabled(view === 'preview')
    return () => setPreviewEnabled(false)
  }, [view, setPreviewEnabled])
  useEffect(() => api.onEvent(event => {
    if (event.topic === 'preview' && (event.data as Preview).instance === instance) setFrame(event.data as Preview)
  }), [instance])
  // 主动兜底取帧：任务运行中的帧经事件推送，这里再定期拉最新帧，
  // 避免事件在断线重连期间丢失后一直停留在"等待任务截图"。
  useEffect(() => {
    if (view !== 'preview') return
    let active = true
    const grab = () => {
      void api.request('preview.capture', {instance}).then(value => {
        if (active && value?.image) setFrame(value)
      }).catch(() => { /* 实例未运行时没有帧，保留空状态。 */ })
    }
    grab()
    const timer = setInterval(grab, 15000)
    return () => {active = false; clearInterval(timer)}
  }, [view, instance])
  return <section className="panel monitor-panel"><div className="monitor-tabs" aria-label={ui('monitor.title')}>
    <SegmentedControl label={ui('monitor.view')} value={view} onChange={setView} options={[
      {value: 'logs', label: <><Terminal size={15}/>{ui('monitor.logs')}</>},
      {value: 'preview', label: <><Image size={15}/>{ui('monitor.preview')}</>},
    ]}/>
    {view === 'preview' && frame?.image && <a className="text-button" href={frame.image} download={`${instance}-screenshot.jpg`}><Download size={14}/>{ui('monitor.saveScreenshot')}</a>}
  </div>
    <div className="monitor-view" hidden={view !== 'logs'}><LogPanel active={view === 'logs'}/></div>
    <div className="monitor-view" hidden={view !== 'preview'}><div className="preview-screen">{frame?.image ? <img src={frame.image} alt={ui('monitor.screenshotAlt')}/> : <Empty icon={<Image size={42}/>} title={ui('monitor.waitingScreenshot')}>{ui('monitor.screenshotHint')}</Empty>}</div></div>
  </section>
}
