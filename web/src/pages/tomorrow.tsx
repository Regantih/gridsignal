import { useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Upload } from 'lucide-react'
import { api, type Fleet, type Plan, type Policy, type RiskGroup, type Calibration } from '@/lib/api'
import { keys, useFleet, useFleetMutation } from '@/lib/queries'
import { count, daysMissed, money, pct, pctPoints, power, dateLabel, clock } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Metric } from '@/components/metric'
import { PageHeader, Callout } from '@/components/page'
import { CardSkeleton, ChartSkeleton, EmptyState, ErrorState } from '@/components/states'
import { RiskBadge, riskTone } from '@/components/status'
import { TexasMap, project } from '@/components/texas-map'
import { StormLab } from '@/components/storm-lab'
import { DragCurve } from '@/components/drag-curve'
import { PromiseDial } from '@/components/promise-dial'
import { HoldButton } from '@/components/hold-button'
import { YearOfDays } from '@/components/year-of-days'
import { Skeleton } from '@/components/ui/skeleton'

const POLICY_LABEL: Record<Policy, string> = {
  naive: 'Naive schedule',
  gridsignal: 'GridSignal, approval on every incident',
  gridsignal_auto: 'GridSignal with recovery playbook',
}
const POLICY_COLOR: Record<Policy, string> = {
  naive: 'var(--chart-3)',
  gridsignal: 'var(--chart-2)',
  gridsignal_auto: 'var(--chart-1)',
}

