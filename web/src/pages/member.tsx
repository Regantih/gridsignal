import { LiquidBattery } from '@/components/liquid-battery'
import { StoryCards } from '@/components/story-cards'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { BatteryCharging, ShieldCheck, Sun, Home as HomeIcon, AlertTriangle } from 'lucide-react'
import { api, type Member } from '@/lib/api'
import { keys } from '@/lib/queries'
import { clock, count, dateLabel, energy, hours, money, power } from '@/lib/format'
import { Card, CardContent } from '@/components/ui/card'
import { Select } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState, ErrorState } from '@/components/states'
import { cn } from '@/lib/utils'

export function MemberPage() {
  const { deviceId } = useParams()
  const navigate = useNavigate()
  const homes = useQuery({ queryKey: keys.homes, queryFn: api.homes })
  const id = deviceId ?? homes.data?.focus_device_id ?? null
  const member = useQuery({ queryKey: keys.member(id ?? ''), queryFn: () => api.member(id!), enabled: id !== null })

  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-5">
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-widest text-brand">Your home battery</div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{member.data ? member.data.summary.site : <Skeleton className="h-8 w-56" />}</h1>
        </div>
        {homes.data && (
          <div className="flex flex-col gap-1">
            <Label htmlFor="home" className="text-xs text-fg-muted">Demo: view as</Label>
            <Select id="home" value={id ?? ''} onChange={(e) => navigate(`/member/${e.target.value}`)} className="w-44" data-testid="home-select">
              {homes.data.homes.map((h) => (
                <option key={h.device_id} value={h.device_id}>{h.site}</option>
              ))}
            </Select>
          </div>
        )}
      </div>

      {member.isError && <ErrorState error={member.error} retry={() => void member.refetch()} />}
      {member.isPending ? <MemberSkeleton /> : member.data ? <MemberView m={member.data} /> : null}
    </div>
  )
}

