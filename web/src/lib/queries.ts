import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Fleet } from '@/lib/api'

export const keys = {
  session: ['session'] as const,
  fleet: ['fleet'] as const,
  risk: ['risk'] as const,
  plan: (scenario: string, target: number) => ['plan', scenario, target] as const,
  learn: ['learn'] as const,
  scenarios: ['scenarios'] as const,
  homes: ['homes'] as const,
  member: (id: string) => ['member', id] as const,
  feed: ['feed'] as const,
  about: ['about'] as const,
}

export function useFleet() {
  return useQuery({ queryKey: keys.fleet, queryFn: api.fleet })
}

/** A mutation that returns the fresh fleet; everything derived from the engine is refetched. */
export function useFleetMutation<TVars, TResult extends { fleet: Fleet }>(
  fn: (vars: TVars) => Promise<TResult>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      qc.setQueryData(keys.fleet, result.fleet)
      void qc.invalidateQueries({ queryKey: ['risk'] })
      void qc.invalidateQueries({ queryKey: ['plan'] })
      void qc.invalidateQueries({ queryKey: ['member'] })
      void qc.invalidateQueries({ queryKey: ['homes'] })
    },
  })
}

export function useResetSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.reset,
    onSuccess: () => void qc.invalidateQueries(),
  })
}
