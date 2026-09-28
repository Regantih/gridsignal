import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Area, AreaChart, CartesianGrid, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AlertTriangle, CheckCircle2, ShieldCheck, ShieldOff, Zap, RotateCcw, WifiOff } from 'lucide-react'
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
import { PageHeader, Section, Callout } from '@/components/page'
import { CardSkeleton, ChartSkeleton, EmptyState, ErrorState } from '@/components/states'
import { DeviceBadge, IncidentBadge } from '@/components/status'
import { TexasMap, project } from '@/components/texas-map'
import { Skeleton } from '@/components/ui/skeleton'

export function TonightPage() {
  const fleet = useFleet()
  if (fleet.isPending) return <TonightSkeleton />
  if (fleet.isError) return <ErrorState error={fleet.error} retry={() => void fleet.refetch()} />
  return <Tonight fleet={fleet.data} />
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
  const resolved = fleet.incidents.filter((i) => i.status === 'resolved')
  const atRisk = fleet.incidents.reduce((a, i) => a + i.dollars_at_risk, 0)
  const recovered = fleet.incidents.reduce((a, i) => a + i.dollars_recovered, 0)
  const busy = trigger.isPending || approve.isPending || stale.isPending
  const error = trigger.error ?? approve.error ?? stale.error
  const toneCls = { ok: 'border-ok/40 bg-ok-soft', warn: 'border-warn/40 bg-warn-soft', risk: 'border-risk/40 bg-risk-soft' }[v.tone]
  const iconCls = { ok: 'text-ok', warn: 'text-warn', risk: 'text-risk' }[v.tone]

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        kicker="Tonight"
        title="Will the fleet keep its grid promise?"
        lede={`${fleet.grid_event.name}, ${fleet.grid_event.zone}. ${dateLabel(fleet.grid_event.started_at)} ${clock(fleet.grid_event.started_at)} to ${clock(fleet.grid_event.ends_at)}. ${fleet.disclosure}`}
        actions={<ScenarioControls />}
      />

      <div role="status" data-testid="verdict" className={`flex flex-col gap-3 rounded-lg border p-5 md:flex-row md:items-start ${toneCls}`}>
        <v.icon aria-hidden className={`size-7 shrink-0 ${iconCls}`} />
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-semibold leading-tight md:text-xl">{v.title}</h2>
          <p className="mt-1 text-sm text-fg-muted">{v.body}</p>
        </div>
        {fleet.pending_incident && (
          <Button size="lg" onClick={() => approve.mutate(undefined)} disabled={busy} data-testid="approve-recovery" className="md:self-center">
            <CheckCircle2 /> Approve recovery
          </Button>
        )}
      </div>

      {error && <ErrorState error={error} />}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Metric label="Coverage" value={pctPoints(s.coverage_pct, s.coverage_pct === 100 ? 0 : 1)} tone={s.coverage_pct >= 100 ? 'ok' : 'warn'} note={`${power(s.committed_kw)} of ${power(s.target_kw)}`} testId="coverage" />
        <Metric label="Headroom" value={power(s.headroom_kw)} note="spare export on healthy units" />
        <Metric label="Dollars at risk" value={money(atRisk)} tone={atRisk > 0 && recovered < atRisk ? 'risk' : 'default'} note={`${count(fleet.incidents.length)} incident${fleet.incidents.length === 1 ? '' : 's'} this window`} testId="dollars-at-risk" />
        <Metric label="Dollars recovered" value={money(recovered)} tone={recovered > 0 ? 'ok' : 'default'} note={`${count(resolved.length)} resolved with approval`} testId="dollars-recovered" />
        <Metric label="Price now" value={`$${Math.round(s.remaining_price_mwh)}`} note={`per MWh, ${hours(s.remaining_hours)} left in the window`} />
        <Metric label="Homes" value={count(s.total_devices)} note={`${count(s.online)} online, ${count(s.degraded)} degraded, ${count(s.offline)} offline, ${count(s.unavailable)} quarantined`} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-6">
          <Section title="What needs you now" description={`In GridSignal ${fleet.approval_sentence}.`}>
            {fleet.pending_incident ? (
              <IncidentCard incident={fleet.pending_incident} onApprove={() => approve.mutate(undefined)} busy={busy} pending />
            ) : (
              <Card>
                <CardContent className="flex flex-col gap-4 p-5">
                  <div className="flex items-start gap-3">
                    <CheckCircle2 aria-hidden className="mt-0.5 size-5 shrink-0 text-ok" />
                    <div>
                      <div className="font-medium">Nothing is waiting for you</div>
                      <p className="text-sm text-fg-muted">{fleet.human_summary}</p>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 border-t border-border pt-4">
                    <span className="w-full text-xs text-fg-subtle">Simulate what tonight could throw at the fleet:</span>
                    <Button variant="secondary" onClick={() => trigger.mutate(undefined)} disabled={busy} data-testid="trigger-incident">
                      <WifiOff /> Gateway loses uplink on {fleet.focus_device_id}
                    </Button>
                    <Button variant="outline" onClick={() => stale.mutate(0.03)} disabled={busy} data-testid="trigger-stale">
                      Stale telemetry wave (3%)
                    </Button>
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
          <Section title="Fleet on the map" description={`${count(fleet.devices.length)} simulated homes across ERCOT load zones.`}>
            <Card>
              <CardContent className="p-3">
                <FleetMap fleet={fleet} />
                <div className="mt-2 flex flex-wrap gap-2 px-1">
                  {(['online', 'degraded', 'offline', 'unavailable'] as const).map((st) => (
                    <DeviceBadge key={st} status={st} />
                  ))}
                </div>
              </CardContent>
            </Card>
          </Section>

          <Section
            title="Tonight's price"
            description={
              <>
                {fleet.price_trace.location} real-time settlement prices, {fleet.price_trace.date}.{' '}
                <a className="underline underline-offset-2" href={fleet.price_trace.source} target="_blank" rel="noreferrer">
                  Source: ERCOT
                </a>
              </>
            }
          >
            <Card>
              <CardContent className="p-3 pt-4">
                <PriceChart fleet={fleet} />
              </CardContent>
            </Card>
          </Section>

          <Section title="Audit trail" description="Who did what, and when. Approvals are named.">
            <AuditList fleet={fleet} />
          </Section>
        </div>
      </div>
    </div>
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
            <Button onClick={onApprove} disabled={busy} data-testid={pending ? undefined : 'approve-in-list'}>
              <CheckCircle2 /> Approve recovery
            </Button>
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

function FleetMap({ fleet }: { fleet: Fleet }) {
  const fill = { online: 'var(--ok)', degraded: 'var(--warn)', offline: 'var(--risk)', unavailable: 'var(--neutral)' }
  const focus = new Set(fleet.pending_incident?.cohort ?? [])
  return (
    <TexasMap label="Map of Texas with one dot per simulated home, coloured by status">
      {fleet.devices.map((d) => {
        const [x, y] = project(d.lon, d.lat)
        const hot = focus.has(d.device_id)
        return (
          <g key={d.device_id}>
            {hot && <circle cx={x} cy={y} r={11} fill="none" stroke="var(--risk)" strokeWidth={2} className="animate-pulse" />}
            <circle cx={x} cy={y} r={fleet.devices.length > 100 ? 3 : 5} fill={fill[d.status]} stroke="var(--surface)" strokeWidth={1}>
              <title>{`${d.device_id}, ${d.site}: ${d.status}, ${power(d.assigned_kw, 1)} assigned`}</title>
            </circle>
          </g>
        )
      })}
    </TexasMap>
  )
}

function PriceChart({ fleet }: { fleet: Fleet }) {
  const data = useMemo(
    () => fleet.prices.map((p) => ({ t: p.interval_start.slice(11, 16), spp: p.spp })),
    [fleet.prices],
  )
  const start = fleet.grid_event.started_at.slice(11, 16)
  const end = fleet.grid_event.ends_at.slice(11, 16)
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
          <defs>
            <linearGradient id="spp" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.5} />
              <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="t" interval={15} tickLine={false} axisLine={false} />
          <YAxis tickLine={false} axisLine={false} width={64} tickFormatter={(v: number) => `$${v}`} />
          <ReferenceArea x1={start} x2={end} fill="var(--chart-band)" strokeOpacity={0} label={{ value: 'event window', position: 'insideTop', fill: 'var(--fg-muted)', fontSize: 11 }} />
          <Tooltip
            contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--fg)', fontSize: 12 }}
            formatter={(v) => [priceMwh(Number(v)), 'Settlement price']}
          />
          <Area type="monotone" dataKey="spp" stroke="var(--chart-1)" strokeWidth={2} fill="url(#spp)" isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
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
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <CardSkeleton lines={5} />
        <ChartSkeleton />
      </div>
    </div>
  )
}
