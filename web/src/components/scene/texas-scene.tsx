import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber'
import { Line, OrbitControls } from '@react-three/drei'
import { Bloom, EffectComposer } from '@react-three/postprocessing'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import type { Device } from '@/lib/api'
import { H, W, project, unproject, texasOutline } from '@/components/texas-map'
import type { Storm } from '@/components/grid-field'
import type { SceneProps } from './scene-types'
import usStates from '@/data/us-states.json'
import { MARKETS, STATUS_LABEL, comedOutline, marketById, marketOfState, type Market, type MarketId, type MarketView } from '@/lib/markets'

/**
 * The fleet as a flight deck. Texas is a slab of night with a faint grid; every home is a
 * point of light; each ERCOT load zone is a column whose height is the kW it exports right
 * now; power streams from homes to their column as particles, denser the more kW they send.
 * The sky warms with the live price. An incident is staged: the failed home flashes coral, a
 * shockwave runs through the homes that would fail with it, the camera glides in; on
 * recovery the healthy homes visibly pick up the pace. All of it is decoration: every number
 * is in the DOM next to this canvas.
 */

const UNIT = 10 // map pixels per scene unit
const PX_PER_KM = H / ((36.7 - 25.7) * 111)
const toScene = (lon: number, lat: number): [number, number] => {
  const [px, py] = project(lon, lat)
  return [(px - W / 2) / UNIT, (py - H / 2) / UNIT]
}
const fromScene = (x: number, z: number): [number, number] => unproject(x * UNIT + W / 2, z * UNIT + H / 2)

/** Resolve a CSS custom property (oklch and friends) to a three.js colour via the 2D canvas. */
const swatch = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null
function cssColor(name: string, fallback: string): THREE.Color {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
  if (!swatch) return new THREE.Color(fallback)
  swatch.fillStyle = '#000'
  swatch.fillStyle = raw
  const v = swatch.fillStyle
  return new THREE.Color(typeof v === 'string' && v.startsWith('#') ? v : fallback)
}

interface Palette {
  flow: THREE.Color
  risk: THREE.Color
  warn: THREE.Color
  price: THREE.Color
  fg: THREE.Color
  subtle: THREE.Color
  land: THREE.Color
  side: THREE.Color
  ink: THREE.Color
  skyCalm: THREE.Color
  skyHot: THREE.Color
  grid: string
  dark: boolean
}

/**
 * Two scene palettes. Dark is the night deck: ink ground, deep blue slab, light as the
 * signal. Light is paper: a warm ground close to --bg, Texas as a darker warm stone with
 * a crisp ink edge, and the deeper light-theme cyan for anything that carries power.
 */
function palette(theme: 'dark' | 'light'): Palette {
  const dark = theme === 'dark'
  return {
    flow: cssColor('--flow', dark ? '#6ee7f9' : '#0e7490'),
    risk: cssColor('--risk', dark ? '#f87171' : '#b91c1c'),
    warn: cssColor('--warn', '#fbbf24'),
    price: cssColor('--price', '#fbbf24'),
    fg: cssColor('--fg', dark ? '#f1f5f9' : '#1e293b'),
    subtle: cssColor('--fg-subtle', '#94a3b8'),
    land: new THREE.Color(dark ? '#111d3b' : '#cfc2ac'),
    side: new THREE.Color(dark ? '#070d1f' : '#a89a80'),
    ink: cssColor('--bg', dark ? '#070b18' : '#f7f4ee'),
    skyCalm: cssColor('--bg', dark ? '#070b18' : '#f7f4ee'),
    skyHot: new THREE.Color(dark ? '#1c1408' : '#f1dfc0'),
    grid: dark ? 'rgba(140,170,220,0.16)' : 'rgba(70,55,30,0.2)',
    dark,
  }
}

const ease = (t: number) => 1 - Math.pow(1 - Math.min(Math.max(t, 0), 1), 3)
const priceHeat = (p: number) => Math.pow(Math.min(Math.max((p - 50) / 200, 0), 1), 2)

// ------------------------------------------------------------------ land

function Land({ pal }: { pal: Palette }) {
  const geo = useMemo(() => {
    const shape = new THREE.Shape()
    texasOutline.forEach(([lon, lat], i) => {
      const [x, z] = toScene(lon, lat)
      if (i === 0) shape.moveTo(x, -z)
      else shape.lineTo(x, -z)
    })
    shape.closePath()
    const g = new THREE.ExtrudeGeometry(shape, { depth: 1.1, bevelEnabled: true, bevelThickness: 0.08, bevelSize: 0.08, bevelSegments: 2 })
    g.rotateX(-Math.PI / 2)
    g.translate(0, -1.1, 0)
    return g
  }, [])
  const top = useMemo(() => {
    const c = document.createElement('canvas')
    c.width = 1024
    c.height = 1024
    const ctx = c.getContext('2d')!
    ctx.fillStyle = `#${pal.land.getHexString()}`
    ctx.fillRect(0, 0, 1024, 1024)
    // Relief: soft blotches so the slab is not flat.
    for (let i = 0; i < 90; i++) {
      const x = Math.random() * 1024
      const y = Math.random() * 1024
      const r = 60 + Math.random() * 160
      const g = ctx.createRadialGradient(x, y, 0, x, y, r)
      const tint = pal.dark ? '255,255,255' : '60,45,20'
      g.addColorStop(0, `rgba(${tint},${0.03 + Math.random() * 0.035})`)
      g.addColorStop(1, `rgba(${tint},0)`)
      ctx.fillStyle = g
      ctx.fillRect(x - r, y - r, r * 2, r * 2)
    }
    ctx.strokeStyle = pal.grid
    ctx.lineWidth = 1
    for (let i = 0; i <= 1024; i += 32) {
      ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, 1024); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(1024, i); ctx.stroke()
    }
    const t = new THREE.CanvasTexture(c)
    t.wrapS = t.wrapT = THREE.RepeatWrapping
    t.repeat.set(1 / (W / UNIT), 1 / (H / UNIT))
    t.colorSpace = THREE.SRGBColorSpace
    return t
  }, [pal])
  const edge = useMemo(() => toEdge(texasOutline, 0.04), [])
  return (
    <group>
      <mesh geometry={geo} receiveShadow>
        <meshStandardMaterial attach="material-0" map={top} roughness={0.9} metalness={0.05} />
        <meshStandardMaterial attach="material-1" color={pal.side} roughness={0.95} />
      </mesh>
      <Line points={edge} color={`#${pal.fg.getHexString()}`} lineWidth={pal.dark ? 0.8 : 1.4} transparent opacity={pal.dark ? 0.35 : 0.8} />
    </group>
  )
}

