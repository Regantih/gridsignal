"""Score the frozen signal policy on days it was never tuned on.

The policy thresholds in :mod:`gridsignal.signals` were written against the two
bundled scenario days. Anything in ``data/holdout/`` is a real LZ_HOUSTON day the
policy has never seen; :func:`evaluate` replays it with the same frozen constants
and reports what the signals earned against the naive schedule, losses included.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from gridsignal import backtest, detect, forecast, signals
from gridsignal.prices import PriceTrace, load_price_trace

HOLDOUT_DIR = Path(__file__).resolve().parents[2] / "data" / "holdout"


def slug_for(date: str) -> str:
    return f"lz_houston_rtm_spp_{date.replace('-', '')}"


def holdout_paths() -> list[Path]:
    """Bundled held-out days, oldest first."""
    return sorted(HOLDOUT_DIR.glob("lz_houston_rtm_spp_*.parquet"))


def load_holdout() -> list[PriceTrace]:
    return [load_price_trace(p) for p in holdout_paths()]


@dataclass(frozen=True)
class DayResult:
    """What the frozen policy earned on one held-out day, per battery."""

    date: str
    peak_mwh: float
    mean_mwh: float
    spike_intervals: int
    signal_usd: float
    naive_usd: float
    uplift_usd: float

    @property
    def won(self) -> bool:
        return self.uplift_usd > 0


def score_day(trace: PriceTrace, kwh: float = backtest.DEFAULT_KWH) -> DayResult:
    """Run detect -> forecast -> signals -> backtest on one day, thresholds untouched."""
    detections = detect.detect_spikes(trace.frame)
    prob = forecast.forecast_spike_probability(forecast.build_features(detections))
    plan = signals.make_signals(detections, prob)
    summary = backtest.summarize(backtest.value_captured(plan, trace.frame, kwh=kwh))
    return DayResult(
        date=trace.date,
        peak_mwh=trace.peak_mwh,
        mean_mwh=trace.mean_mwh,
        spike_intervals=int(detections["is_spike"].sum()),
        signal_usd=summary.signal_usd,
        naive_usd=summary.naive_usd,
        uplift_usd=summary.uplift_usd,
    )


def evaluate(kwh: float = backtest.DEFAULT_KWH) -> list[DayResult]:
    return [score_day(trace, kwh=kwh) for trace in load_holdout()]


def as_frame(results: list[DayResult]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": r.date,
                "peak_mwh": r.peak_mwh,
                "mean_mwh": r.mean_mwh,
                "spikes": r.spike_intervals,
                "signal_usd": r.signal_usd,
                "naive_usd": r.naive_usd,
                "uplift_usd": r.uplift_usd,
            }
            for r in results
        ]
    )


@dataclass(frozen=True)
class HoldoutSummary:
    """Aggregate of the held-out days, per battery per day."""

    days: int
    days_won: int
    total_uplift_usd: float
    mean_uplift_usd: float
    median_uplift_usd: float
    worst_uplift_usd: float
    best_uplift_usd: float

    def fleet_usd(self, devices: int) -> float:
        """Mean daily uplift scaled to a fleet."""
        return round(self.mean_uplift_usd * devices, 2)


def summarize(results: list[DayResult]) -> HoldoutSummary:
    uplifts = pd.Series([r.uplift_usd for r in results], dtype=float)
    return HoldoutSummary(
        days=len(results),
        days_won=int((uplifts > 0).sum()),
        total_uplift_usd=round(float(uplifts.sum()), 2),
        mean_uplift_usd=round(float(uplifts.mean()), 2) if len(uplifts) else 0.0,
        median_uplift_usd=round(float(uplifts.median()), 2) if len(uplifts) else 0.0,
        worst_uplift_usd=round(float(uplifts.min()), 2) if len(uplifts) else 0.0,
        best_uplift_usd=round(float(uplifts.max()), 2) if len(uplifts) else 0.0,
    )


def main() -> None:
    results = evaluate()
    if not results:
        print(f"no held-out days bundled in {HOLDOUT_DIR}")
        return

    print(f"{'date':<12}{'peak $/MWh':>12}{'signal $':>10}{'naive $':>10}{'uplift $':>10}")
    for r in results:
        print(
            f"{r.date:<12}{r.peak_mwh:>12,.2f}{r.signal_usd:>10,.2f}"
            f"{r.naive_usd:>10,.2f}{r.uplift_usd:>10,.2f}"
        )
    s = summarize(results)
    print(
        f"\n{s.days_won}/{s.days} days beat the naive schedule; "
        f"mean ${s.mean_uplift_usd:,.2f}, median ${s.median_uplift_usd:,.2f}, "
        f"worst ${s.worst_uplift_usd:,.2f} per battery per day"
    )


if __name__ == "__main__":
    main()
