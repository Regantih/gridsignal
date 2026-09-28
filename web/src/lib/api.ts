/** Typed client for api/. Shapes mirror the engine's dataclasses one to one. */

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: 'same-origin', ...init })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
      else if (body.detail) detail = JSON.stringify(body.detail)
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

// ------------------------------------------------------------------ types

export type DeviceStatus = 'online' | 'degraded' | 'offline' | 'unavailable'
export type IncidentStatus = 'open' | 'awaiting_approval' | 'recovering' | 'resolved' | string

export interface Device {
  device_id: string
  site: string
  zone: string
  lat: number
  lon: number
  capacity_kwh: number
  state_of_charge: number
  power_kw: number
  status: DeviceStatus
  last_telemetry_s: number
  assigned_kw: number
  unit_type: string
  controller: string
  home_load_kw: number
  mutual_aid: boolean
  medical_device: boolean
  generator_kw: number
  generator_fuel_h: number
  firmware: string
  gateway: string
  measured_power_kw: number | null
}

export interface GridEvent {
  name: string
  zone: string
  status: string
  target_kw: number
  price_mwh: number
  started_at: string
  ends_at: string
  price_source: string
}

export interface Task {
  task_id: string
  title: string
  owner: string
  status: string
  detail?: string
  [key: string]: unknown
}

export interface Incident {
  incident_id: string
  device_id: string
  severity: string
  status: IncidentStatus
  opened_at: string
  title: string
  root_cause_hypothesis: string
  impact: string
  recommended_action: string
  owner: string
  lost_kw: number
  window_hours: number
  price_mwh: number
  dollars_at_risk: number
  restored_kw: number
  dollars_recovered: number
  cohort: string[]
  approval_required: boolean
  executed_under: string | null
  escalation_reason: string | null
  approved_by: string | null
  approved_at: string | null
  resolved_at: string | null
  tasks: Task[]
}

export interface AuditEvent {
  at: string
  actor: string
  kind: string
  summary: string
  detail: string
  [key: string]: unknown
}

export interface Playbook {
  playbook_id: string
  approved_by: string
  approved_at: string
  expires_at: string
  max_kw: number
  max_devices: number
  executions: number
  revoked_at: string | null
}

export interface FleetSummary {
  total_devices: number
  online: number
  degraded: number
  offline: number
  unavailable: number
  available_capacity_kwh: number
  committed_kw: number
  target_kw: number
  coverage_pct: number
  home_load_kw: number
  discharge_kw: number
  partner_kw: number
  open_incidents: number
  headroom_kw: number
  commit_ratio: number
  remaining_hours: number
  remaining_price_mwh: number
}

export interface PriceRow {
  interval_start: string
  interval_end: string
  spp: number
}

export interface Fleet {
  now: string
  summary: FleetSummary
  grid_event: GridEvent
  devices: Device[]
  incidents: Incident[]
  pending_incident: Incident | null
  playbook: Playbook | null
  playbook_defaults: { max_kw: number; max_devices: number }
  audit: AuditEvent[]
  human_summary: string
  prices: PriceRow[]
  price_trace: { location: string; market: string; date: string; source: string }
  focus_device_id: string
  operator: string
  approval_sentence: string
  disclosure: string
}

export interface Session {
  session_id: string
  fleet_size: number
  fleet_sizes: number[]
  price_scenario: string
  price_scenarios: { key: string; label: string; blurb: string }[]
  learn_source: 'none' | 'synthetic' | 'upload'
  upload_name: string | null
  approval_sentence: string
  disclosure: string
}

export interface Scenarios {
  scenarios: { name: string; regime: number | string; scarcity?: number }[]
  default: string
  live_scenario: string
  live_available: boolean
}

export type Policy = 'naive' | 'gridsignal' | 'gridsignal_auto'

export interface Plan {
  scenario: string
  target: number
  years: number
  homes: number
  safe_ratio: Record<Policy, number>
  curve: Record<Policy, Record<string, number>>
  value_usd_per_year: Record<string, number>
  shortfall_mwh_per_year: Record<Policy, Record<string, number>>
  min_reserve_margin_kwh: number
  assumptions: Record<string, unknown>
  calibrated: boolean
  live_prices: boolean
  recommended_ratio: number
  headline: string
  current_ratio: number
  kept_at_current: Record<Policy, number>
  kept_at_recommended: Record<Policy, number>
  learn_source: string
  calibration_source: string | null
}

export interface RiskGroup {
  kind: 'feeder' | 'ring' | string
  key: string
  devices: string[]
  zones: string[]
  lost_kw: number
  share_of_target: number
  worst_zone_share: number
  spare_kw: number
  uncovered_kw: number
  playbook_refusal: string | null
  lat: number
  lon: number
  seen_together: number
  status: string
}

export interface Risk {
  groups: RiskGroup[]
  summary: Record<string, number>
  status_order: string[]
  target_kw: number
  playbook: Playbook | null
  learn_source: string
  calibration_source: string | null
  feeder_note: string
}

