import { useMemo, useState } from 'react'
import type { FeedDays } from '@/lib/api'
import { dateLabel } from '@/lib/format'

/**
 * Every settled day of the year as one ridge, stacked like a pulsar plot. Quiet days are
 * flat lines; scarcity days rise above the rest and burn amber. Hover a ridge for its
 * date and peak. Heights are compressed with a square root so a $1,000 hour does not
 * erase the shape of ordinary evenings.
 */
export function Ridgeline({ d }: { d: FeedDays }) {
  const [hover, setHover] = useState<number | null>(null)
  const W = 720
  const rows = d.dates.length
  const gap = 2.3
  const lift = 52
  const T = lift + 6
  const H = T + rows * gap + 18
  const L = 12
  const R = 12
  const scale = (p: number) => Math.sqrt(Math.max(p, 0) / 1000) * lift
  const paths = useMemo(
    () =>
      d.prices.map((row, r) => {
        const base = T + r * gap
        const n = row.length
        const pts = row.map((p, i) => `${(L + (i / (n - 1)) * (W - L - R)).toFixed(1)} ${(base - scale(p)).toFixed(1)}`)
        return { base, line: `M${pts.join(' L')}`, fill: `M${L} ${base} L${pts.join(' L')} L${W - R} ${base} Z` }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [d],
  )
  const hot = (r: number) => d.peaks[r] >= d.hot_mwh
  const h = hover
  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${rows} settled days of ${d.zone} prices in 2026, ${d.peaks.filter((p) => p >= d.hot_mwh).length} above $${d.hot_mwh}/MWh`}
        onPointerMove={(e) => {
          const b = e.currentTarget.getBoundingClientRect()
          const y = ((e.clientY - b.top) / b.height) * H
          const r = Math.round((y - T) / gap)
          setHover(r >= 0 && r < rows ? r : null)
        }}
        onPointerLeave={() => setHover(null)}
      >
        <defs>
          <filter id="hotglow" x="-5%" y="-50%" width="110%" height="200%">
            <feGaussianBlur stdDeviation="2.2" />
          </filter>
        </defs>
        {paths.map((p, r) => (
          <g key={r}>
            <path d={p.fill} fill="var(--bg)" />
            {hot(r) && <path d={p.line} fill="none" stroke="var(--price)" strokeWidth="2.4" filter="url(#hotglow)" opacity="0.7" />}
            <path d={p.line} fill="none" stroke={hot(r) ? 'var(--price)' : r === h ? 'var(--flow)' : 'var(--fg)'} strokeOpacity={hot(r) || r === h ? 1 : 0.5} strokeWidth={r === h ? 1.6 : hot(r) ? 1.2 : 0.7} />
          </g>
        ))}
        {['00:00', '06:00', '12:00', '18:00', '24:00'].map((t, i) => (
          <text key={t} x={L + (i / 4) * (W - L - R)} y={H - 4} textAnchor={i === 0 ? 'start' : i === 4 ? 'end' : 'middle'} fontSize="10" fill="var(--fg-subtle)" style={{ fontFamily: 'var(--font-mono)' }}>{t}</text>
        ))}
      </svg>
      <div className="pointer-events-none absolute right-2 top-2 rounded-xl border border-border bg-surface/90 px-3 py-2 text-right backdrop-blur" aria-live="polite">
        {h !== null ? (
          <>
            <div className="num text-xs text-fg-muted">{dateLabel(d.dates[h])}</div>
            <div className={`num text-lg ${hot(h) ? 'text-price' : ''}`}>${Math.round(d.peaks[h]).toLocaleString()}<span className="text-2xs text-fg-subtle">/MWh peak</span></div>
          </>
        ) : (
          <>
            <div className="num text-xs text-fg-muted">{dateLabel(d.dates[0])} to {dateLabel(d.dates[rows - 1])}</div>
            <div className="num text-lg text-price">{d.peaks.filter((p) => p >= d.hot_mwh).length} scarcity days</div>
          </>
        )}
      </div>
    </div>
  )
}
