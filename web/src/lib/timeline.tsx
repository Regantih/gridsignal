import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

/**
 * One clock for the evening. Every instrument on Tonight (the map, the dial, the price
 * readout, the audit pins) reads the same minute of the day from here. "Live" means the
 * playhead follows the engine's own clock; scrubbing or playing detaches it. This replays
 * the engine's log and prices; it never re-runs the fleet.
 */

export const minuteOf = (iso: string) => Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16)) + Number(iso.slice(17, 19)) / 60

export type Speed = 60 | 600

interface Timeline {
  /** Minute of day, 0..1439, fractional. */
  m: number
  live: boolean
  playing: boolean
  speed: Speed
  seek: (m: number) => void
  play: () => void
  pause: () => void
  toggle: () => void
  goLive: () => void
  setSpeed: (s: Speed) => void
  /** Bounds of the play range (the event window with a little air either side). */
  range: [number, number]
}

const Ctx = createContext<Timeline | null>(null)

export function TimelineProvider({ now, start, end, children }: { now: string; start: string; end: string; children: ReactNode }) {
  const nowM = minuteOf(now)
  const range = useMemo<[number, number]>(() => [Math.max(0, minuteOf(start) - 30), Math.min(1439, minuteOf(end) + 30)], [start, end])
  const [m, setM] = useState(nowM)
  const [live, setLive] = useState(true)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(60)
  const liveRef = useRef(live)
  liveRef.current = live

  useEffect(() => {
    if (liveRef.current) setM(nowM)
  }, [nowM])

  useEffect(() => {
    if (!playing) return
    let raf = 0
    let last = performance.now()
    const step = (t: number) => {
      const dt = (t - last) / 1000
      last = t
      setM((v) => {
        const n = v + (dt * speed) / 60
        if (n >= range[1]) {
          setPlaying(false)
          return range[1]
        }
        return n
      })
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed, range])

  const seek = useCallback((v: number) => {
    setLive(false)
    setM(Math.max(0, Math.min(1439, v)))
  }, [])
  const play = useCallback(() => {
    setLive(false)
    setM((v) => (v >= range[1] - 0.01 ? range[0] : v))
    setPlaying(true)
  }, [range])
  const pause = useCallback(() => setPlaying(false), [])
  const toggle = useCallback(() => (playing ? pause() : play()), [playing, pause, play])
  const goLive = useCallback(() => {
    setPlaying(false)
    setLive(true)
    setM(nowM)
  }, [nowM])

  const value = useMemo<Timeline>(
    () => ({ m, live, playing, speed, seek, play, pause, toggle, goLive, setSpeed, range }),
    [m, live, playing, speed, seek, play, pause, toggle, goLive, range],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

const fallback: Timeline = {
  m: 0,
  live: true,
  playing: false,
  speed: 60,
  seek: () => {},
  play: () => {},
  pause: () => {},
  toggle: () => {},
  goLive: () => {},
  setSpeed: () => {},
  range: [0, 1439],
}

export function useTimeline(): Timeline {
  return useContext(Ctx) ?? fallback
}
