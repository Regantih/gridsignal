import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Area, ComposedChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { RefreshCw } from 'lucide-react'
import { api, type Feed } from '@/lib/api'
import { keys } from '@/lib/queries'
import { count, dateLabel, pct, priceMwh, slot } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Metric } from '@/components/metric'
import { PageHeader, Callout } from '@/components/page'
import { Ridgeline } from '@/components/ridgeline'
import { ChartSkeleton, EmptyState, ErrorState } from '@/components/states'
import { Skeleton } from '@/components/ui/skeleton'

export function MarketPage() {
  const qc = useQueryClient()
  // Ask the server to refresh on load and every 15 minutes while the page is open. The server
  // caches one refresh per 15-minute bucket, so this never hits ERCOT more often than that.
  const feed = useQuery({
    queryKey: keys.feed,
    queryFn: api.refreshFeed,
    staleTime: 60_000,
    refetchInterval: 15 * 60_000,
    refetchIntervalInBackground: true,
  })
  const refresh = useMutation({
    mutationFn: api.refreshFeed,
    onSuccess: (data) => qc.setQueryData(keys.feed, data),
  })
  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        kicker="Market"
        title="Is today the day the model did not expect?"
        lede="Live ERCOT real-time settlement prices for the three load zones the fleet sits in, compared with the range the planner's price model would have drawn for this month. When today sits outside the band, the plan deserves a second look."
        actions={
          <Button variant="secondary" onClick={() => refresh.mutate()} disabled={refresh.isPending} data-testid="refresh-feed">
            <RefreshCw className={refresh.isPending ? 'animate-spin' : undefined} /> {refresh.isPending ? 'Fetching ERCOT...' : 'Refresh from ERCOT'}
          </Button>
        }
      />
      {feed.isError && <ErrorState error={feed.error} retry={() => void feed.refetch()} />}
      {refresh.error && <ErrorState error={refresh.error} />}
      {feed.isPending ? (
        <div className="flex flex-col gap-6" aria-busy>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24" />)}</div>
          <ChartSkeleton />
        </div>
      ) : feed.data ? (
        <FeedView f={feed.data} />
      ) : null}
      <YearRidges />
    </div>
  )
}

