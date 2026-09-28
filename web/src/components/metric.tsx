import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'

export function Metric({
  label,
  value,
  note,
  tone = 'default',
  className,
  loading = false,
  testId,
}: {
  label: string
  value: ReactNode
  note?: ReactNode
  tone?: 'default' | 'ok' | 'warn' | 'risk' | 'brand'
  className?: string
  loading?: boolean
  testId?: string
}) {
  const color = {
    default: 'text-fg',
    ok: 'text-ok',
    warn: 'text-warn',
    risk: 'text-risk',
    brand: 'text-brand',
  }[tone]
  return (
    <div className={cn('flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-surface p-4', className)}>
      <div className="text-xs font-medium uppercase tracking-wide text-fg-subtle">{label}</div>
      {loading ? (
        <Skeleton className="h-8 w-24" />
      ) : (
        <div className={cn('text-2xl font-semibold leading-none tabular truncate', color)} data-testid={testId}>
          {value}
        </div>
      )}
      {note && <div className="text-xs text-fg-muted">{note}</div>}
    </div>
  )
}
