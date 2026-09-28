import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ShieldCheck, ShieldOff, Zap, RotateCcw, WifiOff, Clapperboard, Square } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api, type Fleet, type Incident } from '@/lib/api'
import { keys, useFleet, useFleetMutation, useResetSession } from '@/lib/queries'
import { clock, count, dateLabel, hours, money, pctPoints, power, priceMwh } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Metric } from '@/components/metric'
import { Section, Callout } from '@/components/page'
import { CardSkeleton, ChartSkeleton, EmptyState, ErrorState } from '@/components/states'
import { IncidentBadge } from '@/components/status'
import { FleetScene } from '@/components/fleet-scene'
import { HoldButton } from '@/components/hold-button'
import { IncidentRail } from '@/components/incident-rail'
import { NightTimeline } from '@/components/night-timeline'
import { PromiseDial } from '@/components/promise-dial'
import { Skeleton } from '@/components/ui/skeleton'
import { useCountUp } from '@/components/count-up'
import { minuteOf, TimelineProvider, useTimeline } from '@/lib/timeline'
import { useReplay } from '@/lib/replay'
import type { Moment } from '@/components/scene/scene-types'
import { MarketPlaceholder } from '@/components/market-placeholder'
import { STATUS_LABEL, useMarket } from '@/lib/markets'
import { useWide } from '@/components/shell'

export function TonightPage() {
  const fleet = useFleet()
  const { market } = useMarket()
  if (market.status !== 'live') return <OtherMarket />
  if (fleet.isPending) return <TonightSkeleton />
  if (fleet.isError) return <ErrorState error={fleet.error} retry={() => void fleet.refetch()} />
  return (
    <TimelineProvider now={fleet.data.now} start={fleet.data.grid_event.started_at} end={fleet.data.grid_event.ends_at}>
      <Tonight fleet={fleet.data} />
    </TimelineProvider>
  )
}

/**
 * Tonight for a market the engine does not model. The map still shows the country and the
 * ERCOT homes where they are, but no verdict, dial, price or dollars: none exist here.
 */
function OtherMarket() {
  const { market, select, setView } = useMarket()
  const fleet = useFleet()
  return (
    <div className="flex flex-col gap-8" data-testid="other-market">
      <header>
        <div className="eyebrow">
          Tonight · {market.name} · {market.iso} · {STATUS_LABEL[market.status]}
        </div>
        <h1 className="mt-2 text-[2.2rem] font-semibold leading-[1.02] md:text-[3.6rem]">
          GridSignal does not <span className="text-brand">run here yet.</span>
        </h1>
      </header>
      <section className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,26rem)]">
        <div className="relative overflow-hidden rounded-3xl border border-border bg-surface/40" style={{ viewTransitionName: 'fleet-map' }}>
          <div className="z-10 flex justify-end px-4 pt-4 sm:absolute sm:right-0 sm:top-0 sm:px-5">
            <MarketViewToggle />
          </div>
          <p className="sr-only" data-testid="scene-summary">
            Map of the United States with {market.name} framed. {STATUS_LABEL[market.status]}. Only Texas, ERCOT, is modelled.
          </p>
          <div className="pt-2 sm:pt-14">
            <FleetScene
              devices={fleet.data?.devices ?? []}
              priceMwh={50}
              market={market.id}
              view="market"
              onMarket={select}
              label={`Map of the United States framing ${market.name}, ${STATUS_LABEL[market.status].toLowerCase()}; only Texas is modelled`}
              height={520}
            />
          </div>
        </div>
        <MarketPlaceholder market={market} onBack={(id) => { select(id); setView('market') }} compact />
      </section>
    </div>
  )
}

/** United States or the chosen market: two buttons over the map, keyboard first. */
function MarketViewToggle() {
  const { market, view, setView } = useMarket()
  const opt = (v: 'us' | 'market', label: string) => (
    <button
      type="button"
      onClick={() => setView(v)}
      aria-pressed={view === v}
      data-testid={`market-view-${v}`}
      className={`rounded-full px-3 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${view === v ? 'bg-fg text-bg' : 'text-fg-muted hover:text-fg'}`}
    >
      {label}
    </button>
  )
  return (
    <div className="pointer-events-auto flex shrink-0 items-center gap-1 self-start rounded-full border border-border bg-surface/80 p-1 backdrop-blur" role="group" aria-label="Map view">
      {opt('us', 'United States')}
      {opt('market', market.short)}
    </div>
  )
}

