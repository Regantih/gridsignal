// Round 4 review screens: the exact captures asked for, WebGL on (swiftshader), dark and light, 1440 and 390.
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const base = process.env.BASE ?? 'http://localhost:8000'
const out = process.env.OUT ?? '/home/ubuntu/qa-r4'
mkdirSync(out, { recursive: true })
const browser = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] })
const errors = []

async function shot(page, name, sel) {
  const el = sel ? page.locator(sel).first() : null
  if (el) { await el.scrollIntoViewIfNeeded(); await page.waitForTimeout(2500); await el.screenshot({ path: `${out}/${name}.png` }) }
  else await page.screenshot({ path: `${out}/${name}.png` })
}

for (const w of [1440, 390]) {
  for (const dark of [true, false]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: w === 390 ? 844 : 900 }, colorScheme: dark ? 'dark' : 'light' })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => errors.push(`${w} ${dark ? 'dark' : 'light'}: ${e.message}`))
    page.on('console', (m) => { if (m.type() === 'error') errors.push(`${w} ${dark ? 'dark' : 'light'}: console ${m.text()}`) })
    const tag = `${dark ? 'dark' : 'light'}-${w}`
    await page.goto(base + '/')
    await page.waitForTimeout(4500)
    await shot(page, `${tag}-texas`, '[style*="fleet-map"]')
    await page.getByRole('group', { name: 'Map view' }).getByRole('button', { name: 'United States' }).click()
    await page.waitForTimeout(2500)
    await shot(page, `${tag}-usa`, '[style*="fleet-map"]')
    await page.getByTestId('market-switcher').selectOption('comed')
    await page.waitForTimeout(3000)
    await shot(page, `${tag}-illinois`, '[style*="fleet-map"]')
    await page.getByTestId('market-switcher').selectOption('ercot')
    await page.goto(base + '/tomorrow')
    await page.waitForTimeout(3000)
    await page.getByTestId('storm-gulf').click()
    await page.waitForTimeout(3500)
    await shot(page, `${tag}-storm`, '[data-testid="storm-lab"]')
    await page.goto(base + '/member')
    await page.waitForTimeout(3000)
    await page.screenshot({ path: `${out}/${tag}-member-full.png`, fullPage: true })
    await page.goto(base + '/how-it-works')
    await page.waitForTimeout(2500)
    await shot(page, `${tag}-how-top`)
    await ctx.close()
  }
}
await browser.close()
console.log(errors.length ? errors.join('\n') : 'no page errors')
