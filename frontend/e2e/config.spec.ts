import { expect, test } from '@playwright/test'

const serialSelector = '[id="Alas.Emulator.Serial"]'
const serialStatus = '[id="Alas.Emulator.Serial-status"]'

test('两个页面的旧快照均能保存，字段更新互不覆盖', async ({page, context}) => {
  const second = await context.newPage()
  await page.goto('/#/i/testpilot/task/Alas')
  await second.goto('/#/i/testpilot/task/Alas')
  await expect(page.locator(serialSelector)).toBeVisible()
  const threshold = second.locator('[id="Alas.Error.GameStuckThreshold"]')
  await expect(threshold).toBeVisible()
  await page.locator(serialSelector).fill('parallel-page-edit')
  await expect(page.locator(serialStatus)).toHaveText('已保存')
  await threshold.fill('7')
  await expect(second.locator('[id="Alas.Error.GameStuckThreshold-status"]')).toHaveText('已保存')
  await second.reload()
  await expect(second.locator(serialSelector)).toHaveValue('parallel-page-edit')
  await expect(threshold).toHaveValue('7')
})

test('旧响应延迟期间连续输入并切页，最终值继续保存', async ({page}) => {
  let release: (() => void) | undefined
  let firstId: string | undefined
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(String(message))
      if (!firstId && request.method === 'config.patch') firstId = request.id
      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(String(message))
      if (firstId && response.id === firstId) release = () => socket.send(message)
      else socket.send(message)
    })
  })
  await page.goto('/#/i/testpilot/task/Alas')
  const serial = page.locator(serialSelector)
  await serial.fill('first-in-flight')
  await expect.poll(() => !!release).toBe(true)
  await serial.fill('latest-user-input')
  await expect(serial).toHaveValue('latest-user-input')
  await page.locator('.breadcrumb .instance-caption').click()
  release!()
  await page.goto('/#/i/testpilot/task/Alas')
  await expect(serial).toHaveValue('latest-user-input')
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('azurpilot.edits.config:testpilot'))).toBeNull()
  await page.reload()
  await expect(serial).toHaveValue('latest-user-input')
})

test('请求尚未送达就断线并刷新，恢复后自动保存原输入', async ({page}) => {
  let drop = true
  let dropped = false
  await page.routeWebSocket('**/api/v1/ws', socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      if (JSON.parse(String(message)).method === 'config.patch' && drop) {
        dropped = true
        void socket.close({code: 1012})
      } else server.send(message)
    })
  })
  await page.goto('/#/i/testpilot/task/Alas')
  const serial = page.locator(serialSelector)
  await serial.fill('survives-disconnect-and-reload')
  await expect.poll(() => dropped).toBe(true)
  drop = false
  await page.reload()
  await expect(serial).toHaveValue('survives-disconnect-and-reload')
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('azurpilot.edits.config:testpilot'))).toBeNull()
  await page.reload()
  await expect(serial).toHaveValue('survives-disconnect-and-reload')
})

test('非法数字、日期和 YAML 保留草稿，其他字段仍即时保存', async ({page}) => {
  await page.goto('/#/i/testpilot/task/Alas')
  const threshold = page.locator('[id="Alas.Error.GameStuckThreshold"]')
  await threshold.fill('-')
  await expect(threshold).toHaveValue('-')
  await expect(threshold).toHaveAttribute('aria-invalid', 'true')
  const yaml = page.locator('[id="Alas.Error.OnePushConfig"]')
  await yaml.fill('provider: [')
  await expect(page.locator('[id="Alas.Error.OnePushConfig-status"]')).toContainText('YAML 格式不正确')
  await page.locator(serialSelector).fill('valid-despite-invalid-fields')
  await expect(page.locator(serialStatus)).toHaveText('已保存')
  await page.reload()
  await expect(threshold).toHaveValue('-')
  await expect(yaml).toHaveText('provider: [')
  await expect(page.locator(serialSelector)).toHaveValue('valid-despite-invalid-fields')
  await threshold.fill('3')
  await yaml.fill('provider: null')
  await expect(page.locator('[id="Alas.Error.GameStuckThreshold-status"]')).toHaveText('已保存')
  await expect(page.locator('[id="Alas.Error.OnePushConfig-status"]')).toHaveText('已保存')
  await page.goto('/#/i/testpilot/task/Main')
  const date = page.locator('[id="Main.Scheduler.NextRun"]')
  await date.fill('2026-02-30 12:00:00')
  await expect(page.locator('[id="Main.Scheduler.NextRun-status"]')).toContainText('日期格式')
  await expect(date).toHaveValue('2026-02-30 12:00:00')
  await date.fill('2099-01-01 12:00:00')
  await expect(page.locator('[id="Main.Scheduler.NextRun-status"]')).toHaveText('已保存')
})
