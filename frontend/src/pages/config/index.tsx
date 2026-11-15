// 配置页：分组表单。数据 /newapi/config/{name}/task/{task}。
// 控件类型映射（args.json type）：input→文本框 / select→下拉 / checkbox→开关 / datetime→文本 / textarea→多行
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiGet } from '../../api/client'

type ArgOption = { label: string; value: string }
type Arg = {
  name: string; title: string; help: string;
  type: string; value: any; option: ArgOption[]
}
type Group = { group: string; title: string; args: Arg[] }
type ConfigResp = { task: string; groups: Group[]; error?: string }

function ArgControl({ arg, onChange }: { arg: Arg; onChange: (v: any) => void }) {
  if (arg.type === 'select' && arg.option.length) {
    return (
      <select className="cfg-input" value={arg.value} onChange={e => onChange(e.target.value)}>
        {arg.option.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    )
  }
  if (arg.type === 'checkbox' || typeof arg.value === 'boolean') {
    return (
      <input type="checkbox" checked={!!arg.value} onChange={e => onChange(e.target.checked)} />
    )
  }
  if (arg.type === 'textarea') {
    return <textarea className="cfg-input" rows={3} value={arg.value ?? ''} onChange={e => onChange(e.target.value)} />
  }
  // input/datetime/其余一律文本框（datetime 保持字符串格式）
  return <input className="cfg-input" value={arg.value ?? ''} onChange={e => onChange(e.target.value)} />
}

export function ConfigPage() {
  const { name = 'alas', task = '' } = useParams()
  const [groups, setGroups] = useState<Group[]>([])
  const [error, setError] = useState('')
  const [dirty, setDirty] = useState<Record<string, any>>({})
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')

  useEffect(() => {
    apiGet<ConfigResp>(`/newapi/config/${name}/task/${task}`).then(r => {
      if (r.error) setError(r.error)
      setGroups(r.groups)
    }).catch(e => setError(String(e)))
  }, [name, task])

  const setArg = (group: string, argName: string, value: any) => {
    setGroups(gs => gs.map(g => g.group !== group ? g : {
      ...g,
      args: g.args.map(a => a.name !== argName ? a : { ...a, value }),
    }))
    setDirty(d => ({ ...d, [`${group}.${argName}`]: value }))
  }

  return (
    <div id="pywebio-scope-content" className="config-page">
      <div className="cfg-header">
        <h2>{task}</h2>
        <span className="cfg-dirty-hint">{saveMsg || (Object.keys(dirty).length ? `未保存修改 ${Object.keys(dirty).length} 项` : '')}</span>
        <button
          className="ov-btn-primary"
          disabled={!Object.keys(dirty).length || saving}
          onClick={async () => {
            setSaving(true)
            try {
              const res = await fetch(`/newapi/config/${name}/task/${task}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ modified: dirty }),
              })
              const j = await res.json()
              if (j.invalid?.length) setSaveMsg(`${j.valid?.length ?? 0} 项已保存，${j.invalid.length} 项校验失败`)
              else { setSaveMsg(`已保存 ${j.valid?.length ?? 0} 项`); setDirty({}) }
            } catch (e) { setSaveMsg('保存失败: ' + String(e)) }
            finally { setSaving(false); setTimeout(() => setSaveMsg(''), 4000) }
          }}
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
      {error && <div className="cfg-error">{error}</div>}
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
                <ArgControl arg={a} onChange={v => setArg(g.group, a.name, v)} />
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  )
}
