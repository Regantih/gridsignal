import { expect, test } from '@playwright/test'
import { dollars, fleet, freshSession } from './helpers'

const APPROVAL = 'a human operator approves every recovery action, one incident at a time or in advance through a playbook with limits'

test.describe('Journey A: operator, Tonight', () => {
  test('incident appears with dollars at risk, operator approves, dollars recovered and audit update', async ({ page }) => {
    await freshSession(page)
    await expect(page.getByTestId('verdict')).toContainText('keep its promise')
    await expect(page.getByText(APPROVAL).first()).toBeVisible()
    await expect(page.getByText('Prices are real ERCOT data. The fleet is simulated.').first()).toBeVisible()

    await page.getByTestId('trigger-incident').click()
    const pending = page.getByTestId('pending-incident')
    await expect(pending).toBeVisible()
    await expect(page.getByTestId('verdict')).toContainText('Promise at risk')
    await expect(pending).toContainText('Nothing has been dispatched')

    const engine = await fleet(page)
    const inc = engine.incidents[0]
    expect(inc.status).toBe('awaiting_approval')
    expect(inc.dollars_at_risk).toBeGreaterThan(0)
    expect(dollars(await pending.getByTestId('incident-at-risk').innerText())).toBeCloseTo(inc.dollars_at_risk, 2)
    expect(dollars(await page.getByTestId('dollars-at-risk').innerText())).toBeCloseTo(inc.dollars_at_risk, 2)

    await page.getByTestId('approve-recovery').click()
    await expect(page.getByTestId('verdict')).toContainText('keep its promise')
    await expect(page.getByTestId('pending-incident')).toHaveCount(0)

    const after = await fleet(page)
    const done = after.incidents[0]
    expect(done.status).toBe('resolved')
    expect(done.dollars_recovered).toBeGreaterThan(0)
    expect(done.dollars_recovered).toBeLessThanOrEqual(done.dollars_at_risk)
    expect(dollars(await page.getByTestId('dollars-recovered').innerText())).toBeCloseTo(done.dollars_recovered, 2)
    await expect(page.getByTestId('audit-list')).toContainText('Recovery plan approved')
    await expect(page.getByTestId('audit-list')).toContainText('human_approval')
  })

  test('a playbook with limits recovers a small incident without a second click', async ({ page }) => {
    await freshSession(page)
    await page.getByTestId('approve-playbook').click()
    await expect(page.getByTestId('playbook-card')).toContainText('Active')
    await expect(page.getByTestId('audit-list')).toContainText('playbook_approved')

    await page.getByTestId('trigger-incident').click()
    await expect(page.getByTestId('incident-list')).toBeVisible()
    await expect(page.getByTestId('pending-incident')).toHaveCount(0)
    await expect(page.getByTestId('verdict')).toContainText('keep its promise')

    const after = await fleet(page)
    expect(after.playbook).not.toBeNull()
    const inc = after.incidents[0]
    expect(inc.status).toBe('resolved')
    expect(inc.dollars_recovered).toBeLessThanOrEqual(inc.dollars_at_risk)
    await expect(page.getByTestId('audit-list')).toContainText('playbook_execution')
  })

  test('is keyboard reachable: skip link, tab to trigger, Enter opens the incident', async ({ page }) => {
    await freshSession(page)
    await page.keyboard.press('Tab')
    await expect(page.getByRole('link', { name: /skip to content/i })).toBeFocused()
    await page.getByTestId('trigger-incident').focus()
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('pending-incident')).toBeVisible()
    await page.getByTestId('approve-recovery').focus()
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('pending-incident')).toHaveCount(0)
  })
})
