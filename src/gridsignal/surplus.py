"""Where the fleet's spare kW goes during a high-price window.

An operator looking at a 10,000-home fleet sitting on tens of MW of nameplate power
deserves one answer per kW: committed to the event, offered on top of it, or held for
a reason with a name — the member's backup reserve, the member's own house, a unit
that is offline, another tenant's batteries, a feeder that cannot take it, or a price
that does not cover the modelled cycle wear.

Everything here is simulated. Prices are real cached ERCOT settlement prints; the
fleet, the feeder limits and the wear cost are assumptions of this repo.

Reproduce with::

    python -m gridsignal.surplus
"""

from __future__ import annotations

from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.engine import SurplusOffer

FLEET_SIZES = (48, 10_000)


def offer_for(fleet_size: int) -> tuple[SurplusOffer, SurplusOffer]:
    """The surplus before and after offering it, for a fleet of ``fleet_size``."""
    engine = ControlRoomEngine(fleet_size=fleet_size)
    before = engine.surplus_offer()
    engine.offer_surplus()
    after = engine.surplus_offer()
    return before, after


def report(fleet_size: int) -> str:
    before, after = offer_for(fleet_size)
    lines = [
        f"fleet of {fleet_size:,} simulated devices — "
        f"${before.price_mwh:,.2f}/MWh over {before.hours:.2f} h"
    ]
    lines.append(
        f"  event target      {before.target_kw:>12,.0f} kW  "
        f"committed {before.committed_kw:>12,.0f} kW"
    )
    lines.append(
        f"  surplus offered   {before.offerable_kw:>12,.0f} kW  "
        f"${before.revenue_usd:,.2f} simulated revenue, "
        f"${before.net_usd:,.2f} after ${before.wear_usd:,.2f} of modelled wear"
    )
    for held in before.held:
        lines.append(f"  held {held.kw:>12,.0f} kW  {held.reason}")
    lines.append(
        f"  after offering, committed {after.committed_kw:,.0f} kW and "
        f"{after.offerable_kw:,.0f} kW still offerable"
    )
    return "\n".join(lines)


def main() -> None:
    print("Idle capacity during the peak window (simulated fleet, real cached prices)")
    for fleet_size in FLEET_SIZES:
        print()
        print(report(fleet_size))


if __name__ == "__main__":
    main()