// ------------------------------------------------------------------ ground

/** Deep ink under everything: a faint grid that fades out towards the edges (vignette). */
function Ground({ pal }: { pal: Palette }) {
  const tex = useMemo(() => {
    const c = document.createElement('canvas')
    c.width = 2048
    c.height = 2048
    const ctx = c.getContext('2d')!
    ctx.fillStyle = `#${pal.ink.getHexString()}`
    ctx.fillRect(0, 0, 2048, 2048)
    ctx.strokeStyle = pal.grid
    ctx.lineWidth = 1
    for (let i = 0; i <= 2048; i += 64) {
      ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, 2048); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(2048, i); ctx.stroke()
    }
    const edge = pal.ink.clone().lerp(pal.side, 0.6).getHexString()
    const v = ctx.createRadialGradient(1024, 1024, 420, 1024, 1024, 1100)
    v.addColorStop(0, `#${edge}00`)
    v.addColorStop(1, `#${edge}ff`)
    ctx.fillStyle = v
    ctx.fillRect(0, 0, 2048, 2048)
    const t = new THREE.CanvasTexture(c)
    t.colorSpace = THREE.SRGBColorSpace
    t.anisotropy = 4
    return t
  }, [pal])
  const [cx, cz] = toScene(-96.5, 38.5)
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[cx, -1.15, cz]}>
      <planeGeometry args={[900, 900]} />
      <meshBasicMaterial map={tex} toneMapped={false} />
    </mesh>
  )
}

// ------------------------------------------------------------------ the rest of the country

interface StateShape {
  id: string
  name: string
  ring: [number, number][]
}
const STATES = (usStates as StateShape[]).filter((s) => s.name !== 'Texas')

function ringGeometry(ring: [number, number][], depth: number) {
  const shape = new THREE.Shape()
  ring.forEach(([lon, lat], i) => {
    const [x, z] = toScene(lon, lat)
    if (i === 0) shape.moveTo(x, -z)
    else shape.lineTo(x, -z)
  })
  shape.closePath()
  const g = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false })
  g.rotateX(-Math.PI / 2)
  g.translate(0, -depth, 0)
  return g
}

const marketEdge = (m: Market, pal: Palette) => (m.status === 'live' ? pal.flow : m.status === 'planned' ? pal.subtle : pal.price)
const toEdge = (ring: [number, number][], y: number) => {
  const pts = ring.map(([lon, lat]) => { const [x, z] = toScene(lon, lat); return new THREE.Vector3(x, y, z) })
  return [...pts, pts[0]]
}

/**
 * The lower 48 as barely-there slabs in the same ink as the ground, so Texas reads as one
 * market among others. Markets are drawn the same way regardless of status: an outline in
 * the status colour and a label; only the ComEd service area in Illinois gets a quiet fill,
 * because that is the region in question, not the whole state. Nothing glows where nothing
 * is modelled. In the Texas view the other markets fade almost out so they never intrude.
 */
function Country({ pal, theme, selected, view, showLabels, labels, onMarket }: { pal: Palette; theme: 'dark' | 'light'; selected: MarketId; view: MarketView; showLabels: boolean; labels: React.RefObject<Map<MarketId, HTMLDivElement>>; onMarket?: (id: MarketId) => void }) {
  const dark = theme === 'dark'
  const focusTexas = view === 'market' && selected === 'ercot'
  const geos = useMemo(
    () =>
      STATES.map((s) => {
        const m = marketOfState(s.name)
        return { s, m, geo: ringGeometry(s.ring, 0.2), edge: toEdge(s.ring, 0.03) }
      }),
    [],
  )
  const comed = useMemo(() => ({ edge: toEdge(comedOutline, 0.05), geo: ringGeometry(comedOutline, 0.22) }), [])
  const quiet = pal.ink.clone().lerp(pal.land, dark ? 0.35 : 0.3)
  const neighbourAlpha = focusTexas ? (dark ? 0.1 : 0.16) : dark ? 0.28 : 0.45
  return (
    <group>
      {geos.map(({ s, m, geo, edge }) => {
        // In the Texas view every other market is just another neighbour: no status colour.
        const highlight = m && !(focusTexas && m.id !== selected) ? m : null
        return (
          <group key={s.id}>
            <mesh
              geometry={geo}
              onClick={m && onMarket ? (e) => { e.stopPropagation(); onMarket(m.id) } : undefined}
              onPointerOver={m ? (e) => { e.stopPropagation(); document.body.style.cursor = 'pointer' } : undefined}
              onPointerOut={m ? () => { document.body.style.cursor = '' } : undefined}
            >
              <meshStandardMaterial color={quiet} roughness={0.95} />
            </mesh>
            <Line
              points={edge}
              color={`#${(highlight ? marketEdge(highlight, pal) : pal.subtle).getHexString()}`}
              lineWidth={highlight ? (highlight.id === selected ? 1.8 : 1.2) : 0.6}
              dashed={highlight?.status === 'equipment'}
              dashSize={0.9}
              gapSize={0.5}
              transparent
              opacity={highlight ? (highlight.id === selected ? 1 : 0.75) : neighbourAlpha}
            />
          </group>
        )
      })}
      {!focusTexas && (
        <>
          <mesh geometry={comed.geo}>
            <meshStandardMaterial color={quiet.clone().lerp(pal.subtle, dark ? 0.35 : 0.3)} roughness={0.95} />
          </mesh>
          <Line points={comed.edge} color={`#${pal.subtle.getHexString()}`} lineWidth={1.2} dashed dashSize={0.6} gapSize={0.4} />
        </>
      )}
      {showLabels && <LabelProjector labels={labels} selected={selected} view={view} />}
    </group>
  )
}

