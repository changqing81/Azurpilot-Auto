import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Activity, CircleAlert, Layers3, Plus, Server } from 'lucide-react'
import { useApp, useConnection } from '../app/context'
import type { UiTranslator } from '../i18n'
import { CreateInstance } from '../app/App'
import { PageTitle, StatusBadge } from '../components/ui'

function getGreeting(ui: UiTranslator): string {
  const hour = new Date().getHours()
  if (hour >= 5 && hour < 12) return ui('home.greetingMorning')
  if (hour >= 12 && hour < 18) return ui('home.greetingAfternoon')
  return ui('home.greetingEvening')
}

export function Home() {
  const {instances, t, ui} = useApp()
  const connection = useConnection()
  const [creating, setCreating] = useState(false)
  return <>
    <div className="home-intro"><span className="eyebrow">{ui('home.commandCenter')}</span><PageTitle title={getGreeting(ui)}/><p>{ui('home.subtitle')}</p></div>
    <div className="home-summary" aria-label={ui('home.summary')}>
      <div><Layers3 size={19}/><span>{ui('home.allInstances')}</span><strong>{instances.length}</strong></div>
      <div><Activity size={19}/><span>{ui('status.running')}</span><strong>{instances.filter(item => item.status === 'running').length}</strong></div>
      <div><CircleAlert size={19}/><span>{ui('status.error')}</span><strong>{instances.filter(item => item.status === 'error').length}</strong></div>
    </div>
    <section className="home-instances">
      <div className="home-section-heading"><h2>{ui('home.instances')} <span className="count-badge">{instances.length}</span></h2><button className="button primary" disabled={connection !== 'ready'} onClick={() => setCreating(true)}><Plus size={16}/>{ui('home.newInstance')}</button></div>
      <div className="instance-grid">
        {instances.map(item => <Link className="instance-card panel" key={item.name} to={`/i/${item.name}/overview`}>
          <div className="instance-card-heading"><span className="home-instance-icon"><Server size={22}/></span><StatusBadge status={item.status}/></div>
          <h3>{item.name}</h3><div className="instance-device">{item.server !== 'disabled' && <span>{t(`Emulator.ServerName.${item.server}`)}</span>}<span>{item.serial}</span></div>
          <div className="instance-card-footer"><span>{item.status === 'running' ? item.currentTask ? t(`Task.${item.currentTask}.name`) : ui('home.waitingSchedule') : item.status === 'error' ? ui('status.error') : item.status === 'updating' ? ui('status.updating') : ui('home.notRunning')}</span><ArrowRight size={17}/></div>
        </Link>)}
        {!instances.length && <button className="instance-card instance-add" disabled={connection !== 'ready'} onClick={() => setCreating(true)}><Plus size={32}/><span>{ui('instance.createFirst')}</span></button>}
      </div>
    </section>
    {creating && <CreateInstance onClose={() => setCreating(false)}/>}
  </>
}
