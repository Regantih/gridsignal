import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { Bloom, EffectComposer } from '@react-three/postprocessing'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import type { Device } from '@/lib/api'
import { H, W, project, unproject, texasOutline } from '@/components/texas-map'
import type { Storm } from '@/components/grid-field'
import type { SceneProps } from './scene-types'

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
  skyCalm: THREE.Color
  skyHot: THREE.Color
  grid: string
}

function palette(theme: 'dark' | 'light'): Palette {
  const dark = theme === 'dark'
  return {
    flow: cssColor('--flow', '#6ee7f9'),
    risk: cssColor('--risk', '#f87171'),
    warn: cssColor('--warn', '#fbbf24'),
    price: cssColor('--price', '#fbbf24'),
    fg: cssColor('--fg', dark ? '#f1f5f9' : '#1e293b'),
    subtle: cssColor('--fg-subtle', '#94a3b8'),
    land: new THREE.Color(dark ? '#111d3b' : '#dfe6f3'),
    side: new THREE.Color(dark ? '#070d1f' : '#b9c4d8'),
    skyCalm: new THREE.Color(dark ? '#070c1c' : '#eef2fb'),
    skyHot: new THREE.Color(dark ? '#2a1405' : '#f6e2c4'),
    grid: dark ? 'rgba(140,170,220,0.16)' : 'rgba(40,60,100,0.14)',
  }
}

const ease = (t: number) => 1 - Math.pow(1 - Math.min(Math.max(t, 0), 1), 3)
const priceHeat = (p: number) => Math.min(Math.max((p - 50) / 200, 0), 1)

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
      g.addColorStop(0, `rgba(255,255,255,${0.03 + Math.random() * 0.035})`)
      g.addColorStop(1, 'rgba(255,255,255,0)')
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
  return (
    <mesh geometry={geo} receiveShadow>
      <meshStandardMaterial attach="material-0" map={top} roughness={0.9} metalness={0.05} />
      <meshStandardMaterial attach="material-1" color={pal.side} roughness={0.95} />
    </mesh>
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
              <meshBasicMaterial color={pal.flow} transparent opacity={0.55} toneMapped={false} side={THREE.DoubleSide} depthWrite={false} />
            </mesh>
            <mesh position={[0, height / 2, 0]}>
              <cylinderGeometry args={[0.07, 0.12, height, 12]} />
              <meshBasicMaterial color={pal.flow.clone().multiplyScalar(2.2)} toneMapped={false} />
            </mesh>
            <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}>
              <ringGeometry args={[0.7, 1.05, 48]} />
              <meshBasicMaterial color={pal.flow} transparent opacity={0.5} toneMapped={false} depthWrite={false} />
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
        <pointsMaterial color={pal.flow.clone().multiplyScalar(1.6)} size={dense ? 0.22 : 0.34} sizeAttenuation transparent opacity={0.9} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
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

function StormCell({ storm, pal, live }: { storm: Storm; pal: Palette; live: boolean }) {
  const [x, z] = toScene(storm.lon, storm.lat)
  const radius = (storm.radius_km * PX_PER_KM) / UNIT
  const light = useRef<THREE.PointLight>(null)
  const flash = useRef<THREE.MeshBasicMaterial>(null)
  const rain = useMemo(() => {
    const n = 500
    const arr = new Float32Array(n * 3)
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2
      const rr = Math.sqrt(Math.random()) * radius
      arr[i * 3] = Math.cos(a) * rr
      arr[i * 3 + 1] = Math.random() * 4
      arr[i * 3 + 2] = Math.sin(a) * rr
    }
    return arr
  }, [radius])
  const pts = useRef<THREE.Points>(null)
  const next = useRef(0)
  useFrame(({ clock }, dt) => {
    const t = clock.elapsedTime
    if (light.current && flash.current) {
      if (live && t > next.current) {
        light.current.intensity = 40 + Math.random() * 60
        flash.current.opacity = 0.35
        next.current = t + 0.6 + Math.random() * 2.2
      } else {
        light.current.intensity *= 0.82
        flash.current.opacity *= 0.8
      }
    }
    if (pts.current && live) {
      const a = pts.current.geometry.attributes.position as THREE.BufferAttribute
      const arr = a.array as Float32Array
      for (let i = 1; i < arr.length; i += 3) {
        arr[i] -= dt * 6
        if (arr[i] < 0) arr[i] = 4
      }
      a.needsUpdate = true
    }
  })
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 4.2, 0]}>
        <cylinderGeometry args={[radius * 1.05, radius * 0.9, 1.4, 48]} />
        <meshStandardMaterial color="#1a2033" transparent opacity={0.85} roughness={1} />
      </mesh>
      <mesh position={[0, 4.2, 0]}>
        <cylinderGeometry args={[radius * 1.05, radius * 0.9, 1.4, 48]} />
        <meshBasicMaterial ref={flash} color="#dbe7ff" transparent opacity={0} toneMapped={false} depthWrite={false} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.04, 0]}>
        <circleGeometry args={[radius, 64]} />
        <meshBasicMaterial color="#000" transparent opacity={0.45} depthWrite={false} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.05, 0]}>
        <ringGeometry args={[radius * 0.98, radius, 64]} />
        <meshBasicMaterial color={pal.risk} transparent opacity={0.9} toneMapped={false} depthWrite={false} />
      </mesh>
      <points ref={pts}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[rain, 3]} />
        </bufferGeometry>
        <pointsMaterial color="#9fb4d9" size={0.08} transparent opacity={0.55} depthWrite={false} />
      </points>
      <pointLight ref={light} position={[0, 3.4, 0]} color="#e6efff" intensity={0} distance={radius * 4} decay={2} />
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