/**
 * Where each market label sits in the world; the DOM labels live outside the canvas. In the
 * US view a label floats over the market centre. When the camera is on one market the label
 * moves to that market's northern edge so it never covers the area being shown.
 */
const LABEL_ANCHORS = MARKETS.map((m) => {
  const [x, z] = toScene(m.center[0], m.center[1])
  const [ex, ez] = toScene((m.bounds[0][0] + m.bounds[1][0]) / 2, m.bounds[0][1] - 0.2)
  return { id: m.id, pos: new THREE.Vector3(x, m.status === 'live' ? 9 : 2.5, z), edge: new THREE.Vector3(ex, 0.4, ez) }
})

/** Projects the anchors to the card each frame and moves the DOM labels directly (no React state). */
function LabelProjector({ labels, selected, view }: { labels: React.RefObject<Map<MarketId, HTMLDivElement>>; selected: MarketId; view: MarketView }) {
  const { camera, size } = useThree()
  const v = useMemo(() => new THREE.Vector3(), [])
  useFrame(() => {
    for (const a of LABEL_ANCHORS) {
      const el = labels.current?.get(a.id)
      if (!el) continue
      const below = view === 'market' && a.id === selected
      v.copy(below ? a.edge : a.pos).project(camera)
      const behind = v.z > 1
      el.style.transform = `${below ? 'translate(-50%, 0) translate(0, 6px)' : 'translate(-50%, -100%) translate(0, -6px)'} translate(${((v.x + 1) / 2) * size.width}px, ${((1 - v.y) / 2) * size.height}px)`
      el.style.opacity = behind ? '0' : '1'
    }
  })
  useEffect(
    () => () => {
      labels.current?.forEach((el) => { el.style.opacity = '0' })
    },
    [labels],
  )
  return null
}

