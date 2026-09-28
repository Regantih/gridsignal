import { chromium } from '@playwright/test'

const base = process.env.BASE ?? 'http://localhost:8000'
const routes = ['/', '/tomorrow', '/member', '/market', '/how-it-works']
const widths = [1440, 1024, 390]
const browser = await chromium.launch()
for (const calm of [false, true]) {
  for (const w of widths) {
    const ctx = await browser.newContext({ viewport: { width: w, height: 900 } })
    const page = await ctx.newPage()
    if (calm) await page.emulateMedia({ reducedMotion: 'reduce' })
    for (const r of routes) {
      await page.goto(base + r)
      await page.waitForTimeout(2500)
      const res = await page.evaluate((w) => {
        const out = []
        for (const el of document.querySelectorAll('body *')) {
          const b = el.getBoundingClientRect()
          if (b.width && b.right > w + 1) {
            const cls = (el.className && typeof el.className === 'string') ? el.className.slice(0, 60) : ''
            out.push(`${el.tagName.toLowerCase()}.${cls} right=${Math.round(b.right)}`)
          }
        }
        return { sw: document.documentElement.scrollWidth, out: out.slice(0, 6) }
      }, w)
      if (res.sw > w) console.log(`calm=${calm} w=${w} ${r} scrollWidth=${res.sw}\n  ` + res.out.join('\n  '))
    }
    await ctx.close()
  }
}
await browser.close()
