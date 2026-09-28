import { chromium } from '@playwright/test'

const base = process.env.BASE ?? 'http://localhost:8000'
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
page.on('pageerror', (e) => console.log('PAGEERROR', e.message, '\n', e.stack?.split('\n').slice(0, 6).join('\n')))
page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 300)) })
const nav = ['Tonight', 'Tomorrow', 'Member', 'Market', 'How it works']
await page.goto(base + '/')
await page.waitForTimeout(3000)
for (let round = 0; round < 2; round++) {
  for (const n of nav) {
    await page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('link', { name: n }).click()
    await page.waitForTimeout(1200)
    if (n === 'How it works') {
      for (let y = 0; y < 6000; y += 400) { await page.mouse.wheel(0, 400); await page.waitForTimeout(150) }
    }
  }
}
await page.getByTestId('calm-toggle').click()
await page.waitForTimeout(500)
for (const n of nav) {
  await page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('link', { name: n }).click()
  await page.waitForTimeout(800)
}
console.log('done')
await browser.close()
