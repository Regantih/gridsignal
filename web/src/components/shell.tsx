import { useEffect } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { MotionConfig } from 'motion/react'
import { Moon, Sun, ShieldCheck, CalendarClock, Home, Activity, BookOpen, Command, Waves } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { keys } from '@/lib/queries'
import { useTheme } from '@/lib/theme'
import { useCalm } from '@/lib/visual-mode'
import { MARKETS, STATUS_SHORT, useMarket, type MarketId } from '@/lib/markets'
import { CommandBarProvider, useCommandBar } from '@/components/command-bar'
import { Logo } from '@/components/logo'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: 'Tonight', icon: ShieldCheck, end: true },
  { to: '/tomorrow', label: 'Tomorrow', icon: CalendarClock },
  { to: '/member', label: 'Member', icon: Home },
  { to: '/market', label: 'Market', icon: Activity },
  { to: '/how-it-works', label: 'How it works', icon: BookOpen },
]

export function Shell() {
  return (
    <CommandBarProvider>
      <Frame />
    </CommandBarProvider>
  )
}

/** Which market the deck is pointed at. Only ERCOT is live; the rest say so. */
function MarketSwitcher() {
  const { market, select } = useMarket()
  return (
    <label className="relative flex h-9 items-center rounded-full border border-border bg-surface/60 pl-3 pr-2 text-xs text-fg-muted transition-colors hover:border-border-strong hover:text-fg">
      <span className={cn('mr-2 size-2 shrink-0 rounded-full', market.status === 'live' ? 'bg-flow' : market.status === 'planned' ? 'bg-fg-subtle' : 'bg-price')} aria-hidden />
      <span className="sr-only">Market</span>
      <select
        value={market.id}
        onChange={(e) => select(e.target.value as MarketId)}
        aria-label="Market"
        data-testid="market-switcher"
        className="num cursor-pointer appearance-none bg-transparent pr-4 text-xs text-fg outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {MARKETS.map((m) => (
          <option key={m.id} value={m.id}>
            {m.iso} · {m.short} · {STATUS_SHORT[m.status]}
          </option>
        ))}
      </select>
      <span aria-hidden className="pointer-events-none absolute right-2.5 text-fg-subtle">▾</span>
    </label>
  )
}

/** A thin live strip: the three numbers an operator glances at, on every screen. */
function Ticker() {
  const fleet = useQuery({ queryKey: keys.fleet, queryFn: api.fleet })
  const { market } = useMarket()
  if (market.status !== 'live') {
    return (
      <div className="num hidden items-center gap-2 whitespace-nowrap text-xs text-fg-subtle 2xl:flex" aria-label="Fleet status" data-testid="ticker">
        <span className="size-2 rounded-full bg-fg-subtle" />
        {market.iso.toUpperCase()} NOT MODELLED YET
      </div>
    )
  }
  if (!fleet.data) return <div className="h-5 w-64 animate-pulse rounded bg-surface-2" />
  const s = fleet.data.summary
  const pending = fleet.data.pending_incident
  const ok = !pending && s.coverage_pct >= 100
  return (
    <div className="num hidden items-center gap-4 whitespace-nowrap text-xs 2xl:flex" aria-label="Fleet status" data-testid="ticker">
      <span className="flex items-center gap-2">
        <span className="relative flex size-2">
          <span className={cn('absolute inline-flex size-full animate-ping rounded-full opacity-60', ok ? 'bg-ok' : 'bg-risk')} />
          <span className={cn('relative inline-flex size-2 rounded-full', ok ? 'bg-ok' : 'bg-risk')} />
        </span>
        <span className={ok ? 'text-ok' : 'text-risk'}>{ok ? 'PROMISE HOLDS' : pending ? 'APPROVAL NEEDED' : 'SHORT'}</span>
      </span>
      <span className="text-fg-subtle">
        <span className="text-fg">{Math.round(s.committed_kw)}</span>/{Math.round(s.target_kw)} kW
      </span>
      <span className="text-fg-subtle">
        <span className="text-price">${Math.round(s.remaining_price_mwh)}</span>/MWh
      </span>
      <span className="text-fg-subtle">
        <span className="text-fg">{s.online}</span>/{s.total_devices} online
      </span>
    </div>
  )
}

