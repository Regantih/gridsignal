import { useQuery } from '@tanstack/react-query'
import { ShieldCheck, Database, Scale, Eye, Route } from 'lucide-react'
import type { ReactNode } from 'react'
import { api, type About, type Policy } from '@/lib/api'
import { keys } from '@/lib/queries'
import { count, pct } from '@/lib/format'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { PageHeader, Section, Callout } from '@/components/page'
import { CardSkeleton, ErrorState } from '@/components/states'

const POLICY: Record<Policy, string> = {
  naive: 'Naive schedule',
  gridsignal: 'GridSignal, approval every time',
  gridsignal_auto: 'GridSignal with playbook',
}

export function HowItWorksPage() {
  const about = useQuery({ queryKey: keys.about, queryFn: api.about, staleTime: Infinity })
  return (
    <div className="flex flex-col gap-10">
      <PageHeader
        kicker="How it works"
        title="Why trust it, and where it stops"
        lede="GridSignal helps a home-battery fleet keep the promise it makes to the grid. It does not dispatch anything on its own. This page says what the numbers are made of and what they are not."
      />

      <Callout tone="info">
        <strong>The rule that never bends:</strong> in GridSignal{' '}
        <span data-testid="approval-sentence">{about.data?.approval_sentence ?? 'a human operator approves every recovery action, one incident at a time or in advance through a playbook with limits'}</span>.
        {' '}
        <span data-testid="disclosure">{about.data?.disclosure ?? 'Prices are real ERCOT data. The fleet is simulated.'}</span>
      </Callout>

      <Section title="What you are looking at">
        <div className="grid gap-4 md:grid-cols-2">
          <Explainer icon={Database} title="Real prices, simulated homes">
            Every dollar figure is priced at the ERCOT real-time settlement price for the load zone and 15-minute interval it belongs to. The homes, batteries, telemetry and failures are a deterministic simulation with a fixed seed, so the same evening replays the same way for everyone. Feeders are simulated as zone quadrants and gateway rings come from device ids; real feeder ids are not in this data.
          </Explainer>
          <Explainer icon={ShieldCheck} title="Nothing moves without a person">
            When a home drops out during a grid event the engine opens an incident, prices the shortfall, proposes a fix and stops. Recovery only runs after an operator approves it, or when an operator has approved a playbook in advance whose limits cover the loss. Anything outside the limits is escalated and waits.
          </Explainer>
          <Explainer icon={Scale} title="Recovered can never exceed at risk">
            Dollars at risk are the lost kW, times the remaining hours in the window, times the current price. Dollars recovered are capped at that number by the engine itself: a fix cannot claim more than the problem was worth.
          </Explainer>
          <Explainer icon={Route} title="Where the recommendation comes from">
            The planner takes tonight's fleet as measured (sizes, charge, load, share of degraded units), draws {about.data ? count(about.data.plan_years) : 'three'} years of days from a price model fitted on ERCOT history, injects failures at the learned rates and replays the recovery rules at each commitment level. The recommendation is the largest level where the playbook policy keeps the promise on your target share of days.
          </Explainer>
          <Explainer icon={Eye} title="Failure rates: SYNTHETIC unless you upload">
            The bundled history is {about.data ? count(about.data.synthetic_days) : '60'} days of synthetic telemetry generated with known rates. It exists to show the learning method, and it is labelled SYNTHETIC wherever it is used. Upload your own JSONL telemetry to replace it; the planner and the risk map switch to your rates.
          </Explainer>
          <Explainer icon={Eye} title="The live feed checks the model">
            Every 15 minutes the app can fetch today's ERCOT prices and compare them with the 5 to 95% band the price model would have drawn for this month. If a fetch fails the stored history is used and the failure is shown, not hidden.
          </Explainer>
        </div>
      </Section>

      <Section title="Honest limits" description="Things a careful reader should know before trusting a number.">
        <ul className="grid gap-3 md:grid-cols-2">
          {[
            'The fleet is simulated. Nothing here has dispatched a real battery, and the engine has no connection to any device.',
            'Failure rates are assumptions unless you upload telemetry. The most sensitive one is how much of a zone one outage takes out; at half a zone the safe commitment falls to 50%.',
            'GridSignal does not claim to earn more than a naive schedule. All policies deliver the same schedule when nothing fails; the difference is how often the promise survives a failure.',
            'The price model is fitted on history and has deliberately wide bands. Nothing fitted on history predicted how fast ERCOT spikes faded from 2022 to 2025, and it will not predict the next regime change either.',
            'The reliability curve is from simulated years, not measured ones. Its confidence comes from the number of replays, not from real outcomes.',
            'Correlated-risk groups are simulated feeders and gateway rings. Real feeder topology would change which homes fail together.',
            'Member earnings are the fleet\'s grid value attributed to one home for tonight\'s event only; they are not a bill or a payout.',
            'This is a decision aid. The operator owns the commitment, the approvals and the consequences.',
          ].map((t) => (
            <li key={t} className="rounded-md border border-border bg-surface p-3 text-sm">{t}</li>
          ))}
        </ul>
      </Section>

      <Section title="The stress test behind the playbook" description="From the bundled study: the share of days each policy keeps its promise over 30 simulated years per scenario.">
        {about.isPending && <CardSkeleton lines={6} />}
        {about.isError && <ErrorState error={about.error} retry={() => void about.refetch()} />}
        {about.data && <StudyTable study={about.data.study} />}
      </Section>

      <Section title="Who this is for">
        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader><CardTitle>Fleet operator</CardTitle><CardDescription>Tonight and Tomorrow</CardDescription></CardHeader>
            <CardContent className="text-sm text-fg-muted">Will we keep the promise tonight, what failed and what it costs, approve the fix, decide tomorrow's commitment, see which homes fail together.</CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Member</CardTitle><CardDescription>Member</CardDescription></CardHeader>
            <CardContent className="text-sm text-fg-muted">Is my home backed up, what did the fleet earn from my battery, is my reserve protected, what happened tonight in plain words.</CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Market analyst</CardTitle><CardDescription>Market and Tomorrow</CardDescription></CardHeader>
            <CardContent className="text-sm text-fg-muted">Live ERCOT prices against the model's expected band, scarcity days, and why the plan recommends what it does.</CardContent>
          </Card>
        </div>
      </Section>
    </div>
  )
}

