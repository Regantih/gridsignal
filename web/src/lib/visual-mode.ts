import { useCallback, useSyncExternalStore } from 'react'

/**
 * How much motion the viewer wants. "Calm" is the 2D field with no orbit, no particles in
 * flight and no shockwaves. It is on when the person asked for it (toggle, remembered in a
 * cookie), when the OS asks for reduced motion, or when the device cannot run WebGL.
 */
const KEY = 'gs-calm'
const listeners = new Set<() => void>()

function readCookie(): boolean | null {
  try {
    const m = document.cookie.match(new RegExp(`(?:^|; )${KEY}=(on|off)`))
    return m ? m[1] === 'on' : null
  } catch {
    return null
  }
}

let manual: boolean | null = typeof document === 'undefined' ? null : readCookie()

export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

let webgl: boolean | null = null
export function hasWebGL(): boolean {
  if (webgl !== null) return webgl
  try {
    const c = document.createElement('canvas')
    webgl = !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch {
    webgl = false
  }
  return webgl
}

export function isCalm(): boolean {
  if (manual !== null) return manual
  return prefersReducedMotion() || !hasWebGL()
}

function setCalm(on: boolean) {
  manual = on
  try {
    document.cookie = `${KEY}=${on ? 'on' : 'off'}; path=/; max-age=31536000; samesite=lax`
  } catch {
    /* sandboxed frame: the choice still holds for this visit */
  }
  listeners.forEach((l) => l())
}

if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => listeners.forEach((l) => l()))
}

/** [calm, toggle]. Calm means: 2D field, no particles in flight, no camera moves. */
export function useCalm(): [boolean, () => void] {
  const calm = useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    isCalm,
    () => true,
  )
  const toggle = useCallback(() => setCalm(!isCalm()), [])
  return [calm, toggle]
}
