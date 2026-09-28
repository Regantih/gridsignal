import type { ReactNode } from 'react'
import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

export function EmptyState({ title, body, action }: { title: string; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border-strong p-8 text-center">
      <Inbox aria-hidden className="size-6 text-fg-subtle" />
      <div className="font-medium">{title}</div>
      {body && <p className="max-w-md text-sm text-fg-muted">{body}</p>}
      {action}
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div role="alert" className="flex flex-col items-start gap-2 rounded-lg border border-risk/40 bg-risk-soft p-4">
      <div className="flex items-center gap-2 font-medium">
        <AlertTriangle aria-hidden className="size-4 text-risk" /> Something did not load
      </div>
      <p className="text-sm text-fg-muted break-words">{message}</p>
      {retry && (
        <Button size="sm" variant="outline" onClick={retry}>
          <RefreshCw /> Try again
        </Button>
      )}
    </div>
  )
}

export function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-5" aria-busy>
      <Skeleton className="h-5 w-40" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className="h-4 w-full" />
      ))}
    </div>
  )
}

export function ChartSkeleton() {
  return <Skeleton className="h-64 w-full" aria-busy />
}
