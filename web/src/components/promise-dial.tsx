/**
 * The promise as an instrument. The outer track is everything the healthy fleet could send;
 * the bright arc is what is promised; a notch marks the ERCOT target. When the arc falls
 * short of the notch the gap glows coral, the kW the operator has to find.
 */
export function PromiseDial({ committed, target, headroom, size = 260 }: { committed: number; target: number; headroom: number; size?: number }) {
  const cap = Math.max(committed + headroom, target, 1)
  const r = 104
  const c = 2 * Math.PI * r
  const sweep = 0.75 // 270 degrees
  const arc = (share: number) => `${Math.max(share, 0) * sweep * c} ${c}`
  const commitShare = committed / cap
  const targetShare = target / cap
  const short = committed < target - 1e-6
  const angle = (share: number) => (135 + share * 270) * (Math.PI / 180)
  const notch = angle(targetShare)
  const coverage = target > 0 ? Math.min(committed / target, 9.99) : 1
  return (
    <svg viewBox="0 0 260 260" width={size} height={size} role="img" aria-label={`${Math.round(committed)} of ${Math.round(target)} kW promised, ${Math.round(headroom)} kW of headroom`} className="max-w-full">
      <defs>
        <linearGradient id="dial" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="var(--brand)" stopOpacity="0.55" />
          <stop offset="100%" stopColor="var(--flow)" />
        </linearGradient>
        <filter id="dialglow" x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="5" />
        </filter>
      </defs>
      <g transform="rotate(135 130 130)">
        <circle cx="130" cy="130" r={r} fill="none" stroke="var(--border)" strokeWidth="14" strokeDasharray={arc(1)} strokeLinecap="round" />
        {Array.from({ length: 28 }).map((_, i) => {
          const a = (i / 27) * sweep * 2 * Math.PI
          return (
            <line key={i} x1={130 + (r - 16) * Math.cos(a)} y1={130 + (r - 16) * Math.sin(a)} x2={130 + (r - 21) * Math.cos(a)} y2={130 + (r - 21) * Math.sin(a)} stroke="var(--fg-subtle)" strokeOpacity={i % 9 === 0 ? 0.8 : 0.3} strokeWidth="1.2" />
          )
        })}
        {short && <circle cx="130" cy="130" r={r} fill="none" stroke="var(--risk)" strokeOpacity="0.8" strokeWidth="14" strokeDasharray={arc(targetShare)} strokeLinecap="round" />}
        <circle cx="130" cy="130" r={r} fill="none" stroke="url(#dial)" strokeWidth="14" strokeDasharray={arc(commitShare)} strokeLinecap="round" filter="url(#dialglow)" opacity="0.6" style={{ transition: 'stroke-dasharray 900ms cubic-bezier(.2,.8,.2,1)' }} />
        <circle cx="130" cy="130" r={r} fill="none" stroke="url(#dial)" strokeWidth="14" strokeDasharray={arc(commitShare)} strokeLinecap="round" style={{ transition: 'stroke-dasharray 900ms cubic-bezier(.2,.8,.2,1)' }} />
      </g>
      <line x1={130 + (r - 12) * Math.cos(notch)} y1={130 + (r - 12) * Math.sin(notch)} x2={130 + (r + 12) * Math.cos(notch)} y2={130 + (r + 12) * Math.sin(notch)} stroke="var(--fg)" strokeWidth="3" strokeLinecap="round" />
      <text x="130" y="112" textAnchor="middle" fontSize="11" letterSpacing="2" fill="var(--fg-subtle)" style={{ fontFamily: 'var(--font-mono)' }}>
        COVERAGE
      </text>
      <text x="130" y="152" textAnchor="middle" fontSize="44" fontWeight="500" fill={short ? 'var(--risk)' : 'var(--fg)'} style={{ fontFamily: 'var(--font-mono)', letterSpacing: '-0.04em' }}>
        {Math.round(coverage * 100)}%
      </text>
      <text x="130" y="176" textAnchor="middle" fontSize="12" fill="var(--fg-muted)" style={{ fontFamily: 'var(--font-mono)' }}>
        {Math.round(committed)} / {Math.round(target)} kW
      </text>
      <text x="130" y="236" textAnchor="middle" fontSize="11" fill="var(--fg-subtle)" style={{ fontFamily: 'var(--font-mono)' }}>
        +{Math.round(headroom)} kW headroom
      </text>
    </svg>
  )
}
