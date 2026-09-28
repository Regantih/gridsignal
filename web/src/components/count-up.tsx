import { useEffect, useRef, useState } from 'react'

/** Animates a number to its new value, so a recovery is felt as money coming back. */
export function useCountUp(value: number, ms = 900) {
  const [shown, setShown] = useState(value)
  const from = useRef(value)
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setShown(value); return }
    const a = from.current
    const t0 = performance.now()
    let raf = 0
    const step = (t: number) => {
      const k = Math.min((t - t0) / ms, 1)
      const e = 1 - Math.pow(1 - k, 3)
      setShown(a + (value - a) * e)
      if (k < 1) raf = requestAnimationFrame(step)
      else from.current = value
    }
    raf = requestAnimationFrame(step)
    return () => { cancelAnimationFrame(raf); from.current = value }
  }, [value, ms])
  return shown
}
