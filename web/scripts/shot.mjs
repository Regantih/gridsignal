// Quick visual check: node scripts/shot.mjs <path> <out.png> [width] [theme] [actions]
import { chromium } from '@playwright/test'

const [path = '/', out = '/home/ubuntu/shot.png', width = '1440', theme = 'dark', actions = ''] = process.argv.slice(2)
const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] })
const ctx = await browser.newContext({ viewport: { width: Number(width), height: width === '390' ? 844 : 900 }, deviceScaleFactor: 1, colorScheme: theme === 'light' ? 'light' : 'dark' })
const page = await ctx.newPage()
const errors = []
page.on('console', (m) => (m.type() === 'error' || m.type() === 'warning') && errors.push(m.text()))
page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message))
await page.goto('http://localhost:8000' + path)
await page.waitForLoadState('networkidle')
if (theme === 'light' && (await page.locator('html.dark').count())) await page.getByTestId('theme-toggle').click()
for (const a of actions.split(',').filter(Boolean)) {
  const [kind, arg] = a.split(':')
  if (kind === 'click') await page.getByTestId(arg).click()
  if (kind === 'select') await page.getByTestId('market-switcher').selectOption(arg)
  if (kind === 'wait') await page.waitForTimeout(Number(arg))
  if (kind === 'hold') {
    const el = page.getByTestId(arg)
    await el.scrollIntoViewIfNeeded()
    await el.hover()
    await page.mouse.down()
    await page.waitForTimeout(1150)
    await page.mouse.up()
  }
}
await page.waitForTimeout(2500)
await page.screenshot({ path: out, fullPage: process.env.FULL === '1' })
console.log(errors.slice(0, 15).join('\n'))
await browser.close()
