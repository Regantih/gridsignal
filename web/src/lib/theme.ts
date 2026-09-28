import { useCallback, useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'
const KEY = 'gs-theme'

function current(): Theme {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => current())
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    try {
      document.cookie = `${KEY}=${theme}; path=/; max-age=31536000; samesite=lax`
    } catch {
      /* sandboxed frame: the theme still applies for this visit */
    }
  }, [theme])
  const toggle = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), [])
  return [theme, toggle]
}
