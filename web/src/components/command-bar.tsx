import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Dialog as DialogPrimitive } from 'radix-ui'
import { useQueryClient } from '@tanstack/react-query'
import { CornerDownLeft, Search } from 'lucide-react'
import { api, type Fleet } from '@/lib/api'
import { keys } from '@/lib/queries'
import { useTheme } from '@/lib/theme'
import { MARKETS, STATUS_LABEL, setMarket, setMarketView } from '@/lib/markets'
import { cn } from '@/lib/utils'

/**
 * One keystroke to anything: Cmd/Ctrl+K opens a bar that navigates, simulates, approves and
 * commits. Actions that change the fleet run through the same API the screens use, and an
 * approval from here is still a named human approval in the audit trail.
 */

interface Action {
  id: string
  group: 'Go to' | 'Simulate' | 'Decide' | 'View'
  label: string
  hint?: string
  keywords?: string
  run: () => Promise<unknown> | void
}

const Ctx = createContext<{ open: () => void }>({ open: () => {} })
export const useCommandBar = () => useContext(Ctx)

export function CommandBarProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const nav = useNavigate()
  const qc = useQueryClient()
  const [, toggleTheme] = useTheme()
  const list = useRef<HTMLUListElement>(null)

  const refreshAll = useCallback(
    (fleet?: Fleet) => {
      if (fleet) qc.setQueryData(keys.fleet, fleet)
      void qc.invalidateQueries()
    },
    [qc],
  )

  const actions: Action[] = useMemo(
    () => [
      { id: 'go-tonight', group: 'Go to', label: 'Tonight', hint: 'Will the fleet keep its promise?', run: () => nav('/') },
      { id: 'go-tomorrow', group: 'Go to', label: 'Tomorrow', hint: 'How much should we promise ERCOT?', keywords: 'plan commitment planner', run: () => nav('/tomorrow') },
      { id: 'go-member', group: 'Go to', label: 'Member', hint: 'One home, in plain words', keywords: 'homeowner customer', run: () => nav('/member') },
      { id: 'go-market', group: 'Go to', label: 'Market', hint: 'Live ERCOT prices and the year so far', keywords: 'price ercot feed', run: () => nav('/market') },
      { id: 'go-how', group: 'Go to', label: 'How it works', hint: 'What is real and what is simulated', run: () => nav('/how-it-works') },
      {
        id: 'sim-gateway',
        group: 'Simulate',
        label: 'Gateway loses uplink on BAT-042',
        hint: 'A home drops out mid-event',
        keywords: 'incident failure trigger',
        run: async () => {
          const r = await api.trigger()
          refreshAll(r.fleet)
          nav('/')
          return `${r.incident.incident_id}: $${r.incident.dollars_at_risk.toFixed(2)} at risk`
        },
      },
      {
        id: 'sim-stale',
        group: 'Simulate',
        label: 'Stale telemetry wave (3%)',
        hint: 'Several homes go quiet at once',
        keywords: 'wave telemetry',
        run: async () => {
          const r = await api.staleWave(0.03)
          refreshAll(r.fleet)
          nav('/')
          return `${r.dropped_kw.toFixed(1)} kW dropped`
        },
      },
      {
        id: 'sim-storm',
        group: 'Simulate',
        label: 'Draw a storm over the fleet',
        hint: 'See whether the promise survives it',
        keywords: 'what if weather hurricane',
        run: () => nav('/tomorrow#storm'),
      },
      {
        id: 'decide-approve',
        group: 'Decide',
        label: 'Approve the pending recovery',
        hint: 'Signed as the fleet operator',
        keywords: 'approve recovery incident',
        run: async () => {
          const r = await api.approve()
          refreshAll(r.fleet)
          return `${r.incident.incident_id} recovered $${r.incident.dollars_recovered.toFixed(2)}`
        },
      },
      ...[0.7, 0.75, 0.8].map((ratio) => ({
        id: `decide-commit-${ratio}`,
        group: 'Decide' as const,
        label: `Commit ${Math.round(ratio * 100)}% of headroom`,
        hint: 'Logged in the audit trail with your name',
        keywords: 'commitment promise set',
        run: async () => {
          const r = await api.setCommitment(ratio, 'command bar')
          refreshAll(r.fleet)
          return `Target now ${r.target_kw.toFixed(0)} kW`
        },
      })),
      { id: 'view-theme', group: 'View', label: 'Switch theme', hint: 'Night or day', keywords: 'dark light', run: () => toggleTheme() },
      ...MARKETS.map<Action>((m) => ({
        id: `market-${m.id}`,
        group: 'Go to',
        label: `Go to market ${m.short}`,
        hint: `${m.iso}: ${STATUS_LABEL[m.status]}`,
        keywords: `market ${m.name} ${m.iso} ${m.status}`,
        run: () => {
          setMarket(m.id)
          nav('/')
        },
      })),
      {
        id: 'market-all',
        group: 'Go to',
        label: 'Go to market overview',
        hint: 'The United States, every market by status',
        keywords: 'usa us all markets map',
        run: () => {
          setMarketView('us')
          nav('/')
        },
      },
    ],
    [nav, refreshAll, toggleTheme],
  )

  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean)
    return actions.filter((a) => {
      const hay = `${a.group} ${a.label} ${a.hint ?? ''} ${a.keywords ?? ''}`.toLowerCase()
      return words.every((w) => hay.includes(w))
    })
  }, [actions, q])

  useEffect(() => setI(0), [q])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const run = async (a: Action | undefined) => {
    if (!a || busy) return
    setBusy(a.id)
    setNote(null)
    try {
      const out = await a.run()
      if (typeof out === 'string') setNote(out)
      else setOpen(false)
      if (a.group === 'Go to' || a.group === 'View') setOpen(false)
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  let lastGroup = ''
  return (
    <Ctx.Provider value={{ open: () => setOpen(true) }}>
      {children}
      <DialogPrimitive.Root open={open} onOpenChange={(o) => { setOpen(o); if (!o) { setQ(''); setNote(null) } }}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
          <DialogPrimitive.Content
            data-testid="command-bar"
            className="fixed left-1/2 top-[14vh] z-50 w-[min(640px,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-xl border border-border-strong bg-surface shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          >
            <DialogPrimitive.Title className="sr-only">Command bar</DialogPrimitive.Title>
            <DialogPrimitive.Description className="sr-only">Type to find an action, use the arrow keys, press Enter to run it.</DialogPrimitive.Description>
            <div className="flex items-center gap-3 border-b border-border px-4">
              <Search aria-hidden className="size-4 text-fg-subtle" />
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown') { e.preventDefault(); setI((v) => Math.min(v + 1, shown.length - 1)) }
                  if (e.key === 'ArrowUp') { e.preventDefault(); setI((v) => Math.max(v - 1, 0)) }
                  if (e.key === 'Enter') { e.preventDefault(); void run(shown[i]) }
                }}
                placeholder="Approve, simulate, commit, or go to..."
                aria-label="Search actions"
                aria-controls="command-list"
                aria-activedescendant={shown[i] ? `cmd-${shown[i].id}` : undefined}
                className="h-14 flex-1 bg-transparent text-base outline-none placeholder:text-fg-subtle"
              />
              <kbd className="num rounded border border-border px-1.5 py-0.5 text-2xs text-fg-subtle">esc</kbd>
            </div>
            <ul id="command-list" ref={list} role="listbox" className="max-h-[50vh] overflow-y-auto p-2">
              {shown.length === 0 && <li className="px-3 py-6 text-center text-sm text-fg-subtle">No action matches "{q}".</li>}
              {shown.map((a, idx) => {
                const header = a.group !== lastGroup ? a.group : null
                lastGroup = a.group
                return (
                  <li key={a.id} role="presentation">
                    {header && <div className="eyebrow px-3 pb-1 pt-3">{header}</div>}
                    <div
                      id={`cmd-${a.id}`}
                      role="option"
                      aria-selected={idx === i}
                      onMouseEnter={() => setI(idx)}
                      onClick={() => void run(a)}
                      className={cn(
                        'flex cursor-pointer items-center justify-between gap-3 rounded-lg px-3 py-2.5',
                        idx === i ? 'bg-brand-soft text-fg' : 'text-fg-muted',
                      )}
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-fg">{a.label}</div>
                        {a.hint && <div className="truncate text-xs text-fg-subtle">{a.hint}</div>}
                      </div>
                      {busy === a.id ? (
                        <span className="num text-2xs text-fg-subtle">working</span>
                      ) : (
                        idx === i && <CornerDownLeft aria-hidden className="size-4 shrink-0 text-fg-subtle" />
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>
            <div className="flex items-center justify-between border-t border-border px-4 py-2 text-2xs text-fg-subtle">
              <span role="status" aria-live="polite" className="num text-fg-muted">{note ?? 'A human operator approves every recovery action.'}</span>
              <span className="num">↑↓ to move · ↵ to run</span>
            </div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </Ctx.Provider>
  )
}
