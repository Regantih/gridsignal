"""What the day-ahead curve already told you, and what only real time did.

The Open Grid Data question this answers: when a Texas battery earns money on a
scarcity day, was that money visible the afternoon before in the ERCOT day-ahead
curve, or only once real time printed?

For every bundled LZ_HOUSTON day we settle two batteries against the *same* real-time
prices:

* the **day-ahead plan** — charge and export windows picked from the day-ahead curve
  alone, executed blind (:func:`gridsignal.dam.hourly_plan`);
* **perfect real-time foresight** — the same top-k rule applied to the real-time prints
  themselves, at 15-minute resolution. Nobody can run this; it is the ceiling.

``visible_share = day_ahead / foresight`` is then the fraction of the capturable value
that was already knowable in advance, and everything else is the part only real time
revealed. Divergent intervals are counted with the same multiple the policy uses to
deviate (:data:`gridsignal.dam.DEVIATION_MULTIPLE`).

Read-only analysis of public historical prices; nothing here dispatches anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import ceil

import pandas as pd

from gridsignal import backtest, dam, holdout
from gridsignal.prices import SCENARIOS, PriceTrace, load_price_trace
from gridsignal.signals import Signal

INTERVALS_PER_HOUR = 4

#: The visibility study is run on the legacy 13.5 kWh / 5 kW unit. Unit size changes the
#: answer — a bigger battery rides more of a scarcity evening out of the day-ahead plan
#: alone — so the CLI rescores the same days on the 40 kWh / 20 kW default as a comparison
#: instead of quietly switching units under a published share.
STUDY_KWH = backtest.LEGACY_KWH
STUDY_POWER_KW = backtest.LEGACY_POWER_KW


def bundled_traces() -> list[PriceTrace]:
    """Every real LZ_HOUSTON day in the repo: scenario, tuning and held-out."""
    paths = [s.path for s in SCENARIOS.values() if s.path.exists()]
    paths += holdout.tuning_paths() + holdout.holdout_paths()
    traces = [load_price_trace(p) for p in paths]
    return sorted(traces, key=lambda t: t.date)


def foresight_plan(
    prices: pd.DataFrame,
    kwh: float = STUDY_KWH,
    power_kw: float = STUDY_POWER_KW,
    efficiency: float = backtest.ROUND_TRIP_EFFICIENCY,
) -> pd.DataFrame:
    """The best one-cycle day a battery could have had knowing the real-time prints.

    Searches every split of the day into a buy half and a sell half, takes the cheapest
    intervals before it and the dearest after, and scores each candidate through the same
    settlement the backtest uses, so the number is comparable. A ceiling, not a strategy:
    it needs prices that do not exist until the day is over. Doing nothing is always an
    option, so the ceiling is never negative.
    """
    frame = prices.reset_index(drop=True)
    series = frame["spp"].astype(float)
    span = float((frame["interval_end"] - frame["interval_start"]).dt.total_seconds().iloc[0])
    per_interval_kwh = power_kw * span / 3600.0
    # Filling 13.5 kWh through a 90% round trip means buying 15 kWh, then selling it back.
    charge_intervals = max(1, round(kwh / efficiency / per_interval_kwh))
    export_intervals = max(1, ceil(kwh / per_interval_kwh))

    idle = frame[["interval_start", "interval_end", "spp"]].copy()
    idle["signal"] = Signal.HOLD.value

    best_value, best_plan = 0.0, idle
    for split in range(charge_intervals, len(frame) - export_intervals + 1):
        buy = series.iloc[:split].nsmallest(charge_intervals).index
        sell = series.iloc[split:].nlargest(export_intervals).index
        candidate = idle.copy()
        candidate.loc[buy, "signal"] = Signal.CHARGE.value
        candidate.loc[sell, "signal"] = Signal.EXPORT.value
        ledger = backtest.value_captured(
            candidate, frame, kwh=kwh, power_kw=power_kw, efficiency=efficiency
        )
        value = float(ledger["signal_usd"].sum())
        if value > best_value:
            best_value, best_plan = value, candidate

    return best_plan


def day_ahead_plan(trace: PriceTrace) -> pd.DataFrame:
    """The day-ahead plan, broadcast onto the real-time interval grid."""
    if trace.dam is None or trace.dam.empty:
        raise ValueError(f"{trace.date} has no bundled day-ahead curve")
    aligned = dam.align_to_intervals(dam.hourly_plan(trace.dam), trace.frame["interval_start"])
    return aligned.rename(columns={"planned": "signal"})


@dataclass(frozen=True)
class DayInsight:
    """One day of the day-ahead-versus-real-time comparison, per battery."""

    date: str
    peak_mwh: float
    day_ahead_usd: float
    foresight_usd: float
    divergent_intervals: int
    intervals: int
    max_divergence: float

    @property
    def only_real_time_usd(self) -> float:
        """Value a day-ahead-only operator left on the table, per battery."""
        return round(self.foresight_usd - max(self.day_ahead_usd, 0.0), 2)

    @property
    def scarcity(self) -> bool:
        return self.peak_mwh >= SCARCITY_PEAK_MWH

    @property
    def visible_share(self) -> float:
        """Fraction of the capturable value the day-ahead curve already exposed."""
        if self.foresight_usd <= 0:
            return 0.0
        return round(max(self.day_ahead_usd, 0.0) / self.foresight_usd, 4)

    @property
    def divergent_share(self) -> float:
        return round(self.divergent_intervals / self.intervals, 4) if self.intervals else 0.0


def analyze_day(
    trace: PriceTrace,
    kwh: float = STUDY_KWH,
    power_kw: float = STUDY_POWER_KW,
) -> DayInsight:
    """Score one bundled day. Requires a day-ahead curve alongside the real-time trace."""
    prices = trace.frame
    da = backtest.summarize(
        backtest.value_captured(day_ahead_plan(trace), prices, kwh=kwh, power_kw=power_kw)
    )
    fore = backtest.summarize(
        backtest.value_captured(
            foresight_plan(prices, kwh=kwh, power_kw=power_kw),
            prices,
            kwh=kwh,
            power_kw=power_kw,
        )
    )

    aligned = dam.align_to_intervals(dam.hourly_plan(trace.dam), prices["interval_start"])
    expected = aligned["dam_mwh"].clip(lower=0.01).to_numpy()
    spp = prices["spp"].astype(float).to_numpy()
    ratio = pd.Series(spp / expected)
    divergent = ratio >= dam.DEVIATION_MULTIPLE

    return DayInsight(
        date=trace.date,
        peak_mwh=trace.peak_mwh,
        day_ahead_usd=da.signal_usd,
        foresight_usd=fore.signal_usd,
        divergent_intervals=int(divergent.sum()),
        intervals=len(prices),
        max_divergence=round(float(ratio.max()), 1),
    )


def analyze(
    traces: list[PriceTrace] | None = None,
    kwh: float = STUDY_KWH,
    power_kw: float = STUDY_POWER_KW,
) -> list[DayInsight]:
    """Every bundled day that has a day-ahead curve, oldest first."""
    traces = traces if traces is not None else bundled_traces()
    return [
        analyze_day(t, kwh=kwh, power_kw=power_kw)
        for t in traces
        if t.dam is not None and not t.dam.empty
    ]


@dataclass(frozen=True)
class InsightSummary:
    """The headline: how much of the money was in the day-ahead curve all along."""

    days: int
    scarcity_days: int
    visible_share: float
    scarcity_visible_share: float
    ordinary_visible_share: float
    divergent_intervals: int
    ordinary_divergent_intervals: int
    intervals: int
    scarcity_blind_usd: float
    ordinary_day_usd: float
    worst_day: str
    worst_day_share: float

    @property
    def divergent_share(self) -> float:
        return round(self.divergent_intervals / self.intervals, 4) if self.intervals else 0.0

    @property
    def ordinary_days_equivalent(self) -> int:
        """How many whole ordinary days the scarcity-day blind spot is worth."""
        if self.ordinary_day_usd <= 0:
            return 0
        return int(self.scarcity_blind_usd / self.ordinary_day_usd)

    @property
    def headline(self) -> str:
        return (
            f"On the {self.scarcity_days} real ERCOT scarcity days bundled here, the day-ahead "
            f"curve exposed only {self.scarcity_visible_share:.0%} of the value a battery could "
            f"have captured. The "
            f"${self.scarcity_blind_usd:,.2f} per battery that shows up only in real time is "
            f"worth about {self.ordinary_days_equivalent} ordinary trading days "
            f"(${self.ordinary_day_usd:,.2f} each) of perfect optimisation."
        )

    @property
    def subhead(self) -> str:
        return (
            f"{self.divergent_intervals} of {self.intervals:,} 15-minute intervals "
            f"({self.divergent_share:.1%}) printed at least "
            f"{dam.DEVIATION_MULTIPLE:.0f}x their day-ahead hour: "
            f"{self.divergent_intervals - self.ordinary_divergent_intervals} on the "
            f"{self.scarcity_days} scarcity days, {self.ordinary_divergent_intervals} on the "
            f"{self.days - self.scarcity_days} ordinary ones."
        )


SCARCITY_PEAK_MWH = 1000.0  # a day that touched four figures is a scarcity day


def summarize(results: list[DayInsight]) -> InsightSummary:
    """Value-weighted across days, so a $0.20 day cannot outvote a $18 one."""
    if not results:
        raise ValueError("no bundled days with a day-ahead curve")

    scarcity = [r for r in results if r.peak_mwh >= SCARCITY_PEAK_MWH]
    ordinary = [r for r in results if r.peak_mwh < SCARCITY_PEAK_MWH]
    worst = min(results, key=lambda r: r.visible_share)
    return InsightSummary(
        days=len(results),
        scarcity_days=len(scarcity),
        visible_share=_weighted_share(results),
        scarcity_visible_share=_weighted_share(scarcity),
        ordinary_visible_share=_weighted_share(ordinary),
        divergent_intervals=sum(r.divergent_intervals for r in results),
        ordinary_divergent_intervals=sum(r.divergent_intervals for r in ordinary),
        intervals=sum(r.intervals for r in results),
        scarcity_blind_usd=_mean(r.only_real_time_usd for r in scarcity),
        ordinary_day_usd=_mean(r.foresight_usd for r in ordinary),
        worst_day=worst.date,
        worst_day_share=worst.visible_share,
    )


def as_frame(results: list[DayInsight]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": r.date,
                "peak_mwh": r.peak_mwh,
                "day_ahead_usd": r.day_ahead_usd,
                "foresight_usd": r.foresight_usd,
                "visible_share": r.visible_share,
                "only_real_time_usd": r.only_real_time_usd,
                "divergent_intervals": r.divergent_intervals,
                "max_divergence": r.max_divergence,
            }
            for r in results
        ]
    )


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return round(sum(items) / len(items), 2) if items else 0.0


def _weighted_share(results: list[DayInsight]) -> float:
    ceiling = sum(r.foresight_usd for r in results)
    if ceiling <= 0:
        return 0.0
    captured = sum(max(r.day_ahead_usd, 0.0) for r in results)
    return round(captured / ceiling, 4)


def main() -> None:
    results = analyze()
    header = (
        f"{'date':<12}{'peak $/MWh':>12}{'day-ahead $':>13}"
        f"{'foresight $':>13}{'visible':>9}{'5x':>5}"
    )
    print(header)
    for r in results:
        print(
            f"{r.date:<12}{r.peak_mwh:>12,.2f}{r.day_ahead_usd:>13,.2f}"
            f"{r.foresight_usd:>13,.2f}{r.visible_share:>9.0%}{r.divergent_intervals:>5}"
        )
    s = summarize(results)
    print()
    print(f"Scored on the legacy {STUDY_KWH:,.1f} kWh / {STUDY_POWER_KW:,.0f} kW unit (simulated).")
    print(s.headline)
    print(s.subhead)

    bigger = summarize(
        analyze(kwh=backtest.DEFAULT_KWH, power_kw=backtest.DEFAULT_POWER_KW),
    )
    print(
        f"\nComparison, same days on the {backtest.DEFAULT_KWH:,.0f} kWh / "
        f"{backtest.DEFAULT_POWER_KW:,.0f} kW default unit: scarcity days "
        f"{bigger.scarcity_visible_share:.0%} visible in advance against "
        f"{bigger.ordinary_visible_share:.0%} on ordinary days — the gap the legacy unit "
        f"shows ({s.scarcity_visible_share:.0%} against {s.ordinary_visible_share:.0%}) "
        f"does not survive the bigger battery, which rides more of the evening out of the "
        f"day-ahead plan alone."
    )
    print(
        f"value-weighted across all {s.days} days the day-ahead curve held "
        f"{s.visible_share:.0%} of the capturable value "
        f"({s.ordinary_visible_share:.0%} on ordinary days); "
        f"worst day {s.worst_day} at {s.worst_day_share:.0%}"
    )


if __name__ == "__main__":
    main()