function MemberView({ m }: { m: Member }) {
  const s = m.summary
  const backupOk = s.backup_hours >= 4
  const cards = [
    {
      id: 'status',
      node: (
        <Card className={cn('h-full', s.is_affected ? 'border-warn/50' : 'border-ok/40')} data-testid="member-headline">
          <CardContent className="flex h-full flex-col gap-2 p-5">
            <div className="flex items-center gap-2">
              {s.is_affected ? <AlertTriangle aria-hidden className="size-5 text-warn" /> : <ShieldCheck aria-hidden className="size-5 text-ok" />}
              <h2 className="text-lg font-semibold leading-tight">{noDash(s.headline)}</h2>
            </div>
            <p className="text-sm text-fg-muted">{noDash(s.body)}</p>
            <p className="text-sm font-medium">{noDash(s.next_step)}</p>
          </CardContent>
        </Card>
      ),
    },
    {
      id: 'backup',
      node: <Tile icon={BatteryCharging} label="Backup if the grid fails" value={hours(s.backup_hours)} tone={backupOk ? 'ok' : 'warn'} note={s.generator_kwh > 0 ? `${hours(s.backup_hours_with_generator)} with your generator` : `${energy(s.backup_kwh, 1)} kept for you`} body="If the grid goes down tonight, this is how long your battery runs your home at its current load. Your home always comes first." testId="backup-hours" />,
    },
    {
      id: 'reserve',
      node: <Tile icon={ShieldCheck} label="Reserve protected" value={energy(s.reserve_kwh, 1)} tone="ok" note="never sold to the grid" body={`The locked layer at the bottom of your battery. It is worth ${money(s.protected_usd)} at tonight's price and it is kept out of the market on purpose.`} testId="reserve" />,
    },
    {
      id: 'earned',
      node: <Tile icon={Sun} label="Earned tonight" value={money(s.earned_usd)} tone="brand" note={`${power(s.export_kw, 1)} shared with the grid`} body={`Your share of what the fleet earned at the real ERCOT price, attributed to ${energy(s.committed_kwh, 1)} you shared. Not a bill or a payout.`} testId="earned" />,
    },
    {
      id: 'now',
      node: (
        <Card className="h-full">
          <CardContent className="flex h-full flex-col gap-3 p-5 text-sm">
            <div className="flex items-center gap-1.5 text-xs text-fg-muted"><HomeIcon aria-hidden className="size-3.5" /> Right now</div>
            <dl className="grid grid-cols-2 gap-y-2">
            <dt className="text-fg-muted">Stored</dt>
            <dd className="text-right tabular">{energy(s.stored_kwh, 1)}</dd>
            <dt className="text-fg-muted">Your home is using</dt>
            <dd className="text-right tabular">{power(s.home_load_kw, 1)}</dd>
            <dt className="text-fg-muted">Sharing with the grid</dt>
            <dd className="text-right tabular">{power(s.export_kw, 1)}</dd>
            <dt className="text-fg-muted">Promised for tonight</dt>
            <dd className="text-right tabular">{energy(s.committed_kwh, 1)}</dd>
            <dt className="text-fg-muted">Battery</dt>
            <dd className="text-right capitalize">{s.unit_type}</dd>
          </dl>
          <p className="text-xs text-fg-subtle">
            Tonight's event: {m.grid_event.name}, {clock(m.grid_event.started_at)} to {clock(m.grid_event.ends_at)}. Your home comes first; only what is left over is shared.
          </p>
        </CardContent>
      </Card>
      ),
    },
  ]
  return (
    <>
      <Card>
        <CardContent className="flex flex-col items-center gap-5 p-5 sm:flex-row">
          <LiquidBattery stored={s.stored_kwh} reserve={s.reserve_kwh} shared={s.committed_kwh} backupHours={hours(s.backup_hours)} affected={s.is_affected} />
          <ul className="flex w-full flex-col gap-3 text-sm">
            <li className="flex items-start gap-2"><span className="mt-1.5 size-2.5 shrink-0 rounded-full bg-ok" /><span><span className="num">{energy(s.reserve_kwh, 1)}</span> locked for your home, beneath everything else. Never sold.</span></li>
            <li className="flex items-start gap-2"><span className="mt-1.5 size-2.5 shrink-0 rounded-full bg-flow" /><span><span className="num">{energy(s.committed_kwh, 1)}</span> shared with the grid tonight, flowing out.</span></li>
            <li className="flex items-start gap-2"><span className="mt-1.5 size-2.5 shrink-0 rounded-full bg-fg-subtle" /><span>The rest stays yours to use.</span></li>
          </ul>
        </CardContent>
      </Card>

      <div>
        <h2 className="eyebrow mb-2">Your home tonight</h2>
        <StoryCards cards={cards} label="Your home tonight" />
      </div>

      <Card>
        <CardContent className="flex flex-col gap-3 p-5">
          <h3 className="font-semibold">What happened</h3>
          {m.history.length === 0 ? (
            <EmptyState title="A quiet evening so far" body="If anything affects your battery it will show up here in plain words." />
          ) : (
            <ol className="flex flex-col gap-3 text-sm" data-testid="member-history">
              {[...m.history].reverse().map((h, i) => (
                <li key={`${h.at}-${i}`} className="flex gap-3">
                  <time className="w-12 shrink-0 text-xs tabular text-fg-subtle">{clock(h.at)}</time>
                  <span>{plain(h.kind, h.summary, s.device_id)}</span>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-col gap-2 text-xs text-fg-subtle">
        {m.neighbours.length > 0 && (
          <p>
            {count(m.neighbours.length)} nearby homes share your feeder. <Badge tone="outline" dot={false}>simulated</Badge>
          </p>
        )}
        <p>{m.disclosure} Numbers above are for {dateLabel(m.grid_event.started_at)}.</p>
      </div>
    </>
  )
}

/** Engine copy uses dashes as pauses; the UI style guide uses commas. */
export function noDash(text: string): string {
  return text.replace(/\s*\u2014\s*/g, ', ').replace(/\s*\u2013\s*/g, ', ')
}

/** Plain words for members. Incident ids, kW figures and operator names stay on the operator screens. */
export function plain(kind: string, summary: string, deviceId: string): string {
  const stripped = summary
    .replace(/INC-\d+\s*/g, '')
    .replace(new RegExp(`${deviceId}`, 'g'), 'your battery')
    .replace(/\s{2,}/g, ' ')
    .trim()
  switch (kind) {
    case 'baseline':
      return 'The fleet started the evening event with every home covered.'
    case 'detection':
      return 'Your battery stopped reporting during the event. Your home stayed powered and your reserve stayed protected.'
    case 'incident_opened':
    case 'recommendation':
      return 'The fleet team was alerted and a fix was proposed. Nothing changes at your home until a person approves it.'
    case 'human_approval':
      return 'An operator approved the fix. Other homes are covering the promise while yours is checked.'
    case 'playbook_execution':
      return 'A pre-approved fix covered the gap using other homes in the fleet.'
    case 'playbook_escalation':
      return 'The gap was too large for the pre-approved fix, so a person was asked to decide.'
    case 'commitment_set':
      return "Tomorrow's grid promise was set by an operator."
    default:
      return noDash(stripped.charAt(0).toUpperCase() + stripped.slice(1))
  }
}

function Tile({ icon: Icon, label, value, note, body, tone = 'default', testId }: { icon: typeof Sun; label: string; value: string; note?: string; body?: string; tone?: 'default' | 'ok' | 'warn' | 'brand'; testId?: string }) {
  const color = { default: 'text-fg', ok: 'text-ok', warn: 'text-warn', brand: 'text-brand' }[tone]
  return (
    <Card className="h-full">
      <CardContent className="flex h-full flex-col gap-1 p-5">
        <div className="flex items-center gap-1.5 text-xs text-fg-muted">
          <Icon aria-hidden className="size-3.5" /> {label}
        </div>
        <div className={`num text-4xl font-medium ${color}`} data-testid={testId}>{value}</div>
        {note && <div className="text-xs text-fg-subtle">{note}</div>}
        {body && <p className="mt-2 text-sm text-fg-muted">{body}</p>}
      </CardContent>
    </Card>
  )
}

function MemberSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-busy>
      <Skeleton className="h-32 w-full" />
      <div className="grid grid-cols-2 gap-3">
        {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
      </div>
      <Skeleton className="h-40 w-full" />
    </div>
  )
}
