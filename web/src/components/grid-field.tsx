import { useEffect, useMemo, useRef, useState } from 'react'
import type { Device } from '@/lib/api'
import { H, W, project, texasPath, unproject } from '@/components/texas-map'
import { power } from '@/lib/format'

/**
 * The fleet as a living field. Every home is a node; power it exports travels as light
 * along a line to its zone's hub, faster and denser the more it sends. A home that drops
 * goes dark and pulses. Hovering a home lights every other home on the same feeder and
 * gateway ring, the homes that would fail with it. In storm mode, drag to draw a storm
 * cell; the parent asks the engine what that storm would cost.
 */

export interface Storm {
  lat: number
  lon: number
  radius_km: number
}

interface Props {
  devices: Device[]
  groups?: Record<string, { feeder: string; ring: string }>
  hit?: Set<string>
  focus?: Set<string>
  storm?: Storm | null
  stormMode?: boolean
  onStorm?: (s: Storm) => void
  onSelect?: (d: Device) => void
  label: string
  height?: number
}

const PX_PER_KM = H / ((36.7 - 25.7) * 111)

function css(el: Element, name: string, fallback: string) {
  const v = getComputedStyle(el).getPropertyValue(name).trim()
  return v || fallback
}

export function GridField({ devices, groups, hit, focus, storm, stormMode, onStorm, onSelect, label, height = 440 }: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [hover, setHover] = useState<Device | null>(null)
  const [draft, setDraft] = useState<Storm | null>(null)
  const drag = useRef<{ x: number; y: number } | null>(null)

  const nodes = useMemo(() => devices.map((d) => ({ d, p: project(d.lon, d.lat) })), [devices])
  const hubs = useMemo(() => {
    const acc: Record<string, { x: number; y: number; n: number; kw: number }> = {}
    for (const { d, p } of nodes) {
      const h = (acc[d.zone] ??= { x: 0, y: 0, n: 0, kw: 0 })
      h.x += p[0]
      h.y += p[1]
      h.n += 1
      h.kw += Math.max(d.assigned_kw, 0)
    }
    return Object.fromEntries(Object.entries(acc).map(([z, h]) => [z, { x: h.x / h.n, y: h.y / h.n, kw: h.kw }]))
  }, [nodes])

  const peers = useMemo(() => {
    if (!hover || !groups) return new Set<string>()
    const g = groups[hover.device_id]
    if (!g) return new Set<string>()
    return new Set(
      devices.filter((d) => d.device_id !== hover.device_id && (groups[d.device_id]?.feeder === g.feeder || groups[d.device_id]?.ring === g.ring)).map((d) => d.device_id),
    )
  }, [hover, groups, devices])

  const state = useRef({ hover, peers, hit, focus, storm: storm ?? null, draft })
  state.current = { hover, peers, hit, focus, storm: storm ?? null, draft }

  useEffect(() => {
    const c = canvas.current
    const box = wrap.current
    if (!c || !box) return
    const ctx = c.getContext('2d')
    if (!ctx) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const texas = new Path2D(texasPath)
    const dense = nodes.length > 400
    // Particles: per exporting home, a few phases along home -> hub.
    const flows = nodes
      .filter(({ d }) => d.status !== 'offline' && d.assigned_kw > 0 && hubs[d.zone])
      .map(({ d, p }) => ({ d, p, h: hubs[d.zone], n: Math.min(3, 1 + Math.floor(d.assigned_kw / 4)), speed: 0.12 + Math.min(d.assigned_kw, 12) * 0.02 }))
    const sample = dense ? flows.filter((_, i) => i % Math.ceil(flows.length / 400) === 0) : flows
    let raf = 0
    let t0 = performance.now()

    const draw = (now: number) => {
      const t = (now - t0) / 1000
      const rect = box.getBoundingClientRect()
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      if (c.width !== Math.round(rect.width * dpr) || c.height !== Math.round(height * dpr)) {
        c.width = Math.round(rect.width * dpr)
        c.height = Math.round(height * dpr)
      }
      const scale = Math.min(rect.width / W, height / H)
      const ox = (rect.width - W * scale) / 2
      const oy = (height - H * scale) / 2
      ctx.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * ox, dpr * oy)
      ctx.clearRect(-ox / scale, -oy / scale, rect.width / scale, height / scale)

      const flow = css(box, '--flow', '#6ee7f9')
      const priceC = css(box, '--price', '#fbbf24')
      const risk = css(box, '--risk', '#f87171')
      const warn = css(box, '--warn', '#fbbf24')
      const fg = css(box, '--fg', '#fff')
      const subtle = css(box, '--fg-subtle', '#999')
      const border = css(box, '--border-strong', '#444')
      const s = state.current

      // Land, with a faint graticule clipped inside.
      ctx.save()
      ctx.fillStyle = css(box, '--surface-2', '#222')
      ctx.globalAlpha = 0.55
      ctx.fill(texas)
      ctx.globalAlpha = 1
      ctx.clip(texas)
      ctx.strokeStyle = border
      ctx.globalAlpha = 0.18
      ctx.lineWidth = 0.6
      for (let x = 0; x < W; x += 24) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke()
      }
      for (let y = 0; y < H; y += 24) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke()
      }
      ctx.restore()
      ctx.strokeStyle = border
      ctx.lineWidth = 1.2
      ctx.stroke(texas)

      // Wires from homes to hubs.
      ctx.lineWidth = 0.5
      ctx.strokeStyle = flow
      ctx.globalAlpha = dense ? 0.05 : 0.14
      for (const f of dense ? sample : flows) {
        ctx.beginPath(); ctx.moveTo(f.p[0], f.p[1]); ctx.lineTo(f.h.x, f.h.y); ctx.stroke()
      }
      ctx.globalAlpha = 1

      // Energy particles.
      ctx.fillStyle = flow
      for (const f of sample) {
        if (s.hit?.has(f.d.device_id)) continue
        for (let k = 0; k < f.n; k++) {
          const ph = reduce ? (k + 0.5) / f.n : (t * f.speed + k / f.n + (f.p[0] % 7) / 7) % 1
          const x = f.p[0] + (f.h.x - f.p[0]) * ph
          const y = f.p[1] + (f.h.y - f.p[1]) * ph
          ctx.globalAlpha = 0.35 + 0.65 * Math.sin(Math.PI * ph)
          ctx.beginPath(); ctx.arc(x, y, dense ? 0.9 : 1.4, 0, Math.PI * 2); ctx.fill()
        }
      }
      ctx.globalAlpha = 1

      // Hubs: a ring sized by the zone's committed kW.
      for (const [zone, h] of Object.entries(hubs)) {
        const r = 5 + Math.sqrt(h.kw) * (dense ? 0.12 : 0.9)
        const g = ctx.createRadialGradient(h.x, h.y, 0, h.x, h.y, r * 2.4)
        g.addColorStop(0, flow)
        g.addColorStop(1, 'transparent')
        ctx.globalAlpha = 0.28 + (reduce ? 0 : 0.08 * Math.sin(t * 2))
        ctx.fillStyle = g
        ctx.beginPath(); ctx.arc(h.x, h.y, r * 2.4, 0, Math.PI * 2); ctx.fill()
        ctx.globalAlpha = 1
        ctx.strokeStyle = flow
        ctx.lineWidth = 1.2
        ctx.beginPath(); ctx.arc(h.x, h.y, r, 0, Math.PI * 2); ctx.stroke()
        ctx.fillStyle = subtle
        ctx.font = '600 10px "Geist Mono Variable", monospace'
        ctx.textAlign = 'center'
        ctx.fillText(zone.replace('LZ_', ''), h.x, h.y - r - 6)
      }

      // Peer lines on hover: the homes that fail together.
      if (s.hover && s.peers.size) {
        const [hx, hy] = project(s.hover.lon, s.hover.lat)
        ctx.strokeStyle = priceC
        ctx.lineWidth = 0.8
        ctx.globalAlpha = 0.7
        ctx.setLineDash([3, 3])
        for (const { d, p } of nodes) {
          if (!s.peers.has(d.device_id)) continue
          ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(p[0], p[1]); ctx.stroke()
        }
        ctx.setLineDash([])
        ctx.globalAlpha = 1
      }

      // Homes.
      const r0 = dense ? 1.6 : nodes.length > 100 ? 2.6 : 3.6
      for (const { d, p } of nodes) {
        const isHit = s.hit?.has(d.device_id)
        const isFocus = s.focus?.has(d.device_id)
        const isPeer = s.peers.has(d.device_id)
        let col = fg
        if (d.status === 'degraded') col = warn
        if (d.status === 'offline' || isHit) col = risk
        if (d.status === 'unavailable') col = subtle
        if (isPeer) col = priceC
        if (isFocus || d.status === 'offline' || isHit) {
          const ph = reduce ? 0.5 : (t * 0.8 + (p[1] % 5) / 5) % 1
          ctx.strokeStyle = risk
          ctx.globalAlpha = 1 - ph
          ctx.lineWidth = 1
          ctx.beginPath(); ctx.arc(p[0], p[1], r0 + 2 + ph * 14, 0, Math.PI * 2); ctx.stroke()
          ctx.globalAlpha = 1
        }
        ctx.fillStyle = col
        ctx.globalAlpha = isHit ? 0.55 : 1
        ctx.beginPath(); ctx.arc(p[0], p[1], s.hover?.device_id === d.device_id ? r0 + 2 : r0, 0, Math.PI * 2); ctx.fill()
        ctx.globalAlpha = 1
      }

      // Storm cell, committed or being drawn.
      const cell = s.draft ?? s.storm
      if (cell) {
        const [sx, sy] = project(cell.lon, cell.lat)
        const rr = cell.radius_km * PX_PER_KM
        const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, rr)
        g.addColorStop(0, 'transparent')
        g.addColorStop(1, risk)
        ctx.globalAlpha = 0.14
        ctx.fillStyle = g
        ctx.beginPath(); ctx.arc(sx, sy, rr, 0, Math.PI * 2); ctx.fill()
        ctx.globalAlpha = 0.9
        ctx.strokeStyle = risk
        ctx.lineWidth = 1.2
        for (let k = 0; k < 3; k++) {
          ctx.setLineDash([6 - k * 2, 5])
          ctx.lineDashOffset = reduce ? 0 : -t * (12 + k * 8) * (k % 2 ? -1 : 1)
          ctx.beginPath(); ctx.arc(sx, sy, rr * (1 - k * 0.22), 0, Math.PI * 2); ctx.stroke()
        }
        ctx.setLineDash([])
        ctx.globalAlpha = 1
        ctx.fillStyle = risk
        ctx.font = '600 11px "Geist Mono Variable", monospace'
        ctx.textAlign = 'center'
        ctx.fillText(`${Math.round(cell.radius_km)} km`, sx, sy - rr - 6)
      }

      if (!reduce) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    const onVis = () => {
      if (document.hidden) cancelAnimationFrame(raf)
      else { t0 = performance.now() - 0; raf = requestAnimationFrame(draw) }
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [nodes, hubs, height])

  const toMap = (e: React.PointerEvent) => {
    const box = wrap.current!.getBoundingClientRect()
    const scale = Math.min(box.width / W, height / H)
    const ox = (box.width - W * scale) / 2
    const oy = (height - H * scale) / 2
    return { x: (e.clientX - box.left - ox) / scale, y: (e.clientY - box.top - oy) / scale }
  }

  const nearest = (x: number, y: number) => {
    let best: Device | null = null
    let bd = 12 * 12
    for (const { d, p } of nodes) {
      const dd = (p[0] - x) ** 2 + (p[1] - y) ** 2
      if (dd < bd) { bd = dd; best = d }
    }
    return best
  }

  const onMove = (e: React.PointerEvent) => {
    const { x, y } = toMap(e)
    if (stormMode && drag.current) {
      const [lon, lat] = unproject(drag.current.x, drag.current.y)
      const r = Math.hypot(x - drag.current.x, y - drag.current.y) / PX_PER_KM
      setDraft({ lat, lon, radius_km: Math.max(10, Math.min(r, 600)) })
      return
    }
    setHover(nearest(x, y))
  }

  const hovered = hover ? project(hover.lon, hover.lat) : null
  const box = wrap.current?.getBoundingClientRect()
  const scale = box ? Math.min(box.width / W, height / H) : 1
  const ox = box ? (box.width - W * scale) / 2 : 0
  const oy = box ? (height - H * scale) / 2 : 0

  return (
    <div
      ref={wrap}
      className="relative w-full touch-none select-none"
      style={{ height, cursor: stormMode ? 'crosshair' : hover ? 'pointer' : 'default' }}
      onPointerMove={onMove}
      onPointerLeave={() => { setHover(null) }}
      onPointerDown={(e) => {
        if (!stormMode) return
        ;(e.target as Element).setPointerCapture?.(e.pointerId)
        drag.current = toMap(e)
        const [lon, lat] = unproject(drag.current.x, drag.current.y)
        setDraft({ lat, lon, radius_km: 10 })
      }}
      onPointerUp={() => {
        if (stormMode && drag.current && draft) {
          onStorm?.(draft)
          setDraft(null)
          drag.current = null
          return
        }
        if (hover) onSelect?.(hover)
      }}
    >
      <canvas ref={canvas} role="img" aria-label={label} className="absolute inset-0 h-full w-full" />
      {hover && hovered && !drag.current && (
        <div
          className="pointer-events-none absolute z-10 w-56 -translate-x-1/2 -translate-y-full rounded-md border border-border bg-surface/95 px-3 py-2 text-xs shadow backdrop-blur"
          style={{ left: ox + hovered[0] * scale, top: oy + hovered[1] * scale - 12 }}
        >
          <div className="font-medium">{hover.site}</div>
          <div className="num mt-0.5 text-fg-muted">
            {hover.status} · {power(hover.assigned_kw, 1)} promised · {Math.round(hover.state_of_charge * 100)}% charged
          </div>
          {groups?.[hover.device_id] && (
            <div className="mt-1 text-fg-subtle">
              Fails with <span className="text-price">{peers.size}</span> neighbours on {groups[hover.device_id].feeder} or {groups[hover.device_id].ring}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