/** Fire a staged moment when an incident opens or resolves, so the scene can act it out. */
function useMoments(fleet: Fleet): Moment | null {
  const [moment, setMoment] = useState<Moment | null>(null)
  const seen = useRef<Map<string, string> | null>(null)
  useEffect(() => {
    const prev = seen.current
    const next = new Map(fleet.incidents.map((i) => [i.incident_id, i.status]))
    seen.current = next
    if (!prev) return
    let timer = 0
    for (const i of fleet.incidents) {
      const was = prev.get(i.incident_id)
      if (was === undefined) {
        setMoment({ kind: 'incident', device: i.device_id, at: performance.now() })
        if (i.status === 'resolved') {
          // Handled by a playbook in one step: show the drop, then the recovery.
          timer = window.setTimeout(() => setMoment({ kind: 'recovery', device: i.device_id, at: performance.now() }), 1600)
        }
      } else if (was !== 'resolved' && i.status === 'resolved') {
        setMoment({ kind: 'recovery', device: i.device_id, at: performance.now() })
      }
    }
    return () => window.clearTimeout(timer)
  }, [fleet])
  return moment
}

/** The dial and the map at the playhead, read off the engine's own incident stamps. */
function useReplayView(fleet: Fleet) {
  const tl = useTimeline()
  return useMemo(() => {
    const s = fleet.summary
    if (tl.live) return { committed: s.committed_kw, down: null as Set<string> | null, live: true }
    let committed = s.committed_kw
    const down = new Set<string>()
    for (const i of fleet.incidents) {
      const opened = minuteOf(i.opened_at)
      const resolved = i.resolved_at ? minuteOf(i.resolved_at) : Infinity
      const openAt = tl.m >= opened && tl.m < resolved
      if (openAt) i.cohort.forEach((d) => down.add(d))
      if (i.resolved_at) {
        if (openAt) committed -= i.restored_kw
      } else if (tl.m < opened) {
        committed += i.lost_kw
      }
    }
    return { committed, down, live: false }
  }, [fleet, tl.live, tl.m])
}

function verdict(f: Fleet) {
  const s = f.summary
  if (f.pending_incident) {
    return {
      tone: 'risk' as const,
      title: `Promise at risk: ${money(f.pending_incident.dollars_at_risk)} waits for your approval`,
      body: `${power(f.pending_incident.lost_kw, 1)} dropped out of the ${power(s.target_kw)} commitment. Nothing moves until you approve the recovery.`,
      icon: AlertTriangle,
    }
  }
  if (s.coverage_pct >= 100) {
    return {
      tone: 'ok' as const,
      title: 'The fleet will keep its promise tonight',
      body: `${power(s.committed_kw)} committed against a ${power(s.target_kw)} target with ${power(s.headroom_kw)} of headroom. ${hours(s.remaining_hours)} left in the window at ${priceMwh(s.remaining_price_mwh)}.`,
      icon: ShieldCheck,
    }
  }
  return {
    tone: 'warn' as const,
    title: `Covering ${pctPoints(s.coverage_pct, 1)} of tonight's promise`,
    body: `${power(s.committed_kw)} committed against ${power(s.target_kw)}. Review open incidents below.`,
    icon: ShieldOff,
  }
}

