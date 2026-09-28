import { test, expect } from '@playwright/test'
import { freshSession } from './helpers'

test.describe('Markets: ERCOT live, the rest honestly not modelled', () => {
  test('the switcher defaults to ERCOT live and the API agrees', async ({ page }) => {
    await freshSession(page)
    const sw = page.getByTestId('market-switcher')
    await expect(sw).toHaveValue('ercot')
    await expect(sw.locator('option[value=ercot]')).toHaveText(/ERCOT.*Live/)
    await expect(sw.locator('option[value=comed]')).toHaveText(/PJM.*Coming soon/)
    await expect(sw.locator('option[value=colorado]')).toHaveText(/Equipment only/)
    await expect(page.getByTestId('verdict')).toBeVisible()

    const markets = await (await page.request.get('/api/markets')).json()
    expect(markets.live).toEqual(['ercot'])
    const comed = markets.markets.find((m: { id: string }) => m.id === 'comed')
    expect(comed.status).toBe('planned')
    expect(comed.iso).toBe('PJM')
  })

  test('Illinois is planned, shows no dollar figures and no fleet numbers, then returns to Texas', async ({ page }) => {
    await freshSession(page)
    await page.getByTestId('market-switcher').selectOption('comed')
    const other = page.getByTestId('other-market')
    await expect(other).toBeVisible()
    await expect(other).toContainText('Coming soon')
    await expect(other).toContainText('not modelled yet')
    await expect(page.getByTestId('market-placeholder')).toContainText('PJM and ComEd price')
    await expect(page.getByTestId('market-placeholder')).toContainText('ComEd promise model')
    await expect(page.getByTestId('verdict')).toHaveCount(0)
    await expect(page.getByTestId('dollars-at-risk')).toHaveCount(0)

    const main = await page.locator('main').innerText()
    expect(main).not.toMatch(/\$\s?\d/)
    expect(main).not.toMatch(/\bkW\b/)
    expect(main).not.toMatch(/MWh/)
    await expect(page.getByTestId('market-switcher')).toHaveValue('comed')

    await page.getByTestId('market-back-ercot').click()
    await expect(page.getByTestId('market-switcher')).toHaveValue('ercot')
    await expect(page.getByTestId('verdict')).toBeVisible()
  })

  test('Calm mode shows the same markets on a flat USA and the keyboard reaches them', async ({ page }) => {
    await freshSession(page)
    await page.getByTestId('calm-toggle').click()
    await page.getByTestId('market-view-us').click()
    await expect(page.getByTestId('fleet-2d')).toBeVisible()
    await expect(page.getByTestId('us-map-ercot')).toHaveAttribute('aria-label', /Live/)
    await expect(page.getByTestId('us-map-comed')).toHaveAttribute('aria-label', /Coming soon/)
    await expect(page.getByTestId('us-map-colorado')).toHaveAttribute('aria-label', /Equipment only/)
    await page.getByTestId('us-map-comed').focus()
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('other-market')).toBeVisible()
  })

  test('the command bar offers Go to market', async ({ page }) => {
    await freshSession(page)
    await page.keyboard.press('Control+k')
    await page.getByPlaceholder(/Approve, simulate/i).fill('market Illinois')
    await page.getByText('Go to market Illinois').click()
    await expect(page.getByTestId('other-market')).toBeVisible()
  })
})
