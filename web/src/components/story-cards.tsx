import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * "Your home tonight" as a strip of cards a thumb can swipe through. Native scroll snapping
 * does the physics, so it works with touch, trackpad and keyboard; the arrows and dots are
 * 48 px targets. Every card stays in the DOM, so the numbers are always readable.
 */
export function StoryCards({ cards, label }: { cards: { id: string; node: ReactNode }[]; label: string }) {
  const track = useRef<HTMLDivElement>(null)
  const [i, setI] = useState(0)

  useEffect(() => {
    const el = track.current
    if (!el) return
    const onScroll = () => {
      const w = el.clientWidth
      if (w > 0) setI(Math.round(el.scrollLeft / w))
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  const go = useCallback((n: number) => {
    const el = track.current
    if (!el) return
    const k = Math.max(0, Math.min(cards.length - 1, n))
    el.scrollTo({ left: k * el.clientWidth, behavior: 'smooth' })
    setI(k)
  }, [cards.length])

  return (
    <section aria-roledescription="carousel" aria-label={label} className="flex flex-col gap-3" data-testid="story-cards">
      <div
        ref={track}
        className="snap-x -mx-1 flex overflow-x-auto px-1"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight') { e.preventDefault(); go(i + 1) }
          if (e.key === 'ArrowLeft') { e.preventDefault(); go(i - 1) }
        }}
      >
        {cards.map((c, k) => (
          <div
            key={c.id}
            role="group"
            aria-roledescription="slide"
            aria-label={`${k + 1} of ${cards.length}`}
            className="w-full shrink-0 px-1"
            data-testid={`story-card-${c.id}`}
          >
            {c.node}
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => go(i - 1)}
          disabled={i === 0}
          aria-label="Previous card"
          className="grid size-12 place-items-center rounded-full border border-border bg-surface text-fg disabled:opacity-40"
        >
          <ChevronLeft aria-hidden className="size-5" />
        </button>
        <div className="flex items-center gap-1" role="tablist" aria-label="Cards">
          {cards.map((c, k) => (
            <button
              key={c.id}
              type="button"
              role="tab"
              aria-selected={k === i}
              aria-label={`Card ${k + 1}`}
              onClick={() => go(k)}
              className="grid size-11 place-items-center"
            >
              <span className={cn('block h-2 rounded-full transition-all', k === i ? 'w-6 bg-flow' : 'w-2 bg-fg-subtle/60')} />
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => go(i + 1)}
          disabled={i === cards.length - 1}
          aria-label="Next card"
          className="grid size-12 place-items-center rounded-full border border-border bg-surface text-fg disabled:opacity-40"
        >
          <ChevronRight aria-hidden className="size-5" />
        </button>
      </div>
    </section>
  )
}