function Rig({ stage, nodes, price, pal, drawing }: { stage: React.RefObject<Stage>; nodes: Node[]; price: number; pal: Palette; drawing: boolean }) {
  const controls = useRef<OrbitControlsImpl>(null)
  const { scene, camera } = useThree()
  const home = useMemo(() => ({ target: new THREE.Vector3(0, 0, 1), dist: 64 }), [])
  const tween = useRef<{ from: THREE.Vector3; to: THREE.Vector3; d0: number; d1: number; t0: number } | null>(null)
  const staged = useRef<number>(0)
  const index = useMemo(() => new Map(nodes.map((n, i) => [n.d.device_id, i])), [nodes])
  const sky = useMemo(() => new THREE.Color(), [])

  useFrame(() => {
    const c = controls.current
    if (!c) return
    const mo = stage.current.moment
    // Stage a new moment: glide to the cluster (incident) or back home (recovery, later).
    if (mo && mo.at !== staged.current) {
      staged.current = mo.at
      const n = nodes[index.get(mo.device) ?? -1]
      if (mo.kind === 'incident' && n) {
        tween.current = { from: c.target.clone(), to: new THREE.Vector3(n.x, 0, n.z), d0: camera.position.distanceTo(c.target), d1: 28, t0: performance.now() }
      } else {
        tween.current = { from: c.target.clone(), to: home.target.clone(), d0: camera.position.distanceTo(c.target), d1: home.dist, t0: performance.now() + 2500 }
      }
    }
    const tw = tween.current
    if (tw) {
      const k = ease((performance.now() - tw.t0) / 1100)
      if (k >= 0) {
        const target = tw.from.clone().lerp(tw.to, k)
        const dir = camera.position.clone().sub(c.target).normalize()
        const dist = tw.d0 + (tw.d1 - tw.d0) * k
        c.target.copy(target)
        camera.position.copy(target.clone().add(dir.multiplyScalar(dist)))
      }
      if (k >= 1) tween.current = null
    }
    c.update()
    // Sky and haze warm with the price.
    sky.copy(pal.skyCalm).lerp(pal.skyHot, priceHeat(price))
    scene.background = sky
    if (scene.fog instanceof THREE.Fog) scene.fog.color.copy(sky)
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
        autoRotateSpeed={0.35}
        minPolarAngle={0.55}
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
  const { devices, groups, focus, hit, storm, stormMode, onStorm, onSelect, priceMwh, moment, theme, height, tempo = 1 } = props
  const pal = useMemo(() => palette(theme), [theme])
  const { nodes, hubs, maxKw } = useLayout(devices)
  const [hover, setHover] = useState<{ d: Device; x: number; y: number } | null>(null)
  const [draft, setDraft] = useState<Storm | null>(null)
  const [drawing, setDrawing] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
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
        camera={{ position: [0, 40, 50], fov: 36, near: 0.5, far: 300 }}
        gl={{ antialias: false, powerPreference: 'high-performance', alpha: false }}
        onPointerMissed={() => setHover(null)}
        style={{ touchAction: stormMode ? 'none' : 'pan-y' }}
      >
        <ambientLight intensity={theme === 'dark' ? 0.55 : 1.1} />
        <directionalLight position={[-20, 30, 10]} intensity={theme === 'dark' ? 1.1 : 1.6} color={theme === 'dark' ? '#9fb8ff' : '#fff7e8'} />
        <hemisphereLight args={[theme === 'dark' ? '#3a4b7a' : '#ffffff', theme === 'dark' ? '#05070f' : '#c9d3e6', theme === 'dark' ? 0.5 : 0.7]} />
        <Land pal={pal} />
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
        {cell && <StormCell storm={cell} pal={pal} live={!draft} />}
        {stormMode && <DrawPlane onStorm={onStorm} setDraft={setDraft} setDrawing={setDrawing} />}
        <Rig stage={stage} nodes={nodes} price={priceMwh} pal={pal} drawing={drawing} />
        <EffectComposer multisampling={0}>
          <Bloom luminanceThreshold={theme === 'dark' ? 0.55 : 0.85} luminanceSmoothing={0.2} intensity={theme === 'dark' ? 1.1 : 0.5} mipmapBlur radius={0.6} />
        </EffectComposer>
      </Canvas>
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
