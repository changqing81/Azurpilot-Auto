import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AppContext, type AppContextValue } from '../app/context'
import { TaskNav, isDesktopDevice } from './TaskNav'
import type { Schema } from '../api/types'
import { translateUi } from '../i18n'

const mockSchema: Schema = {
  menu: {
    Alas: {
      menu: 'collapse',
      page: 'setting',
      tasks: ['Alas', 'General', 'Restart'],
    },
    Farm: {
      menu: 'collapse',
      page: 'setting',
      tasks: ['Main', 'Main2', 'ThreeOilLowCost'],
    },
  },
  args: {},
  translations: {},
}

const mockTranslations: Record<string, string> = {
  'Menu.Alas.name': '系统',
  'Menu.Farm.name': '出击Plus',
  'Task.Alas.name': '系统设置',
  'Task.General.name': '通用设置',
  'Task.Restart.name': '游戏重启',
  'Task.Main.name': '主线常规出击',
  'Task.Main2.name': '主线常规出击2',
  'Task.ThreeOilLowCost.name': '3油低耗出击',
}

const mockContext: AppContextValue = {
  instancesLoaded: true,
  instances: [{ name: 'default', status: 'stopped', serial: '127.0.0.1:5555', server: 'cn' }],
  schema: mockSchema,
  refresh: async () => {},
  t: (key: string) => mockTranslations[key] ?? key,
  ui: (key, params) => translateUi('zh-CN', key, params),
  notify: () => {},
  previewEnabled: false,
  setPreviewEnabled: () => {},
  devMode: false,
  setDevMode: () => {},
  theme: 'light',
  setTheme: () => {},
  language: 'zh-CN',
  setLanguage: () => {},
}

describe('TaskNav 导航组件', () => {
  it('正确渲染向右展开的一级菜单按钮及无障碍属性', () => {
    const html = renderToStaticMarkup(
      <AppContext.Provider value={mockContext}>
        <MemoryRouter initialEntries={['/i/default/overview']}>
          <Routes>
            <Route path="/i/:instance/*" element={<TaskNav />} />
          </Routes>
        </MemoryRouter>
      </AppContext.Provider>
    )

    // 检查容器与搜索框
    expect(html).toContain('task-nav-container')
    expect(html).toContain('展开任务搜索')
    expect(html).not.toContain('搜索任务…')

    // 检查一级菜单项按钮
    expect(html).toContain('task-group-button')
    expect(html).toContain('aria-haspopup="menu"')
    expect(html).toContain('aria-expanded="false"')

    // 检查向右箭头图标和菜单文本
    expect(html).toContain('task-group-arrow')
    expect(html).toContain('系统')
    expect(html).toContain('出击Plus')

    // 一级菜单不展示任务数量，避免与展开箭头争夺视觉焦点
    expect(html).not.toContain('task-group-badge')
  })

  it('当处于某任务页面时，对应的一级菜单具备 active 高亮状态', () => {
    const html = renderToStaticMarkup(
      <AppContext.Provider value={mockContext}>
        <MemoryRouter initialEntries={['/i/default/task/Main']}>
          <Routes>
            <Route path="/i/:instance/task/:task" element={<TaskNav />} />
          </Routes>
        </MemoryRouter>
      </AppContext.Provider>
    )

    // Main 任务属于 Farm 分组（出击Plus），该一级菜单按钮应带有 active 类
    expect(html).toContain('task-group-button active')
    expect(html).toContain('出击Plus')
  })

  it('点击展开一级菜单时，右侧弹出二级子菜单，并按子任务项动态渲染', () => {
    const html = renderToStaticMarkup(
      <AppContext.Provider value={mockContext}>
        <MemoryRouter initialEntries={['/i/default/overview']}>
          <Routes>
            <Route path="/i/:instance/*" element={<TaskNav defaultOpenKey="Alas" />} />
          </Routes>
        </MemoryRouter>
      </AppContext.Provider>
    )

    // 一级菜单应带有 expanded 类和 aria-expanded="true"
    expect(html).toContain('task-group-button expanded')
    expect(html).toContain('aria-expanded="true"')

    // 弹出层检查（无标题栏）
    expect(html).toContain('task-submenu-flyout')
    expect(html).not.toContain('task-submenu-header')
    expect(html).not.toContain('3 项')

    // 子菜单列表检查：应动态渲染出 Alas 下的所有子任务
    expect(html).toContain('task-submenu-list')
    expect(html).toContain('task-submenu-item')
    expect(html).toContain('系统设置')
    expect(html).toContain('通用设置')
    expect(html).toContain('游戏重启')
    expect(html).toContain('href="/i/default/task/Alas"')
    expect(html).toContain('href="/i/default/task/General"')
    expect(html).toContain('href="/i/default/task/Restart"')
  })

  it('isDesktopDevice 正确区分电脑端与移动端环境', () => {
    // node/SSR 环境下无 window，应安全回退为 false
    expect(isDesktopDevice()).toBe(false)

    const originalWindow = globalThis.window

    try {
      const mockWindow = {
        innerWidth: 1280,
        matchMedia: (query: string) => ({
          matches: query.includes('(hover: none)') ? false : true,
        }),
      }
      globalThis.window = mockWindow as unknown as Window & typeof globalThis

      // 电脑端：宽度 > 950 且支持 hover
      expect(isDesktopDevice()).toBe(true)

      // 移动端：宽度 <= 950
      mockWindow.innerWidth = 768
      expect(isDesktopDevice()).toBe(false)

      // 移动端触屏：宽度 > 950 但为 touch-only (hover: none)
      mockWindow.innerWidth = 1024
      mockWindow.matchMedia = (query: string) => ({
        matches: query.includes('(hover: none)') ? true : false,
      })
      expect(isDesktopDevice()).toBe(false)
    } finally {
      if (originalWindow === undefined) {
        delete (globalThis as { window?: unknown }).window
      } else {
        globalThis.window = originalWindow
      }
    }
  })
})
