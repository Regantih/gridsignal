/**
 * The member's battery as a vessel of liquid. The locked reserve sits at the bottom, never
 * sold; the cyan layer above it is what is shared with the grid tonight and drains out to
 * the right; the rest is the household's to use. Backup hours sit in the middle. Every level
 * is a member API figure; the wave is decoration and stops under Calm mode.
 */
export function LiquidBattery({ stored, reserve, shared, backupHours, affected = false }: { stored: number; reserve: number; shared: number; backupHours: string; affected?: boolean }) {
  const cap = Math.max(stored, reserve + shared, 0.1)
  const res = Math.min(reserve, stored)
  const sh = Math.min(shared, Math.max(stored - res, 0))
  const W = 200
  const H = 260
  const x0 = 30
  const x1 = 170
  const top = 28
  const bottom = 244
  const h = bottom - top
  const yOf = (kwh: number) => bottom - (Math.min(kwh, cap) / cap) * h
  const yStored = yOf(stored)
  const yReserve = yOf(res)
  const yShared = yOf(res + sh)
  /* A wave crest at y=0 with a body reaching well past the vessel floor; each layer is then
     translated so its crest sits at the level it represents. */
  const wave = (amp: number) =>
    `M${x0 - 40} 0 q20 ${-amp} 40 0 t40 0 t40 0 t40 0 t40 0 t40 0 t40 0 t40 0 V${H} H${x0 - 40} Z`
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      role="img"
      aria-label={`Battery holds ${stored.toFixed(1)} kWh: ${res.toFixed(1)} locked for your home, ${sh.toFixed(1)} shared with the grid. Backup ${backupHours}.`}
      className="max-w-full shrink-0"
      data-testid="liquid-battery"
    >
      <defs>
        <clipPath id="vessel">
          <rect x={x0} y={top} width={x1 - x0} height={bottom - top} rx="26" />
        </clipPath>
        <linearGradient id="lq-shared" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="var(--flow)" stopOpacity="0.95" />
          <stop offset="1" stopColor="var(--flow)" stopOpacity="0.55" />
        </linearGradient>
        <linearGradient id="lq-reserve" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="var(--ok)" stopOpacity="0.9" />
          <stop offset="1" stopColor="var(--ok)" stopOpacity="0.6" />
        </linearGradient>
        <filter id="lq-glow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
      </defs>

      {/* cap */}
      <rect x={x0 + 46} y={12} width={x1 - x0 - 92} height={14} rx="5" fill="var(--border)" />
      {/* vessel */}
      <rect x={x0} y={top} width={x1 - x0} height={bottom - top} rx="26" fill="var(--surface)" stroke="var(--border)" strokeWidth="2" />

      <g clipPath="url(#vessel)">
        {/* the household's share: everything stored above what is shared */}
        <g style={{ transform: `translateY(${yStored}px)`, transition: 'transform 900ms cubic-bezier(.2,.8,.2,1)' }}>
          <path d={wave(5)} fill="var(--fg-subtle)" fillOpacity="0.28" className="wave" />
        </g>
        {/* shared with the grid tonight */}
        <g style={{ transform: `translateY(${yShared}px)`, transition: 'transform 900ms cubic-bezier(.2,.8,.2,1)' }}>
          <path d={wave(7)} fill="url(#lq-shared)" filter="url(#lq-glow)" opacity="0.5" className="wave wave-slow" />
          <path d={wave(6)} fill="url(#lq-shared)" className="wave wave-slow" />
        </g>
        {/* locked reserve */}
        <g style={{ transform: `translateY(${yReserve}px)`, transition: 'transform 900ms cubic-bezier(.2,.8,.2,1)' }}>
          <path d={wave(3)} fill="url(#lq-reserve)" className="wave wave-slower" />
        </g>
        <line x1={x0} x2={x1} y1={yReserve} y2={yReserve} stroke="var(--bg)" strokeOpacity="0.6" strokeDasharray="3 4" />
        {/* lock on the reserve */}
        <g transform={`translate(${(x0 + x1) / 2 - 7} ${Math.min(yReserve + 8, bottom - 22)})`} fill="none" stroke="var(--bg)" strokeWidth="1.6" opacity="0.9">
          <rect x="1" y="6" width="12" height="9" rx="2" fill="var(--bg)" fillOpacity="0.5" />
          <path d="M4 6V4a3 3 0 0 1 6 0v2" />
        </g>
      </g>

      {/* the shared portion leaves the vessel to the right */}
      {sh > 0 && (
        <g aria-hidden>
          <path d={`M${x1} ${(yShared + yReserve) / 2} h14 q10 0 10 10 v${bottom - (yShared + yReserve) / 2 - 4}`} fill="none" stroke="var(--flow)" strokeOpacity="0.35" strokeWidth="3" strokeLinecap="round" />
          <path d={`M${x1} ${(yShared + yReserve) / 2} h14 q10 0 10 10 v${bottom - (yShared + yReserve) / 2 - 4}`} fill="none" stroke="var(--flow)" strokeWidth="3" strokeLinecap="round" strokeDasharray="6 14" className="drain" />
        </g>
      )}

      {/* centre readout */}
      <g style={{ fontFamily: 'var(--font-mono)' }} textAnchor="middle">
        <rect x={(x0 + x1) / 2 - 62} y={104} width={124} height={78} rx="12" fill="var(--surface)" fillOpacity="0.82" />
        <text x={(x0 + x1) / 2} y={122} fontSize="10" letterSpacing="2" fill="var(--fg-muted)">BACKUP</text>
        <text x={(x0 + x1) / 2} y={154} fontSize="32" fontWeight="500" fill={affected ? 'var(--warn)' : 'var(--fg)'} style={{ letterSpacing: '-0.04em' }}>
          {backupHours}
        </text>
        <text x={(x0 + x1) / 2} y={174} fontSize="11" fill="var(--fg-muted)">
          {stored.toFixed(1)} kWh stored
        </text>
      </g>
    </svg>
  )
}
