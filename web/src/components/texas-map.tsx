import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** A simplified Texas outline (lon, lat). Enough to place a fleet, not a survey. */
const TEXAS: [number, number][] = [
  [-103.04, 36.5], [-100.0, 36.5], [-100.0, 34.56], [-99.2, 34.4], [-98.0, 34.0], [-96.5, 33.8],
  [-95.5, 33.9], [-94.4, 33.6], [-94.04, 33.0], [-94.04, 31.5], [-93.7, 31.0], [-93.6, 30.3],
  [-93.8, 29.7], [-94.7, 29.4], [-95.3, 28.9], [-96.4, 28.4], [-97.2, 27.6], [-97.4, 26.5],
  [-97.15, 25.95], [-97.6, 25.9], [-98.3, 26.1], [-99.1, 26.4], [-99.5, 27.5], [-100.3, 28.3],
  [-100.7, 29.2], [-101.4, 29.75], [-102.4, 29.8], [-103.1, 29.0], [-103.8, 29.3], [-104.5, 29.6],
  [-104.9, 30.6], [-106.2, 31.5], [-106.6, 31.8], [-106.6, 32.0], [-103.06, 32.0], [-103.06, 36.5],
]

const LON = [-106.8, -93.4]
const LAT = [25.7, 36.7]
export const W = 600
export const H = 490

export function project(lon: number, lat: number): [number, number] {
  const x = ((lon - LON[0]) / (LON[1] - LON[0])) * W
  const y = ((LAT[1] - lat) / (LAT[1] - LAT[0])) * H
  return [x, y]
}

const path = TEXAS.map(([lon, lat], i) => {
  const [x, y] = project(lon, lat)
  return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`
}).join(' ') + ' Z'

export function TexasMap({ children, className, label }: { children: ReactNode; className?: string; label: string }) {
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={label}
      className={cn('h-auto w-full max-h-[480px]', className)}
    >
      <path d={path} fill="var(--surface-2)" stroke="var(--border-strong)" strokeWidth={1.5} strokeLinejoin="round" />
      {children}
    </svg>
  )
}
