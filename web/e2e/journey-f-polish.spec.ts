import { expect, test } from '@playwright/test'
import { freshSession, holdToConfirm } from './helpers'

test.describe('Round 3 polish: story, morph and liquid battery', () => {
  test('How it works tells the story with engine numbers and the hold gesture dispatches nothing', async ({ page }) => {
    await freshSession(page)
    await page.goto('/how-it-works')
    const story = page.getByTestId('how-story')
    await expect(story).toHaveAttribute('data-motion', 'on')
    for (const id of ['promise', 'storm', 'correlated', 'approval', 'limits']) {
      await expect(page.getByTestId(`story-step-${id}`)).toBeAttached()
    }

    const fleet = await (await page.request.get('/api/fleet')).json()
    const stormRes = await (await page.request.post('/api/whatif/storm', { data: { lat: 29.76, lon: -95.37, radius_km: 90 } })).json()
    const storm = page.getByTestId('story-step-storm')
    await storm.scrollIntoViewIfNeeded()
    await expect(storm).toContainText(`${stormRes.homes} homes`)
    await expect(page.getByTestId('story-approval')).toContainText('a human operator approves every recovery action')

    const before = (await (await page.request.get('/api/fleet')).json()).audit.length
    await page.getByTestId('story-step-approval').scrollIntoViewIfNeeded()
    await holdToConfirm(page, 'story-hold')
    await expect(page.getByTestId('story-hold')).toContainText(/rehearsal/i)
    const after = (await (await page.request.get('/api/fleet')).json()).audit.length
    expect(after).toBe(before)
    expect(fleet.summary.target_kw).toBeGreaterThan(0)
  })

  test('with motion off the story is a plain page', async ({ page }) => {
    await freshSession(page)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/how-it-works')
    await expect(page.getByTestId('how-story')).toHaveAttribute('data-motion', 'off')
    await expect(page.getByTestId('story-step-limits')).toContainText('Prices are real ERCOT data')
  })

  test('Tonight and Tomorrow share the dial and the map for the route morph', async ({ page }) => {
    await freshSession(page)
    await page.goto('/')
    await expect(page.locator('[style*="promise-dial"]')).toHaveCount(1)
    await expect(page.locator('[style*="fleet-map"]')).toHaveCount(1)
    await page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('link', { name: 'Tomorrow' }).click()
    await expect(page).toHaveURL(/\/tomorrow/)
    await expect(page.locator('[style*="promise-dial"]')).toHaveCount(1)
    await expect(page.locator('[style*="fleet-map"]')).toHaveCount(1)
    const fleet = await (await page.request.get('/api/fleet')).json()
    await expect(page.getByTestId('plan-headline')).toContainText(`${Math.round(fleet.summary.committed_kw)}`)
  })

  test('Member: liquid battery levels come from the member API and the cards swipe', async ({ page }) => {
    await freshSession(page)
    await page.goto('/member')
    const homes = await (await page.request.get('/api/members')).json()
    const m = await (await page.request.get(`/api/members/${homes.focus_device_id}`)).json()
    const label = await page.getByTestId('liquid-battery').getAttribute('aria-label')
    expect(label).toContain(`${m.summary.stored_kwh.toFixed(1)} kWh`)
    expect(label).toContain(`${Math.min(m.summary.reserve_kwh, m.summary.stored_kwh).toFixed(1)} locked`)

    const cards = page.getByTestId('story-cards')
    await expect(cards.getByRole('tab', { selected: true })).toHaveAttribute('aria-label', 'Card 1')
    await cards.getByRole('button', { name: 'Next card' }).click()
    await expect(cards.getByRole('tab', { selected: true })).toHaveAttribute('aria-label', 'Card 2')
    await expect(page.getByTestId('backup-hours')).toBeInViewport()
    const box = await cards.getByRole('button', { name: 'Next card' }).boundingBox()
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  })
})
