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
        <div className="eyebrow text-brand">{kicker}</div>
        <h1 className="mt-2 max-w-4xl text-[2rem] font-semibold leading-[1.05] md:text-[3.25rem]">{title}</h1>
        {lede && <p className="mt-3 max-w-3xl text-sm text-fg-muted md:text-base">{lede}</p>}
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
          <h2 className="text-xl font-semibold">{title}</h2>
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
  return <div className={`rounded-xl border p-3 text-sm ${cls}`}>{children}</div>
}
