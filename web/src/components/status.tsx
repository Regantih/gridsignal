import { Badge, type Tone } from '@/components/ui/badge'
import { humanize } from '@/lib/format'
import type { DeviceStatus, IncidentStatus } from '@/lib/api'

export const deviceTone: Record<DeviceStatus, Tone> = {
  online: 'ok',
  degraded: 'warn',
  offline: 'risk',
  unavailable: 'neutral',
}

export function incidentTone(status: IncidentStatus): Tone {
  switch (status) {
    case 'resolved':
      return 'ok'
    case 'awaiting_approval':
      return 'risk'
    case 'recovering':
      return 'warn'
    default:
      return 'neutral'
  }
}

export function incidentLabel(status: IncidentStatus): string {
  switch (status) {
    case 'awaiting_approval':
      return 'Waiting for your approval'
    case 'recovering':
      return 'Recovering'
    case 'resolved':
      return 'Resolved'
    default:
      return humanize(status)
  }
}

export function riskTone(status: string): Tone {
  if (status.startsWith('Promise')) return 'risk'
  if (status.startsWith('Waits')) return 'warn'
  if (status.startsWith('Playbook')) return 'ok'
  return 'neutral'
}

export function DeviceBadge({ status }: { status: DeviceStatus }) {
  return <Badge tone={deviceTone[status]}>{status === 'unavailable' ? 'quarantined' : status}</Badge>
}

export function IncidentBadge({ status }: { status: IncidentStatus }) {
  return <Badge tone={incidentTone(status)}>{incidentLabel(status)}</Badge>
}

export function RiskBadge({ status }: { status: string }) {
  return <Badge tone={riskTone(status)}>{status}</Badge>
}
