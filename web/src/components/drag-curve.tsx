import { useRef } from 'react'
import type { Policy } from '@/lib/api'
import { pct } from '@/lib/format'

const COLOR: Record<Policy, string> = { naive: 'var(--chart-3)', gridsignal: 'var(--chart-2)', gridsignal_auto: 'var(--chart-1)' }
const LABEL: Record<Policy, string> = { naive: 'Naive schedule', gridsignal: 'Approval on every incident', gridsignal_auto: 'With recovery playbook' }

/**
 * The reliability curve is also the control. Drag the handle (or use the arrow keys) along
 * the commitment axis; the dots ride each policy's curve and the target line shows where
 * the promise stops being safe. Values snap to the levels the planner actually tested.
 */
export function DragCurve({
  data,
  ratio,
  onRatio,
  target,
  current,
  recommended,
}: {
  data: { ratio: number; naive: number; gridsignal: number; gridsignal_auto: number }[]
  ratio: number
  onRatio: (r: number) => void
  target: number
  current: number
  recommended: number
}) {
  const W = 640
  const H = 300
  const L = 48
  const R = 16
  const T = 16
  const B = 30
  const xs = data.map((d) => d.ratio)
  const x0 = Math.min(...xs)
  const x1 = Math.max(...xs)
  const y0 = Math.max(0.8, Math.min(0.9, target - 0.05))
  const x = (r: number) => L + ((r - x0) / (x1 - x0 || 1)) * (W - L - R)
  const y = (v: number) => T + (1 - (Math.max(v, y0 - 0.02) - y0) / (1 - y0)) * (H - T - B)
  const svg = useRef<SVGSVGElement>(null)
  const snap = (r: number) => xs.reduce((b, v) => (Math.abs(v - r) < Math.abs(b - r) ? v : b), xs[0])
  const fromX = (clientX: number) => {
    const b = svg.current!.getBoundingClientRect()
    const px = ((clientX - b.left) / b.width) * W
    onRatio(snap(x0 + ((px - L) / (W - L - R)) * (x1 - x0)))
  }
  const at = data.find((d) => d.ratio === snap(ratio)) ?? data[0]
  const idx = xs.indexOf(snap(ratio))
  const drag = useRef(false)
  const policies: Policy[] = ['naive', 'gridsignal', 'gridsignal_auto']
  return (
    <svg
      ref={svg}
      viewBox={`0 0 ${W} ${H}`}
      className="h-auto w-full touch-none select-none"
      role="slider"
      tabIndex={0}
      aria-label="Commitment level"
      aria-valuemin={x0}
      aria-valuemax={x1}
      aria-valuenow={snap(ratio)}
      aria-valuetext={`Commit ${pct(snap(ratio))}: ${pct(at.gridsignal_auto, 1)} of days kept with the playbook`}
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight' || e.key === 'ArrowUp') onRatio(xs[Math.min(idx + 1, xs.length - 1)])
        if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') onRatio(xs[Math.max(idx - 1, 0)])
      }}
      onPointerDown={(e) => { drag.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); fromX(e.clientX) }}
      onPointerMove={(e) => drag.current && fromX(e.clientX)}
      onPointerUp={() => { drag.current = false }}
      style={{ cursor: 'ew-resize' }}
    >
      <defs>
        <linearGradient id="safe" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--ok)" stopOpacity="0.14" />
          <stop offset="100%" stopColor="var(--ok)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect x={L} y={T} width={W - L - R} height={y(target) - T} fill="url(#safe)" />
      {[y0, (y0 + 1) / 2, 1].map((v) => (
        <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="var(--border)" />
          <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill="var(--fg-subtle)" style={{ fontFamily: 'var(--font-mono)' }}>{pct(v, 1)}</text>
        </g>
      ))}
      <line x1={L} x2={W - R} y1={y(target)} y2={y(target)} stroke="var(--fg-muted)" strokeDasharray="5 5" />
      <text x={W - R} y={y(target) - 6} textAnchor="end" fontSize="11" fill="var(--fg-muted)" style={{ fontFamily: 'var(--font-mono)' }}>target {pct(target, 1)}</text>
      {xs.map((r) => (
        <text key={r} x={x(r)} y={H - 10} textAnchor="middle" fontSize="11" fill={r === snap(ratio) ? 'var(--fg)' : 'var(--fg-subtle)'} style={{ fontFamily: 'var(--font-mono)' }}>{pct(r)}</text>
      ))}
      <clipPath id="plot"><rect x={L} y={T - 4} width={W - L - R} height={H - T - B + 4} /></clipPath>
      {policies.map((p) => (
        <path clipPath="url(#plot)" key={p} d={data.map((d, i) => `${i ? 'L' : 'M'}${x(d.ratio)} ${y(d[p])}`).join(' ')} fill="none" stroke={COLOR[p]} strokeWidth={p === 'gridsignal_auto' ? 3 : 1.6} strokeLinejoin="round" />
      ))}
      <line x1={x(current)} x2={x(current)} y1={T} y2={H - B} stroke="var(--fg-subtle)" strokeDasharray="2 4" />
      <text x={x(current) + 4} y={T + 12} fontSize="10" fill="var(--fg-subtle)" style={{ fontFamily: 'var(--font-mono)' }}>today</text>
      <line x1={x(recommended)} x2={x(recommended)} y1={T} y2={H - B} stroke="var(--brand)" strokeOpacity="0.5" />
      <text x={x(recommended) + 4} y={T + 26} fontSize="10" fill="var(--brand)" style={{ fontFamily: 'var(--font-mono)' }}>recommended</text>
      <g style={{ transition: 'transform 180ms cubic-bezier(.2,.8,.2,1)' }} transform={`translate(${x(snap(ratio))} 0)`}>
        <line x1={0} x2={0} y1={T} y2={H - B} stroke="var(--fg)" strokeWidth="1.5" />
        <rect x={-18} y={H - B - 2} width={36} height={8} rx={4} fill="var(--fg)" />
        {policies.map((p) => (
          <circle key={p} cx={0} cy={y(at[p])} r={p === 'gridsignal_auto' ? 6 : 4} fill={COLOR[p]} stroke="var(--bg)" strokeWidth="2" />
        ))}
      </g>
      <g transform={`translate(${L + 8} ${H - B - 58})`}>
        {policies.map((p, i) => (
          <g key={p} transform={`translate(0 ${i * 16})`}>
            <rect width="10" height="3" y="-4" rx="1.5" fill={COLOR[p]} />
            <text x="16" fontSize="11" fill="var(--fg-muted)">{LABEL[p]} <tspan fill="var(--fg)" style={{ fontFamily: 'var(--font-mono)' }}>{pct(at[p], 1)}</tspan></text>
          </g>
        ))}
      </g>
    </svg>
  )
}