function Explainer({ icon: Icon, title, children }: { icon: typeof Eye; title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader className="flex-row items-center gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-md bg-brand-soft text-fg"><Icon aria-hidden className="size-4" /></span>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-fg-muted">{children}</CardContent>
    </Card>
  )
}

function StudyTable({ study }: { study: About['study'] }) {
  const scenarios = Object.entries(study.stress.scenarios)
  const policies: Policy[] = ['naive', 'gridsignal', 'gridsignal_auto']
  const at = (results: About['study']['stress']['scenarios'][string]['results'], p: Policy, r: number) =>
    results.find((x) => x.policy === p && Math.abs(x.commit_ratio - r) < 1e-6)?.kept_day_rate
  return (
    <Card>
      <CardContent className="overflow-x-auto p-0">
        <table className="w-full text-sm" data-testid="study-table">
          <thead className="text-left text-xs uppercase tracking-wide text-fg-subtle">
            <tr>
              <th className="p-3 font-medium">Scenario</th>
              {policies.map((p) => (
                <th key={p} className="p-3 text-right font-medium">{POLICY[p]}<br /><span className="normal-case">days kept at 75%</span></th>
              ))}
              <th className="p-3 text-right font-medium">Safe with playbook<br /><span className="normal-case">99% of days</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {scenarios.map(([name, sc]) => (
              <tr key={name}>
                <td className="p-3">{name}</td>
                {policies.map((p) => {
                  const v = at(sc.results, p, 0.75)
                  return <td key={p} className="p-3 text-right tabular">{v === undefined ? '' : pct(v, 1)}</td>
                })}
                <td className="p-3 text-right tabular font-medium">{sc.safe_ratio.gridsignal_auto ? pct(sc.safe_ratio.gridsignal_auto) : 'none'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="p-3 text-xs text-fg-subtle">{count(study.stress.years_per_scenario)} simulated years per scenario. The playbook column is the largest commitment that kept the promise on at least 99% of days.</p>
      </CardContent>
    </Card>
  )
}
