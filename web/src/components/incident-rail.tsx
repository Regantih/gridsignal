import type { Incident } from '@/lib/api'
import { clock } from '@/lib/format'
import { cn } from '@/lib/utils'

/**
 * Where an incident is on its way from detection to recovery, as a rail of states. Each
 * stamp is the engine's own: opened_at, approved_at, resolved_at. Nothing here is inferred
 * beyond "later states have not happened yet".
 */
export function IncidentRail({ incident, compact = false }: { incident: Incident; compact?: boolean }) {
  const escalated = incident.status === 'escalated'
  const steps = [
    { label: 'Detected', at: incident.opened_at, done: true },
    { label: 'Priced', at: incident.opened_at, done: true },
    { label: escalated ? 'Escalated' : 'Awaiting approval', at: incident.opened_at, done: true, active: incident.status === 'awaiting_approval' || escalated },
    { label: incident.executed_under ? 'Playbook' : 'Approved', at: incident.approved_at, done: !!incident.approved_at },
    { label: 'Recovered', at: incident.resolved_at, done: !!incident.resolved_at, active: !!incident.resolved_at },
  ]
  const lastDone = steps.reduce((a, s, i) => (s.done ? i : a), 0)
  return (
    <ol className={cn('flex w-full items-start', compact ? 'gap-0' : 'gap-0')} aria-label="Incident progress" data-testid="incident-rail">
      {steps.map((s, i) => {
        const tone = s.done ? (i === lastDone && incident.status !== 'resolved' ? 'risk' : 'ok') : 'idle'
        return (
          <li key={s.label} className="relative flex min-w-0 flex-1 flex-col items-start">
            {i > 0 && (
              <span aria-hidden className={cn('absolute left-0 right-1/2 top-[5px] h-px', s.done ? (incident.status === 'resolved' ? 'bg-ok' : 'bg-risk/70') : 'bg-border-strong')} style={{ left: 'calc(-50% + 6px)', right: 'calc(50% + 6px)' }} />
            )}
            <span
              aria-hidden
              className={cn(
                'relative z-10 mx-auto block size-[11px] rounded-full border-2',
                tone === 'ok' && 'border-ok bg-ok',
                tone === 'risk' && 'border-risk bg-risk shadow-[0_0_0_4px_var(--risk-soft)]',
                tone === 'idle' && 'border-border-strong bg-surface',
                s.active && tone === 'risk' && 'animate-pulse',
              )}
            />
            <span className={cn('mt-1.5 w-full truncate text-center text-2xs font-medium', s.done ? 'text-fg' : 'text-fg-subtle', compact && 'hidden sm:block')}>
              {s.label}
              <span className="sr-only">{s.done ? ', done' : ', not yet'}</span>
            </span>
            {!compact && <span className="num w-full text-center text-2xs text-fg-subtle">{s.at ? clock(s.at) : '·'}</span>}
          </li>
        )
      })}
    </ol>
  )
}
