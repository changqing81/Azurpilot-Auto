import { useState } from 'react'
import { Box, GripVertical, Plus, X } from 'lucide-react'
import type { Resource } from '../api/types'
import { useApp } from '../app/context'
import type { UiKey } from '../i18n'

export const resourceLabels: Record<string, UiKey> = {Oil: 'resource.Oil', Coin: 'resource.Coin', Gem: 'resource.Gem', Cube: 'resource.Cube', Pt: 'resource.Pt', ActionPoint: 'resource.ActionPoint', YellowCoin: 'resource.YellowCoin', PurpleCoin: 'resource.PurpleCoin', Core: 'resource.Core', Medal: 'resource.Medal', Merit: 'resource.Merit', GuildCoin: 'resource.GuildCoin', Chip: 'resource.Chip'}
const iconImages: Record<string, string> = {
  Oil: '/oil.webp',
  Coin: '/gold.webp',
  Gem: '/diamond.webp',
  Cube: '/cube.webp',
  Pt: '/pt.webp',
  ActionPoint: '/guild_coin.webp',
  YellowCoin: '/supply_token.webp',
  PurpleCoin: '/special_token.webp',
  Core: '/core_data.webp',
  Medal: '/honor_medal.webp',
  Merit: '/merit.webp',
  GuildCoin: '/stamina.webp',
}

function ResourceIcon({resourceKey, size = 32}: {resourceKey: string; size?: number}) {
  const src = iconImages[resourceKey]
  return src ? <img className="resource-icon-image" src={src} alt="" width={size} height={size}/> : <Box size={Math.round(size * .62)}/>
}
export const defaultResourceKeys = ['Oil', 'Coin', 'Gem', 'Cube']

export function ResourceCards({resources, selected}: {resources: Resource[]; selected: string[]}) {
  const {ui} = useApp()
  return <div className="resource-grid">{selected.map((key, index) => {
      const resource = resources.find(item => item.name === key)
      const recorded = resource?.record && !resource.record.startsWith('2020-01-01')
      const labelKey = resourceLabels[key]
      const label = labelKey ? ui(labelKey) : resource?.label ?? key
      const limit = resource?.limit
      const total = resource?.total
      const showLimit = typeof limit === 'number' && limit > 0
      const showTotal = !showLimit && resource?.name === 'ActionPoint' && typeof resource.value === 'number' && typeof total === 'number' && Number.isFinite(total) && total > resource.value
      return <section key={key} className={`resource-card resource-${index % 4}`}><div className="resource-heading"><span>{label}</span><div className="resource-image-wrap"><ResourceIcon resourceKey={key} size={32}/></div></div><div className="resource-value">{recorded && resource?.value != null ? resource.value.toLocaleString() : '—'}{recorded && showLimit && <small>/ {limit.toLocaleString()}</small>}{recorded && showTotal && <small>/ {ui('resource.totalActionPoint')} {total.toLocaleString()}</small>}</div><div className="resource-foot">{recorded ? ui('resource.recordedAt', {time: resource.record?.replace('T', ' ').slice(5, 19) ?? ''}) : ui('resource.waitingSync')}</div></section>
    })}</div>
}

export function ResourceSettings({resources, selected, onChange}: {resources: Resource[]; selected: string[]; onChange: (keys: string[]) => void}) {
  const {ui} = useApp()
  const [pickerOpen, setPickerOpen] = useState(false)
  const [draggingKey, setDraggingKey] = useState<string | null>(null)
  const available = resources.filter(resource => !selected.includes(resource.name))

  function add(key: string) {
    if (!selected.includes(key)) onChange([...selected, key])
  }
  function remove(key: string) {
    onChange(selected.filter(item => item !== key))
  }
  function move(fromKey: string, toKey: string) {
    if (fromKey === toKey) return
    const from = selected.indexOf(fromKey)
    const to = selected.indexOf(toKey)
    if (from < 0 || to < 0) return
    const keys = [...selected]
    const [moved] = keys.splice(from, 1)
    keys.splice(to, 0, moved)
    onChange(keys)
  }

  return <div className="resource-settings">
    <div className="resource-settings-heading"><div><strong>{ui('resource.cards')}</strong><span>{ui('resource.cardsHint')}</span></div><button type="button" className="text-button" onClick={() => onChange(defaultResourceKeys)}>{ui('resource.restoreDefault')}</button></div>
    <div className="resource-card-editor">
      {selected.map(key => {
        const resource = resources.find(item => item.name === key)
          const labelKey = resourceLabels[key]
          const label = labelKey ? ui(labelKey) : resource?.label ?? key
        return <div key={key} className={`resource-editor-card${draggingKey === key ? ' dragging' : ''}`} draggable onDragStart={event => {setDraggingKey(key); event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', key)}} onDragEnd={() => setDraggingKey(null)} onDragOver={event => {event.preventDefault(); event.dataTransfer.dropEffect = 'move'}} onDrop={event => {event.preventDefault(); const source = draggingKey ?? event.dataTransfer.getData('text/plain'); if (source) move(source, key); setDraggingKey(null)}}>
          <span className="resource-editor-grip" aria-hidden="true"><GripVertical size={16}/></span>
          <span className="resource-editor-icon resource-editor-icon-image"><ResourceIcon resourceKey={key} size={30}/></span>
          <span className="resource-editor-label">{label}</span>
          <button type="button" className="resource-editor-remove" aria-label={ui('resource.remove', {label})} title={ui('resource.remove', {label})} onClick={() => remove(key)}><X size={15}/></button>
        </div>
      })}
      <button type="button" className={`resource-editor-card resource-editor-add${pickerOpen ? ' open' : ''}`} onClick={() => setPickerOpen(open => !open)}><span className="resource-editor-add-icon"><Plus size={18}/></span><span>{ui('resource.addCard')}</span></button>
    </div>
    {pickerOpen && <div className="resource-picker">{available.length ? <div className="resource-picker-grid">{available.map(resource => {
      const labelKey = resourceLabels[resource.name]
      const label = labelKey ? ui(labelKey) : resource.label ?? resource.name
      return <button type="button" key={resource.name} className="resource-picker-card" onClick={() => add(resource.name)}><span className="resource-editor-icon resource-editor-icon-image"><ResourceIcon resourceKey={resource.name} size={28}/></span><span>{label}</span><Plus size={15}/></button>
    })}</div> : <div className="resource-picker-empty">{ui('resource.allAdded')}</div>}</div>}
  </div>
}