function YearRidges() {
  const [zone, setZone] = useState('LZ_HOUSTON')
  const days = useQuery({ queryKey: ['feed-days', zone], queryFn: () => api.feedDays(zone), staleTime: 15 * 60_000 })
  return (
    <Card data-testid="ridgeline">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div>
          <CardTitle>The year so far, one ridge per day</CardTitle>
          <CardDescription>Every settled 2026 day in {zone.replace('LZ_', '')}, midnight to midnight. Amber ridges crossed $250/MWh. Hover a ridge for its date.</CardDescription>
        </div>
        <div role="group" aria-label="Ridgeline zone" className="flex gap-1">
          {(days.data?.zones ?? ['LZ_HOUSTON', 'LZ_NORTH', 'LZ_SOUTH']).map((z) => (
            <Button key={z} size="sm" variant={zone === z ? 'secondary' : 'ghost'} aria-pressed={zone === z} onClick={() => setZone(z)}>{z.replace('LZ_', '')}</Button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {days.isPending ? <ChartSkeleton /> : days.isError ? <ErrorState error={days.error} retry={() => void days.refetch()} /> : <Ridgeline d={days.data} />}
      </CardContent>
    </Card>
  )
}

function FeedView({ f }: { f: Feed }) {
  const st = f.status
  const check = f.check
  const [zoneIdx, setZoneIdx] = useState(0)
  const data = useMemo(() => {
    if (!check) return []
    return check.actual[zoneIdx].map((a, i) => ({
      t: slot(i),
      actual: a,
      low: check.band_low[zoneIdx][i],
      high: check.band_high[zoneIdx][i],
      band: [check.band_low[zoneIdx][i], check.band_high[zoneIdx][i]] as [number, number],
    }))
  }, [check, zoneIdx])
  const verdictTone = check ? (check.inside_share >= 0.8 ? 'ok' : check.inside_share >= 0.5 ? 'warn' : 'risk') : 'info'
  const r = f.refresh
  return (
    <div className="flex flex-col gap-6">
      {r && !r.ok && (
        <Callout tone="warn">
          The last refresh from ERCOT failed ({r.error}). Showing the stored history instead; nothing below is missing, it is just not newer.
        </Callout>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric label="Days collected" value={count(st.days_in_store)} note={st.first_day ? `since ${dateLabel(st.first_day)}` : 'stored history'} testId="days-collected" />
        <Metric label="Intervals today" value={`${count(st.intervals_today)} of ${count(f.intervals_per_day)}`} note={st.latest_interval ? `latest ${st.latest_interval.slice(11)}` : 'none yet'} testId="intervals-today" />
        <Metric label="Settled days in model" value={count(f.settled_days)} note="used to learn the price model" />
        <Metric label="Scarcity days" value={count(f.hot_days)} note="any zone above $250/MWh" tone={f.hot_days > 0 ? 'warn' : 'default'} />
      </div>

      <Card>
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Today versus the model's 5 to 95% band</CardTitle>
            <CardDescription>
              {dateLabel(f.today)}. Shaded area is where the model expected prices this month; the line is what ERCOT actually settled.
            </CardDescription>
          </div>
          {check && (
            <div role="group" aria-label="Load zone" className="flex gap-1">
              {check.zones.map((z, i) => (
                <Button key={z} size="sm" variant={zoneIdx === i ? 'secondary' : 'ghost'} aria-pressed={zoneIdx === i} onClick={() => setZoneIdx(i)}>
                  {z.replace('LZ_', '')}
                </Button>
              ))}
            </div>
          )}
        </CardHeader>
        <CardContent>
          {check ? (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Badge tone={verdictTone}>{check.verdict}</Badge>
                <span className="text-sm text-fg-muted tabular" data-testid="inside-share">
                  {pct(check.inside_share)} of zone-intervals inside the band across {count(check.intervals)} intervals so far.
                </span>
              </div>
              <div className="h-72" data-testid="band-chart">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={data} margin={{ top: 8, right: 12, left: -8, bottom: 0 }}>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="t" interval={11} tickLine={false} axisLine={false} />
                    <YAxis tickLine={false} axisLine={false} width={64} tickFormatter={(v: number) => `$${Math.round(v)}`} />
                    <Tooltip
                      contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--fg)', fontSize: 12 }}
                      formatter={(v, name) => {
                        if (name === 'band') {
                          const [lo, hi] = v as [number, number]
                          return [`${priceMwh(lo)} to ${priceMwh(hi)}`, 'Model 5 to 95%']
                        }
                        return [priceMwh(Number(v)), 'ERCOT actual']
                      }}
                    />
                    <Area type="monotone" dataKey="band" stroke="none" fill="var(--chart-band)" isAnimationActive={false} />
                    <Line type="monotone" dataKey="actual" stroke="var(--chart-2)" strokeWidth={2} dot={false} isAnimationActive={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </>
          ) : (
            <EmptyState title="No intervals for today yet" body="ERCOT publishes a settlement price every 15 minutes. The first one for today has not arrived in the store." />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-3 text-xs text-fg-subtle md:grid-cols-2">
        <p>
          Store: {count(st.days_in_store)} days, last full day {st.last_full_day ? dateLabel(st.last_full_day) : 'none'}, fetched {st.fetched_at}
          {st.revised_intervals > 0 ? `, ${count(st.revised_intervals)} intervals revised by ERCOT` : ''}. Refreshes are cached for {Math.round(f.refresh_seconds / 60)} minutes.
        </p>
        <p>
          Source: <a className="underline underline-offset-2 hover:text-fg" href={f.source} target="_blank" rel="noreferrer">ERCOT real-time settlement point prices</a>. {f.disclosure}
          {st.errors.length > 0 ? ` Feed notes: ${st.errors.join('; ')}` : ''}
        </p>
      </div>
    </div>
  )
}
