// Quick runtime smoke: load every route, fail on console errors, save screenshots.
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const base = process.env.BASE_URL ?? 'http://localhost:8000'
const out = process.env.OUT ?? 'screens'
mkdirSync(out, { recursive: true })
const routes = ['/', '/tomorrow', '/member', '/market', '/how-it-works']
const viewports = { desktop: { width: 1440, height: 900 }, tablet: { width: 1024, height: 800 }, mobile: { width: 390, height: 844 } }
const browser = await chromium.launch()
let failed = false
for (const theme of ['dark', 'light']) {
  for (const [vp, size] of Object.entries(viewports)) {
    if (theme === 'light' && vp === 'tablet') continue
    const ctx = await browser.newContext({ viewport: size, colorScheme: theme })
    const page = await ctx.newPage()
    const errors = []
    page.on('pageerror', (e) => errors.push(String(e)))
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
    await page.goto(base)
    await page.evaluate((t) => localStorage.setItem('gs-theme', t), theme)
    for (const r of routes) {
      await page.goto(base + r, { waitUntil: 'networkidle' })
      await page.waitForTimeout(800)
      const name = (r === '/' ? 'tonight' : r.slice(1)) + `-${theme}-${vp}.png`
      await page.screenshot({ path: `${out}/${name}`, fullPage: true })
      if (errors.length) {
        failed = true
        console.log(`${r} [${theme} ${vp}]`, errors)
        errors.length = 0
      }
    }
    await ctx.close()
  }
}
await browser.close()
process.exit(failed ? 1 : 0)
