import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { AppContext, type AppContextValue } from '../app/context'
import type { Resource } from '../api/types'
import { translateUi } from '../i18n'
import { ResourceCards } from './ResourceCards'

function renderResources(resources: Resource[]) {
  return renderToStaticMarkup(
    <AppContext.Provider value={{ui: (key, params) => translateUi('zh-CN', key, params)} as AppContextValue}>
      <ResourceCards resources={resources} selected={['ActionPoint']}/>
    </AppContext.Provider>,
  )
}

describe('资源卡片', () => {
  it('行动力没有上限时显示不同的总行动力', () => {
    const html = renderResources([{name: 'ActionPoint', label: '行动力', value: 101, total: 1301, record: '2026-09-16 12:00:00'}])

    expect(html).toContain('101')
    expect(html).toContain('总行动力')
    expect(html).toContain('1,301')
  })

  it('总行动力未增加时不显示冗余或异常后缀', () => {
    const html = renderResources([{name: 'ActionPoint', label: '行动力', value: 101, total: 101, record: '2026-09-16 12:00:00'}])
    const invalid = renderResources([{name: 'ActionPoint', label: '行动力', value: 101, total: 0, record: '2026-09-16 12:00:00'}])

    expect(html).toContain('101')
    expect(html).not.toContain('总行动力')
    expect(invalid).not.toContain('总行动力')
  })
})
