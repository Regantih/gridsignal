import { useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion, useScroll, useSpring } from 'motion/react'
import { api, type About, type RiskGroup } from '@/lib/api'
import { keys, useFleet } from '@/lib/queries'
import { count, money, pct, power } from '@/lib/format'
import { useCalm } from '@/lib/visual-mode'
import { cn } from '@/lib/utils'
import { PromiseDial } from '@/components/promise-dial'
import { FleetScene } from '@/components/fleet-scene'
import { TexasMap, project } from '@/components/texas-map'
import { HoldButton } from '@/components/hold-button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import type { Storm } from '@/components/grid-field'

/**
 * How it works, told as a scroll story: five pinned steps, each built from the same
 * components and the same engine numbers the operator screens use. Under Calm mode or
 * reduced motion the pinning and the reveals go away and it reads as a plain page.
 */

const STORM: Storm = { lat: 29.76, lon: -95.37, radius_km: 90 }
const RAIL = ['Detected', 'Priced', 'Awaiting approval', 'Approved', 'Recovered'] as const

export function HowStory({ about }: { about: About | undefined }) {
  const [calm] = useCalm()
  const fleet = useFleet()
  const risk = useQuery({ queryKey: keys.risk, queryFn: api.risk, staleTime: 60_000 })
  const storm = useQuery({ queryKey: ['story-storm', STORM], queryFn: () => api.storm(STORM), staleTime: Infinity })
  const ref = useRef<HTMLDivElement>(null)
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end end'] })
  const progress = useSpring(scrollYProgress, { stiffness: 120, damping: 30, mass: 0.4 })

  const f = fleet.data
  const s = f?.summary
  const worst = useMemo(() => {
    const gs = risk.data?.groups ?? []
    return [...gs].sort((a, b) => b.lost_kw - a.lost_kw)[0]
  }, [risk.data])
  const feeders = risk.data?.groups.filter((g) => g.kind === 'feeder').length ?? 0
  const rings = risk.data?.groups.filter((g) => g.kind === 'ring').length ?? 0
  const approval = about?.approval_sentence ?? 'a human operator approves every recovery action, one incident at a time or in advance through a playbook with limits'

  const steps: { id: string; kicker: string; title: string; body: ReactNode; visual: ReactNode }[] = [
    {
      id: 'promise',
      kicker: '1 · The promise',
      title: 'Tonight the fleet promises the grid a number of kilowatts.',
      body: s ? (
        <>
          Right now that is <b className="num text-fg">{power(s.committed_kw)}</b> against a target of <b className="num text-fg">{power(s.target_kw)}</b>, with{' '}
          <b className="num text-fg">{power(s.headroom_kw)}</b> of measured headroom held back. Keeping it earns the fleet money at the real ERCOT price; missing it costs more than it earns.
        </>
      ) : (
        <Skeleton className="h-16 w-full" />
      ),
      visual: s ? (
        <div className="grid place-items-center p-6">
          <PromiseDial committed={s.committed_kw} target={s.target_kw} headroom={s.headroom_kw} size={240} />
        </div>
      ) : (
        <Skeleton className="h-72 w-full" />
      ),
    },
    {
      id: 'storm',
      kicker: '2 · What breaks it',
      title: 'A storm takes a piece of the fleet with it.',
      body: storm.data ? (
        <>
          A Gulf storm over Houston, {count(STORM.radius_km)} km wide, drops <b className="num text-fg">{count(storm.data.homes)} homes</b> and{' '}
          <b className="num text-fg">{power(storm.data.lost_kw)}</b> of export. The rest of each zone has <b className="num text-fg">{power(storm.data.spare_kw)}</b> spare, so{' '}
          <b className={cn('num', storm.data.uncovered_kw > 0 ? 'text-risk' : 'text-ok')}>{power(storm.data.uncovered_kw)}</b> of the promise goes uncovered and{' '}
          <b className="num text-fg">{pct(storm.data.kept_share)}</b> of it is kept. These are the engine's numbers for tonight's fleet; nothing is dispatched.
        </>
      ) : (
        <Skeleton className="h-16 w-full" />
      ),
      visual: f ? (
        <FleetScene
          devices={f.devices}
          storm={STORM}
          hit={new Set(storm.data?.devices ?? [])}
          priceMwh={f.summary.remaining_price_mwh}
          height={360}
          label={`Map of Texas with a storm over Houston covering ${storm.data?.homes ?? 0} homes`}
        />
      ) : (
        <Skeleton className="h-[360px] w-full" />
      ),
    },
    {
      id: 'correlated',
      kicker: '3 · Correlated failures',
      title: 'Homes do not fail one at a time. They fail by feeder and by gateway ring.',
      body: risk.data ? (
        <>
          The risk map groups tonight's fleet into <b className="num text-fg">{count(feeders)} feeders</b> and <b className="num text-fg">{count(rings)} gateway rings</b>.
          {worst && (
            <>
              {' '}The largest, {worst.kind} {worst.key}, would take <b className="num text-fg">{power(worst.lost_kw, 1)}</b> with it, {pct(worst.share_of_target)} of the target.{' '}
              Its status tonight: <Badge tone={worst.status.startsWith('Promise') ? 'risk' : worst.status.startsWith('Waits') ? 'warn' : 'ok'}>{worst.status}</Badge>
            </>
          )}
          {' '}{risk.data.feeder_note}
        </>
      ) : (
        <Skeleton className="h-16 w-full" />
      ),
      visual: risk.data ? <StoryRiskMap groups={risk.data.groups} targetKw={risk.data.target_kw} /> : <Skeleton className="h-72 w-full" />,
    },
    {
      id: 'approval',
      kicker: '4 · The human approval',
      title: 'Nothing moves until a person holds the button.',
      body: (
        <>
          When a home drops, the engine opens an incident, prices it, proposes a fix and stops. In GridSignal <span data-testid="story-approval">{approval}</span>.
          {f?.pending_incident ? (
            <>
              {' '}Tonight there is one waiting: <b className="num text-risk">{money(f.pending_incident.dollars_at_risk)}</b> at risk.
            </>
          ) : (
            ' Nothing is waiting for approval right now.'
          )}
        </>
      ),
      visual: <ApprovalDemo />,
    },
    {
      id: 'limits',
      kicker: '5 · The honest limits',
      title: 'What this is not.',
      body: (
        <>
          <span data-testid="story-disclosure">{about?.disclosure ?? 'Prices are real ERCOT data. The fleet is simulated.'}</span> GridSignal does not claim to earn more than a naive schedule; every policy delivers the same schedule when nothing fails. Failure rates are SYNTHETIC unless you upload telemetry. The operator owns the commitment, the approvals and the consequences.
        </>
      ),
      visual: (
        <ul className="grid gap-2 p-5 text-sm">
          {[
            'No real battery has been dispatched by this app.',
            'Simulated feeders and rings, not real topology.',
            'Reliability from simulated years, not measured ones.',
            'Wide price bands on purpose. Regime changes are not predicted.',
            'Member earnings are an attribution, not a payout.',
          ].map((t) => (
            <li key={t} className="rounded-lg border border-border bg-surface/70 px-3 py-2">{t}</li>
          ))}
        </ul>
      ),
    },
  ]

  return (
    <div ref={ref} className="relative" data-testid="how-story" data-motion={calm ? 'off' : 'on'}>
      {!calm && (
        <motion.div
          aria-hidden
          className="pointer-events-none fixed left-0 top-0 z-30 h-0.5 origin-left bg-flow"
          style={{ scaleX: progress, width: '100%' }}
        />
      )}
      {steps.map((st, i) => (
        <section
          key={st.id}
          id={`story-${st.id}`}
          aria-labelledby={`story-${st.id}-title`}
          className={cn('grid gap-6 lg:grid-cols-2 lg:gap-10', calm ? 'py-8' : 'min-h-[100dvh] py-16')}
          data-testid={`story-step-${st.id}`}
        >
          <div className={cn('order-2 lg:order-1', !calm && 'lg:sticky lg:top-24 lg:self-start')}>
            <Reveal calm={calm}>
              <div className="overflow-hidden rounded-3xl border border-border bg-surface/40">{st.visual}</div>
            </Reveal>
          </div>
          <div className={cn('order-1 flex flex-col justify-center lg:order-2', !calm && 'lg:min-h-[70dvh]')}>
            <Reveal calm={calm} delay={0.08}>
              <div className="eyebrow">{st.kicker}</div>
              <h2 id={`story-${st.id}-title`} className="mt-2 text-balance font-display text-2xl font-semibold leading-tight md:text-[2.2rem]">
                {st.title}
              </h2>
              <p className="mt-4 max-w-prose text-base leading-relaxed text-fg-muted">{st.body}</p>
            </Reveal>
          </div>
          {i < steps.length - 1 && !calm && <div aria-hidden className="col-span-full h-px bg-border/40" />}
        </section>
      ))}
    </div>
  )
}