export interface Estimate {
  name: string
  label: string
  prior: number
  learned: number
  low: number
  high: number
  evidence: string
  observed: number | null
}

export interface Cluster {
  slot: string
  key: string
  devices: string[]
  zone_share: Record<string, number>
}

export interface Calibration {
  estimates: Record<string, Estimate>
  clusters: Cluster[]
  devices: number
  reports: number
  days: number
  first: string | null
  last: string | null
  failures: number
  independent_failures: number
  exposure_h: number
  source: string
  notes: string[]
  rows: unknown[]
  synthetic: boolean
}

export interface Learn {
  learn_source: 'none' | 'synthetic' | 'upload'
  sources: string[]
  synthetic: Calibration
  upload: Calibration | null
  upload_name: string | null
}

export interface MemberSummary {
  device_id: string
  site: string
  stored_kwh: number
  committed_kwh: number
  backup_kwh: number
  backup_hours: number
  backup_hours_with_generator: number
  generator_kwh: number
  reserve_kwh: number
  home_load_kw: number
  export_kw: number
  discharge_kw: number
  unit_type: string
  controller: string
  grid_value_usd: number
  earned_usd: number
  protected_usd: number
  is_affected: boolean
  headline: string
  body: string
  next_step: string
}

export interface Member {
  summary: MemberSummary
  neighbours: string[]
  history: AuditEvent[]
  grid_event: GridEvent
  disclosure: string
}

export interface Homes {
  homes: { device_id: string; site: string; zone: string; status: DeviceStatus }[]
  focus_device_id: string
}

export interface FeedStatus {
  days_in_store: number
  first_day: string | null
  last_full_day: string | null
  latest_interval: string | null
  intervals_today: number
  days_fetched: number
  fetched_at: string
  errors: string[]
  revised_intervals: number
}

export interface TodayCheck {
  intervals: number
  inside_share: number
  band_low: number[][]
  band_high: number[][]
  actual: number[][]
  verdict: string
  zones: string[]
}

export interface Feed {
  status: FeedStatus
  intervals_per_day: number
  today: string
  settled_days: number
  hot_days: number
  check: TodayCheck | null
  refresh: { ok: boolean; bucket: number; status: FeedStatus | null; error: string | null } | null
  refresh_seconds: number
  source: string
  disclosure: string
}

export interface About {
  approval_sentence: string
  disclosure: string
  study: {
    stress: {
      years_per_scenario: number
      scenarios: Record<
        string,
        {
          safe_ratio: Record<Policy, number | null>
          results: { policy: Policy; commit_ratio: number; kept_day_rate: number; [k: string]: unknown }[]
        }
      >
      [k: string]: unknown
    }
    validation: Record<string, unknown>
  }
  plan_years: number
  synthetic_days: number
}

// ------------------------------------------------------------------ calls

export const api = {
  session: () => request<Session>('/api/session'),
  reset: (body: { fleet_size?: number; price_scenario?: string }) =>
    request<Session>('/api/session/reset', json('POST', body)),
  fleet: () => request<Fleet>('/api/fleet'),
  trigger: (device_id?: string) =>
    request<{ incident: Incident; fleet: Fleet }>(
      '/api/incidents/trigger',
      json('POST', device_id ? { device_id } : {}),
    ),
  approve: () => request<{ incident: Incident; fleet: Fleet }>('/api/incidents/approve', json('POST')),
  staleWave: (share: number) =>
    request<{ dropped_kw: number; fleet: Fleet }>('/api/incidents/stale-wave', json('POST', { share })),
  approvePlaybook: (body: { max_kw?: number; max_devices?: number }) =>
    request<{ playbook: Playbook; fleet: Fleet }>('/api/playbook', json('POST', body)),
  revokePlaybook: () => request<{ fleet: Fleet }>('/api/playbook', json('DELETE')),
  setCommitment: (ratio: number, basis: string) =>
    request<{ target_kw: number; fleet: Fleet }>('/api/commitment', json('POST', { ratio, basis })),
  scenarios: () => request<Scenarios>('/api/scenarios'),
  plan: (scenario: string, target: number) =>
    request<Plan>(`/api/plan?scenario=${encodeURIComponent(scenario)}&target=${target}`),
  risk: () => request<Risk>('/api/risk'),
  learn: () => request<Learn>('/api/learn'),
  setLearnSource: (source: string) => request<{ learn_source: string }>('/api/learn/source', json('PUT', { source })),
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ learn_source: string; upload_name: string; upload: Calibration }>('/api/learn/upload', {
      method: 'POST',
      body: form,
    })
  },
  homes: () => request<Homes>('/api/members'),
  member: (device_id: string) => request<Member>(`/api/members/${encodeURIComponent(device_id)}`),
  feed: () => request<Feed>('/api/feed'),
  refreshFeed: () => request<Feed>('/api/feed/refresh', json('POST')),
  about: () => request<About>('/api/about'),
}
