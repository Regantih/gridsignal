"""Plan a battery day from the ERCOT day-ahead curve.

ERCOT publishes day-ahead market results the afternoon before the trade day, so the
shape of tomorrow is known before it happens: planning charge and export windows from
it is information the operator genuinely has, not lookahead. The real-time spike
detector then only has to answer a narrower question — has today diverged enough from
the day-ahead plan to be worth deviating from it?

Nothing here dispatches a battery; the output is an advisory plan.
"""

from __future__ import annotations

import pandas as pd

from gridsignal.signals import SPIKE_THRESHOLD, Signal, make_signals

# Frozen after a grid search on the tuning split only — the two bundled scenario days
# plus data/tuning — maximising median uplift per battery per day (scripts/tune_policy.py).
# Several spreads tie at the top; the middle value is taken. The held-out days in
# data/holdout were scored once, afterwards, and never fed back into these numbers.
CHARGE_HOURS = 5
EXPORT_HOURS = 6
# A round trip at 90% efficiency needs the sell price to beat the buy price by ~1.11x
# before it is worth cycling the battery at all; below MIN_SPREAD the plan is to sit out.
MIN_SPREAD = 1.4
DEVIATION_MULTIPLE = 5.0  # real time must beat the day-ahead hour 5x before deviating
CHARGE_CEILING = 5.0  # ...a planned charge is abandoned if real time runs 5x hot
EXPORT_FLOOR = 0.8  # ...a planned export waits if real time is under 80% of day-ahead


def hourly_plan(
    dam: pd.DataFrame,
    charge_hours: int = CHARGE_HOURS,
    export_hours: int = EXPORT_HOURS,
    min_spread: float = MIN_SPREAD,
) -> pd.DataFrame:
    """Charge / hold / export per hour of the day-ahead curve.

    Buys the ``charge_hours`` cheapest day-ahead hours and sells the ``export_hours``
    dearest ones that fall after the last charge hour, but only if the day-ahead spread
    is wide enough to pay for the round trip. A flat day is planned as do-nothing, which
    is the whole point: the naive fixed schedule cycles the battery anyway.
    """
    frame = dam.reset_index(drop=True)[["interval_start", "interval_end", "spp"]].copy()
    frame["planned"] = Signal.HOLD.value
    prices = frame["spp"].astype(float)

    cheapest = prices.nsmallest(charge_hours).index
    # Only hours after the last planned charge can sell what that charge bought.
    sellable = prices[prices.index > max(cheapest)]
    dearest = sellable.nlargest(export_hours).index if len(sellable) else pd.Index([])
    if len(dearest) and prices[dearest].mean() >= min_spread * prices[cheapest].mean():
        frame.loc[cheapest, "planned"] = Signal.CHARGE.value
        frame.loc[dearest, "planned"] = Signal.EXPORT.value
    return frame


def align_to_intervals(plan: pd.DataFrame, interval_start: pd.Series) -> pd.DataFrame:
    """Broadcast an hourly day-ahead plan onto the real-time interval grid.

    Returns ``planned`` and ``dam_mwh`` indexed like ``interval_start``; intervals with
    no matching day-ahead hour (a short trade day) fall back to hold at the day's mean.
    """
    hours = plan.set_index(plan["interval_start"].dt.floor("h"))
    keys = pd.to_datetime(interval_start).dt.floor("h")
    aligned = hours.reindex(keys)
    return pd.DataFrame(
        {
            "planned": aligned["planned"].fillna(Signal.HOLD.value).to_numpy(),
            "dam_mwh": aligned["spp"].fillna(plan["spp"].mean()).astype(float).to_numpy(),
        }
    )


