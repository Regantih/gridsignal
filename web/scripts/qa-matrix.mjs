// Full visual QA matrix: every screen, dark and light, 1440/1024/390, WebGL (swiftshader) and Calm mode.
// Also reports page errors, console errors and any horizontal overflow per screen.
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const base = process.env.BASE ?? 'http://localhost:8000'
const out = process.env.OUT ?? '/home/ubuntu/qa-matrix'
mkdirSync(out, { recursive: true })
const browser = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'] })
const problems = []
let shots = 0

async function overflow(page, w, tag) {
  const res = await page.evaluate((w) => {
    const bad = []
    for (const el of document.querySelectorAll('body *')) {
      const b = el.getBoundingClientRect()
      if (b.width && b.right > w + 1) bad.push(`${el.tagName.toLowerCase()}.${typeof el.className === 'string' ? el.className.slice(0, 50) : ''} right=${Math.round(b.right)}`)
    }
    return { sw: document.documentElement.scrollWidth, bad: bad.slice(0, 4) }
  }, w)
  if (res.sw > w) problems.push(`${tag}: scrollWidth ${res.sw} > ${w}\n    ${res.bad.join('\n    ')}`)
}

async function shot(page, w, name, sel) {
  const el = sel ? page.locator(sel).first() : null
  if (el) { await el.scrollIntoViewIfNeeded(); await page.waitForTimeout(2200); await el.screenshot({ path: `${out}/${name}.png` }) }
  else await page.screenshot({ path: `${out}/${name}.png` })
  shots++
  await overflow(page, w, name)
}

for (const w of [1440, 1024, 390]) {
  for (const calm of [false, true]) for (const dark of [true, false]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: w === 390 ? 844 : 900 }, colorScheme: dark ? 'dark' : 'light' })
    const page = await ctx.newPage()
    const tag = `${calm ? 'calm' : 'gl'}-${dark ? 'dark' : 'light'}-${w}`
    page.on('pageerror', (e) => problems.push(`${tag}: pageerror ${e.message}`))
    page.on('console', (m) => { if (m.type() === 'error') problems.push(`${tag}: console ${m.text()}`) })

    await page.goto(base + '/')
    await page.waitForTimeout(1500)
    if (calm) { await page.getByTestId('calm-toggle').click(); await page.waitForTimeout(500) }
    await page.waitForTimeout(3500)
    await shot(page, w, `${tag}-tonight`)
    await shot(page, w, `${tag}-texas`, '[style*="fleet-map"]')
    await page.getByRole('group', { name: 'Map view' }).getByRole('button', { name: 'United States' }).click()
    await page.waitForTimeout(2500)
    await shot(page, w, `${tag}-usa`, '[style*="fleet-map"]')
    await page.getByTestId('market-switcher').selectOption('comed')
    await page.waitForTimeout(3000)
    await shot(page, w, `${tag}-illinois`, '[style*="fleet-map"]')
    await page.screenshot({ path: `${out}/${tag}-illinois-page.png`, fullPage: true })
    await page.getByTestId('market-switcher').selectOption('ercot')

    await page.goto(base + '/tomorrow')
    await page.waitForTimeout(3000)
    await shot(page, w, `${tag}-tomorrow`)
    await page.getByTestId('storm-gulf').click()
    await page.waitForTimeout(3500)
    await shot(page, w, `${tag}-storm`, '[data-testid="storm-lab"]')
    await page.screenshot({ path: `${out}/${tag}-tomorrow-full.png`, fullPage: true })

    await page.goto(base + '/member')
    await page.waitForTimeout(3000)
    await shot(page, w, `${tag}-member`)
    await page.screenshot({ path: `${out}/${tag}-member-full.png`, fullPage: true })

    await page.goto(base + '/market')
    await page.waitForTimeout(3000)
    await shot(page, w, `${tag}-market`)
    await page.screenshot({ path: `${out}/${tag}-market-full.png`, fullPage: true })

    await page.goto(base + '/how-it-works')
    await page.waitForTimeout(2500)
    await shot(page, w, `${tag}-how-top`)
    const steps = page.locator('[data-testid^="story-step-"]')
    const n = await steps.count()
    for (let i = 0; i < n; i++) {
      await steps.nth(i).scrollIntoViewIfNeeded()
      await page.waitForTimeout(1200)
      await shot(page, w, `${tag}-how-step${i + 1}`)
    }
    await ctx.close()
  }
}
await browser.close()
console.log(`${shots} screenshots in ${out}`)
console.log(problems.length ? problems.join('\n') : 'no page errors, console errors or overflow')
