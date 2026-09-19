import { Select } from '../components/FormControls'
import { useEffect, useState, useSyncExternalStore } from 'react'
import { Palette } from 'lucide-react'
import { api } from '../api/client'
import type { Settings as SettingsData } from '../api/types'
import { languages, useApp, useConnection } from '../app/context'
import { ErrorBox, Loading, PageTitle } from '../components/ui'
import { editor, prepareValue } from '../config/editors'
import { EditStatus } from '../components/EditStatus'
import { FieldInput } from '../components/FieldInput'

export function Settings() {
  const {theme, setTheme, language, setLanguage, t, ui} = useApp()
  const [data, setData] = useState<SettingsData>()
  const [error, setError] = useState('')
  const connection = useConnection()

  const deployQueue = editor('deploy')
  const deployEdits = useSyncExternalStore(deployQueue.subscribe, deployQueue.getSnapshot)

  useEffect(() => {
    if (connection !== 'ready') return
    let active = true
    const confirmedDeploy = deployQueue.confirmed()
    void api.request('settings.get', {})
      .then(data => {
        if (active) {
          setData(data)
          setError('')
          deployQueue.reconcile(confirmedDeploy)
        }
      })
      .catch(error => {
        if (active) setError(error.message)
      })
    return () => { active = false }
  }, [connection, deployQueue])

  return (
    <>
      <PageTitle title={ui('nav.settings')} />
      {error && <ErrorBox message={error} />}
      {deployEdits.storageError && <ErrorBox message={deployEdits.storageError} />}
      <section className="panel config-group">
        <div className="panel-heading">
          <div>
            <Palette size={18} />
            <h2 data-text={ui('settings.uiPreferences')}>{ui('settings.uiPreferences')}</h2>
          </div>
        </div>
        <div className="field-row">
          <div className="field-label">
            <label htmlFor="ui-theme">{ui('settings.theme')}</label>
          </div>
          <div className="field-control">
            <Select id="ui-theme" value={theme} onChange={event => setTheme(event.target.value as typeof theme)}>
              <option value="light">{ui('settings.themeLight')}</option>
              <option value="dark">{ui('settings.themeDark')}</option>
            </Select>
          </div>
        </div>
        <div className="field-row">
          <div className="field-label">
            <label htmlFor="ui-language">{ui('settings.language')}</label>
          </div>
          <div className="field-control">
            <Select id="ui-language" value={language} disabled={connection !== 'ready'} onChange={event => setLanguage(event.target.value as typeof language)}>
              {Object.entries(languages).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </Select>
          </div>
        </div>
      </section>
      {!data ? (
        <Loading />
      ) : (
        data.groups.map(group => (
          <section className="panel config-group" key={group.key}>
            <div className="panel-heading">
              <h2 data-text={t(`Gui.DeploySetting.Group${group.key}`)}>{t(`Gui.DeploySetting.Group${group.key}`)}</h2>
            </div>
            {group.fields
              .filter(field => !['CDN', 'DpiScaling', 'Theme', 'Language'].includes(field.key))
              .map(field => (
                <div className={`field-row ${['textarea', 'yaml', 'task_priority'].includes(field.type) ? 'field-row-multiline' : ''}`} key={field.key}>
                  <div className="field-label">
                    <label htmlFor={`deploy-${field.key}`}>{field.label}</label>
                    <p>{field.key === 'Password' ? ui('settings.passwordHelp') : field.help.replace(/<[^>]*>/g, '')}</p>
                  </div>
                  <div className="field-control">
                    <FieldInput
                      id={`deploy-${field.key}`}
                      label={field.label}
                      type={field.type}
                      options={field.options}
                      value={deployEdits.edits[field.key]?.value ?? field.value}
                      preserveText
                      invalid={deployEdits.edits[field.key]?.status === 'error'}
                      disabled={data.demo}
                      onChange={value => {
                        const {payload, error} = prepareValue(value, field)
                        deployQueue.change(field.key, value, payload, error)
                      }}
                    />
                    <EditStatus id={`deploy-${field.key}`} edit={deployEdits.edits[field.key]} retry={deployQueue.retry} />
                  </div>
                </div>
              ))}
          </section>
        ))
      )}
    </>
  )
}