def deviate_from_plan(
    prices: pd.DataFrame,
    spike_prob: pd.Series,
    dam_curve: pd.DataFrame,
    charge_hours: int = CHARGE_HOURS,
    export_hours: int = EXPORT_HOURS,
    min_spread: float = MIN_SPREAD,
    spike_threshold: float = SPIKE_THRESHOLD,
    deviation_multiple: float = DEVIATION_MULTIPLE,
    charge_ceiling: float = CHARGE_CEILING,
    export_floor: float = EXPORT_FLOOR,
) -> pd.DataFrame:
    """Follow the day-ahead plan, overriding it only where real time has diverged.

    Three overrides, in order of precedence:

    - **Scarcity.** Real time at ``deviation_multiple`` times the day-ahead hour with a
      spike forecast: sell now, whatever the plan said. This is the $5,000/MWh case the
      day-ahead curve never priced.
    - **Do not buy a spike.** A planned charge whose real-time print is running
      ``charge_ceiling`` times hot is abandoned; the plan assumed cheap energy.
    - **Do not sell a dud.** A planned export printing under ``export_floor`` of its
      day-ahead hour waits, unless it is the last planned export of the day, where
      selling something beats carrying the energy into tomorrow.
    """
    frame = prices.reset_index(drop=True).copy()
    prob = pd.Series(spike_prob).reset_index(drop=True).astype(float)
    if len(prob) != len(frame):
        raise ValueError(f"spike_prob has {len(prob)} rows, prices has {len(frame)}")

    plan = hourly_plan(dam_curve, charge_hours, export_hours, min_spread)
    aligned = align_to_intervals(plan, frame["interval_start"])
    planned = pd.Series(aligned["planned"].to_numpy(), index=frame.index)
    # A zero or negative day-ahead print would make the divergence ratio meaningless.
    expected = pd.Series(aligned["dam_mwh"].to_numpy(), index=frame.index).clip(lower=0.01)
    spp = frame["spp"].astype(float)
    ratio = spp / expected

    scarcity = (ratio >= deviation_multiple) & (prob >= spike_threshold)
    hot_charge = (planned == Signal.CHARGE.value) & (ratio >= charge_ceiling)
    weak_export = (planned == Signal.EXPORT.value) & (ratio <= export_floor) & ~scarcity
    exports = planned.index[planned == Signal.EXPORT.value]
    if len(exports):
        weak_export.loc[exports.max()] = False

    action = planned.copy()
    action[hot_charge | weak_export] = Signal.HOLD.value
    action[scarcity] = Signal.EXPORT.value

    frame["spike_prob"] = prob.round(4)
    frame["dam_mwh"] = expected.round(2)
    frame["planned"] = planned
    frame["signal"] = action
    frame["reason"] = [
        _plan_reason(a, p, price, exp, dev)
        for a, p, price, exp, dev in zip(action, planned, spp, expected, scarcity, strict=True)
    ]
    return frame


def signals_for(
    prices: pd.DataFrame,
    spike_prob: pd.Series,
    dam_curve: pd.DataFrame | None,
) -> pd.DataFrame:
    """DAM-anchored signals when a day-ahead curve exists, real-time-only when it does not."""
    if dam_curve is None or dam_curve.empty:
        return make_signals(prices, spike_prob)
    return deviate_from_plan(prices, spike_prob, dam_curve)


def _plan_reason(action: str, planned: str, price: float, expected: float, deviated: bool) -> str:
    if deviated:
        return (
            f"real time ${price:,.2f}/MWh is {price / expected:,.1f}x the "
            f"${expected:,.2f} day-ahead print: sell into the spike"
        )
    if action != planned:
        return (
            f"day-ahead planned {planned}, but real time printed ${price:,.2f}/MWh "
            f"against ${expected:,.2f} day-ahead: wait"
        )
    if action == Signal.EXPORT.value:
        return f"day-ahead export window, ${price:,.2f}/MWh real time"
    if action == Signal.CHARGE.value:
        return f"day-ahead charge window, ${price:,.2f}/MWh real time"
    return f"day-ahead plan is to hold at ${expected:,.2f}/MWh"
