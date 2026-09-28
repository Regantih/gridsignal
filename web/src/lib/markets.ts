import { useCallback, useSyncExternalStore } from 'react'

/**
 * Where Base Power operates and which of it GridSignal models. Only ERCOT is live: the
 * engine, the prices and every dollar in the app belong to Texas. The other markets are
 * listed so the product is honest about its footprint, never to imply it runs there.
 * GET /api/markets returns the same list (api/markets.py).
 */
export type MarketStatus = 'live' | 'planned' | 'equipment'
export type MarketId = 'ercot' | 'comed' | 'colorado'

export interface Market {
  id: MarketId
  name: string
  short: string
  iso: string
  status: MarketStatus
  note: string
  /** [[lonMin, latMin], [lonMax, latMax]] */
  bounds: [[number, number], [number, number]]
  center: [number, number]
  /** Lower-48 state names this market touches, for the maps. */
  states: string[]
}

export const MARKETS: Market[] = [
  {
    id: 'ercot',
    name: 'Texas',
    short: 'Texas',
    iso: 'ERCOT',
    status: 'live',
    note: 'Live. Prices are real ERCOT data; the fleet is simulated.',
    bounds: [[-106.65, 25.84], [-93.51, 36.5]],
    center: [-99.3, 31.2],
    states: ['Texas'],
  },
  {
    id: 'comed',
    name: 'Illinois, ComEd area',
    short: 'Illinois',
    iso: 'PJM',
    status: 'planned',
    note: 'Coming soon, not modelled yet. Needs PJM prices and a ComEd promise model.',
    bounds: [[-91.51, 36.97], [-87.5, 42.51]],
    center: [-88.6, 41.6],
    states: ['Illinois'],
  },
  {
    id: 'colorado',
    name: 'Colorado',
    short: 'Colorado',
    iso: 'WECC',
    status: 'equipment',
    note: 'Equipment only. No grid programme is modelled.',
    bounds: [[-109.06, 36.99], [-102.04, 41.0]],
    center: [-105.55, 39.0],
    states: ['Colorado'],
  },
]

export const STATUS_LABEL: Record<MarketStatus, string> = {
  live: 'Live',
  planned: 'Coming soon, not modelled yet',
  equipment: 'Equipment only',
}
/** The same statuses, short enough for the top bar. */
export const STATUS_SHORT: Record<MarketStatus, string> = {
  live: 'Live',
  planned: 'Coming soon',
  equipment: 'Equipment only',
}

export const marketById = (id: MarketId): Market => MARKETS.find((m) => m.id === id) ?? MARKETS[0]
export const marketOfState = (state: string): Market | undefined => MARKETS.find((m) => m.states.includes(state))

/**
 * Approximate ComEd service area, northern Illinois (lon, lat). Drawn for orientation
 * only; it is not a modelled footprint.
 */
export const comedOutline: [number, number][] = [
  [-90.64, 42.5], [-87.8, 42.49], [-87.6, 42.0], [-87.53, 41.72], [-87.53, 41.2], [-87.53, 40.95],
  [-88.0, 40.75], [-88.6, 40.7], [-89.2, 40.85], [-89.8, 41.1], [-90.3, 41.5], [-90.6, 41.95],
]

// ------------------------------------------------------------------ selection store

export type MarketView = 'us' | 'market'

let current: MarketId = 'ercot'
let view: MarketView = 'market'
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())
const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function setMarket(id: MarketId) {
  current = id
  view = 'market'
  emit()
}
export function setMarketView(v: MarketView) {
  view = v
  emit()
}

export function useMarket(): { market: Market; view: MarketView; select: (id: MarketId) => void; setView: (v: MarketView) => void } {
  const id = useSyncExternalStore(subscribe, () => current, () => 'ercot' as MarketId)
  const v = useSyncExternalStore(subscribe, () => view, () => 'market' as MarketView)
  const select = useCallback((m: MarketId) => setMarket(m), [])
  const setView = useCallback((x: MarketView) => setMarketView(x), [])
  return { market: marketById(id), view: v, select, setView }
}
