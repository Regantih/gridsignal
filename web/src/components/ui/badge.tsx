import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap',
  {
    variants: {
      tone: {
        neutral: 'border-transparent bg-neutral-soft text-fg',
        brand: 'border-transparent bg-brand-soft text-fg',
        ok: 'border-transparent bg-ok-soft text-fg',
        warn: 'border-transparent bg-warn-soft text-fg',
        risk: 'border-transparent bg-risk-soft text-fg',
        info: 'border-transparent bg-info-soft text-fg',
        outline: 'border-border-strong text-fg-muted',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)

export type Tone = NonNullable<VariantProps<typeof badgeVariants>['tone']>

export function Badge({
  className,
  tone,
  dot = true,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants> & { dot?: boolean }) {
  const dotColor: Record<Tone, string> = {
    neutral: 'bg-neutral',
    brand: 'bg-brand',
    ok: 'bg-ok',
    warn: 'bg-warn',
    risk: 'bg-risk',
    info: 'bg-info',
    outline: 'bg-fg-subtle',
  }
  return (
    <span className={cn(badgeVariants({ tone }), className)} {...props}>
      {dot && <span aria-hidden className={cn('size-1.5 rounded-full', dotColor[tone ?? 'neutral'])} />}
      {props.children}
    </span>
  )
}
