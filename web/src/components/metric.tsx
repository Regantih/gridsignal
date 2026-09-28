import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'
import { Panel, SpringValue } from '@/components/motion'

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
    <Panel className={cn('flex min-w-0 flex-col gap-1.5 rounded-2xl border border-border bg-surface/70 p-4', className)}>
      <div className="eyebrow">{label}</div>
      {loading ? (
        <Skeleton className="h-8 w-24" />
      ) : (
        <div className={cn('num text-[1.7rem] font-medium leading-none truncate', color)} data-testid={testId}>
          {typeof value === 'string' || typeof value === 'number' ? <SpringValue k={value}>{value}</SpringValue> : value}
        </div>
      )}
      {note && <div className="text-xs text-fg-muted">{note}</div>}
    </Panel>
  )
}
