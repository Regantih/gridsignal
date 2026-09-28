/** GridSignal mark: a grid node emitting one signal pulse. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden className={className}>
      <rect x="1.5" y="1.5" width="29" height="29" rx="9" stroke="currentColor" strokeWidth="1.5" opacity="0.35" />
      <path d="M5 17h6l2.5-7 4 13 3-9 1.5 3H27" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="27" cy="17" r="2" fill="currentColor" />
    </svg>
  )
}