function MarketLabels({ labels }: { labels: React.RefObject<Map<MarketId, HTMLDivElement>> }) {
  return (
    <div className="pointer-events-none absolute inset-0 z-[5] overflow-hidden" aria-hidden>
      {MARKETS.map((m) => (
        <div
          key={m.id}
          ref={(el) => {
            if (el) labels.current?.set(m.id, el)
            else labels.current?.delete(m.id)
          }}
          className="absolute left-0 top-0 whitespace-nowrap rounded-full border border-border bg-surface/90 px-2.5 py-1 text-center text-[11px] leading-tight text-fg shadow backdrop-blur transition-opacity duration-300"
          style={{ opacity: 0 }}
        >
          <span className="font-display font-semibold">{m.short}</span>
          <span className="num ml-1.5 hidden text-fg-muted sm:inline">{m.iso} · {STATUS_LABEL[m.status]}</span>
          {m.id === 'comed' && <span className="num ml-1.5 hidden text-fg-subtle md:inline">· approximate ComEd area</span>}
        </div>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ fleet

interface Node {
  d: Device
  x: number
  z: number
}

interface Hub {
  zone: string
  x: number
  z: number
  kw: number
}

function useLayout(devices: Device[]) {
  return useMemo(() => {
    const nodes: Node[] = devices.map((d) => {
      const [x, z] = toScene(d.lon, d.lat)
      return { d, x, z }
    })
    const acc: Record<string, { x: number; z: number; n: number; kw: number }> = {}
    for (const n of nodes) {
      const h = (acc[n.d.zone] ??= { x: 0, z: 0, n: 0, kw: 0 })
      h.x += n.x
      h.z += n.z
      h.n += 1
      h.kw += Math.max(n.d.assigned_kw, 0)
    }
    const hubs: Hub[] = Object.entries(acc).map(([zone, h]) => ({ zone, x: h.x / h.n, z: h.z / h.n, kw: h.kw }))
    const maxKw = Math.max(...hubs.map((h) => h.kw), 1)
    return { nodes, hubs, maxKw }
  }, [devices])
}

const hubHeight = (kw: number, maxKw: number) => 1 + 5.5 * Math.sqrt(kw / maxKw)

function Hubs({ hubs, maxKw, pal }: { hubs: Hub[]; maxKw: number; pal: Palette }) {
  return (
    <group>
      {hubs.map((h) => {
        const height = hubHeight(h.kw, maxKw)
        return (
          <group key={h.zone} position={[h.x, 0, h.z]}>
            <mesh position={[0, height / 2, 0]}>
              <cylinderGeometry args={[0.22, 0.4, height, 24, 1, true]} />
              <meshBasicMaterial color={pal.flow} transparent opacity={pal.dark ? 0.55 : 0.75} toneMapped={false} side={THREE.DoubleSide} depthWrite={false} />
            </mesh>
            <mesh position={[0, height / 2, 0]}>
              <cylinderGeometry args={[0.07, 0.12, height, 12]} />
              <meshBasicMaterial color={pal.dark ? pal.flow.clone().multiplyScalar(2.2) : pal.flow.clone().multiplyScalar(0.7)} toneMapped={false} />
            </mesh>
            <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}>
              <ringGeometry args={[0.7, 1.05, 48]} />
              <meshBasicMaterial color={pal.flow} transparent opacity={pal.dark ? 0.5 : 0.8} toneMapped={false} depthWrite={false} />
            </mesh>
          </group>
        )
      })}
    </group>
  )
}

const tmpObj = new THREE.Object3D()
const tmpCol = new THREE.Color()

interface Stage {
  moment: SceneProps['moment']
  focus?: Set<string>
  hit?: Set<string>
  hover: string | null
  tempo: number
}

function Homes({
  nodes,
  hubs,
  maxKw,
  pal,
  peersOf,
  stage,
  onHover,
  onSelect,
}: {
  nodes: Node[]
  hubs: Hub[]
  maxKw: number
  pal: Palette
  peersOf: (id: string) => Set<string>
  stage: React.RefObject<Stage>
  onHover: (d: Device | null, e?: ThreeEvent<PointerEvent>) => void
  onSelect?: (d: Device) => void
}) {
  const mesh = useRef<THREE.InstancedMesh>(null)
  const dense = nodes.length > 300
  const r = dense ? 0.11 : nodes.length > 100 ? 0.17 : 0.24
  const index = useMemo(() => new Map(nodes.map((n, i) => [n.d.device_id, i])), [nodes])
  const hubOf = useMemo(() => Object.fromEntries(hubs.map((h) => [h.zone, h])), [hubs])

  // Particle streams: one quadratic curve per exporting home, a few phases each.
  const streams = useMemo(() => {
    const out: { i: number; zone: string; p0: THREE.Vector3; p1: THREE.Vector3; p2: THREE.Vector3; n: number; speed: number }[] = []
    nodes.forEach((n, i) => {
      const h = hubOf[n.d.zone]
      if (!h || n.d.status === 'offline' || n.d.assigned_kw <= 0) return
      const p0 = new THREE.Vector3(n.x, 0.25, n.z)
      const p2 = new THREE.Vector3(h.x, hubHeight(h.kw, maxKw) * 0.9, h.z)
      const dist = p0.distanceTo(p2)
      const p1 = p0.clone().lerp(p2, 0.5).add(new THREE.Vector3(0, 1.5 + dist * 0.28, 0))
      out.push({ i, zone: n.d.zone, p0, p1, p2, n: Math.min(4, 1 + Math.floor(n.d.assigned_kw / 3)), speed: 0.08 + Math.min(n.d.assigned_kw, 12) * 0.012 })
    })
    const budget = 3000
    const total = out.reduce((a, s) => a + s.n, 0)
    if (total > budget) {
      const keep = Math.ceil(out.length / (budget / (total / out.length)))
      return out.filter((_, k) => k % keep === 0)
    }
    return out
  }, [nodes, hubOf, maxKw])
  const count = streams.reduce((a, s) => a + s.n, 0)
  const positions = useMemo(() => new Float32Array(Math.max(count, 1) * 3), [count])
  const points = useRef<THREE.Points>(null)

  const [hoverId, setHoverId] = useState<number | null>(null)

  useFrame(({ clock }) => {
    const m = mesh.current
    if (!m) return
    const s = stage.current
    const t = clock.elapsedTime
    const now = performance.now()
    const mo = s.moment
    const age = mo ? (now - mo.at) / 1000 : Infinity
    const origin = mo ? nodes[index.get(mo.device) ?? -1] : undefined
    const peers = mo ? peersOf(mo.device) : null

    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i]
      const id = n.d.device_id
      const isHit = s.hit?.has(id)
      const isFocus = s.focus?.has(id)
      const down = n.d.status === 'offline' || isHit
      let col = pal.fg
      if (n.d.status === 'degraded') col = pal.warn
      if (n.d.status === 'unavailable') col = pal.subtle
      if (down || isFocus) col = pal.risk
      let scale = 1
      let lift = 0
      if (down || isFocus) scale = 1.15 + 0.35 * Math.sin(t * 4 + i)
      if (hoverId === i) scale = 1.8
      // Shockwave from the failed home through its feeder and ring peers.
      if (mo && origin && peers && age < 2.4) {
        const dx = n.x - origin.x
        const dz = n.z - origin.z
        const dist = Math.hypot(dx, dz)
        const front = age * 9
        if (i === (index.get(mo.device) ?? -1)) {
          scale = 1 + 2.2 * Math.max(0, 1 - age / 1.2)
          lift = 0.6 * Math.max(0, 1 - age / 1.2)
          col = mo.kind === 'incident' ? pal.risk : pal.flow
        } else if (peers.has(id) && Math.abs(dist - front) < 1.6) {
          scale = 1 + 1.4 * (1 - Math.abs(dist - front) / 1.6)
          col = mo.kind === 'incident' ? pal.price : pal.flow
        }
      }
      tmpObj.position.set(n.x, 0.12 + lift, n.z)
      tmpObj.scale.setScalar(scale)
      tmpObj.updateMatrix()
      m.setMatrixAt(i, tmpObj.matrix)
      tmpCol.copy(col)
      if (col === pal.fg) tmpCol.multiplyScalar(0.72)
      if (down) tmpCol.multiplyScalar(0.7)
      m.setColorAt(i, tmpCol)
    }
    m.instanceMatrix.needsUpdate = true
    if (m.instanceColor) m.instanceColor.needsUpdate = true

    // Particles.
    const pts = points.current
    if (pts) {
      let k = 0
      const recoverZone = mo?.kind === 'recovery' && age < 4 ? origin?.d.zone : null
      const tt = t * s.tempo
      for (const st of streams) {
        const n = nodes[st.i]
        const hidden = s.hit?.has(n.d.device_id) || s.focus?.has(n.d.device_id)
        const boost = recoverZone === st.zone ? 2.6 : 1
        for (let j = 0; j < st.n; j++) {
          if (hidden) {
            positions[k++] = 0; positions[k++] = -100; positions[k++] = 0
            continue
          }
          const ph = (tt * st.speed * boost + j / st.n + (st.i % 11) / 11) % 1
          const u = 1 - ph
          positions[k++] = u * u * st.p0.x + 2 * u * ph * st.p1.x + ph * ph * st.p2.x
          positions[k++] = u * u * st.p0.y + 2 * u * ph * st.p1.y + ph * ph * st.p2.y
          positions[k++] = u * u * st.p0.z + 2 * u * ph * st.p1.z + ph * ph * st.p2.z
        }
      }
      pts.geometry.attributes.position.needsUpdate = true
    }
  })

  useEffect(() => {
    // Colours are written per frame; set instanceColor once so the attribute exists.
    const m = mesh.current
    if (!m) return
    for (let i = 0; i < nodes.length; i++) m.setColorAt(i, pal.fg)
    if (m.instanceColor) m.instanceColor.needsUpdate = true
  }, [nodes, pal])

  return (
    <group>
      <instancedMesh
        ref={mesh}
        args={[undefined, undefined, nodes.length]}
        onPointerMove={(e) => {
          e.stopPropagation()
          if (e.instanceId === undefined) return
          setHoverId(e.instanceId)
          onHover(nodes[e.instanceId].d, e)
        }}
        onPointerOut={() => {
          setHoverId(null)
          onHover(null)
        }}
        onClick={(e) => {
          if (e.instanceId !== undefined) onSelect?.(nodes[e.instanceId].d)
        }}
      >
        <sphereGeometry args={[r, 10, 8]} />
        <meshBasicMaterial toneMapped={false} />
      </instancedMesh>
      <points ref={points} frustumCulled={false}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color={pal.dark ? pal.flow.clone().multiplyScalar(1.6) : pal.flow.clone().multiplyScalar(0.85)}
          size={dense ? 0.22 : 0.34}
          sizeAttenuation
          transparent
          opacity={0.9}
          depthWrite={false}
          blending={pal.dark ? THREE.AdditiveBlending : THREE.NormalBlending}
          toneMapped={false}
        />
      </points>
    </group>
  )
}

// ------------------------------------------------------------------ shockwave ring

function Shockwave({ nodes, stage, pal }: { nodes: Node[]; stage: React.RefObject<Stage>; pal: Palette }) {
  const ring = useRef<THREE.Mesh>(null)
  const mat = useRef<THREE.MeshBasicMaterial>(null)
  const index = useMemo(() => new Map(nodes.map((n, i) => [n.d.device_id, i])), [nodes])
  useFrame(() => {
    const r = ring.current
    const m = mat.current
    if (!r || !m) return
    const mo = stage.current.moment
    const n = mo ? nodes[index.get(mo.device) ?? -1] : undefined
    const age = mo ? (performance.now() - mo.at) / 1000 : Infinity
    if (!mo || !n || age > 2.4) {
      r.visible = false
      return
    }
    r.visible = true
    r.position.set(n.x, 0.06, n.z)
    const s = 0.2 + age * 9
    r.scale.setScalar(s)
    m.opacity = 0.9 * (1 - age / 2.4)
    m.color.copy(mo.kind === 'incident' ? pal.risk : pal.flow)
  })
  return (
    <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} visible={false}>
      <ringGeometry args={[0.92, 1, 64]} />
      <meshBasicMaterial ref={mat} transparent toneMapped={false} depthWrite={false} side={THREE.DoubleSide} />
    </mesh>
  )
}

// ------------------------------------------------------------------ storm

const CLOUD_BASE = 3.6
const CLOUD_DEPTH = 1.6
const RAIN_N = 260
const RAIN_LEN = 0.55

/**
 * A storm you can read from across the room: a dark slate cloud cap on a translucent rain
 * column, rain as falling streaks, a lightning flicker inside the cloud that lights the
 * ground under it, and a coral rim on the ground marking the radius the engine will use.
 * `animate` false freezes rain and lightning (the 2D field replaces this scene entirely under
 * Calm mode and reduced motion).
 */
function StormCell({ storm, pal, animate }: { storm: Storm; pal: Palette; animate: boolean }) {
  const [x, z] = toScene(storm.lon, storm.lat)
  const radius = (storm.radius_km * PX_PER_KM) / UNIT
  const light = useRef<THREE.PointLight>(null)
  const flash = useRef<THREE.MeshBasicMaterial>(null)
  const rain = useMemo(() => {
    const arr = new Float32Array(RAIN_N * 6)
    for (let i = 0; i < RAIN_N; i++) {
      const a = Math.random() * Math.PI * 2
      const rr = Math.sqrt(Math.random()) * radius * 0.96
      const px = Math.cos(a) * rr
      const pz = Math.sin(a) * rr
      const y = Math.random() * CLOUD_BASE
      arr.set([px, y, pz, px, y + RAIN_LEN, pz], i * 6)
    }
    return arr
  }, [radius])
  const streaks = useRef<THREE.LineSegments>(null)
  const next = useRef(0)
  useFrame(({ clock }, dt) => {
    const t = clock.elapsedTime
    if (light.current && flash.current) {
      if (animate && t > next.current) {
        light.current.intensity = pal.dark ? 60 + Math.random() * 80 : 30 + Math.random() * 40
        flash.current.opacity = 0.55
        next.current = t + 0.7 + Math.random() * 2.4
      } else {
        light.current.intensity *= 0.8
        flash.current.opacity *= 0.78
      }
    }
    if (streaks.current && animate) {
      const a = streaks.current.geometry.attributes.position as THREE.BufferAttribute
      const arr = a.array as Float32Array
      const fall = dt * 7
      for (let i = 0; i < arr.length; i += 6) {
        let y = arr[i + 1] - fall
        if (y < -RAIN_LEN) y = CLOUD_BASE
        arr[i + 1] = y
        arr[i + 4] = y + RAIN_LEN
      }
      a.needsUpdate = true
    }
  })
  const slate = pal.dark ? '#141a2b' : '#2b3345'
  const rainColor = pal.dark ? '#8ea3cc' : '#1f2738'
  const risk = `#${pal.risk.getHexString()}`
  return (
    <group position={[x, 0, z]}>
      {/* rain column: an open translucent shell from the ground to the cloud base */}
      <mesh position={[0, CLOUD_BASE / 2, 0]}>
        <cylinderGeometry args={[radius * 0.97, radius * 0.9, CLOUD_BASE, 56, 1, true]} />
        <meshStandardMaterial color={slate} transparent opacity={pal.dark ? 0.3 : 0.16} roughness={1} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      {/* cloud cap */}
      <mesh position={[0, CLOUD_BASE + CLOUD_DEPTH / 2, 0]}>
        <cylinderGeometry args={[radius * 1.08, radius * 0.98, CLOUD_DEPTH, 56]} />
        <meshStandardMaterial color={slate} roughness={1} />
      </mesh>
      <mesh position={[0, CLOUD_BASE + CLOUD_DEPTH + 0.35, 0]} scale={[1, 0.45, 1]}>
        <sphereGeometry args={[radius * 0.95, 40, 20, 0, Math.PI * 2, 0, Math.PI / 2]} />
        <meshStandardMaterial color={slate} roughness={1} />
      </mesh>
      {/* lightning: a flash inside the cloud plus a light on the ground */}
      <mesh position={[0, CLOUD_BASE + CLOUD_DEPTH / 2, 0]}>
        <cylinderGeometry args={[radius * 1.09, radius * 0.99, CLOUD_DEPTH + 0.02, 56]} />
        <meshBasicMaterial ref={flash} color={pal.dark ? '#dbe7ff' : '#f6f0ff'} transparent opacity={0} toneMapped={false} depthWrite={false} />
      </mesh>
      <pointLight ref={light} position={[0, CLOUD_BASE - 0.2, 0]} color="#e6efff" intensity={0} distance={radius * 4} decay={2} />
      {/* rain */}
      <lineSegments ref={streaks}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[rain, 3]} />
        </bufferGeometry>
        <lineBasicMaterial color={rainColor} transparent opacity={pal.dark ? 0.45 : 0.85} depthWrite={false} />
      </lineSegments>
      {/* ground: shade under the cell and the coral radius rim */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.1, 0]}>
        <circleGeometry args={[radius, 64]} />
        <meshBasicMaterial color={pal.dark ? '#000' : '#2b3345'} transparent opacity={pal.dark ? 0.5 : 0.35} depthWrite={false} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.12, 0]}>
        <ringGeometry args={[radius * 0.96, radius, 96]} />
        <meshBasicMaterial color={risk} toneMapped={false} depthWrite={false} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.11, 0]}>
        <ringGeometry args={[radius * 1.0, radius * 1.16, 96]} />
        <meshBasicMaterial color={risk} transparent opacity={0.35} toneMapped={false} depthWrite={false} />
      </mesh>
    </group>
  )
}