function Tonight({ fleet }: { fleet: Fleet }) {
  const s = fleet.summary
  const v = verdict(fleet)
  const trigger = useFleetMutation((id: string | undefined) => api.trigger(id))
  const approve = useFleetMutation(() => api.approve())
  const stale = useFleetMutation((share: number) => api.staleWave(share))
  const risk = useQuery({ queryKey: keys.risk, queryFn: api.risk })
  const resolved = fleet.incidents.filter((i) => i.status === 'resolved')
  const atRisk = fleet.incidents.reduce((a, i) => a + i.dollars_at_risk, 0)
  const recovered = fleet.incidents.reduce((a, i) => a + i.dollars_recovered, 0)
  const busy = trigger.isPending || approve.isPending || stale.isPending
  const error = trigger.error ?? approve.error ?? stale.error
  const toneText = { ok: 'text-ok', warn: 'text-warn', risk: 'text-risk' }[v.tone]
  const moment = useMoments(fleet)
  const view = useReplayView(fleet)
  const tl = useTimeline()
  const replay = useReplayShared()
  const mk = useMarket()
  const phone = !useWide('(min-width: 640px)')
  const focus = useMemo(() => (view.live ? new Set(fleet.pending_incident?.cohort ?? []) : new Set<string>()), [fleet.pending_incident, view.live])
  const recoveredShown = useCountUp(recovered, 1400)
  const priceAt = useMemo(() => {
    if (view.live) return s.remaining_price_mwh
    const row = fleet.prices.find((p) => tl.m >= minuteOf(p.interval_start) && tl.m < minuteOf(p.interval_start) + 15)
    return row ? row.spp : s.remaining_price_mwh
  }, [view.live, tl.m, fleet.prices, s.remaining_price_mwh])
  const groups = useMemo(() => {
    const out: Record<string, { feeder: string; ring: string }> = {}
    for (const g of risk.data?.groups ?? []) {
      for (const id of g.devices) {
        const o = (out[id] ??= { feeder: '', ring: '' })
        if (g.kind === 'feeder') o.feeder = `feeder ${g.key}`
        else o.ring = `ring ${g.key}`
      }
    }
    return out
  }, [risk.data])

  return (
    <div className="flex flex-col gap-10">
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0">
          <div className="eyebrow">
            Tonight · {dateLabel(fleet.grid_event.started_at)} · {clock(fleet.grid_event.started_at)} to {clock(fleet.grid_event.ends_at)} · {fleet.grid_event.zone}
          </div>
          <h1 className="mt-2 text-[2.2rem] font-semibold leading-[1.02] md:text-[3.6rem]">
            Will the fleet keep its <span className="text-brand">grid promise?</span>
          </h1>
          <p className="mt-3 max-w-2xl text-sm text-fg-muted md:text-base">
            {fleet.grid_event.name}. {fleet.disclosure}
          </p>
        </div>
        <div className="flex flex-col items-start gap-3 md:items-end">
          <ScenarioControls />
          <ReplayControls r={replay} />
        </div>
      </header>
      <ReplayCaption r={replay} />

      <section className="grid gap-6 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="flex flex-col gap-5">
          <div role="status" data-testid="verdict" className="rounded-3xl border border-border bg-surface/70 p-6">
            <div className="flex items-center gap-2">
              <v.icon aria-hidden className={`size-5 ${toneText}`} />
              <span className={`eyebrow ${toneText}`}>{v.tone === 'ok' ? 'Promise holds' : v.tone === 'risk' ? 'Promise at risk' : 'Short of target'}</span>
            </div>
            <h2 className="mt-3 text-2xl font-semibold leading-tight md:text-[1.9rem]">{v.title}</h2>
            <p className="mt-2 text-sm text-fg-muted">{v.body}</p>
            <div className="mt-5 flex flex-col items-center gap-4 sm:flex-row sm:items-center">
              <div className="w-[230px] shrink-0" style={{ viewTransitionName: 'promise-dial' }}>
                <PromiseDial committed={view.committed} target={s.target_kw} headroom={s.headroom_kw} size={230} />
                {!view.live && <div className="num -mt-1 text-center text-2xs text-fg-subtle">at the playhead, from the audit trail</div>}
              </div>
              <dl className="grid w-full grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-1">
                <div>
                  <dt className="eyebrow">Headroom</dt>
                  <dd className="num text-2xl">{power(s.headroom_kw)}</dd>
                </div>
                <div>
                  <dt className="eyebrow">Homes online</dt>
                  <dd className="num text-2xl">{count(s.online)}<span className="text-sm text-fg-subtle">/{count(s.total_devices)}</span></dd>
                </div>
                <div>
                  <dt className="eyebrow">Price now</dt>
                  <dd className="num text-2xl text-price">${Math.round(s.remaining_price_mwh)}<span className="text-sm text-fg-subtle">/MWh</span></dd>
                </div>
                <div>
                  <dt className="eyebrow">Window left</dt>
                  <dd className="num text-2xl">{hours(s.remaining_hours)}</dd>
                </div>
              </dl>
            </div>
            {fleet.pending_incident ? (
              <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-border pt-5">
                <HoldButton onConfirm={() => approve.mutate(undefined)} disabled={busy} tone="risk" testId="approve-recovery">
                  Approve recovery
                </HoldButton>
                <span className="text-xs text-fg-subtle">Signed as {fleet.operator}. Nothing moves until you do.</span>
              </div>
            ) : (
              <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-5">
                <span className="eyebrow w-full">Throw something at the fleet</span>
                <Button variant="secondary" onClick={() => trigger.mutate(undefined)} disabled={busy} data-testid="trigger-incident">
                  <WifiOff /> Gateway loses uplink on {fleet.focus_device_id}
                </Button>
                <Button variant="outline" onClick={() => stale.mutate(0.03)} disabled={busy} data-testid="trigger-stale">
                  Stale telemetry wave (3%)
                </Button>
              </div>
            )}
          </div>
          {error && <ErrorState error={error} />}
        </div>

        <div className="relative overflow-hidden rounded-3xl border border-border bg-surface/40" style={{ viewTransitionName: 'fleet-map' }}>
          <div className="z-10 flex items-start justify-between gap-3 px-4 pt-4 sm:pointer-events-none sm:absolute sm:inset-x-0 sm:top-0 sm:px-5">
            <div>
              <div className="eyebrow">{view.live ? 'The fleet, live' : 'The fleet, replayed'}</div>
              <p className="mt-1 max-w-xs text-xs text-fg-muted">
                {mk.view === 'us'
                  ? 'Every market Base Power is in. Only Texas, ERCOT, is modelled; click a market to fly to it.'
                  : "Light travels from each home to its zone as it exports; each zone's column is the kW it sends. Hover a home to see the neighbours that would fail with it. Drag to look around."}
              </p>
            </div>
            <MarketViewToggle />
          </div>
          <p className="sr-only" data-testid="scene-summary">
            {count(s.online)} of {count(s.total_devices)} homes online, {power(s.committed_kw)} committed against {power(s.target_kw)}, price {priceMwh(priceAt)}.
            {fleet.pending_incident ? ` ${fleet.pending_incident.cohort.length} home${fleet.pending_incident.cohort.length === 1 ? '' : 's'} dropped and waiting for approval.` : ' No incident waiting.'}
          </p>
          <div className={cn('absolute bottom-4 left-5 z-10 flex flex-wrap gap-3 text-2xs text-fg-muted', mk.view === 'us' && 'hidden')}>
            <Legend color="var(--fg)" label="exporting" />
            <Legend color="var(--warn)" label="degraded" />
            <Legend color="var(--risk)" label="dropped" />
            <Legend color="var(--price)" label="fails together" />
            <Legend color="var(--flow)" label="power to zone" />
          </div>
          <div className="pt-2 sm:pt-14">
            <FleetScene
              devices={fleet.devices}
              groups={groups}
              focus={focus}
              hit={view.down ?? undefined}
              priceMwh={priceAt}
              moment={moment}
              tempo={tl.playing ? 3 : 1}
              market={mk.market.id}
              view={mk.view}
              onMarket={(id) => { mk.select(id); mk.setView('market') }}
              label={`Map of ${fleet.devices.length} simulated homes across ERCOT load zones, coloured by status, with power flowing to each zone`}
              height={phone && mk.view === 'us' ? 360 : 520}
            />
          </div>
        </div>
      </section>

      <NightTimeline
        prices={fleet.prices}
        audit={fleet.audit}
        start={fleet.grid_event.started_at}
        end={fleet.grid_event.ends_at}
        now={fleet.now}
        caption={
          <>
            {fleet.price_trace.location} real-time settlement prices, {fleet.price_trace.date}.{' '}
            <a className="underline underline-offset-2" href={fleet.price_trace.source} target="_blank" rel="noreferrer">
              Source: ERCOT
            </a>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric label="Coverage" value={pctPoints(s.coverage_pct, s.coverage_pct === 100 ? 0 : 1)} tone={s.coverage_pct >= 100 ? 'ok' : 'warn'} note={`${power(s.committed_kw)} of ${power(s.target_kw)}`} testId="coverage" />
        <Metric label="Dollars at risk" value={money(atRisk)} tone={atRisk > 0 && recovered < atRisk ? 'risk' : 'default'} note={`${count(fleet.incidents.length)} incident${fleet.incidents.length === 1 ? '' : 's'} this window`} testId="dollars-at-risk" />
        <Metric
          label="Dollars recovered"
          value={money(recovered)}
          tone={recovered > 0 ? 'ok' : 'default'}
          note={
            <span className="flex items-center gap-2">
              {count(resolved.length)} resolved with approval
              {recovered > 0 && Math.abs(recoveredShown - recovered) > 0.005 && (
                <span aria-hidden className="num rounded-full bg-ok-soft px-1.5 py-px text-2xs text-ok">+{money(recoveredShown)}</span>
              )}
            </span>
          }
          testId="dollars-recovered"
        />
        <Metric label="Quarantined" value={count(s.unavailable)} note={`${count(s.degraded)} degraded, ${count(s.offline)} offline`} />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-6">
          <Section title="What needs you now" description={`In GridSignal ${fleet.approval_sentence}.`}>
            {fleet.pending_incident ? (
              <IncidentCard incident={fleet.pending_incident} onApprove={() => approve.mutate(undefined)} busy={busy} pending />
            ) : (
              <Card>
                <CardContent className="flex items-start gap-3 p-5">
                  <CheckCircle2 aria-hidden className="mt-0.5 size-5 shrink-0 text-ok" />
                  <div>
                    <div className="font-medium">Nothing is waiting for you</div>
                    <p className="text-sm text-fg-muted">{fleet.human_summary}</p>
                  </div>
                </CardContent>
              </Card>
            )}
          </Section>

          <PlaybookCard fleet={fleet} />

          <Section title="Incidents this window" description="Every incident shows what it costs if nobody acts and what was recovered once someone did.">
            {fleet.incidents.length === 0 ? (
              <EmptyState title="No incidents yet" body="When a home drops out during the event it will appear here with its dollars at risk." />
            ) : (
              <div className="flex flex-col gap-3" data-testid="incident-list">
                {[...fleet.incidents].reverse().map((i) => (
                  <IncidentCard key={i.incident_id} incident={i} busy={busy} onApprove={() => approve.mutate(undefined)} />
                ))}
              </div>
            )}
          </Section>
        </div>

        <div className="flex flex-col gap-6">
          <Section title="Audit trail" description="Who did what, and when. Approvals are named.">
            <AuditList fleet={fleet} />
          </Section>
        </div>
      </div>
    </div>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="size-2 rounded-full" style={{ background: color }} />
      {label}
    </span>
  )
}

function IncidentCard({ incident, onApprove, busy, pending = false }: { incident: Incident; onApprove: () => void; busy: boolean; pending?: boolean }) {
  const [open, setOpen] = useState(pending)
  const waiting = incident.status === 'awaiting_approval'
  return (
    <Card className={pending ? 'border-risk/50' : undefined} data-testid={pending ? 'pending-incident' : undefined}>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2">
            {incident.title}
            <IncidentBadge status={incident.status} />
          </CardTitle>
          <CardDescription>
            {incident.incident_id}, opened {clock(incident.opened_at)}, severity {incident.severity}
            {incident.executed_under ? `, executed under ${incident.executed_under}` : ''}
          </CardDescription>
        </div>
        <div className="grid grid-cols-2 gap-3 text-right">
          <div>
            <div className="text-2xs uppercase tracking-wide text-fg-subtle">At risk</div>
            <div className="text-lg font-semibold tabular text-risk" data-testid="incident-at-risk">{money(incident.dollars_at_risk)}</div>
          </div>
          <div>
            <div className="text-2xs uppercase tracking-wide text-fg-subtle">Recovered</div>
            <div className={`text-lg font-semibold tabular ${incident.dollars_recovered > 0 ? 'text-ok' : 'text-fg-subtle'}`} data-testid="incident-recovered">
              {money(incident.dollars_recovered)}
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <IncidentRail incident={incident} compact={!pending && !open} />
        <p className="text-sm">{incident.impact}</p>
        {waiting && (
          <Callout tone="risk">
            <strong>If nobody acts:</strong> {money(incident.dollars_at_risk)} of under-delivery across the remaining {hours(incident.window_hours)} at {priceMwh(incident.price_mwh)}.
            Recovery is capped at what is at risk; it can never claim more. Nothing has been dispatched: the fix waits for your approval.
          </Callout>
        )}
        {incident.status === 'resolved' && (
          <Callout tone="ok">
            {power(incident.restored_kw, 1)} reassigned to healthy homes. Recovered {money(incident.dollars_recovered)} of {money(incident.dollars_at_risk)}.
            {incident.approved_by ? ` Approved by ${incident.approved_by}${incident.approved_at ? ` at ${clock(incident.approved_at)}` : ''}.` : ''}
          </Callout>
        )}
        {incident.escalation_reason && <Callout tone="warn">{incident.escalation_reason}</Callout>}
        <div className="flex flex-wrap items-center gap-2">
          {waiting && (
            <HoldButton onConfirm={onApprove} disabled={busy} tone="risk" testId={pending ? undefined : 'approve-in-list'}>
              Approve recovery
            </HoldButton>
          )}
          <Button variant="ghost" size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? 'Hide details' : 'Why, and what happens next'}
          </Button>
        </div>
        {open && (
          <div className="grid gap-3 border-t border-border pt-3 text-sm md:grid-cols-2">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-fg-subtle">Likely cause</div>
              <p className="mt-1 text-fg-muted">{incident.root_cause_hypothesis}</p>
            </div>
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-fg-subtle">Recommended action</div>
              <p className="mt-1 text-fg-muted">{incident.recommended_action}</p>
            </div>
            <div className="md:col-span-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-fg-subtle">Tasks</div>
              <ul className="mt-1 flex flex-col gap-1">
                {incident.tasks.map((t) => (
                  <li key={t.task_id} className="flex flex-wrap items-baseline gap-2">
                    <Badge tone={t.status === 'done' ? 'ok' : 'neutral'} dot={false}>{String(t.status)}</Badge>
                    <span>{t.title}</span>
                    <span className="text-fg-subtle">{t.owner}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function PlaybookCard({ fleet }: { fleet: Fleet }) {
  const approvePb = useFleetMutation((body: { max_kw: number; max_devices: number }) => api.approvePlaybook(body))
  const revoke = useFleetMutation(() => api.revokePlaybook())
  const [maxKw, setMaxKw] = useState(String(fleet.playbook_defaults.max_kw))
  const [maxDevices, setMaxDevices] = useState(String(fleet.playbook_defaults.max_devices))
  const pb = fleet.playbook
  const active = pb && !pb.revoked_at
  return (
    <Section
      title="Pre-approved playbook"
      description="Approve small recoveries in advance, inside written limits. Anything larger still waits for a person."
    >
      <Card data-testid="playbook-card">
        <CardContent className="flex flex-col gap-4 p-5">
          {active ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="ok">Active</Badge>
                <span className="text-sm">
                  {pb.playbook_id}: up to <strong className="tabular">{power(pb.max_kw, 1)}</strong> and{' '}
                  <strong className="tabular">{count(pb.max_devices)}</strong> home{pb.max_devices === 1 ? '' : 's'} per incident.
                </span>
              </div>
              <p className="text-sm text-fg-muted">
                Approved by {pb.approved_by} at {clock(pb.approved_at)}, expires {clock(pb.expires_at)}. Used {count(pb.executions)} time{pb.executions === 1 ? '' : 's'}.
                Losses above these limits still wait for you.
              </p>
              <div>
                <Button variant="outline" onClick={() => revoke.mutate(undefined)} disabled={revoke.isPending} data-testid="revoke-playbook">
                  Revoke playbook
                </Button>
              </div>
            </>
          ) : (
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault()
                approvePb.mutate({ max_kw: Number(maxKw), max_devices: Number(maxDevices) })
              }}
            >
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1">
                  <Label htmlFor="pb-kw">Max kW per incident</Label>
                  <Input id="pb-kw" type="number" step="0.1" min="0" value={maxKw} onChange={(e) => setMaxKw(e.target.value)} data-testid="playbook-max-kw" />
                  <span className="text-2xs text-fg-subtle">Default {power(fleet.playbook_defaults.max_kw, 1)} (10% of target)</span>
                </div>
                <div className="flex flex-col gap-1">
                  <Label htmlFor="pb-dev">Max homes per incident</Label>
                  <Input id="pb-dev" type="number" step="1" min="1" value={maxDevices} onChange={(e) => setMaxDevices(e.target.value)} data-testid="playbook-max-devices" />
                  <span className="text-2xs text-fg-subtle">Default {count(fleet.playbook_defaults.max_devices)} (1% of our homes)</span>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button type="submit" variant="secondary" disabled={approvePb.isPending} data-testid="approve-playbook">
                  <Zap /> Approve playbook with these limits
                </Button>
                <span className="text-xs text-fg-subtle">You are signing as {fleet.operator}.</span>
              </div>
              {approvePb.error && <ErrorState error={approvePb.error} />}
            </form>
          )}
        </CardContent>
      </Card>
    </Section>
  )
}

function AuditList({ fleet }: { fleet: Fleet }) {
  const [all, setAll] = useState(false)
  const rows = [...fleet.audit].reverse()
  const shown = all ? rows : rows.slice(0, 6)
  return (
    <Card>
      <CardContent className="p-0">
        <ol className="divide-y divide-border" data-testid="audit-list">
          {shown.map((a, i) => (
            <li key={`${a.at}-${i}`} className="flex gap-3 p-4">
              <time className="w-12 shrink-0 text-xs tabular text-fg-subtle">{clock(a.at)}</time>
              <div className="min-w-0">
                <div className="text-sm font-medium">{a.summary}</div>
                <div className="text-xs text-fg-muted">
                  {a.actor} <span className="text-fg-subtle">{a.kind}</span>
                </div>
                {a.detail && <p className="mt-1 text-xs text-fg-muted">{a.detail}</p>}
              </div>
            </li>
          ))}
        </ol>
        {rows.length > 6 && (
          <div className="border-t border-border p-2">
            <Button variant="ghost" size="sm" onClick={() => setAll((v) => !v)}>
              {all ? 'Show fewer' : `Show all ${count(rows.length)} entries`}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ScenarioControls() {
  const session = useQuery({ queryKey: keys.session, queryFn: api.session })
  const reset = useResetSession()
  if (!session.data) return <Skeleton className="h-10 w-64" />
  const s = session.data
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="flex flex-col gap-1">
        <Label htmlFor="fleet-size">Fleet</Label>
        <Select id="fleet-size" value={s.fleet_size} onChange={(e) => reset.mutate({ fleet_size: Number(e.target.value), price_scenario: s.price_scenario })} disabled={reset.isPending}>
          {s.fleet_sizes.map((n) => (
            <option key={n} value={n}>
              {count(n)} homes
            </option>
          ))}
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="price-day">Price day</Label>
        <Select id="price-day" value={s.price_scenario} onChange={(e) => reset.mutate({ fleet_size: s.fleet_size, price_scenario: e.target.value })} disabled={reset.isPending} className="max-w-[220px]">
          {s.price_scenarios.map((p) => (
            <option key={p.key} value={p.key} title={p.blurb}>
              {p.label}
            </option>
          ))}
        </Select>
      </div>
      <Button variant="ghost" onClick={() => reset.mutate({ fleet_size: s.fleet_size, price_scenario: s.price_scenario })} disabled={reset.isPending} aria-label="Reset the simulated evening" data-testid="reset-session">
        <RotateCcw /> Reset
      </Button>
    </div>
  )
}

function TonightSkeleton() {
  return (
    <div className="flex flex-col gap-8" aria-busy>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-24 w-full" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <CardSkeleton lines={5} />
        <ChartSkeleton />
      </div>
    </div>
  )
}

function useReplayShared() {
  const session = useQuery({ queryKey: keys.session, queryFn: api.session })
  return useReplay(() => (session.data ? { fleet_size: session.data.fleet_size, price_scenario: session.data.price_scenario } : null))
}
type Replay = ReturnType<typeof useReplay>

function ReplayControls({ r }: { r: Replay }) {
  return r.phase === 'running' ? (
    <Button variant="outline" onClick={r.stop} data-testid="replay-stop">
      <Square /> Stop replay
    </Button>
  ) : (
    <Button variant="secondary" onClick={r.start} data-testid="replay-tonight" aria-label="Replay tonight: the whole evening in about forty seconds">
      <Clapperboard /> Replay tonight
    </Button>
  )
}

function ReplayCaption({ r }: { r: Replay }) {
  if (r.phase === 'done') {
    return (
      <div role="status" data-testid="replay-summary" data-swept={r.swept} className="flex items-center gap-4 rounded-2xl border border-border bg-surface/60 px-5 py-3 text-sm text-fg-muted">
        <span className="num shrink-0 text-2xs uppercase tracking-wider text-fg-subtle">Replay complete</span>
        <span>The evening was replayed at 600x on the shared clock and the deck is live again. Every approval was recorded in the audit trail.</span>
      </div>
    )
  }
  if (r.phase === 'idle' || !r.caption) return null
  return (
    <div role="status" aria-live="polite" data-testid="replay-caption" data-step={r.step} data-swept={r.swept} className="flex items-center gap-4 rounded-2xl border border-brand/40 bg-brand-soft/60 px-5 py-3 text-sm text-fg">
      <span className="num shrink-0 text-2xs uppercase tracking-wider text-brand">Replay {Math.min(r.step + 1, r.total)}/{r.total}</span>
      <span key={r.step} className="animate-fade-up">{r.caption}</span>
    </div>
  )
}
