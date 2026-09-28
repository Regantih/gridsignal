import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Pause, Play } from 'lucide-react'
import type { AuditEvent, PriceRow } from '@/lib/api'
import { priceMwh } from '@/lib/format'

/**
 * The operator's day on one line. The ridge is the real ERCOT settlement price, the lit band
 * is the event window, and every audit entry is pinned at its minute. Drag the playhead, use
 * the arrow keys, or press play to sweep the day: the readout shows the price and whatever
 * happened at that moment. This replays prices and the log; it does not re-run the fleet.
 */
export function NightTimeline({ prices, audit, start, end, now, caption }: { prices: PriceRow[]; audit: AuditEvent[]; start: string; end: string; now: string; caption?: ReactNode }) {
  const W = 1000
  const H = 120
  const pad = 8
  const mins = (iso: string) => Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16))
  const series = useMemo(() => prices.map((p) => ({ m: mins(p.interval_start), v: p.spp })), [prices])
  const max = Math.max(...series.map((s) => s.v), 1)
  const x = (m: number) => (m / 1440) * W
  const y = (v: number) => H - pad - (Math.max(v, 0) / max) * (H - pad * 2 - 14)
  const line = series.map((s, i) => `${i ? 'L' : 'M'}${x(s.m + 7.5).toFixed(1)} ${y(s.v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(1440)} ${H} L0 ${H} Z`
  const [m, setM] = useState(mins(now))
  const [playing, setPlaying] = useState(false)
  const svg = useRef<SVGSVGElement>(null)
  const drag = useRef(false)
  const pins = useMemo(() => audit.map((a) => ({ a, m: mins(a.at) })), [audit])

  useEffect(() => {
    if (!playing) return
    let raf = 0
    let last = performance.now()
    const step = (t: number) => {
      const dt = (t - last) / 1000
      last = t
      setM((v) => {
        const n = v + dt * 120
        if (n >= 1439) { setPlaying(false); return 1439 }
        return n
      })
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  const at = series.find((s) => m >= s.m && m < s.m + 15) ?? series[series.length - 1]
  const near = pins.filter((p) => Math.abs(p.m - m) <= 7)
  const hhmm = `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(Math.floor(m % 60)).padStart(2, '0')}`
  const setFrom = (clientX: number) => {
    const r = svg.current!.getBoundingClientRect()
    setM(Math.max(0, Math.min(1439, ((clientX - r.left) / r.width) * 1440)))
  }
  const inWindow = m >= mins(start) && m < mins(end)

  return (
    <div className="rounded-2xl border border-border bg-surface/70 p-4 md:p-5" data-testid="night-timeline">
      <div className="mb-3 flex flex-wrap items-center gap-x-6 gap-y-2">
        <button
          type="button"
          onClick={() => { if (m >= 1439) setM(0); setPlaying((p) => !p) }}
          className="grid size-9 place-items-center rounded-full bg-fg text-bg"
          aria-label={playing ? 'Pause the replay' : 'Play the day'}
        >
          {playing ? <Pause className="size-4" /> : <Play className="size-4 translate-x-px" />}
        </button>
        <div>
          <div className="eyebrow">The day, replayed</div>
          <div className="num text-2xl font-semibold tabular">
            {hhmm} <span className="text-price">{at ? priceMwh(at.v) : ''}</span>
            {inWindow && <span className="ml-3 rounded-full bg-brand-soft px-2 py-0.5 align-middle text-2xs font-medium text-brand">event window</span>}
          </div>
        </div>
        <div className="min-h-10 min-w-0 flex-1 text-sm text-fg-muted" aria-live="polite">
          {near.length ? (
            near.slice(0, 2).map((p, i) => (
              <div key={i} className="truncate">
                <span className="num text-fg-subtle">{p.a.at.slice(11, 16)}</span> <span className="text-fg">{p.a.summary}</span> <span className="text-fg-subtle">· {p.a.actor}</span>
              </div>
            ))
          ) : (
            <span className="text-fg-subtle">Drag across the ridge. Pins are entries in the audit trail.</span>
          )}
        </div>
      </div>
      <svg
        ref={svg}
        viewBox={`0 0 ${W} ${H + 18}`}
        preserveAspectRatio="none"
        className="h-32 w-full cursor-ew-resize touch-none md:h-36"
        role="slider"
        tabIndex={0}
        aria-label="Time of day"
        aria-valuemin={0}
        aria-valuemax={1439}
        aria-valuenow={Math.round(m)}
        aria-valuetext={`${hhmm}, ${at ? priceMwh(at.v) : ''}`}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight') setM((v) => Math.min(1439, v + 15))
          if (e.key === 'ArrowLeft') setM((v) => Math.max(0, v - 15))
          if (e.key === 'Home') setM(0)
          if (e.key === 'End') setM(1439)
        }}
        onPointerDown={(e) => { drag.current = true; setPlaying(false); (e.target as Element).setPointerCapture?.(e.pointerId); setFrom(e.clientX) }}
        onPointerMove={(e) => drag.current && setFrom(e.clientX)}
        onPointerUp={() => { drag.current = false }}
      >
        <defs>
          <linearGradient id="ridge" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--price)" stopOpacity="0.45" />
            <stop offset="100%" stopColor="var(--price)" stopOpacity="0" />
          </linearGradient>
          <clipPath id="played">
            <rect x="0" y="0" width={x(m)} height={H} />
          </clipPath>
        </defs>
        <rect x={x(mins(start))} y={0} width={x(mins(end)) - x(mins(start))} height={H} fill="var(--brand)" opacity="0.1" />
        <path d={area} fill="url(#ridge)" opacity="0.35" />
        <path d={line} fill="none" stroke="var(--fg-subtle)" strokeWidth="1.2" vectorEffect="non-scaling-stroke" opacity="0.6" />
        <g clipPath="url(#played)">
          <path d={area} fill="url(#ridge)" />
          <path d={line} fill="none" stroke="var(--price)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </g>
        {pins.map((p, i) => (
          <g key={i}>
            <line x1={x(p.m)} x2={x(p.m)} y1={H - 4} y2={H + 4} stroke="var(--fg-muted)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            <circle cx={x(p.m)} cy={H + 9} r="3" fill={p.a.kind.includes('approv') ? 'var(--ok)' : p.a.kind.includes('incident') || p.a.kind.includes('fail') ? 'var(--risk)' : 'var(--brand)'} />
          </g>
        ))}
        {[0, 360, 720, 1080].map((t) => (
          <text key={t} x={x(t) + 3} y={12} fontSize="10" fill="var(--fg-subtle)" style={{ fontFamily: 'var(--font-mono)' }}>
            {String(t / 60).padStart(2, '0')}:00
          </text>
        ))}
        <line x1={x(m)} x2={x(m)} y1={0} y2={H + 14} stroke="var(--fg)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        {at && <circle cx={x(m)} cy={y(at.v)} r="4" fill="var(--price)" stroke="var(--bg)" strokeWidth="2" />}
      </svg>
      {caption && <p className="mt-2 text-2xs text-fg-subtle">{caption}</p>}
    </div>
  )
}