function DrawPlane({ onStorm, setDraft, setDrawing }: { onStorm?: (s: Storm) => void; setDraft: (s: Storm | null) => void; setDrawing: (b: boolean) => void }) {
  const start = useRef<{ x: number; z: number } | null>(null)
  const draft = useRef<Storm | null>(null)
  return (
    <mesh
      rotation={[-Math.PI / 2, 0, 0]}
      position={[0, 0.02, 0]}
      onPointerDown={(e) => {
        e.stopPropagation()
        start.current = { x: e.point.x, z: e.point.z }
        const [lon, lat] = fromScene(e.point.x, e.point.z)
        draft.current = { lat, lon, radius_km: 10 }
        setDraft(draft.current)
        setDrawing(true)
        ;(e.target as Element).setPointerCapture?.(e.pointerId)
      }}
      onPointerMove={(e) => {
        if (!start.current) return
        const [lon, lat] = fromScene(start.current.x, start.current.z)
        const r = (Math.hypot(e.point.x - start.current.x, e.point.z - start.current.z) * UNIT) / PX_PER_KM
        draft.current = { lat, lon, radius_km: Math.max(10, Math.min(r, 600)) }
        setDraft(draft.current)
      }}
      onPointerUp={() => {
        if (start.current && draft.current) onStorm?.(draft.current)
        start.current = null
        draft.current = null
        setDraft(null)
        setDrawing(false)
      }}
    >
      <planeGeometry args={[W / UNIT + 20, H / UNIT + 20]} />
      <meshBasicMaterial visible={false} />
    </mesh>
  )
}

