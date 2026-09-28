import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, type Fleet } from '@/lib/api'
import { keys } from '@/lib/queries'
import { useTimeline } from '@/lib/timeline'

/**
 * "Replay tonight": the whole story in about forty seconds, for a demo. It runs the real
 * engine through the same endpoints the buttons use (reset, an incident, an approval, a
 * playbook) and then sweeps the evening on the shared clock. The approval is made by the
 * signed-in demo operator, and the audit trail records it like any other.
 */

export interface ReplayStep {
  at: number
  caption: (f: Fleet | undefined) => string
  run?: () => Promise<Fleet | void>
}

export type ReplayPhase = 'idle' | 'running' | 'done'

export function useReplay(sessionArgs: () => { fleet_size: number; price_scenario: string } | null) {
  const qc = useQueryClient()
  const tl = useTimeline()
  const [phase, setPhase] = useState<ReplayPhase>('idle')
  const [step, setStep] = useState(-1)
  const timers = useRef<number[]>([])
  const tlRef = useRef(tl)
  tlRef.current = tl

  const put = useCallback(
    (f: Fleet | void) => {
      if (f) qc.setQueryData(keys.fleet, f)
      void qc.invalidateQueries({ queryKey: ['risk'] })
      void qc.invalidateQueries({ queryKey: ['member'] })
    },
    [qc],
  )

  const steps: ReplayStep[] = [
    {
      at: 0,
      caption: () => 'The evening event opens. Every home is covered and light flows from each home to its zone.',
      run: async () => {
        const a = sessionArgs()
        if (a) await api.reset(a)
        await qc.invalidateQueries()
        tlRef.current.goLive()
      },
    },
    {
      at: 7000,
      caption: (f) => {
        const i = f?.pending_incident
        return i
          ? `A gateway loses uplink. ${i.lost_kw.toFixed(1)} kW drops out and $${i.dollars_at_risk.toFixed(2)} is at risk. Nothing moves until a person approves.`
          : 'A gateway loses uplink. Dollars are at risk. Nothing moves until a person approves.'
      },
      run: async () => put((await api.trigger()).fleet),
    },
    {
      at: 19000,
      caption: (f) => {
        const i = f?.incidents.find((x) => x.status === 'resolved')
        return i
          ? `The operator approves, one incident at a time. Healthy homes pick up ${i.restored_kw.toFixed(1)} kW and $${i.dollars_recovered.toFixed(2)} of $${i.dollars_at_risk.toFixed(2)} comes back.`
          : 'The operator approves, one incident at a time. Healthy homes pick up the gap.'
      },
      run: async () => {
        const f = qc.getQueryData<Fleet>(keys.fleet)
        if (f && !f.pending_incident) return
        put((await api.approve()).fleet)
      },
    },
    {
      at: 29000,
      caption: (f) =>
        f?.playbook
          ? `A playbook with limits is approved in advance: up to ${f.playbook.max_kw.toFixed(1)} kW and ${f.playbook.max_devices} home${f.playbook.max_devices === 1 ? '' : 's'} per incident. Anything larger still waits for a person.`
          : 'A playbook with limits is approved in advance. Anything larger still waits for a person.',
      run: async () => {
        const f = qc.getQueryData<Fleet>(keys.fleet)
        if (!f) return
        put((await api.approvePlaybook({ max_kw: f.playbook_defaults.max_kw, max_devices: f.playbook_defaults.max_devices })).fleet)
      },
    },
    {
      at: 34000,
      caption: () => 'The evening on one clock: price, the incident, the approval and the playbook, replayed at 600x.',
      run: async () => {
        const t = tlRef.current
        t.setSpeed(600)
        t.seek(t.range[0])
        t.play()
      },
    },
    {
      at: 42000,
      caption: () => 'That was tonight. Scrub the timeline, hover a home, or throw something else at the fleet.',
      run: async () => {
        tlRef.current.setSpeed(60)
        tlRef.current.goLive()
      },
    },
  ]

  const stop = useCallback(() => {
    timers.current.forEach((t) => window.clearTimeout(t))
    timers.current = []
    setPhase('idle')
    setStep(-1)
    tlRef.current.setSpeed(60)
    tlRef.current.goLive()
  }, [])

  const start = useCallback(() => {
    stop()
    setPhase('running')
    steps.forEach((s, i) => {
      timers.current.push(
        window.setTimeout(() => {
          setStep(i)
          void s.run?.().catch(() => {})
          if (i === steps.length - 1) {
            timers.current.push(window.setTimeout(() => { setPhase('done'); setStep(-1) }, 6000))
          }
        }, s.at),
      )
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stop])

  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), [])

  const fleet = qc.getQueryData<Fleet>(keys.fleet)
  return {
    phase,
    step,
    total: steps.length,
    caption: step >= 0 ? steps[step].caption(fleet) : null,
    start,
    stop,
  }
}
