import { useCallback, useSyncExternalStore } from 'react'

export type Theme = 'dark' | 'light'
const KEY = 'gs-theme'
const listeners = new Set<() => void>()

function current(): Theme {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

function set(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  try {
    document.cookie = `${KEY}=${theme}; path=/; max-age=31536000; samesite=lax`
  } catch {
    /* sandboxed frame: the theme still applies for this visit */
  }
  listeners.forEach((l) => l())
}

/** One theme for the whole app; every caller sees the same value. */
export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    current,
    () => 'dark' as Theme,
  )
  const toggle = useCallback(() => set(current() === 'dark' ? 'light' : 'dark'), [])
  return [theme, toggle]
}