// ------------------------------------------------------------------ camera and sky

/** Where the camera rests for a market or the whole country: look-at point and distance. */
const US_POLAR = 0.55
const MARKET_POLAR = 0.95
const FOV = 36
const US_BOUNDS: [[number, number], [number, number]] = [[-124.8, 24.5], [-66.9, 49.4]]
// Rough hull of the lower 48, so the fit hugs the land and not the empty corners of its bounding box.
const US_HULL: [number, number][] = [
  [-124.8, 48.4], [-124.4, 40.4], [-117.1, 32.5], [-106.5, 31.8], [-97.4, 25.9], [-81.0, 25.1],
  [-80.0, 32.0], [-75.5, 35.3], [-70.0, 41.5], [-66.9, 44.8], [-68.2, 47.4], [-95.2, 49.4], [-123.3, 49.0],
]

/**
 * Frame a lon/lat box so it fills the card and sits in its centre at any aspect ratio.
 * A scratch camera is placed at the wanted pitch, the box corners (ground and a little
 * above it, for columns and labels) are projected, and the target and distance are refined
 * until the projected box is centred and padded. This is the same for the whole country,
 * Texas, Illinois or Colorado, so a phone card gets the map in its middle and a wide card
 * gets it edge to edge.
 */
function frameFor(market: MarketId, view: MarketView, aspect: number, theta = 0, phi?: number): { target: THREE.Vector3; dist: number } {
  const us = view === 'us'
  const [[lon0, lat0], [lon1, lat1]] = us ? US_BOUNDS : marketById(market).bounds
  const polar = phi ?? (us ? US_POLAR : MARKET_POLAR)
  const lift = us ? 1.5 : market === 'ercot' ? 4 : 1.5
  const samples: [number, number][] = us ? US_HULL : []
  if (!us) {
    for (const lon of [lon0, (lon0 + lon1) / 2, lon1]) for (const lat of [lat0, (lat0 + lat1) / 2, lat1]) samples.push([lon, lat])
  }
  const corners: THREE.Vector3[] = []
  for (const [lon, lat] of samples) {
    const [x, z] = toScene(lon, lat)
    corners.push(new THREE.Vector3(x, 0, z), new THREE.Vector3(x, lift, z))
  }
  const [cx0, cz0] = toScene((lon0 + lon1) / 2, (lat0 + lat1) / 2)
  let target = new THREE.Vector3(cx0, 0, cz0)
  let dist = 60
  const cam = new THREE.PerspectiveCamera(FOV, Math.max(aspect, 0.2), 0.1, 2000)
  const dir = new THREE.Vector3().setFromSphericalCoords(1, polar, theta)
  const usable = us ? 0.9 : market === 'ercot' ? 0.8 : 0.66
  const v = new THREE.Vector3()
  for (let i = 0; i < 6; i++) {
    cam.position.copy(target).addScaledVector(dir, dist)
    cam.lookAt(target)
    cam.updateMatrixWorld()
    cam.updateProjectionMatrix()
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
    for (const p of corners) {
      v.copy(p).project(cam)
      x0 = Math.min(x0, v.x); x1 = Math.max(x1, v.x)
      y0 = Math.min(y0, v.y); y1 = Math.max(y1, v.y)
    }
    // Re-aim so the box centre lands on the card centre: cast that screen point onto the ground.
    v.set((x0 + x1) / 2, (y0 + y1) / 2, 0.5).unproject(cam).sub(cam.position).normalize()
    if (v.y < -1e-4) target = cam.position.clone().addScaledVector(v, -cam.position.y / v.y)
    dist *= Math.max((x1 - x0) / 2 / usable, (y1 - y0) / 2 / usable)
  }
  return { target, dist: Math.max(dist, 18) }
}

