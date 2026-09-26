"""Estimate dollars captured by following the signals versus a naive schedule."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from gridsignal.signals import Signal

# A Base-style home battery: ~13.5 kWh usable, 5 kW inverter, 90% round-trip efficiency.
DEFAULT_KWH = 13.5
DEFAULT_POWER_KW = 5.0
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

    def fleet_usd(self, devices: int) -> float:
        """Uplift if every device in a fleet of ``devices`` followed the signals."""
        return round(self.uplift_usd * devices, 2)


def value_captured(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    kwh: float = DEFAULT_KWH,
    power_kw: float = DEFAULT_POWER_KW,
    efficiency: float = ROUND_TRIP_EFFICIENCY,
) -> pd.DataFrame:
    """Settle both strategies interval by interval and return the ledger.

    Charging buys energy at the settlement price (a cost), exporting sells stored
    energy at it (revenue), both limited by the inverter and the state of charge.
    Columns added per strategy ``s`` in ``(signal, naive)``: ``{s}_action``,
    ``{s}_soc_kwh``, ``{s}_usd``, ``{s}_cum_usd``.
    """
    frame = prices.reset_index(drop=True)[["interval_start", "interval_end", "spp"]].copy()
    plan = signals.reset_index(drop=True)
    if len(plan) != len(frame):
        raise ValueError(f"signals has {len(plan)} rows, prices has {len(frame)}")

    hours = _interval_hours(frame)
    frame["signal_action"] = plan["signal"].to_numpy()
    frame["naive_action"] = [_naive_action(ts) for ts in frame["interval_start"]]

    for strategy in ("signal", "naive"):
        soc, socs, cashflows = 0.0, [], []
        for action, price, span in zip(
            frame[f"{strategy}_action"], frame["spp"].astype(float), hours, strict=True
        ):
            delta = 0.0
            if action == Signal.CHARGE.value:
                bought = min(power_kw * span, (kwh - soc) / efficiency)
                soc += bought * efficiency
                delta = -bought * price / 1000.0
            elif action == Signal.EXPORT.value:
                sold = min(power_kw * span, soc)
                soc -= sold
                delta = sold * price / 1000.0
            socs.append(round(soc, 3))
            cashflows.append(round(delta, 4))
        frame[f"{strategy}_soc_kwh"] = socs
        frame[f"{strategy}_usd"] = cashflows
        frame[f"{strategy}_cum_usd"] = frame[f"{strategy}_usd"].cumsum().round(4)

    return frame


def summarize(ledger: pd.DataFrame) -> BacktestSummary:
    """Collapse a ledger from :func:`value_captured` into the headline numbers."""
    signal_usd = round(float(ledger["signal_usd"].sum()), 2)
    naive_usd = round(float(ledger["naive_usd"].sum()), 2)
    uplift = round(signal_usd - naive_usd, 2)
    # State of charge starts empty, so prepend 0 to catch energy moved in interval one.
    moved = pd.Series([0.0, *ledger["signal_soc_kwh"]]).diff().dropna()
    return BacktestSummary(
        signal_usd=signal_usd,
        naive_usd=naive_usd,
        uplift_usd=uplift,
        # Against a naive day that loses money, percentage uplift is meaningless.
        uplift_pct=round(100.0 * uplift / abs(naive_usd), 1) if naive_usd else 0.0,
        exported_kwh=round(float(-moved.clip(upper=0).sum()), 2),
        charged_kwh=round(float(moved.clip(lower=0).sum()), 2),
    )


def _interval_hours(frame: pd.DataFrame) -> list[float]:
    span = (frame["interval_end"] - frame["interval_start"]).dt.total_seconds() / 3600.0
    return [float(h) for h in span]


def _naive_action(timestamp: pd.Timestamp) -> str:
    if timestamp.hour in NAIVE_CHARGE_HOURS:
        return Signal.CHARGE.value
    if timestamp.hour in NAIVE_EXPORT_HOURS:
        return Signal.EXPORT.value
    return Signal.HOLD.value