function Reveal({ children, calm, delay = 0 }: { children: ReactNode; calm: boolean; delay?: number }) {
  if (calm) return <div>{children}</div>
  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ type: 'spring', stiffness: 160, damping: 24, delay }}
    >
      {children}
    </motion.div>
  )
}

function StoryRiskMap({ groups, targetKw }: { groups: RiskGroup[]; targetKw: number }) {
  const fill = (s: string) => (s.startsWith('Promise') ? 'var(--risk)' : s.startsWith('Waits') ? 'var(--warn)' : 'var(--ok)')
  const maxKw = Math.max(1, ...groups.map((g) => g.lost_kw))
  return (
    <TexasMap label={`Map of Texas showing ${groups.length} groups of homes that fail together, sized by kW at stake`}>
      {[...groups].sort((a, b) => b.lost_kw - a.lost_kw).map((g) => {
        const [x, y] = project(g.lon, g.lat)
        const r = 4 + 14 * Math.sqrt(g.lost_kw / maxKw)
        return (
          <circle key={`${g.kind}:${g.key}`} cx={x} cy={y} r={r} fill={fill(g.status)} fillOpacity={g.kind === 'ring' ? 0.35 : 0.55} stroke={fill(g.status)} strokeDasharray={g.kind === 'ring' ? '4 3' : undefined}>
            <title>{`${g.kind} ${g.key}: ${g.status}, ${power(g.lost_kw, 1)} of ${power(targetKw)}`}</title>
          </circle>
        )
      })}
    </TexasMap>
  )
}

/** The real hold-to-confirm control, wired to nothing: it shows the gesture, not a dispatch. */
function ApprovalDemo() {
  const [stage, setStage] = useState(2)
  return (
    <div className="flex flex-col gap-5 p-6">
      <ol className="flex flex-wrap gap-2 text-2xs" aria-label="Incident states">
        {RAIL.map((label, i) => (
          <li key={label} className={cn('rounded-full border px-2.5 py-1 font-mono uppercase tracking-wider', i <= stage ? 'border-flow/60 bg-flow/10 text-fg' : 'border-border text-fg-subtle')}>
            {label}
          </li>
        ))}
      </ol>
      <HoldButton
        tone={stage >= 3 ? 'ok' : 'brand'}
        disabled={stage >= 3}
        onConfirm={() => setStage(4)}
        testId="story-hold"
        hint={stage >= 3 ? 'Rehearsal only, nothing was dispatched' : 'Hold for about a second'}
      >
        {stage >= 3 ? 'Approved, in rehearsal' : 'Approve recovery'}
      </HoldButton>
      <p className="text-xs text-fg-muted">
        On Tonight this same button records a named approval in the audit trail and the engine recovers what it can, never more than was at risk. Here it changes nothing.
      </p>
    </div>
  )
}

