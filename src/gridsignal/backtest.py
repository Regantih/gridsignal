"""Estimate dollars captured by following the signals versus a naive schedule."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from gridsignal.load import HOURLY_LOAD_KW
from gridsignal.signals import Signal

# The unit this product is modelled on: a Base Core-style 40 kWh / 20 kW home battery
# (sized from public interviews, not vendor data), at 90% round-trip efficiency.
DEFAULT_KWH = 40.0
DEFAULT_POWER_KW = 20.0
# The older, smaller unit most fleets still run, kept as the comparison case.
LEGACY_KWH = 13.5
LEGACY_POWER_KW = 5.0
ROUND_TRIP_EFFICIENCY = 0.90
# The strategy every battery owner runs without a signal: cheap overnight charge,
# evening export, same clock times regardless of what the market is doing.
NAIVE_CHARGE_HOURS = range(1, 5)
NAIVE_EXPORT_HOURS = range(17, 21)


@dataclass(frozen=True)
class BacktestSummary:
    """Headline numbers for one day of one battery."""

    signal_usd: float
    naive_usd: float
    uplift_usd: float
    uplift_pct: float
    exported_kwh: float
    charged_kwh: float
    #: Stored energy the battery gave the house instead of the grid.
    home_served_kwh: float = 0.0
    #: What that energy would have cost the member at the settlement price.
    member_savings_usd: float = 0.0
    #: The same saving under the naive clock, so the two are compared like for like.
    naive_savings_usd: float = 0.0
    #: Grid energy bought to cover the house while storage was held for the peak.
    home_from_grid_kwh: float = 0.0

    @property
    def household_usd(self) -> float:
        """Everything the battery was worth to the household: export plus bill avoided."""
        return round(self.signal_usd + self.member_savings_usd, 2)

    @property
    def naive_household_usd(self) -> float:
        return round(self.naive_usd + self.naive_savings_usd, 2)

    @property
    def uplift_vs_nothing_usd(self) -> float:
        """Against a battery that never moves, which earns and saves nothing."""
        return self.household_usd

    def fleet_usd(self, devices: int) -> float:
        """Uplift if every device in a fleet of ``devices`` followed the signals."""
        return round(self.uplift_usd * devices, 2)


def value_captured(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    kwh: float = DEFAULT_KWH,
    power_kw: float = DEFAULT_POWER_KW,
    efficiency: float = ROUND_TRIP_EFFICIENCY,
    serve_home: bool = False,
    hold_for_peak: bool = True,
) -> pd.DataFrame:
    """Settle both strategies interval by interval and return the ledger.

    Charging buys energy at the settlement price (a cost), exporting sells stored
    energy at it (revenue), both limited by the inverter and the state of charge.
    Columns added per strategy ``s`` in ``(signal, naive)``: ``{s}_action``,
    ``{s}_soc_kwh``, ``{s}_usd``, ``{s}_cum_usd``, ``{s}_home_kwh``.

    With ``serve_home`` the battery is home-first: the simulated house is served
    from storage before anything is sold, the inverter limit covers both, and only
    the surplus is exported. The home is left on the grid while the battery is
    charging, so a cheap overnight charge is not spent on the house immediately.

    Home load is only served from storage out of energy the day-ahead plan does not
    still need for its remaining export hours, unless this hour's day-ahead price is
    already at least as high as those hours pay — otherwise an ordinary afternoon of
    household load empties the battery before the evening it was charged for. The
    reserve is read from the day-ahead curve, published the afternoon before, so the
    decision uses nothing the operator would not have had. ``hold_for_peak=False``
    restores the earlier behaviour, where the house drank the battery dry all afternoon.
    """
    frame = prices.reset_index(drop=True)[["interval_start", "interval_end", "spp"]].copy()
    plan = signals.reset_index(drop=True)
    if len(plan) != len(frame):
        raise ValueError(f"signals has {len(plan)} rows, prices has {len(frame)}")

    hours = _interval_hours(frame)
    frame["signal_action"] = plan["signal"].to_numpy()
    frame["naive_action"] = [_naive_action(ts) for ts in frame["interval_start"]]

    loads = _home_load_kwh(frame, hours) if serve_home else [0.0] * len(frame)
    reserved, peak_now = (
        _peak_reserve(plan, hours, power_kw, kwh)
        if hold_for_peak
        else ([0.0] * len(hours), [True] * len(hours))
    )

    for strategy in ("signal", "naive"):
        soc, socs, cashflows, served, from_grid = 0.0, [], [], [], []
        for action, price, span, load_kwh, reserve, at_peak in zip(
            frame[f"{strategy}_action"],
            frame["spp"].astype(float),
            hours,
            loads,
            reserved,
            peak_now,
            strict=True,
        ):
            delta = 0.0
            home_kwh = 0.0
            if action != Signal.CHARGE.value:
                spare = soc if (at_peak or strategy == "naive") else max(soc - reserve, 0.0)
                home_kwh = min(load_kwh, spare, power_kw * span)
                soc -= home_kwh
            if action == Signal.CHARGE.value:
                bought = min(power_kw * span, (kwh - soc) / efficiency)
                soc += bought * efficiency
                delta = -bought * price / 1000.0
            elif action == Signal.EXPORT.value:
                sold = min(max(power_kw * span - home_kwh, 0.0), soc)
                soc -= sold
                delta = sold * price / 1000.0
            socs.append(round(soc, 3))
            cashflows.append(round(delta, 4))
            served.append(round(home_kwh, 4))
            from_grid.append(round(max(load_kwh - home_kwh, 0.0), 4))
        frame[f"{strategy}_soc_kwh"] = socs
        frame[f"{strategy}_usd"] = cashflows
        frame[f"{strategy}_home_kwh"] = served
        frame[f"{strategy}_grid_kwh"] = from_grid
        frame[f"{strategy}_cum_usd"] = frame[f"{strategy}_usd"].cumsum().round(4)

    return frame


def summarize(ledger: pd.DataFrame) -> BacktestSummary:
    """Collapse a ledger from :func:`value_captured` into the headline numbers."""
    signal_usd = round(float(ledger["signal_usd"].sum()), 2)
    naive_usd = round(float(ledger["naive_usd"].sum()), 2)
    uplift = round(signal_usd - naive_usd, 2)
    # State of charge starts empty, so prepend 0 to catch energy moved in interval one.
    moved = pd.Series([0.0, *ledger["signal_soc_kwh"]]).diff().dropna()
    home = ledger["signal_home_kwh"]
    spp = ledger["spp"].astype(float)
    savings = float((home * spp / 1000.0).sum())
    naive_savings = float((ledger["naive_home_kwh"] * spp / 1000.0).sum())
    grid_kwh = float(ledger["signal_grid_kwh"].sum()) if "signal_grid_kwh" in ledger else 0.0
    return BacktestSummary(
        signal_usd=signal_usd,
        naive_usd=naive_usd,
        uplift_usd=uplift,
        # Against a naive day that loses money, percentage uplift is meaningless.
        uplift_pct=round(100.0 * uplift / abs(naive_usd), 1) if naive_usd else 0.0,
        exported_kwh=round(float(-moved.clip(upper=0).sum() - home.sum()), 2),
        charged_kwh=round(float(moved.clip(lower=0).sum()), 2),
        home_served_kwh=round(float(home.sum()), 2),
        member_savings_usd=round(savings, 2),
        naive_savings_usd=round(naive_savings, 2),
        home_from_grid_kwh=round(grid_kwh, 2),
    )


def _peak_reserve(
    plan: pd.DataFrame, hours: list[float], power_kw: float, kwh: float
) -> tuple[list[float], list[bool]]:
    """Energy each interval owes the day-ahead plan's remaining export hours.

    Also returns, per interval, whether the day-ahead curve prices this hour at least as
    high as those remaining hours: when it does there is nothing to save the energy for,
    so the house may have it. Without a day-ahead plan in the frame nothing is reserved.
    """
    if "planned" not in plan or "dam_mwh" not in plan:
        return [0.0] * len(hours), [True] * len(hours)
    planned = list(plan["planned"])
    dam_mwh = [float(p) for p in plan["dam_mwh"]]

    reserved, peak_now, owed, best_ahead = [], [], 0.0, 0.0
    for i in reversed(range(len(hours))):
        reserved.append(min(owed, kwh))
        peak_now.append(dam_mwh[i] >= best_ahead)
        if planned[i] == Signal.EXPORT.value:
            owed += power_kw * hours[i]
            best_ahead = max(best_ahead, dam_mwh[i])
    return reserved[::-1], peak_now[::-1]


def _home_load_kwh(frame: pd.DataFrame, hours: list[float]) -> list[float]:
    """Simulated household draw for each interval, in kWh."""
    return [
        HOURLY_LOAD_KW[pd.Timestamp(ts).hour % 24] * span
        for ts, span in zip(frame["interval_start"], hours, strict=True)
    ]


def _interval_hours(frame: pd.DataFrame) -> list[float]:
    span = (frame["interval_end"] - frame["interval_start"]).dt.total_seconds() / 3600.0
    return [float(h) for h in span]


def _naive_action(timestamp: pd.Timestamp) -> str:
    if timestamp.hour in NAIVE_CHARGE_HOURS:
        return Signal.CHARGE.value
    if timestamp.hour in NAIVE_EXPORT_HOURS:
        return Signal.EXPORT.value
    return Signal.HOLD.value
