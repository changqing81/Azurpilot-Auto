import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarClock, ChevronRight, CirclePlay, Clock3, Hourglass, ListTodo, Play, Square, TriangleAlert, X } from 'lucide-react'
import { api } from '../api/client'
import type { Overview } from '../api/types'
import { useApp, useConnection } from '../app/context'
import { editor } from '../config/editors'
import type { UiKey, UiTranslator } from '../i18n'

const taskStateLabel = {
  running: 'scheduler.running',
  pending: 'scheduler.pending',
  waiting: 'scheduler.waiting',
} as const

const taskGroups = [
  {state: 'running', label: 'scheduler.running', empty: 'scheduler.noRunning', icon: CirclePlay},
  {state: 'pending', label: 'scheduler.pending', empty: 'scheduler.noPending', icon: ListTodo},
  {state: 'waiting', label: 'scheduler.waiting', empty: 'scheduler.noWaiting', icon: Hourglass},
] as const

function formatExecutionTime(nextRun: string, ui: UiTranslator) {
  const value = nextRun.replace('T', ' ').trim()
  return value ? ui('scheduler.executionTime', {time: value}) : ui('scheduler.executionUnset')
}

export function RightRail({instance, onMobileClose}: {instance: string; onMobileClose: () => void}) {
  const connection = useConnection()
  const {notify, t, ui} = useApp()
  const [data, setData] = useState<Overview>()
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    void api.request('overview.get', {instance})
      .then(value => { if (active) setData(value) })
      .catch(error => notify((error as Error).message, true))
    return () => { active = false }
  }, [connection, instance, notify])

  useEffect(() => api.onEvent(event => {
    if (event.topic !== 'overview') return
    const next = event.data as Overview
    if (next.instance === instance) setData(next)
  }), [instance])

  async function toggleScheduler() {
    if (!data) return
    setBusy(true)
    try {
      if (data.status !== 'running') await editor(`config:${instance}`).settled()
      const next = await api.request(data.status === 'running' ? 'scheduler.stop' : 'scheduler.start', {instance})
      setData(next)
      notify(data.status === 'running' ? ui('scheduler.stoppedNotice') : ui('scheduler.started'))
    } catch (error) {
      notify((error as Error).message, true)
    } finally {
      setBusy(false)
    }
  }

  const running = data?.tasks.filter(task => task.state === 'running').length ?? 0
  const pending = data?.tasks.filter(task => task.state === 'pending').length ?? 0
  const waiting = data?.tasks.filter(task => task.state === 'waiting').length ?? 0

  return <aside className="right-rail" id="right-rail-menu" aria-label={ui('scheduler.rail')}>
    <div className="right-rail-header">
      <div>
        <span className="right-rail-eyebrow">{ui('scheduler.workspace')}</span>
        <strong>{instance}</strong>
      </div>
      <button className="mobile-rail-close icon-button" aria-label={ui('nav.closeRail')} onClick={onMobileClose}><X size={18}/></button>
    </div>

    <section className="scheduler-widget" aria-label={ui('scheduler.title')}>
      <div className="scheduler-widget-heading">
        <div><CalendarClock size={17}/><span>{ui('scheduler.title')}</span></div>
        <span className={`scheduler-status ${data?.status === 'running' ? 'running' : ''}`}>
          {data?.status === 'running' ? <CirclePlay size={13}/> : data?.status === 'error' ? <TriangleAlert size={13}/> : <Square size={12}/>} 
          {data?.status === 'running' ? ui('status.running') : data?.status === 'error' ? ui('scheduler.abnormal') : ui('scheduler.stopped')}
        </span>
      </div>
      <div className="scheduler-stats">
        <div><span>{ui('scheduler.running')}</span><strong>{running}</strong></div>
        <div><span>{ui('scheduler.pending')}</span><strong>{pending}</strong></div>
        <div><span>{ui('scheduler.waiting')}</span><strong>{waiting}</strong></div>
      </div>
      <button
        className={`button scheduler-toggle ${data?.status === 'running' ? 'danger' : 'primary'}`}
        onClick={toggleScheduler}
        disabled={!data || busy || connection !== 'ready'}
      >
        {data?.status === 'running' ? <Square size={14}/> : <Play size={14}/>} {busy ? ui('scheduler.processing') : data?.status === 'running' ? ui('scheduler.stop') : ui('scheduler.start')}
      </button>
    </section>

    <section className="rail-schedule" aria-label={ui('scheduler.plan')}>
      <div className="rail-section-heading">
        <div><Clock3 size={15}/><span>{ui('scheduler.plan')}</span></div>
        <span>{data?.tasks.length ?? 0}</span>
      </div>
      <div className="rail-task-list">
        {data?.tasks.length ? taskGroups.map(group => {
          const tasks = data.tasks.filter(task => task.state === group.state)
          const GroupIcon = group.icon
          return <section className={`rail-queue-group ${group.state}`} key={group.state} aria-label={ui(group.label as UiKey)}>
            <div className="rail-queue-heading">
              <div><GroupIcon size={16}/><strong>{ui(group.label as UiKey)}</strong></div>
              <span>{tasks.length}</span>
            </div>
            <div className="rail-queue-body">
              {tasks.length ? tasks.map(task => <Link key={task.name} className="rail-task-item" to={`/i/${instance}/task/${task.name}`} onClick={onMobileClose}>
                <div>
                  <strong>{t(`Task.${task.name}.name`)}</strong>
                  <small>{task.state === 'running' ? ui('scheduler.executing') : formatExecutionTime(task.nextRun, ui)}</small>
                </div>
                <span className={`task-state ${task.state}`}><GroupIcon size={12}/>{ui(taskStateLabel[task.state])}</span>
                <ChevronRight size={13}/>
              </Link>) : <div className="rail-queue-empty">{ui(group.empty as UiKey)}</div>}
            </div>
          </section>
        }) : <div className="rail-empty">{ui('scheduler.noEnabled')}</div>}
      </div>
    </section>
  </aside>
}
