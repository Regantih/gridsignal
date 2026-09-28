import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { CloudLightning, RotateCcw } from 'lucide-react'
import { api, type Fleet } from '@/lib/api'
import { keys } from '@/lib/queries'
import type { Storm } from '@/components/grid-field'
import { FleetScene } from '@/components/fleet-scene'
import { power, pct, count } from '@/lib/format'
import { Button } from '@/components/ui/button'

const PRESETS: { label: string; storm: Storm }[] = [
  { label: 'Gulf storm over Houston', storm: { lat: 29.76, lon: -95.37, radius_km: 90 } },
  { label: 'Hail line across Dallas', storm: { lat: 32.78, lon: -96.8, radius_km: 70 } },
  { label: 'Hill Country flash flood', storm: { lat: 30.1, lon: -98.2, radius_km: 120 } },
]

/**
 * Drag a storm onto the map. Every home under it drops, and the engine answers the only
 * question that matters: can the rest of each zone cover tonight's promise? Nothing is
 * dispatched; this is a rehearsal.
 */
export function StormLab({ fleet }: { fleet: Fleet }) {
  const [storm, setStorm] = useState<Storm | null>(null)
  const risk = useQuery({ queryKey: keys.risk, queryFn: api.risk })
  const run = useMutation({ mutationFn: api.storm })
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
  const hit = useMemo(() => new Set(run.data?.devices ?? []), [run.data])
  const place = (s: Storm) => {
    setStorm(s)
    run.mutate(s)
  }
  const r = run.data
  return (
    <section id="storm" className="grid gap-6 overflow-hidden rounded-3xl border border-border bg-surface/40 lg:grid-cols-[minmax(0,7fr)_minmax(0,4fr)]" data-testid="storm-lab">
      <div className="relative">
        <div className="absolute left-5 top-4 z-10 max-w-sm">
          <div className="eyebrow text-risk">Storm rehearsal</div>
          <p className="mt-1 text-xs text-fg-muted">Press and drag on the map to draw a storm cell. Every home under it drops. Nothing is dispatched.</p>
        </div>
        <div className="pt-14">
          <FleetScene
            devices={fleet.devices}
            groups={groups}
            hit={hit}
            storm={storm}
            stormMode
            onStorm={place}
            priceMwh={fleet.summary.remaining_price_mwh}
            label={storm ? `Map with a storm cell of ${Math.round(storm.radius_km)} km drawn over the fleet; ${hit.size} homes under it` : 'Map for drawing a storm over the fleet'}
            height={500}
          />
        </div>
      </div>
      <div className="flex flex-col gap-4 border-t border-border p-6 lg:border-l lg:border-t-0">
        <div className="flex flex-wrap gap-2">
          {PRESETS.map((p) => (
            <Button key={p.label} size="sm" variant="outline" onClick={() => place(p.storm)} data-testid={`storm-${p.label.split(' ')[0].toLowerCase()}`}>
              <CloudLightning /> {p.label}
            </Button>
          ))}
        </div>
        {!r && !run.isPending && (
          <div className="flex flex-1 flex-col justify-center gap-2 text-sm text-fg-muted">
            <h3 className="font-display text-2xl font-semibold text-fg">What breaks the promise?</h3>
            <p>Tomorrow's plan assumes failures arrive one feeder or one gateway ring at a time. Weather does not. Draw one and find out how big a storm the fleet can ride through.</p>
          </div>
        )}
        {run.isPending && <div className="h-40 animate-pulse rounded-2xl bg-surface-2" />}
        {run.error && <p className="text-sm text-risk">{String(run.error)}</p>}
        {r && (
          <div className="ticker-in flex flex-col gap-4" data-testid="storm-result" key={`${r.lat}-${r.lon}-${r.radius_km}`}>
            <div>
              <div className={`eyebrow ${r.holds ? 'text-ok' : 'text-risk'}`}>{r.homes === 0 ? 'No homes under this storm' : r.holds ? 'The promise holds' : 'The promise breaks'}</div>
              <div className={`num mt-1 text-5xl font-medium ${r.holds ? 'text-ok' : 'text-risk'}`}>{pct(r.kept_share, 1)}</div>
              <div className="text-xs text-fg-muted">of tonight's {power(r.target_kw)} still delivered</div>
            </div>
            <dl className="grid grid-cols-2 gap-3">
              <Stat k="Homes down" v={count(r.homes)} />
              <Stat k="Promised kW lost" v={power(r.lost_kw, 1)} />
              <Stat k="Spare in those zones" v={power(r.spare_kw, 1)} />
              <Stat k="Nobody can cover" v={power(r.uncovered_kw, 1)} tone={r.uncovered_kw > 0 ? 'risk' : 'ok'} />
            </dl>
            {r.zones.length > 0 && (
              <ul className="flex flex-col gap-1.5 text-xs">
                {r.zones.map((z) => {
                  const b = r.by_zone[z]
                  const share = b.lost_kw > 0 ? Math.min(b.spare_kw / b.lost_kw, 1) : 1
                  return (
                    <li key={z}>
                      <div className="flex justify-between"><span className="num">{z.replace('LZ_', '')}</span><span className="num text-fg-subtle">{power(b.lost_kw, 1)} lost · {power(b.spare_kw, 1)} spare</span></div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-risk/40"><div className="h-full rounded-full bg-ok" style={{ width: `${share * 100}%` }} /></div>
                    </li>
                  )
                })}
              </ul>
            )}
            <p className="text-xs text-fg-subtle">Only healthy homes in the same zone, outside the storm, can pick up the loss, each up to its spare export. Same arithmetic as the risk map below.</p>
            <Button variant="ghost" size="sm" className="self-start" onClick={() => { setStorm(null); run.reset() }}>
              <RotateCcw /> Clear the storm
            </Button>
          </div>
        )}
      </div>
    </section>
  )
}

function Stat({ k, v, tone }: { k: string; v: string; tone?: 'ok' | 'risk' }) {
  return (
    <div className="rounded-xl border border-border p-3">
      <dt className="eyebrow">{k}</dt>
      <dd className={`num mt-1 text-lg ${tone === 'risk' ? 'text-risk' : tone === 'ok' ? 'text-ok' : ''}`}>{v}</dd>
    </div>
  )
}
