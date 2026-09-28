import { expect, test } from '@playwright/test'
import { dollars, freshSession } from './helpers'

test.describe('Journey C: member, mobile-first', () => {
  test('shows backup, protected reserve, earnings and plain-language history without operator tooling', async ({ page }) => {
    await freshSession(page)
    await page.goto('/member')
    await expect(page.getByTestId('member-headline')).toBeVisible()

    const homes = await (await page.request.get('/api/members')).json()
    const id: string = homes.focus_device_id
    const m = await (await page.request.get(`/api/members/${id}`)).json()
    await expect(page.getByTestId('member-headline')).toContainText(m.summary.headline)
    expect(dollars(await page.getByTestId('earned').innerText())).toBeCloseTo(m.summary.earned_usd, 2)
    await expect(page.getByTestId('reserve')).toContainText(`${m.summary.reserve_kwh.toFixed(1)} kWh`)
    await expect(page.getByTestId('backup-hours')).toContainText(/h$/)

    // Mobile: bottom navigation is present, sidebar is not.
    const width = page.viewportSize()?.width ?? 0
    if (width < 768) {
      await expect(page.getByRole('navigation', { name: /primary, mobile/i })).toBeVisible()
    }

    // An event at this home reads in plain words, with no incident ids or operator names.
    await page.request.post('/api/incidents/trigger', { data: {} })
    await page.reload()
    await expect(page.getByTestId('member-headline')).toContainText(/lost contact|paused|reporting/i)
    const history = page.getByTestId('member-history')
    await expect(history).toBeVisible()
    const text = await history.innerText()
    expect(text).not.toMatch(/INC-\d+/)
    expect(text).not.toMatch(/BAT-\d+/)
    expect(text).not.toMatch(/M\. Alvarez/)
    await expect(page.getByText(/Approve/)).toHaveCount(0)

    await page.request.post('/api/incidents/approve', { data: {} })
    await page.reload()
    await expect(page.getByTestId('member-history')).toContainText('An operator approved the fix')
  })
})