export function TomorrowPage() {
  const scenarios = useQuery({ queryKey: keys.scenarios, queryFn: api.scenarios, staleTime: Infinity })
  const [scenario, setScenario] = useState<string | null>(null)
  const [target, setTarget] = useState(0.99)
  const active = scenario ?? scenarios.data?.default ?? null
  const plan = useQuery({
    queryKey: keys.plan(active ?? '', target),
    queryFn: () => api.plan(active!, target),
    enabled: active !== null,
    staleTime: 60_000,
  })
  const fleet = useFleet()

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        kicker="Tomorrow's promise"
        title="How much of the fleet should we promise ERCOT?"
        lede="The planner replays tomorrow thousands of times against real ERCOT price years and the failure rates it has learned. It recommends the largest commitment that still keeps the promise on your target share of days."
        actions={
          scenarios.data ? (
            <div className="flex flex-wrap items-end gap-2">
              <div className="flex flex-col gap-1">
                <Label htmlFor="scenario">Price year to replay</Label>
                <Select id="scenario" value={active ?? ''} onChange={(e) => setScenario(e.target.value)} data-testid="scenario-select">
                  {scenarios.data.scenarios.map((s) => (
                    <option key={s.name} value={s.name} disabled={s.name === scenarios.data!.live_scenario && !scenarios.data!.live_available}>
                      {s.name}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="target">Target: keep the promise on</Label>
                <Select id="target" value={target} onChange={(e) => setTarget(Number(e.target.value))} data-testid="target-select">
                  {[0.95, 0.97, 0.99, 0.995].map((t) => (
                    <option key={t} value={t}>
                      {pct(t, t === 0.995 ? 1 : 0)} of days
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          ) : (
            <Skeleton className="h-10 w-72" />
          )
        }
      />

      {scenarios.isError && <ErrorState error={scenarios.error} retry={() => void scenarios.refetch()} />}
      {plan.isError && <ErrorState error={plan.error} retry={() => void plan.refetch()} />}

      {plan.isPending || !fleet.data ? (
        <div className="flex flex-col gap-6" aria-busy>
          <div className="flex items-center gap-5 rounded-3xl border border-border p-6">
            <Skeleton className="h-24 min-w-0 flex-1" />
            {fleet.data && (
              <div className="w-[150px] shrink-0" style={{ viewTransitionName: 'promise-dial' }}>
                <PromiseDial committed={fleet.data.summary.committed_kw} target={fleet.data.summary.target_kw} headroom={fleet.data.summary.headroom_kw} size={150} />
              </div>
            )}
          </div>
          <div className="grid gap-3 md:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
          <ChartSkeleton />
        </div>
      ) : plan.data ? (
        <PlanView plan={plan.data} target={target} summary={fleet.data.summary} />
      ) : null}

      {fleet.data && <StormLab fleet={fleet.data} />}

      <Tabs defaultValue="risk">
        <TabsList aria-label="Tomorrow details">
          <TabsTrigger value="risk" data-testid="tab-risk">Homes that fail together</TabsTrigger>
          <TabsTrigger value="learn" data-testid="tab-learn">Learned failure rates</TabsTrigger>
        </TabsList>
        <TabsContent value="risk">
          <RiskView />
        </TabsContent>
        <TabsContent value="learn">
          <LearnView />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function PlanView({ plan, target, summary }: { plan: Plan; target: number; summary: Fleet['summary'] }) {
  const currentRatio = summary.commit_ratio
  const targetKw = summary.target_kw
  const commit = useFleetMutation((ratio: number) => api.setCommitment(ratio, `Planner recommendation for ${plan.scenario}, ${pct(target, 1)} target`))
  const [ratio, setRatio] = useState(plan.recommended_ratio)
  const rec = plan.recommended_ratio
  const recommendedIsCurrent = Math.abs(currentRatio - rec) < 0.005
  const data = useMemo(() => {
    // Keys are Python floats ("1.0"); String(1) is "1", so match by numeric value.
    const pick = (row: Record<string, number>, x: number) => row[Object.keys(row).find((k) => Number(k) === x) ?? ''] ?? NaN
    const xs = Object.keys(plan.curve.gridsignal_auto).map(Number).sort((a, b) => a - b)
    return xs.map((x) => ({
      ratio: x,
      naive: pick(plan.curve.naive, x),
      gridsignal: pick(plan.curve.gridsignal, x),
      gridsignal_auto: pick(plan.curve.gridsignal_auto, x),
    }))
  }, [plan])
  const keptRec = plan.kept_at_recommended.gridsignal_auto
  const keptCur = plan.kept_at_current.gridsignal_auto
  const valueAt = (r: number) => plan.value_usd_per_year[Object.keys(plan.value_usd_per_year).find((k) => Math.abs(Number(k) - r) < 1e-6) ?? '']
  const ratioKey = (r: number) => data.reduce((best, d) => (Math.abs(d.ratio - r) < Math.abs(best - r) ? d.ratio : best), data[0]?.ratio ?? r)

  return (
    <div className="flex flex-col gap-6">
      <div role="status" data-testid="plan-headline" className="flex flex-col gap-5 rounded-3xl border border-brand/40 bg-brand-soft/60 p-6 sm:flex-row sm:items-center md:p-8">
        <div className="min-w-0 flex-1">
          <div className="eyebrow text-brand">Recommendation</div>
          <h2 className="mt-2 text-2xl font-semibold leading-tight md:text-[2.4rem]">
            Commit <span className="tabular" data-testid="recommended-ratio">{pct(rec)}</span> of measured headroom with the playbook on
          </h2>
          <p className="mt-2 text-sm text-fg-muted">{plan.headline}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
            {plan.live_prices && <Badge tone="info">Live ERCOT prices this year</Badge>}
            <Badge tone={plan.calibrated ? (plan.learn_source === 'synthetic' ? 'warn' : 'ok') : 'neutral'}>
              {plan.calibrated ? `Failure rates: ${plan.learn_source === 'synthetic' ? 'SYNTHETIC history' : 'uploaded telemetry'}` : 'Failure rates: assumed defaults'}
            </Badge>
            <span>{count(plan.years)} simulated years, {count(plan.homes)} homes, replaying {plan.scenario}.</span>
          </div>
        </div>
        <div className="w-[150px] shrink-0 self-center" style={{ viewTransitionName: 'promise-dial' }}>
          <PromiseDial committed={summary.committed_kw} target={summary.target_kw} headroom={summary.headroom_kw} size={150} />
          <div className="num -mt-1 text-center text-2xs text-fg-subtle">tonight's promise</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric label="Today's commitment" value={pct(currentRatio, 1)} note={`${power(targetKw)} promised tonight`} testId="current-ratio" />
        <Metric label="Days kept at today's level" value={pct(keptCur, 1)} tone={keptCur >= target ? 'ok' : 'warn'} note={`about ${daysMissed(keptCur)} missed days a year with the playbook`} />
        <Metric label="Days kept at recommended" value={pct(keptRec, 1)} tone={keptRec >= target ? 'ok' : 'warn'} note={`about ${daysMissed(keptRec)} missed days a year with the playbook`} />
        <Metric label="Grid value at recommended" value={money(valueAt(rec) ?? 0, false)} note="per year, before any shortfall" />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Reliability curve</CardTitle>
            <CardDescription>Drag along the curve to choose a commitment. The shaded band is above your target: the promise is safe there.</CardDescription>
          </CardHeader>
          <CardContent>
            <div data-testid="reliability-curve">
              <DragCurve data={data} ratio={ratio} onRatio={setRatio} target={target} current={ratioKey(currentRatio)} recommended={rec} />
            </div>
            <div className="mt-3 grid gap-2 text-xs text-fg-muted sm:grid-cols-3">
              {(['naive', 'gridsignal', 'gridsignal_auto'] as Policy[]).map((p) => (
                <div key={p} className="rounded-md border border-border p-2">
                  <div className="flex items-center gap-2 font-medium text-fg">
                    <span aria-hidden className="size-2 rounded-full" style={{ background: POLICY_COLOR[p] }} /> {POLICY_LABEL[p]}
                  </div>
                  <div className="mt-1 tabular">
                    {plan.safe_ratio[p] > 0 ? `Safe up to ${pct(plan.safe_ratio[p])}` : `No tested level reaches ${pct(target, 1)}`}
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-fg-subtle">
              Grid value is shown for information only; all three policies deliver the same schedule when nothing fails. GridSignal is about keeping the promise, not about earning more than a naive schedule.
            </p>
          </CardContent>
        </Card>

        <Card data-testid="commit-card">
          <CardHeader>
            <CardTitle>Set tomorrow's commitment</CardTitle>
            <CardDescription>This changes the target the fleet is dispatched to. It is logged in the audit trail with your name and the basis.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div>
              <div className="flex items-baseline justify-between">
                <Label htmlFor="ratio-slider">Commit</Label>
                <span className="text-2xl font-semibold tabular" data-testid="ratio-value">{pct(ratio)}</span>
              </div>
              <Slider id="ratio-slider" aria-label="Commitment ratio" min={0.5} max={1} step={0.05} value={[ratio]} onValueChange={([v]) => setRatio(v)} />
              <div className="flex justify-between text-2xs text-fg-subtle tabular">
                <span>50%</span>
                <span>100%</span>
              </div>
            </div>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              <dt className="text-fg-muted">Days kept with playbook</dt>
              <dd className="text-right tabular">{pct(plan.curve.gridsignal_auto[String(ratioKey(ratio))] ?? 0, 1)}</dd>
              <dt className="text-fg-muted">Days kept, approval each time</dt>
              <dd className="text-right tabular">{pct(plan.curve.gridsignal[String(ratioKey(ratio))] ?? 0, 1)}</dd>
              <dt className="text-fg-muted">Grid value per year</dt>
              <dd className="text-right tabular">{money(valueAt(ratioKey(ratio)) ?? 0, false)}</dd>
            </dl>
            <YearOfDays
              kept={plan.curve.gridsignal_auto[String(ratioKey(ratio))] ?? 0}
              compare={plan.curve.gridsignal[String(ratioKey(ratio))] ?? 0}
              label={`A simulated year at ${pct(ratio)}`}
            />
            {ratio > rec + 0.001 && (
              <Callout tone="warn">
                Above the recommendation. The cost of promising too much is missed days: about {daysMissed(plan.curve.gridsignal_auto[String(ratioKey(ratio))] ?? 0)} a year at this level versus {daysMissed(keptRec)} at {pct(rec)}.
              </Callout>
            )}
            <div className="flex flex-wrap gap-2">
              <HoldButton onConfirm={() => commit.mutate(ratio)} disabled={commit.isPending} testId="set-commitment" hint="Hold to commit">
                {`Commit ${pct(ratio)}`}
              </HoldButton>
              {!recommendedIsCurrent && (
                <Button variant="secondary" onClick={() => { setRatio(rec); commit.mutate(rec) }} disabled={commit.isPending} data-testid="use-recommended">
                  Use recommended {pct(rec)}
                </Button>
              )}
            </div>
            {commit.isSuccess && (
              <Callout tone="ok">
                Commitment set. Tonight's target is now {power(commit.data.target_kw)}. See the audit trail on Tonight.
              </Callout>
            )}
            {commit.error && <ErrorState error={commit.error} />}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function RiskView() {
  const risk = useQuery({ queryKey: keys.risk, queryFn: api.risk })
  const [kind, setKind] = useState<'all' | 'feeder' | 'ring'>('all')
  const [selected, setSelected] = useState<string | null>(null)
  if (risk.isPending) return <div className="grid gap-6 lg:grid-cols-2" aria-busy><ChartSkeleton /><CardSkeleton lines={6} /></div>
  if (risk.isError) return <ErrorState error={risk.error} retry={() => void risk.refetch()} />
  const r = risk.data
  const groups = r.groups.filter((g) => kind === 'all' || g.kind === kind)
  const sel = r.groups.find((g) => `${g.kind}:${g.key}` === selected) ?? null
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-3 gap-3">
        {r.status_order.map((st) => (
          <Metric key={st} label={st} value={count(r.summary[st] ?? 0)} tone={riskTone(st) === 'risk' ? 'risk' : riskTone(st) === 'warn' ? 'warn' : 'ok'} note="groups" testId={`risk-${st.split(' ')[0].toLowerCase()}`} />
        ))}
      </div>
      <Callout tone="info">
        A group is a set of homes that lose power together: a feeder, or the homes behind one gateway ring. <strong>Promise at risk</strong> means healthy homes cannot absorb the loss.{' '}
        <strong>Waits for a person</strong> means they can, once you approve. <strong>Playbook recovers it</strong> means the loss is inside the pre-approved limits.
        {r.playbook ? '' : ' No playbook is active tonight, so nothing recovers on its own.'}
      </Callout>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card>
          <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
            <CardTitle>Correlated-risk map</CardTitle>
            <div role="group" aria-label="Group kind" className="flex gap-1">
              {(['all', 'feeder', 'ring'] as const).map((k) => (
                <Button key={k} size="sm" variant={kind === k ? 'secondary' : 'ghost'} onClick={() => setKind(k)} aria-pressed={kind === k}>
                  {k === 'all' ? 'All' : k === 'feeder' ? 'Feeders' : 'Gateway rings'}
                </Button>
              ))}
            </div>
          </CardHeader>
          <CardContent>
            <RiskMap groups={groups} targetKw={r.target_kw} selected={selected} onSelect={setSelected} />
            <p className="mt-2 text-xs text-fg-subtle">{r.feeder_note} Circle size is kW lost if the group drops.</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{sel ? `${sel.kind === 'feeder' ? 'Feeder' : 'Gateway ring'} ${sel.key}` : 'Groups'}</CardTitle>
            <CardDescription>{sel ? `${count(sel.devices.length)} homes in ${sel.zones.join(', ')}` : 'Select a circle or a row for detail. Sorted by uncovered kW.'}</CardDescription>
          </CardHeader>
          <CardContent>
            {sel ? (
              <GroupDetail g={sel} targetKw={r.target_kw} onBack={() => setSelected(null)} />
            ) : (
              <ul className="max-h-[420px] divide-y divide-border overflow-auto" data-testid="risk-groups">
                {[...groups].sort((a, b) => b.uncovered_kw - a.uncovered_kw).map((g) => (
                  <li key={`${g.kind}:${g.key}`}>
                    <button type="button" className="flex w-full items-center justify-between gap-2 py-2 text-left hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-ring rounded-sm px-1" onClick={() => setSelected(`${g.kind}:${g.key}`)}>
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium">{g.kind === 'feeder' ? 'Feeder' : 'Ring'} {g.key}</span>
                        <span className="block text-xs text-fg-muted tabular">{count(g.devices.length)} homes, {power(g.lost_kw, 1)} at stake, {power(g.uncovered_kw, 1)} uncovered</span>
                      </span>
                      <RiskBadge status={g.status} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function GroupDetail({ g, targetKw, onBack }: { g: RiskGroup; targetKw: number; onBack: () => void }) {
  return (
    <div className="flex flex-col gap-3 text-sm">
      <RiskBadge status={g.status} />
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
        <dt className="text-fg-muted">If they all drop</dt>
        <dd className="text-right tabular">{power(g.lost_kw, 1)} ({pct(g.share_of_target, 1)} of {power(targetKw)})</dd>
        <dt className="text-fg-muted">Spare on healthy homes</dt>
        <dd className="text-right tabular">{power(g.spare_kw, 1)}</dd>
        <dt className="text-fg-muted">Left uncovered</dt>
        <dd className={`text-right tabular ${g.uncovered_kw > 0 ? 'text-risk font-medium' : ''}`}>{power(g.uncovered_kw, 1)}</dd>
        <dt className="text-fg-muted">Worst single zone share</dt>
        <dd className="text-right tabular">{pct(g.worst_zone_share, 1)}</dd>
        <dt className="text-fg-muted">Seen dropping together</dt>
        <dd className="text-right tabular">{g.seen_together > 0 ? `${count(g.seen_together)} time${g.seen_together === 1 ? '' : 's'} in history` : 'not yet in history'}</dd>
      </dl>
      {g.playbook_refusal && <Callout tone="warn">Playbook would refuse: {g.playbook_refusal}.</Callout>}
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-fg-subtle">Homes</div>
        <p className="mt-1 break-words text-xs text-fg-muted tabular">{g.devices.join(', ')}</p>
      </div>
      <Button variant="ghost" size="sm" onClick={onBack} className="self-start">Back to all groups</Button>
    </div>
  )
}

function RiskMap({ groups, targetKw, selected, onSelect }: { groups: RiskGroup[]; targetKw: number; selected: string | null; onSelect: (k: string) => void }) {
  const fill = (s: string) => (s.startsWith('Promise') ? 'var(--risk)' : s.startsWith('Waits') ? 'var(--warn)' : 'var(--ok)')
  const maxKw = Math.max(1, ...groups.map((g) => g.lost_kw))
  return (
    <TexasMap label="Map of Texas showing groups of homes that fail together, coloured by whether the promise survives">
      {[...groups].sort((a, b) => b.lost_kw - a.lost_kw).map((g) => {
        const [x, y] = project(g.lon, g.lat)
        const id = `${g.kind}:${g.key}`
        const r = 4 + 14 * Math.sqrt(g.lost_kw / maxKw)
        return (
          <g key={id} tabIndex={0} role="button" aria-label={`${g.kind} ${g.key}: ${g.status}, ${power(g.lost_kw, 1)} of ${power(targetKw)}`} onClick={() => onSelect(id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(id) } }} className="cursor-pointer focus-visible:outline-none">
            <circle cx={x} cy={y} r={r} fill={fill(g.status)} fillOpacity={g.kind === 'ring' ? 0.35 : 0.55} stroke={selected === id ? 'var(--fg)' : fill(g.status)} strokeWidth={selected === id ? 3 : 1} strokeDasharray={g.kind === 'ring' ? '4 3' : undefined}>
              <title>{`${g.kind} ${g.key}: ${g.status}, ${power(g.lost_kw, 1)} at stake`}</title>
            </circle>
          </g>
        )
      })}
    </TexasMap>
  )
}

function LearnView() {
  const qc = useQueryClient()
  const learn = useQuery({ queryKey: keys.learn, queryFn: api.learn })
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<unknown>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: keys.learn })
    void qc.invalidateQueries({ queryKey: ['plan'] })
    void qc.invalidateQueries({ queryKey: keys.risk })
    void qc.invalidateQueries({ queryKey: keys.session })
  }
  const setSource = async (source: string) => {
    await api.setLearnSource(source)
    invalidate()
  }
  const onFile = async (file: File | undefined) => {
    if (!file) return
    setUploading(true)
    setUploadError(null)
    try {
      await api.upload(file)
      invalidate()
    } catch (e) {
      setUploadError(e)
    } finally {
      setUploading(false)
    }
  }
  if (learn.isPending) return <CardSkeleton lines={8} />
  if (learn.isError) return <ErrorState error={learn.error} retry={() => void learn.refetch()} />
  const l = learn.data
  const shown: Calibration | null = l.learn_source === 'upload' ? l.upload : l.synthetic
  return (
    <div className="flex flex-col gap-4">
      <div data-testid="learn-source">
      <Callout tone={l.learn_source === 'none' ? 'info' : l.learn_source === 'synthetic' ? 'warn' : 'ok'}>
        {l.learn_source === 'none' && 'The planner is using assumed defaults. Turn on the bundled SYNTHETIC history or upload your own telemetry to have it learn rates from data.'}
        {l.learn_source === 'synthetic' && 'The planner is using rates learned from a SYNTHETIC history. It was generated with known rates; it shows the method, not your fleet.'}
        {l.learn_source === 'upload' && `The planner is using rates learned from your upload ${l.upload_name ?? ''}.`}
      </Callout>
      </div>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Failure-rate source">
        {(['none', 'synthetic', 'upload'] as const).map((s) => (
          <Button key={s} size="sm" variant={l.learn_source === s ? 'secondary' : 'ghost'} aria-pressed={l.learn_source === s} disabled={s === 'upload' && !l.upload} onClick={() => void setSource(s)} data-testid={`learn-${s}`}>
            {s === 'none' ? 'Assumed defaults' : s === 'synthetic' ? 'SYNTHETIC history' : 'Uploaded telemetry'}
          </Button>
        ))}
        <span className="mx-1 hidden h-6 w-px bg-border sm:block" />
        <input ref={fileRef} type="file" accept=".jsonl,.ndjson,.json,.txt,application/jsonl" className="sr-only" onChange={(e) => void onFile(e.target.files?.[0])} data-testid="upload-input" />
        <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={uploading}>
          <Upload /> {uploading ? 'Learning from upload...' : 'Upload JSONL telemetry'}
        </Button>
      </div>
      {uploadError ? <ErrorState error={uploadError} /> : null}
      {shown ? <CalibrationView c={shown} /> : <EmptyState title="No upload yet" body="Upload a JSONL file with one telemetry report per line." />}
    </div>
  )
}

function CalibrationView({ c }: { c: Calibration }) {
  const est = Object.values(c.estimates)
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {c.synthetic ? <Badge tone="warn" data-testid="synthetic-label">SYNTHETIC</Badge> : <Badge tone="ok">Uploaded telemetry</Badge>}
        <span className="text-sm text-fg-muted">
          {c.source}. {count(c.devices)} homes, {count(c.reports)} reports over {count(c.days)} days{c.first ? ` (${dateLabel(c.first)} to ${dateLabel(c.last)})` : ''}.
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric label="Failures seen" value={count(c.failures)} note={`${count(c.independent_failures)} independent`} />
        <Metric label="Correlated drops" value={count(c.clusters.length)} note="groups seen dropping together" />
        <Metric label="Exposure" value={`${count(Math.round(c.exposure_h))} h`} note="device-hours observed" />
        <Metric label="Rates learned" value={count(est.length)} note="with 90% ranges" />
      </div>
      <Card>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-sm" data-testid="rates-table">
            <thead className="text-left text-xs uppercase tracking-wide text-fg-subtle">
              <tr>
                <th className="p-3 font-medium">What</th>
                <th className="p-3 text-right font-medium">Assumed</th>
                <th className="p-3 text-right font-medium">Learned</th>
                <th className="p-3 text-right font-medium">90% range</th>
                <th className="p-3 font-medium">Evidence</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {est.map((e) => (
                <tr key={e.name}>
                  <td className="p-3">{e.label}</td>
                  <td className="p-3 text-right tabular text-fg-muted">{rate(e.prior)}</td>
                  <td className="p-3 text-right tabular font-medium">{rate(e.learned)}</td>
                  <td className="p-3 text-right tabular text-fg-muted">{rate(e.low)} to {rate(e.high)}</td>
                  <td className="p-3 text-fg-muted">{e.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
      {c.notes.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-fg-muted">
          {c.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
      {c.clusters.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer font-medium">Correlated drops in this history ({count(c.clusters.length)})</summary>
          <ul className="mt-2 flex flex-col gap-1 text-xs text-fg-muted">
            {c.clusters.slice(0, 20).map((cl) => (
              <li key={`${cl.slot}-${cl.key}`} className="tabular">
                {dateLabel(cl.slot)} {clock(cl.slot)}: {cl.key}, {count(cl.devices.length)} homes,{' '}
                {Object.entries(cl.zone_share).map(([z, s]) => `${pctPoints(s * 100, 0)} of ${z}`).join(', ')}
              </li>
            ))}
            {c.clusters.length > 20 && <li>and {count(c.clusters.length - 20)} more</li>}
          </ul>
        </details>
      )}
    </div>
  )
}

function rate(v: number): string {
  if (v >= 0.01) return pct(v, 1)
  return v.toExponential(2)
}
