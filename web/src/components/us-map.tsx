import { useId } from 'react'
import states from '@/data/us-states.json'
import { project } from '@/components/texas-map'
import { MARKETS, STATUS_LABEL, comedOutline, marketOfState, type Market, type MarketId } from '@/lib/markets'
import { cn } from '@/lib/utils'

/**
 * The flat United States for Calm mode and no-WebGL: lower 48 as quiet shapes, the three
 * markets picked out by status. Same projection as the Texas map so the shapes agree with
 * the 3D scene. Each market is a real button, so keyboard users get the same choice.
 */

interface StateShape {
  id: string
  name: string
  ring: [number, number][]
}
const SHAPES = states as StateShape[]

const toPath = (ring: [number, number][]) =>
  ring
    .map(([lon, lat], i) => {
      const [x, y] = project(lon, lat)
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' ') + ' Z'

const PATHS = SHAPES.map((s) => ({ ...s, d: toPath(s.ring), market: marketOfState(s.name) }))
const COMED = toPath(comedOutline)

const [X0, Y0] = project(-125.5, 49.8)
const [X1, Y1] = project(-66.5, 24.3)
const W = X1 - X0
const H = Y1 - Y0
export const US_VIEWBOX = `${X0.toFixed(0)} ${Y0.toFixed(0)} ${W.toFixed(0)} ${H.toFixed(0)}`
/** Map coordinates to a percentage of the (aspect-locked) container, for HTML labels. */
const pct = (x: number, y: number) => ({ left: `${((x - X0) / W) * 100}%`, top: `${((y - Y0) / H) * 100}%` })

const FILL: Record<Market['status'], string> = {
  live: 'fill-flow/30 stroke-flow',
  planned: 'fill-fg-subtle/20 stroke-fg-subtle',
  equipment: 'fill-surface stroke-price',
}
const [COMED_CX, COMED_CY] = project(-88.9, 40.45)

export function UsMap({
  selected,
  onSelect,
  height = 420,
  className,
}: {
  selected: MarketId
  onSelect?: (id: MarketId) => void
  height?: number
  className?: string
}) {
  const uid = useId()
  return (
    <div className={cn('relative flex w-full items-center justify-center px-2 pb-11 pt-1 sm:px-4', className)} style={{ height }} data-testid="us-map">
      <div className="relative w-full" style={{ aspectRatio: `${W} / ${H}`, maxHeight: height - 52, maxWidth: ((height - 52) * W) / H }}>
      <svg viewBox={US_VIEWBOX} className="h-full w-full" role="group" aria-label="Markets where Base Power operates">
        <defs>
          <pattern id={`${uid}-grid`} width="20" height="20" patternUnits="userSpaceOnUse">
            <path d="M20 0H0V20" fill="none" className="stroke-border" strokeWidth="0.4" />
          </pattern>
        </defs>
        <rect x={X0} y={Y0} width={X1 - X0} height={Y1 - Y0} fill={`url(#${uid}-grid)`} opacity="0.5" />
        {PATHS.filter((p) => !p.market).map((p) => (
          <path key={p.id} d={p.d} className="fill-surface stroke-border-strong" strokeWidth="1" strokeLinejoin="round" />
        ))}
        {PATHS.filter((p) => p.market).map((p) => {
          const m = p.market!
          const on = m.id === selected
          return (
            <g key={p.id}>
              {m.id === 'comed' && <path d={COMED} className="fill-fg-subtle/30" stroke="none" pointerEvents="none" />}
              <path
                d={p.d}
                role="button"
                tabIndex={0}
                aria-label={`${m.name}: ${STATUS_LABEL[m.status]}`}
                aria-pressed={on}
                data-testid={`us-map-${m.id}`}
                onClick={() => onSelect?.(m.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onSelect?.(m.id)
                  }
                }}
                className={cn('cursor-pointer outline-none transition-opacity hover:opacity-90 focus-visible:stroke-[3]', m.id === 'comed' ? 'fill-transparent stroke-fg-subtle' : FILL[m.status], on ? 'opacity-100' : 'opacity-75')}
                strokeWidth={on ? 2 : 1.2}
                strokeDasharray={m.status === 'equipment' ? '4 3' : undefined}
                strokeLinejoin="round"
              />
              {m.id === 'comed' && <path d={COMED} fill="none" className="stroke-fg-subtle" strokeWidth="1.2" strokeDasharray="3 3" pointerEvents="none" />}
            </g>
          )
        })}
        </svg>
        {MARKETS.map((m) => {
          const [cx, cy] = project(m.center[0], m.center[1])
          const on = m.id === selected
          return (
            <div
              key={m.id}
              aria-hidden
              className={cn(
                'pointer-events-none absolute flex -translate-x-1/2 flex-col items-center leading-tight',
                m.id === 'ercot' ? '-translate-y-1/2' : '-translate-y-full pb-1',
                on ? 'opacity-100' : 'opacity-85',
              )}
              style={pct(cx, cy)}
            >
              <span className="rounded-full border border-border bg-surface/90 px-2 py-0.5 text-xs font-semibold text-fg shadow-sm backdrop-blur font-display sm:text-[13px]">
                {m.short}
              </span>
              <span className="num mt-0.5 hidden whitespace-nowrap text-[10px] uppercase tracking-wider text-fg-muted sm:block">
                {m.iso} · {STATUS_LABEL[m.status]}
              </span>
            </div>
          )
        })}
        <span
          aria-hidden
          className="num pointer-events-none absolute hidden -translate-x-1/2 whitespace-nowrap text-[9px] uppercase tracking-wider text-fg-subtle sm:block"
          style={pct(COMED_CX, COMED_CY + 6)}
        >
          approximate ComEd area
        </span>
      </div>
      <ul className="absolute bottom-2 left-2 flex flex-wrap gap-1.5 text-[11px] text-fg-muted sm:bottom-3 sm:left-4 sm:gap-2" aria-hidden>
        {MARKETS.map((m) => (
          <li key={m.id} className="flex items-center gap-1.5 rounded-full border border-border bg-surface/80 px-2 py-0.5 backdrop-blur">
            <span className={cn('size-2 rounded-full', m.status === 'live' ? 'bg-flow' : m.status === 'planned' ? 'bg-fg-subtle' : 'bg-price')} />
            {m.short}: {STATUS_LABEL[m.status]}
          </li>
        ))}
      </ul>
    </div>
  )
}
