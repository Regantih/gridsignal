import type { Device } from '@/lib/api'
import type { Storm } from '@/components/grid-field'

/** A thing that just happened, so the scene can stage it once. `at` is performance.now(). */
export interface Moment {
  kind: 'incident' | 'recovery'
  device: string
  at: number
}

export interface SceneProps {
  devices: Device[]
  groups?: Record<string, { feeder: string; ring: string }>
  /** Homes in the pending incident's cohort: they pulse coral and the camera goes to them. */
  focus?: Set<string>
  /** Homes shown dark (under a storm, or dropped at the replay moment). */
  hit?: Set<string>
  storm?: Storm | null
  stormMode?: boolean
  onStorm?: (s: Storm) => void
  onSelect?: (d: Device) => void
  /** Live price, drives the sky: calm blue under $50, amber past $250. */
  priceMwh: number
  moment?: Moment | null
  theme: 'dark' | 'light'
  height: number
  /** Time scale for particle motion, 1 = live. Replay runs faster. */
  tempo?: number
}
