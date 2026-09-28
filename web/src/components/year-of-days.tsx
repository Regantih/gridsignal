/**
 * A year you can see. 365 squares, one per day of a simulated year; the coral ones are the
 * days the promise is missed at this commitment. The count comes from the planner; the
 * squares are spread evenly because the planner reports how many, not which dates.
 */
export function YearOfDays({ kept, compare, label }: { kept: number; compare?: number; label: string }) {
  const days = 365
  const missed = Math.max(0, Math.round((1 - kept) * days))
  const missedB = compare === undefined ? 0 : Math.max(0, Math.round((1 - compare) * days))
  const red = new Set<number>()
  for (let k = 0; k < missed; k++) red.add(Math.floor(((k + 0.5) * days) / Math.max(missed, 1)))
  const amber = new Set<number>()
  for (let k = 0; k < missedB; k++) {
    const i = Math.floor(((k + 0.5) * days) / Math.max(missedB, 1))
    if (!red.has(i)) amber.add(i)
  }
  return (
    <figure className="flex flex-col gap-2" data-testid="year-of-days">
      <div className="grid grid-flow-col grid-rows-7 gap-[3px]" role="img" aria-label={`${label}: about ${missed} missed days in a year`}>
        {Array.from({ length: days }).map((_, i) => (
          <span
            key={i}
            className="aspect-square w-full rounded-[2px]"
            style={{
              background: red.has(i) ? 'var(--risk)' : amber.has(i) ? 'var(--warn)' : 'var(--brand)',
              opacity: red.has(i) || amber.has(i) ? 1 : 0.22,
              boxShadow: red.has(i) ? '0 0 8px var(--risk)' : undefined,
              transition: 'background 300ms, opacity 300ms',
            }}
          />
        ))}
      </div>
      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-fg-subtle">
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-[2px] bg-risk" /> <span className="num text-fg">{missed}</span> missed with the playbook</span>
        {compare !== undefined && <span className="flex items-center gap-1.5"><span className="size-2 rounded-[2px] bg-warn" /> <span className="num text-fg">{missedB}</span> missed if every fix waits for approval</span>}
        <span>Count from the planner; placement is illustrative.</span>
      </figcaption>
    </figure>
  )
}