function Rig({ stage, nodes, price, pal, drawing, market, view }: { stage: React.RefObject<Stage>; nodes: Node[]; price: number; pal: Palette; drawing: boolean; market: MarketId; view: MarketView }) {
  const controls = useRef<OrbitControlsImpl>(null)
  const { scene, camera, size } = useThree()
  const aspect = size.width / Math.max(size.height, 1)
  const home = useMemo(() => frameFor(market, view, aspect), [market, view, aspect])
  const refit = useRef(false)
  const lastAspect = useRef(aspect)
  if (lastAspect.current !== aspect) {
    lastAspect.current = aspect
    refit.current = true
  }
  const tween = useRef<{ from: THREE.Vector3; to: THREE.Vector3; d0: number; d1: number; t0: number; ms: number; polar?: number; heading?: number } | null>(null)
  const staged = useRef<number>(0)
  const framed = useRef<string>('')
  const index = useMemo(() => new Map(nodes.map((n, i) => [n.d.device_id, i])), [nodes])
  const sky = useMemo(() => new THREE.Color(), [])
  const sph = useMemo(() => new THREE.Spherical(), [])

  useFrame(() => {
    const c = controls.current
    if (!c) return
    // Fly to a newly chosen market or back out to the country, under 1.5 s, eased.
    const key = `${market}/${view}`
    if (framed.current !== key) {
      const first = framed.current === ''
      framed.current = key
      if (first) {
        c.target.copy(home.target)
        const dir = new THREE.Vector3().setFromSphericalCoords(1, view === 'us' ? US_POLAR : MARKET_POLAR, 0)
        camera.position.copy(home.target.clone().add(dir.multiplyScalar(home.dist)))
      } else {
        tween.current = { from: c.target.clone(), to: home.target.clone(), d0: camera.position.distanceTo(c.target), d1: home.dist, t0: performance.now(), ms: 1400, polar: view === 'us' ? US_POLAR : MARKET_POLAR, heading: 0 }
      }
    } else if (!tween.current && view === 'us') {
      // The country keeps its fit as the camera idles around it and as the card resizes.
      sph.setFromVector3(camera.position.clone().sub(c.target))
      const fit = frameFor(market, view, aspect, sph.theta, sph.phi)
      c.target.lerp(fit.target, 0.15)
      sph.radius += (fit.dist - sph.radius) * 0.15
      camera.position.copy(c.target).add(new THREE.Vector3().setFromSpherical(sph))
    } else if (!tween.current && refit.current) {
      // The card was resized: keep the framed market fitted and centred.
      refit.current = false
      sph.setFromVector3(camera.position.clone().sub(c.target))
      const fit = frameFor(market, view, aspect, sph.theta, sph.phi)
      sph.radius = fit.dist
      c.target.copy(fit.target)
      camera.position.copy(c.target).add(new THREE.Vector3().setFromSpherical(sph))
    }
    const mo = stage.current.moment
    // Stage a new moment: glide to the cluster (incident) or back home (recovery, later).
    if (mo && mo.at !== staged.current) {
      staged.current = mo.at
      const n = nodes[index.get(mo.device) ?? -1]
      if (mo.kind === 'incident' && n) {
        tween.current = { from: c.target.clone(), to: new THREE.Vector3(n.x, 0, n.z), d0: camera.position.distanceTo(c.target), d1: 28, t0: performance.now(), ms: 1100 }
      } else {
        tween.current = { from: c.target.clone(), to: home.target.clone(), d0: camera.position.distanceTo(c.target), d1: home.dist, t0: performance.now() + 2500, ms: 1100 }
      }
    }
    const tw = tween.current
    if (tw) {
      const k = ease((performance.now() - tw.t0) / tw.ms)
      if (k >= 0) {
        const target = tw.from.clone().lerp(tw.to, k)
        const dir = camera.position.clone().sub(c.target).normalize()
        const dist = tw.d0 + (tw.d1 - tw.d0) * k
        if (tw.polar !== undefined) {
          // Tip the camera towards the wanted pitch (and heading) as it flies.
          const s = new THREE.Spherical().setFromVector3(dir)
          const kk = Math.min(1, k * 1.5)
          s.phi += (tw.polar - s.phi) * kk
          if (tw.heading !== undefined) {
            let dTheta = tw.heading - s.theta
            dTheta = Math.atan2(Math.sin(dTheta), Math.cos(dTheta))
            s.theta += dTheta * kk
          }
          dir.setFromSpherical(s)
        }
        c.target.copy(target)
        camera.position.copy(target.clone().add(dir.multiplyScalar(dist)))
      }
      if (k >= 1) tween.current = null
    }
    c.update()
    // Sky and haze warm with the price.
    sky.copy(pal.skyCalm).lerp(pal.skyHot, priceHeat(price))
    scene.background = sky
    if (scene.fog instanceof THREE.Fog) {
      scene.fog.color.copy(sky)
      const d = camera.position.distanceTo(c.target)
      scene.fog.near = d * 1.7
      scene.fog.far = d * 3
    }
  })

  return (
    <>
      <OrbitControls
        ref={controls}
        target={home.target}
        enabled={!drawing}
        enablePan={false}
        enableZoom={false}
        autoRotate={!drawing}
        autoRotateSpeed={view === 'us' ? 0.15 : 0.35}
        minPolarAngle={0.45}
        maxPolarAngle={1.25}
        dampingFactor={0.08}
        enableDamping
        makeDefault
      />
      <fog attach="fog" args={['#000', 110, 190]} />
    </>
  )
}

