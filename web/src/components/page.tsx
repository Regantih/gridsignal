import type { ReactNode } from 'react'

export function PageHeader({
  kicker,
  title,
  lede,
  actions,
}: {
  kicker: string
  title: string
  lede?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div className="min-w-0">
        <div className="text-xs font-semibold uppercase tracking-widest text-brand">{kicker}</div>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight md:text-3xl">{title}</h1>
        {lede && <p className="mt-2 max-w-3xl text-sm text-fg-muted md:text-base">{lede}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </header>
  )
}

export function Section({ title, description, children, aside }: { title: string; description?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          {description && <p className="text-sm text-fg-muted">{description}</p>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  )
}

export function Callout({ tone = 'info', children }: { tone?: 'info' | 'warn' | 'ok' | 'risk'; children: ReactNode }) {
  const cls = {
    info: 'bg-info-soft border-info/30',
    warn: 'bg-warn-soft border-warn/30',
    ok: 'bg-ok-soft border-ok/30',
    risk: 'bg-risk-soft border-risk/30',
  }[tone]
  return <div className={`rounded-md border p-3 text-sm ${cls}`}>{children}</div>
}