function Frame() {
  const [theme, toggle] = useTheme()
  const [calm, toggleCalm] = useCalm()
  const bar = useCommandBar()
  const session = useQuery({ queryKey: ['session'], queryFn: api.session, staleTime: 60_000 })
  // Calm mode and reduced motion take the instant path: no route morph, no springs.
  useEffect(() => {
    document.documentElement.dataset.calm = calm ? 'on' : 'off'
  }, [calm])
  return (
    <MotionConfig reducedMotion={calm ? 'always' : 'user'} transition={{ type: 'spring', stiffness: 260, damping: 28, mass: 0.9 }}>
    <div className="grain min-h-dvh bg-bg text-fg">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-brand focus:px-3 focus:py-2 focus:text-brand-fg"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-border/70 bg-bg/75 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-4 px-4 md:px-8">
          <NavLink to="/" className="flex items-center gap-2.5" aria-label="GridSignal, Tonight">
            <Logo className="size-7 text-brand" />
            <span className="font-display text-[1.05rem] font-semibold tracking-tight">GridSignal</span>
          </NavLink>
          <nav aria-label="Primary" className="ml-4 hidden items-center gap-0.5 rounded-full border border-border bg-surface/60 p-1 md:flex">
            {NAV.map(({ to, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                viewTransition={!calm}
                className={({ isActive }) =>
                  cn(
                    'whitespace-nowrap rounded-full px-3.5 py-1.5 text-[0.8rem] font-medium transition-colors',
                    isActive ? 'bg-fg text-bg' : 'text-fg-muted hover:text-fg',
                  )
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <Ticker />
            <MarketSwitcher />
            <button
              type="button"
              onClick={bar.open}
              data-testid="open-command-bar"
              className="num flex h-9 items-center gap-2 rounded-full border border-border bg-surface/60 px-3 text-xs text-fg-muted transition-colors hover:border-border-strong hover:text-fg"
            >
              <Command aria-hidden className="size-3.5" />
              <span className="hidden sm:inline">Command</span>
              <kbd className="rounded bg-surface-2 px-1 text-2xs">⌘K</kbd>
            </button>
            <button
              type="button"
              role="switch"
              aria-checked={calm}
              onClick={toggleCalm}
              aria-label="Calm mode: flat map, no motion"
              title={calm ? 'Calm mode on: flat map, no motion' : 'Calm mode off: 3D flight deck'}
              data-testid="calm-toggle"
              className={cn(
                'flex h-9 items-center gap-1.5 rounded-full border px-3 text-xs',
                calm ? 'border-brand/50 bg-brand-soft text-brand' : 'border-border bg-surface/60 text-fg-muted hover:text-fg',
              )}
            >
              <Waves aria-hidden className="size-3.5" />
              <span className="hidden sm:inline">Calm</span>
            </button>
            <button
              type="button"
              onClick={toggle}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              data-testid="theme-toggle"
              className="grid size-9 place-items-center rounded-full border border-border bg-surface/60 text-fg-muted hover:text-fg"
            >
              {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-[1440px] px-4 pb-28 pt-6 md:px-8 md:pb-14 md:pt-10" tabIndex={-1}>
        <Outlet />
      </main>
      <footer className="mx-auto hidden max-w-[1440px] border-t border-border px-8 py-5 text-xs text-fg-subtle md:block">
        {session.data?.approval_sentence
          ? `In GridSignal ${session.data.approval_sentence}. ${session.data.disclosure}`
          : 'In GridSignal a human operator approves every recovery action, one incident at a time or in advance through a playbook with limits. Prices are real ERCOT data. The fleet is simulated.'}
      </footer>
      <nav
        aria-label="Primary, mobile"
        className="fixed inset-x-3 bottom-3 z-40 grid grid-cols-5 rounded-2xl border border-border bg-surface/90 shadow-lg backdrop-blur-xl md:hidden"
      >
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            viewTransition={!calm}
            className={({ isActive }) =>
              cn('flex flex-col items-center gap-0.5 py-2 text-2xs font-medium', isActive ? 'text-brand' : 'text-fg-muted')
            }
          >
            <Icon aria-hidden className="size-5" />
            {label === 'How it works' ? 'How' : label}
          </NavLink>
        ))}
      </nav>
    </div>
    </MotionConfig>
  )
}
