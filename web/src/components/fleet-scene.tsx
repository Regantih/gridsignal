import { Suspense, lazy, useEffect, useState } from 'react'
import { GridField } from '@/components/grid-field'
import { useTheme } from '@/lib/theme'
import { useCalm } from '@/lib/visual-mode'
import type { SceneProps } from '@/components/scene/scene-types'

const TexasScene = lazy(() => import('@/components/scene/texas-scene'))

/**
 * The fleet, live. The 3D flight deck when the viewer wants motion and the device can draw
 * it; the 2D field otherwise (Calm mode, reduced motion, no WebGL, or while the 3D chunk is
 * still loading after first paint). Both are decoration: the canvas carries an aria-label
 * summary and every figure also lives in the page as text.
 */
export function FleetScene(props: Omit<SceneProps, 'theme'> & { label: string }) {
  const { label, height, ...rest } = props
  const [calm] = useCalm()
  const [theme] = useTheme()
  const [ready, setReady] = useState(false)
  useEffect(() => {
    // Wait for first paint before pulling the 3D chunk.
    const id = window.requestIdleCallback ? window.requestIdleCallback(() => setReady(true), { timeout: 800 }) : window.setTimeout(() => setReady(true), 250)
    return () => (window.cancelIdleCallback ? window.cancelIdleCallback(id) : window.clearTimeout(id))
  }, [])
  const flat = (
    <GridField
      devices={props.devices}
      groups={props.groups}
      hit={props.hit}
      focus={props.focus}
      storm={props.storm}
      stormMode={props.stormMode}
      onStorm={props.onStorm}
      onSelect={props.onSelect}
      label={label}
      height={height}
    />
  )
  if (calm || !ready) return <div data-testid="fleet-2d">{flat}</div>
  return (
    <div role="img" aria-label={label} data-testid="fleet-3d">
      <Suspense fallback={flat}>
        <TexasScene {...rest} height={height} theme={theme} />
      </Suspense>
    </div>
  )
}
