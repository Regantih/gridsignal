import { ArrowLeft, MapPinned } from 'lucide-react'
import { STATUS_LABEL, type Market, type MarketId } from '@/lib/markets'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'

/**
 * What the deck says for a market GridSignal does not model. Deliberately free of numbers:
 * no homes, no prices, no dollars, because none exist for it. It names what would have to
 * be true before the market could be modelled, and points back to the live one.
 */
export function MarketPlaceholder({ market, onBack, compact = false }: { market: Market; onBack: (id: MarketId) => void; compact?: boolean }) {
  const planned = market.status === 'planned'
  return (
    <Card data-testid="market-placeholder" className={compact ? '' : 'mx-auto max-w-2xl'}>
      <CardContent className="flex flex-col gap-4 p-6">
        <div className="flex flex-wrap items-center gap-2">
          <MapPinned aria-hidden className="size-4 text-fg-muted" />
          <span className="eyebrow">{market.iso}</span>
          <Badge tone={planned ? 'neutral' : 'warn'}>{STATUS_LABEL[market.status]}</Badge>
        </div>
        <h2 className="font-display text-3xl font-semibold tracking-tight">
          {planned ? `${market.name}: not modelled yet` : `${market.name}: equipment only`}
        </h2>
        {planned ? (
          <>
            <p className="text-sm text-fg-muted">
              Base Power operates in Texas and Illinois. In Illinois it sells through retail choice under the state VPP incentive law, with ComEd compensating battery
              exports; that is not an ERCOT-style energy-only market, so nothing on this deck applies to it yet. GridSignal runs no fleet, prices or dollars here.
            </p>
            <div className="rounded-2xl border border-border bg-surface-2/60 p-4 text-sm">
              <div className="eyebrow mb-2">Before this market can be modelled</div>
              <ul className="list-disc space-y-1 pl-5 text-fg-muted">
                <li>A PJM and ComEd price feed with the same provenance the ERCOT feed has today.</li>
                <li>A ComEd promise model: what is committed, to whom, and what a missed export costs.</li>
                <li>Illinois failure history, so learned rates are not borrowed from Texas.</li>
              </ul>
            </div>
          </>
        ) : (
          <p className="text-sm text-fg-muted">
            Base Power sells equipment in Colorado. There is no grid programme or fleet promise to keep here, so GridSignal has nothing to model and shows nothing.
          </p>
        )}
        <p className="text-xs text-fg-subtle">The dashed outline on the map is an approximate service area for orientation only.</p>
        <div>
          <Button variant="secondary" onClick={() => onBack('ercot')} data-testid="market-back-ercot">
            <ArrowLeft /> Back to Texas, ERCOT, live
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
