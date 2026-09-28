/**
 * The member's battery as a ring: the locked green arc is the reserve that is never sold,
 * the cyan arc is what is shared with the grid tonight, the rest is theirs to use.
 */
export function ChargeRing({ stored, reserve, shared, capacity, backupHours }: { stored: number; reserve: number; shared: number; capacity: number; backupHours: string }) {
  const cap = Math.max(capacity, stored, reserve + shared, 0.1)
  const r = 84
  const c = 2 * Math.PI * r
  const seg = (a: number, b: number, color: string, w = 16, glow = false) => (
    <circle cx="110" cy="110" r={r} fill="none" stroke={color} strokeWidth={w} strokeDasharray={`${Math.max((b - a) / cap, 0) * c - 3} ${c}`} strokeDashoffset={-(a / cap) * c} strokeLinecap="butt" filter={glow ? 'url(#ringglow)' : undefined} style={{ transition: 'stroke-dasharray 900ms, stroke-dashoffset 900ms' }} />
  )
  const res = Math.min(reserve, stored)
  const sh = Math.min(shared, Math.max(stored - res, 0))
  return (
    <svg viewBox="0 0 220 220" width="220" height="220" role="img" aria-label={`Battery holds ${stored.toFixed(1)} kWh: ${res.toFixed(1)} reserved for you, ${sh.toFixed(1)} shared with the grid`} className="max-w-full shrink-0">
      <defs>
        <filter id="ringglow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="4" /></filter>
      </defs>
      <g transform="rotate(-90 110 110)">
        <circle cx="110" cy="110" r={r} fill="none" stroke="var(--border)" strokeWidth="16" />
        {seg(0, res, 'var(--ok)')}
        {seg(res, res + sh, 'var(--flow)', 16, true)}
        {seg(res, res + sh, 'var(--flow)')}
        {seg(res + sh, stored, 'var(--fg-subtle)')}
      </g>
      <text x="110" y="92" textAnchor="middle" fontSize="10" letterSpacing="2" fill="var(--fg-subtle)" style={{ fontFamily: 'var(--font-mono)' }}>BACKUP</text>
      <text x="110" y="126" textAnchor="middle" fontSize="34" fontWeight="500" fill="var(--fg)" style={{ fontFamily: 'var(--font-mono)', letterSpacing: '-0.04em' }}>{backupHours}</text>
      <text x="110" y="146" textAnchor="middle" fontSize="11" fill="var(--fg-muted)" style={{ fontFamily: 'var(--font-mono)' }}>{stored.toFixed(1)} kWh stored</text>
    </svg>
  )
}
