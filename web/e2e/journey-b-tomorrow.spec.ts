import path from 'node:path'
import { expect, test } from '@playwright/test'
import { fleet, freshSession } from './helpers'

test.describe("Journey B: operator, Tomorrow's promise", () => {
  test('planner recommendation matches the API, commitment can be set, risk map and SYNTHETIC rates show', async ({ page }) => {
    await freshSession(page)
    await page.goto('/tomorrow')
    await expect(page.getByTestId('plan-headline')).toBeVisible()

    const scenario = await page.getByTestId('scenario-select').inputValue()
    const plan = await (await page.request.get(`/api/plan?scenario=${encodeURIComponent(scenario)}&target=0.99`)).json()
    const shown = await page.getByTestId('recommended-ratio').innerText()
    expect(Number(shown.replace('%', '')) / 100).toBeCloseTo(plan.recommended_ratio, 2)
    await expect(page.getByTestId('reliability-curve').locator('svg').first()).toBeVisible()
    await expect(page.getByText(/not about earning more than a naive schedule/)).toBeVisible()

    const commitRes = page.waitForResponse((r) => r.url().includes('/api/commitment') && r.request().method() === 'POST')
    await page.getByTestId('use-recommended').click()
    const committed = await (await commitRes).json()
    await expect(page.getByTestId('commit-card')).toContainText('Commitment set')
    // The engine derives the ratio from whole kW, so it can land a hair under the request.
    const after = await fleet(page)
    expect(after.summary.commit_ratio).toBeCloseTo(committed.fleet.summary.commit_ratio, 6)
    expect(after.summary.target_kw).toBeCloseTo(committed.target_kw, 6)
    expect(Math.abs(after.summary.commit_ratio - plan.recommended_ratio)).toBeLessThan(0.001)
    await page.goto('/')
    await expect(page.getByTestId('audit-list')).toContainText('commitment_set')
    await page.goto('/tomorrow')

    await expect(page.getByTestId('scenario-select')).toBeVisible()
    const options = await page.getByTestId('scenario-select').locator('option').evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
    expect(options).toContain('This year so far (live feed)')

    await page.getByTestId('tab-risk').click()
    const risk = await (await page.request.get('/api/risk')).json()
    await expect(page.getByTestId('risk-groups')).toBeVisible()
    await expect(page.getByTestId('risk-promise')).toHaveText(String(risk.summary['Promise at risk']))
    await expect(page.getByTestId('risk-waits')).toHaveText(String(risk.summary['Waits for a person']))
    await expect(page.getByTestId('risk-playbook')).toHaveText(String(risk.summary['Playbook recovers it']))
    await expect(page.getByRole('img', { name: /Map of Texas/ })).toBeVisible()
    await expect(page.getByText(/Gateway rings/)).toBeVisible()
    await expect(page.getByText(/simulated as zone quadrants/)).toBeVisible()

    await page.getByTestId('tab-learn').click()
    await page.getByTestId('learn-synthetic').click()
    await expect(page.getByTestId('synthetic-label')).toBeVisible()
    await expect(page.getByTestId('synthetic-label')).toHaveText('SYNTHETIC')
    await expect(page.getByTestId('rates-table')).toBeVisible()

    await page.getByTestId('upload-input').setInputFiles(path.join(import.meta.dirname, 'fixtures/telemetry.jsonl'))
    await expect(page.getByTestId('learn-source')).toContainText('telemetry.jsonl')
    await expect(page.getByTestId('synthetic-label')).toHaveCount(0)
    await expect(page.getByText('Uploaded telemetry', { exact: true }).last()).toBeVisible()
    const learn = await (await page.request.get('/api/learn')).json()
    expect(learn.learn_source).toBe('upload')
    expect(learn.upload.synthetic).toBe(false)
  })
})

test.describe('Journey B2: storm rehearsal', () => {
  test('a preset storm returns the same answer as the API and never dispatches', async ({ page }) => {
    await freshSession(page)
    await page.goto('/tomorrow')
    await page.getByTestId('storm-gulf').click()
    const out = page.getByTestId('storm-result')
    await expect(out).toBeVisible()
    const res = await page.request.post('/api/whatif/storm', { data: { lat: 29.76, lon: -95.37, radius_km: 90 } })
    const s = (await res.json()) as { homes: number; holds: boolean }
    await expect(out).toContainText(s.holds ? 'The promise holds' : 'The promise breaks')
    await expect(out).toContainText(String(s.homes))
    const f = await fleet(page)
    expect(f.incidents.length).toBe(0)
  })
})
