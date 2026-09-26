"""Turn detections and forecasts into charge / hold / export signals."""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd


class Signal(StrEnum):
    CHARGE = "charge"
    HOLD = "hold"
    EXPORT = "export"


# Thresholds are relative to what the day has already done (expanding min/max), never
# fixed $/MWh and never a quantile of the whole day, so the policy stays causal: it can
# be replayed interval by interval knowing only prices it has already seen.
CHARGE_MULTIPLE = 1.15  # charge within 15% of the cheapest price seen today
OPENING_ARB_MULTIPLE = 30.0  # at midnight, only sell for 30x the cheapest energy
CLOSING_ARB_MULTIPLE = 1.5  # by the last interval, take anything clearly profitable
RETENTION = 0.8  # ...and hold unless the print is within 20% of today's best
SPIKE_THRESHOLD = 0.5
TAIL_FRACTION = 0.1  # last 10% of the horizon: empty the battery rather than waste it


def reservation_price(prices: pd.Series, opening: float, closing: float) -> pd.Series:
    """Declining reservation price: what a stored kWh must fetch to be worth selling.

    Starts at ``opening`` times the cheapest energy bought so far and decays linearly to
    ``closing`` by the end of the horizon — the operator holds out for scarcity early in
    the day and becomes progressively less picky as the chance to sell runs out.
    """
    cheapest_so_far = prices.expanding().min()
    remaining = pd.Series(np.linspace(1.0, 0.0, len(prices)), index=prices.index)
    return cheapest_so_far * (closing + (opening - closing) * remaining)


def make_signals(
    prices: pd.DataFrame,
    spike_prob: pd.Series,
    charge_multiple: float = CHARGE_MULTIPLE,
    opening_arb_multiple: float = OPENING_ARB_MULTIPLE,
    closing_arb_multiple: float = CLOSING_ARB_MULTIPLE,
    retention: float = RETENTION,
    spike_threshold: float = SPIKE_THRESHOLD,
) -> pd.DataFrame:
    """Recommend charge / hold / export per interval from real-time prices alone.

    This is the fallback for a day with no day-ahead curve bundled; when there is one,
    :func:`gridsignal.dam.signals_for` plans the day from it instead and uses these
    real-time detections only to deviate.

    Two gates must open before energy leaves a battery: the price clears the declining
    :func:`reservation_price`, *and* either it is within ``retention`` of today's best
    print, the forecast says a spike is landing, or the horizon is nearly over. Holding
    through a merely-good $35/MWh morning is what leaves the battery full for a
    $5,000/MWh afternoon.

    Advisory only: nothing here dispatches a battery.
    """
    frame = prices.reset_index(drop=True).copy()
    prob = pd.Series(spike_prob).reset_index(drop=True).astype(float)
    if len(prob) != len(frame):
        raise ValueError(f"spike_prob has {len(prob)} rows, prices has {len(frame)}")

    spp = frame["spp"].astype(float)
    reservation = reservation_price(spp, opening_arb_multiple, closing_arb_multiple)
    remaining = pd.Series(np.linspace(1.0, 0.0, len(frame)))
    likely_spike = prob >= spike_threshold

    worth_selling = spp >= reservation
    sell_now = (
        (spp >= retention * spp.expanding().max()) | likely_spike | (remaining <= TAIL_FRACTION)
    )
    cheap = spp <= charge_multiple * spp.expanding().min()

    frame["spike_prob"] = prob.round(4)
    frame["reservation_mwh"] = reservation.round(2)
    frame["signal"] = Signal.HOLD.value
    frame.loc[cheap & ~likely_spike, "signal"] = Signal.CHARGE.value
    frame.loc[worth_selling & sell_now, "signal"] = Signal.EXPORT.value
    frame["reason"] = [
        _reason(s, p, price, r)
        for s, p, price, r in zip(frame["signal"], prob, spp, reservation, strict=True)
    ]
    return frame


def _reason(signal: str, prob: float, price: float, reservation: float) -> str:
    if signal == Signal.EXPORT.value:
        return (
            f"${price:,.2f}/MWh clears the ${reservation:,.2f} reservation price, "
            f"spike probability {prob:.0%}"
        )
    if signal == Signal.CHARGE.value:
        return f"near today's cheapest energy at ${price:,.2f}/MWh, no spike forecast"
    return f"hold: ${price:,.2f}/MWh is below the ${reservation:,.2f} reservation price"
