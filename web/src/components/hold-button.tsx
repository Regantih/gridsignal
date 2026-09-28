import { useEffect, useRef, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Press and hold to confirm. A recovery moves real kilowatts, so it is a deliberate gesture,
 * not a stray click: the ring fills over `ms`, releasing early cancels. Enter or Space held
 * on the focused button does the same, so it stays keyboard accessible.
 */
export function HoldButton({
  children,
  onConfirm,
  ms = 900,
  disabled,
  tone = 'brand',
  testId,
  className,
  hint = 'Hold to confirm',
}: {
  children: ReactNode
  onConfirm: () => void
  ms?: number
  disabled?: boolean
  tone?: 'brand' | 'risk' | 'ok'
  testId?: string
  className?: string
  hint?: string
}) {
  const [p, setP] = useState(0)
  const start = useRef<number | null>(null)
  const raf = useRef(0)
  const done = useRef(false)

  const tick = (now: number) => {
    if (start.current === null) return
    const v = Math.min((now - start.current) / ms, 1)
    setP(v)
    if (v >= 1 && !done.current) {
      done.current = true
      start.current = null
      onConfirm()
      window.setTimeout(() => setP(0), 350)
      return
    }
    raf.current = requestAnimationFrame(tick)
  }
  const begin = () => {
    if (disabled || start.current !== null) return
    done.current = false
    start.current = performance.now()
    raf.current = requestAnimationFrame(tick)
  }
  const cancel = () => {
    if (done.current) return
    start.current = null
    cancelAnimationFrame(raf.current)
    setP(0)
  }
  useEffect(() => () => cancelAnimationFrame(raf.current), [])

  const bg = { brand: 'bg-brand text-brand-fg', risk: 'bg-risk text-brand-fg', ok: 'bg-ok text-brand-fg' }[tone]
  return (
    <button
      type="button"
      data-testid={testId}
      disabled={disabled}
      aria-label={typeof children === 'string' ? `${children}. ${hint}` : undefined}
      aria-describedby={undefined}
      onPointerDown={begin}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onKeyDown={(e) => {
        if ((e.key === 'Enter' || e.key === ' ') && !e.repeat) {
          e.preventDefault()
          begin()
        }
      }}
      onKeyUp={(e) => {
        if (e.key === 'Enter' || e.key === ' ') cancel()
      }}
      className={cn(
        'group relative inline-flex h-11 select-none items-center gap-3 overflow-hidden rounded-full pl-2 pr-5 text-sm font-semibold shadow-[var(--glow)] transition-transform active:scale-[0.98] disabled:opacity-50',
        bg,
        className,
      )}
    >
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 bg-white/20"
        style={{ width: `${p * 100}%`, transition: p === 0 ? 'width 200ms ease-out' : 'none' }}
      />
      <svg aria-hidden viewBox="0 0 32 32" className="relative size-8 -rotate-90">
        <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeOpacity="0.3" strokeWidth="2.5" />
        <circle
          cx="16"
          cy="16"
          r="13"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeDasharray={2 * Math.PI * 13}
          strokeDashoffset={(1 - p) * 2 * Math.PI * 13}
        />
      </svg>
      <span className="relative flex flex-col items-start leading-tight">
        <span>{children}</span>
        <span className="text-2xs font-medium opacity-75">{p > 0 ? `${Math.round(p * 100)}%` : hint}</span>
      </span>
    </button>
  )
}
