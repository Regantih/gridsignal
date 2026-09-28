import { test, expect } from '@playwright/test'
import { freshSession } from './helpers'

test.describe('Flight deck: replay and Calm mode', () => {
  test('Replay tonight runs the story through the real engine and ends back live', async ({ page }) => {
    await freshSession(page)
    await page.getByTestId('replay-tonight').click()
    const caption = page.getByTestId('replay-caption')
    await expect(caption).toContainText('The evening event opens')
    await expect(page.getByTestId('replay-stop')).toBeVisible()

    // The incident is real: it shows up as the pending card, priced by the engine.
    await expect(page.getByTestId('pending-incident')).toBeVisible({ timeout: 15_000 })
    // On a slow machine the story may approve before we look, so read engine numbers from
    // DOM that outlives the pending card (the metric and the incident list), never from an
    // API call that races the replay clock.
    await expect(page.getByTestId('dollars-at-risk')).toContainText(/\$[0-9]*[1-9]/)
    await expect(page.getByTestId('incident-list')).toContainText(/Awaiting approval|Recovered/)
    await expect(caption).toContainText(/at risk|approves/)

    // Then the named demo operator approves it, and the dollars come back.
    await expect(page.getByTestId('pending-incident')).toHaveCount(0, { timeout: 20_000 })
    await expect(caption).toContainText('approves')
    const after = await (await page.request.get('/api/fleet')).json()
    const done = after.incidents.find((i: { status: string }) => i.status === 'resolved')
    expect(done.dollars_recovered).toBeGreaterThan(0)
    await expect(page.getByTestId('audit-list')).toContainText('human_approval')

    // A playbook with limits, then the evening sweeps on the shared clock.
    await expect(page.getByTestId('playbook-card')).toContainText('Active', { timeout: 20_000 })
    await expect(page.getByTestId('night-timeline')).toContainText('Replayed', { timeout: 15_000 })
    await expect(page.getByTestId('timeline-clock')).toBeVisible()

    // About forty seconds in, it hands the deck back, live.
    await expect(page.getByTestId('night-timeline')).toContainText('Live', { timeout: 20_000 })
    await expect(page.getByTestId('replay-tonight')).toBeVisible()
    await expect(page.getByTestId('verdict')).toContainText('keep its promise')
  })

  test('Stop hands control back at once', async ({ page }) => {
    await freshSession(page)
    await page.getByTestId('replay-tonight').click()
    await expect(page.getByTestId('replay-stop')).toBeVisible()
    await page.getByTestId('replay-stop').click()
    await expect(page.getByTestId('replay-tonight')).toBeVisible()
    await expect(page.getByTestId('replay-caption')).toHaveCount(0)
  })

  test('the scrubber replays the evening: map and dial follow the playhead, then back to live', async ({ page }) => {
    await freshSession(page)
    await page.getByTestId('trigger-incident').click()
    await expect(page.getByTestId('pending-incident')).toBeVisible()
    const slider = page.getByRole('slider', { name: 'Time of day' })
    await slider.focus()
    await page.keyboard.press('Home')
    await expect(page.getByTestId('night-timeline')).toContainText('Replayed')
    // Before the incident opened, the dial reads the full commitment from the audit stamps.
    await expect(page.getByText('at the playhead, from the audit trail')).toBeVisible()
    await page.getByTestId('timeline-live').click()
    await expect(page.getByTestId('night-timeline')).toContainText('Live')
  })

  test('Calm mode switches to the flat 2D field, remembers the choice, and keeps every number as text', async ({ page }) => {
    await freshSession(page)
    const toggle = page.getByTestId('calm-toggle')
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await expect(page.getByTestId('fleet-3d')).toBeVisible({ timeout: 15_000 })
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')
    await expect(page.getByTestId('fleet-2d')).toBeVisible()
    await expect(page.getByTestId('fleet-3d')).toHaveCount(0)
    await expect(page.getByTestId('scene-summary')).toContainText('homes online')

    await page.reload()
    await expect(page.getByTestId('calm-toggle')).toHaveAttribute('aria-checked', 'true')
    await expect(page.getByTestId('fleet-2d')).toBeVisible()

    // Journey A still works with the flat field.
    await page.getByTestId('trigger-incident').click()
    await expect(page.getByTestId('pending-incident')).toBeVisible()
    await page.getByTestId('calm-toggle').click()
    await expect(page.getByTestId('fleet-3d')).toBeVisible({ timeout: 15_000 })
  })

  test('reduced motion means Calm by default', async ({ browser }) => {
    const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } })
    const page = await ctx.newPage()
    await freshSession(page)
    await expect(page.getByTestId('calm-toggle')).toHaveAttribute('aria-checked', 'true')
    await expect(page.getByTestId('fleet-2d')).toBeVisible()
    await ctx.close()
  })
})
