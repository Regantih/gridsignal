import { NavLink, Outlet } from 'react-router-dom'
import { Moon, Sun, Zap, ShieldCheck, CalendarClock, Home, Activity, BookOpen } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useTheme } from '@/lib/theme'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: 'Tonight', icon: ShieldCheck, end: true },
  { to: '/tomorrow', label: 'Tomorrow', icon: CalendarClock },
  { to: '/member', label: 'Member', icon: Home },
  { to: '/market', label: 'Market', icon: Activity },
  { to: '/how-it-works', label: 'How it works', icon: BookOpen },
]

export function Shell() {
  const [theme, toggle] = useTheme()
  const session = useQuery({ queryKey: ['session'], queryFn: api.session, staleTime: 60_000 })
  const link = ({ isActive }: { isActive: boolean }) =>
    cn(
      'flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
      isActive ? 'bg-brand-soft text-fg' : 'text-fg-muted hover:bg-surface-2 hover:text-fg',
    )
  return (
    <div className="min-h-dvh bg-bg text-fg md:grid md:grid-cols-[220px_1fr]">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-brand focus:px-3 focus:py-2 focus:text-brand-fg"
      >
        Skip to content
      </a>
      <aside className="sticky top-0 z-40 flex items-center justify-between gap-3 border-b border-border bg-surface/95 px-3 py-2 backdrop-blur md:h-dvh md:flex-col md:items-stretch md:justify-start md:border-b-0 md:border-r md:px-3 md:py-4">
        <div className="flex items-center gap-2 px-1">
          <span className="grid size-8 place-items-center rounded-md bg-brand text-brand-fg">
            <Zap aria-hidden className="size-4" />
          </span>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight">GridSignal</div>
            <div className="hidden text-2xs text-fg-subtle md:block">
              {session.data ? `${session.data.fleet_size.toLocaleString()} simulated homes` : 'Fleet control'}
            </div>
          </div>
        </div>
        <nav aria-label="Primary" className="hidden md:mt-6 md:flex md:flex-col md:gap-1">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={link}>
              <Icon aria-hidden className="size-4" /> {label}
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-1 md:mt-auto md:flex-col md:items-stretch md:gap-3">
          <p className="hidden px-1 text-2xs leading-snug text-fg-subtle md:block">
            {session.data?.disclosure ?? 'Prices are real ERCOT data. The fleet is simulated.'}
          </p>
          <Button variant="ghost" size="icon" onClick={toggle} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} data-testid="theme-toggle">
            {theme === 'dark' ? <Sun /> : <Moon />}
          </Button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-col">
        <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 pb-24 pt-5 md:px-8 md:pb-10 md:pt-8" tabIndex={-1}>
          <Outlet />
        </main>
        <footer className="hidden border-t border-border px-8 py-4 text-xs text-fg-subtle md:block">
          {session.data?.approval_sentence
            ? `In GridSignal ${session.data.approval_sentence}. ${session.data.disclosure}`
            : 'In GridSignal a human operator approves every recovery action, one incident at a time or in advance through a playbook with limits. Prices are real ERCOT data. The fleet is simulated.'}
        </footer>
      </div>
      <nav
        aria-label="Primary, mobile"
        className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 border-t border-border bg-surface/95 backdrop-blur md:hidden"
      >
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'flex flex-col items-center gap-0.5 py-2 text-2xs font-medium focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring',
                isActive ? 'text-brand' : 'text-fg-muted',
              )
            }
          >
            <Icon aria-hidden className="size-5" />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
