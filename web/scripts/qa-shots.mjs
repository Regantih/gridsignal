import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const base = process.env.BASE ?? 'http://localhost:8000'
const out = process.env.OUT ?? '/home/ubuntu/qa-shots'
mkdirSync(out, { recursive: true })
const browser = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] })

async function shot(page, name, sel) {
  const el = sel ? page.locator(sel).first() : null
  if (el) { await el.scrollIntoViewIfNeeded(); await page.waitForTimeout(2500); await el.screenshot({ path: `${out}/${name}.png` }) }
  else await page.screenshot({ path: `${out}/${name}.png` })
}

for (const w of [1440, 1024, 390]) {
  for (const calm of [false, true]) for (const dark of [true, false]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: w === 390 ? 844 : 900 }, colorScheme: dark ? 'dark' : 'light' })
    const page = await ctx.newPage()
    const tag = `${calm ? 'calm' : 'gl'}-${dark ? 'dark' : 'light'}-${w}`
    await page.goto(base + '/')
    await page.waitForTimeout(1500)
    if (calm) { await page.getByTestId('calm-toggle').click(); await page.waitForTimeout(500) }
    await page.waitForTimeout(3000)
    await shot(page, `${tag}-tonight-header`)
    await shot(page, `${tag}-texas`, '[style*="fleet-map"]')
    await page.getByRole('group', { name: 'Map view' }).getByRole('button', { name: 'United States' }).click()
    await page.waitForTimeout(2500)
    await shot(page, `${tag}-usa`, '[style*="fleet-map"]')
    await page.getByTestId('market-switcher').selectOption('comed')
    await page.waitForTimeout(2500)
    await shot(page, `${tag}-illinois`, '[style*="fleet-map"]')
    await page.getByTestId('market-switcher').selectOption('ercot')
    await page.goto(base + '/member')
    await page.waitForTimeout(2000)
    await shot(page, `${tag}-member`)
    await ctx.close()
  }
}
await browser.close()
console.log('ok')