// ------------------------------------------------------------------ scene

export default function TexasScene(props: SceneProps) {
  const { devices, groups, focus, hit, storm, stormMode, onStorm, onSelect, priceMwh, moment, theme, height, tempo = 1, market = 'ercot', view = 'market', onMarket } = props
  const pal = useMemo(() => palette(theme), [theme])
  const { nodes, hubs, maxKw } = useLayout(devices)
  const [hover, setHover] = useState<{ d: Device; x: number; y: number } | null>(null)
  const [draft, setDraft] = useState<Storm | null>(null)
  const [drawing, setDrawing] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const labels = useRef<Map<MarketId, HTMLDivElement>>(new Map())
  const [running, setRunning] = useState(true)

  const stage = useRef<Stage>({ moment: moment ?? null, focus, hit, hover: hover?.d.device_id ?? null, tempo })
  stage.current = { moment: moment ?? null, focus, hit, hover: hover?.d.device_id ?? null, tempo }

  const peersOf = useMemo(() => {
    const cache = new Map<string, Set<string>>()
    return (id: string) => {
      const c = cache.get(id)
      if (c) return c
      const g = groups?.[id]
      const set = new Set<string>()
      if (g) for (const d of devices) if (d.device_id !== id && (groups![d.device_id]?.feeder === g.feeder || groups![d.device_id]?.ring === g.ring)) set.add(d.device_id)
      cache.set(id, set)
      return set
    }
  }, [groups, devices])

  // Only render while on screen and the tab is visible.
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    let onScreen = true
    const io = new IntersectionObserver(([e]) => { onScreen = e.isIntersecting; setRunning(onScreen && !document.hidden) }, { threshold: 0.05 })
    io.observe(el)
    const onVis = () => setRunning(onScreen && !document.hidden)
    document.addEventListener('visibilitychange', onVis)
    return () => { io.disconnect(); document.removeEventListener('visibilitychange', onVis) }
  }, [])

  const cell = draft ?? storm ?? null
  const peerCount = hover ? peersOf(hover.d.device_id).size : 0

  return (
    <div ref={wrap} className="relative w-full" style={{ height, cursor: stormMode ? 'crosshair' : hover ? 'pointer' : 'grab' }} data-testid="fleet-scene">
      <Canvas
        dpr={[1, 1.5]}
        frameloop={running ? 'always' : 'never'}
        camera={{ position: [0, 40, 50], fov: 36, near: 0.5, far: 900 }}
        gl={{ antialias: false, powerPreference: 'high-performance', alpha: false }}
        onPointerMissed={() => setHover(null)}
        style={{ touchAction: stormMode ? 'none' : 'pan-y' }}
      >
        <ambientLight intensity={theme === 'dark' ? 0.55 : 1.1} />
        <directionalLight position={[-20, 30, 10]} intensity={theme === 'dark' ? 1.1 : 1.6} color={theme === 'dark' ? '#9fb8ff' : '#fff7e8'} />
        <hemisphereLight args={[theme === 'dark' ? '#3a4b7a' : '#ffffff', theme === 'dark' ? '#05070f' : '#c9d3e6', theme === 'dark' ? 0.5 : 0.7]} />
        <Ground pal={pal} />
        <Land pal={pal} />
        <Country pal={pal} theme={theme} selected={market} view={view} showLabels={view === 'us' || market !== 'ercot'} labels={labels} onMarket={onMarket} />
        <Hubs hubs={hubs} maxKw={maxKw} pal={pal} />
        <Homes
          nodes={nodes}
          hubs={hubs}
          maxKw={maxKw}
          pal={pal}
          peersOf={peersOf}
          stage={stage}
          onHover={(d, e) => setHover(d && e ? { d, x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY } : null)}
          onSelect={onSelect}
        />
        <Shockwave nodes={nodes} stage={stage} pal={pal} />
        {cell && <StormCell storm={cell} pal={pal} animate />}
        {stormMode && <DrawPlane onStorm={onStorm} setDraft={setDraft} setDrawing={setDrawing} />}
        <Rig stage={stage} nodes={nodes} price={priceMwh} pal={pal} drawing={drawing} market={market} view={view} />
        <EffectComposer multisampling={0}>
          <Bloom luminanceThreshold={theme === 'dark' ? 0.55 : 0.92} luminanceSmoothing={0.2} intensity={theme === 'dark' ? 1.1 : 0.3} mipmapBlur radius={0.6} />
        </EffectComposer>
      </Canvas>
      <MarketLabels labels={labels} />
      {hover && !drawing && (
        <div
          className="pointer-events-none absolute z-10 w-56 -translate-x-1/2 -translate-y-full rounded-md border border-border bg-surface/95 px-3 py-2 text-xs shadow backdrop-blur"
          style={{ left: hover.x, top: hover.y - 14 }}
        >
          <div className="font-medium">{hover.d.site}</div>
          <div className="num mt-0.5 text-fg-muted">
            {hover.d.status} · {hover.d.assigned_kw.toFixed(1)} kW promised · {Math.round(hover.d.state_of_charge * 100)}% charged
          </div>
          {groups?.[hover.d.device_id] && (
            <div className="mt-1 text-fg-subtle">
              Fails with <span className="text-price">{peerCount}</span> neighbours on {groups[hover.d.device_id].feeder} or {groups[hover.d.device_id].ring}
            </div>
          )}
        </div>
      )}
      {cell && (
        <div className="pointer-events-none absolute bottom-3 right-4 z-10 rounded-full border border-risk/40 bg-surface/90 px-3 py-1 text-xs text-risk backdrop-blur">
          Storm cell, {Math.round(cell.radius_km)} km
        </div>
      )}
    </div>
  )
}
