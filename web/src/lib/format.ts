/** Presentation only. Every value comes from the engine; nothing here recomputes it. */

export function money(amount: number, cents = true): string {
  const body = Math.abs(amount).toLocaleString('en-US', {
    minimumFractionDigits: cents ? 2 : 0,
    maximumFractionDigits: cents ? 2 : 0,
  })
  return amount <= -0.005 ? `-$${body}` : `$${body}`
}

export function power(kw: number, decimals = 0): string {
  return `${kw.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })} kW`
}

export function energy(kwh: number, decimals = 0): string {
  return `${kwh.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })} kWh`
}

export function pct(share: number, decimals = 0): string {
  return `${(share * 100).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}%`
}

export function pctPoints(points: number, decimals = 0): string {
  return `${points.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}%`
}

export function hours(h: number): string {
  if (h >= 48) return `${Math.round(h / 24)} days`
  if (h >= 24) return `${Math.round(h)} h`
  return `${h.toLocaleString('en-US', { maximumFractionDigits: 1 })} h`
}

export function priceMwh(p: number): string {
  return `$${p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}/MWh`
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
}

export function dateLabel(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export function daysMissed(kept: number): string {
  return (365 * (1 - kept)).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
}

export function count(n: number): string {
  return n.toLocaleString('en-US')
}

/** Sentence case for enum values like "awaiting_approval". */
export function humanize(value: string): string {
  const s = value.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function slot(i: number): string {
  const m = i * 15
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}
