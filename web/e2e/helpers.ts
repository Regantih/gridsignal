import { expect, type Page } from '@playwright/test'

export const dollars = (text: string) => Number(text.replace(/[^0-9.-]/g, ''))

/**
 * E2E_SLOW=<rate> throttles the page's CPU through CDP (Chromium only), e.g. E2E_SLOW=4 for a
 * quarter-speed machine, to prove timing-sensitive journeys hold on slow hardware.
 */
export async function slowMachine(page: Page) {
  const rate = Number(process.env.E2E_SLOW ?? 0)
  if (!rate || page.context().browser()?.browserType().name() !== 'chromium') return
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate })
}

/** Fresh simulated evening for this browser context. The session id lives in a cookie. */
export async function freshSession(page: Page, fleetSize = 48) {
  await slowMachine(page)
  await page.goto('/')
  const res = await page.request.post('/api/session/reset', {
    data: { fleet_size: fleetSize, price_scenario: 'normal' },
  })
  expect(res.ok()).toBeTruthy()
  await page.reload()
  await expect(page.getByTestId('verdict')).toBeVisible()
}

export async function fleet(page: Page) {
  const res = await page.request.get('/api/fleet')
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as {
    incidents: { incident_id: string; dollars_at_risk: number; dollars_recovered: number; status: string }[]
    summary: { commit_ratio: number; target_kw: number }
    playbook: { max_kw: number; max_devices: number } | null
    audit: { kind: string }[]
  }
}

/** Recovery and commitment buttons are press-and-hold. Hold past the 900 ms fill, then release. */
export async function holdToConfirm(page: Page, testId: string) {
  const el = page.getByTestId(testId)
  await el.scrollIntoViewIfNeeded()
  await el.hover()
  await page.mouse.down()
  await page.waitForTimeout(1150)
  await page.mouse.up()
}

/** Keyboard version: hold Enter on the focused hold button. */
export async function holdKey(page: Page, testId: string, key = 'Enter') {
  await page.getByTestId(testId).focus()
  await page.keyboard.down(key)
  await page.waitForTimeout(1150)
  await page.keyboard.up(key)
}
