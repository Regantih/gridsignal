"""Score the frozen signal policy on days it was never tuned on.

The policy parameters in :mod:`gridsignal.dam` were fitted on the two bundled scenario
days plus ``data/tuning/`` and nothing else. Anything in ``data/holdout/`` is a real
LZ_HOUSTON day the policy has never seen; :func:`evaluate` replays it with the same
frozen constants and reports what the signals earned against the naive schedule,
losses included.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from gridsignal import backtest, dam, detect, forecast, paths
from gridsignal.prices import AS_SUFFIX, DAM_SUFFIX, PriceTrace, load_price_trace

DATA_DIR = paths.DATA_DIR
HOLDOUT_DIR = DATA_DIR / "holdout"
TUNING_DIR = DATA_DIR / "tuning"


def slug_for(date: str) -> str:
    return f"lz_houston_rtm_spp_{date.replace('-', '')}"


def rtm_paths(directory: Path) -> list[Path]:
    """Real-time traces in a split directory, oldest first.

    Day-ahead and ancillary siblings live next to each trace under the same prefix and
    are not days of their own.
    """
    siblings = (DAM_SUFFIX, AS_SUFFIX)
    return sorted(
        p for p in directory.glob("lz_houston_rtm_spp_*.parquet") if not p.stem.endswith(siblings)
    )


def holdout_paths() -> list[Path]:
    """Bundled held-out days, oldest first."""
    return rtm_paths(HOLDOUT_DIR)


def load_holdout() -> list[PriceTrace]:
    return [load_price_trace(p) for p in holdout_paths()]


def tuning_paths() -> list[Path]:
    """Bundled tuning days — the only real days parameters may be fitted on."""
    return rtm_paths(TUNING_DIR)


def load_tuning() -> list[PriceTrace]:
    return [load_price_trace(p) for p in tuning_paths()]


@dataclass(frozen=True)
class DayResult:
    """What the frozen policy earned on one day, per battery."""

    date: str
    peak_mwh: float
    mean_mwh: float
    spike_intervals: int
    signal_usd: float
    naive_usd: float
    uplift_usd: float
    #: Spot value of the energy the battery gave the house instead of exporting it.
    member_savings_usd: float = 0.0

    @property
    def won(self) -> bool:
        return self.uplift_usd > 0


def score_day(
    trace: PriceTrace,
    kwh: float = backtest.DEFAULT_KWH,
    serve_home: bool = True,
    same_interval_price: bool = False,
) -> DayResult:
    """Run detect -> forecast -> signals -> backtest on one day, thresholds untouched.

    ``serve_home`` is the product's dispatch: the house is paid first out of storage
    and only the surplus is sold, so it scores lower than a grid-only battery. Pass
    ``False`` to score the same frozen policy without home load, for comparison.

    ``same_interval_price`` restores the original deviation rule, which read an
    interval's own real-time print before it settled; the default decides each interval
    from the day-ahead curve and the last settled print only.
    """
    detections = detect.detect_spikes(trace.frame)
    prob = forecast.forecast_spike_probability(forecast.build_features(detections))
    plan = dam.signals_for(detections, prob, trace.dam, same_interval_price=same_interval_price)
    summary = backtest.summarize(
        backtest.value_captured(plan, trace.frame, kwh=kwh, serve_home=serve_home)
    )
    return DayResult(
        date=trace.date,
        peak_mwh=trace.peak_mwh,
        mean_mwh=trace.mean_mwh,
        spike_intervals=int(detections["is_spike"].sum()),
        signal_usd=summary.signal_usd,
        naive_usd=summary.naive_usd,
        uplift_usd=summary.uplift_usd,
        member_savings_usd=summary.member_savings_usd,
    )


def evaluate(
    kwh: float = backtest.DEFAULT_KWH,
    serve_home: bool = True,
    same_interval_price: bool = False,
) -> list[DayResult]:
    return [
        score_day(trace, kwh=kwh, serve_home=serve_home, same_interval_price=same_interval_price)
        for trace in load_holdout()
    ]


def evaluate_tuning(
    kwh: float = backtest.DEFAULT_KWH,
    serve_home: bool = True,
    same_interval_price: bool = False,
) -> list[DayResult]:
    """Same scoring on the tuning split, for the in-sample/out-of-sample comparison."""
    return [
        score_day(trace, kwh=kwh, serve_home=serve_home, same_interval_price=same_interval_price)
        for trace in load_tuning()
    ]


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
                "member_savings_usd": r.member_savings_usd,
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
    )


def headline(summary: HoldoutSummary) -> str:
    return (
        f"{summary.days_won}/{summary.days} days beat the naive schedule; "
        f"mean ${summary.mean_uplift_usd:,.2f}, median ${summary.median_uplift_usd:,.2f}, "
        f"worst ${summary.worst_uplift_usd:,.2f} per battery per day"
    )


def main() -> None:
    results = evaluate()
    if not results:
        print(f"no held-out days bundled in {HOLDOUT_DIR}")
        return

    lookahead = evaluate(same_interval_price=True)
    print(
        f"{'date':<12}{'peak $/MWh':>12}{'signal $':>10}{'naive $':>10}"
        f"{'uplift $':>10}{'as first scored $':>19}"
    )
    for r, old in zip(results, lookahead, strict=True):
        print(
            f"{r.date:<12}{r.peak_mwh:>12,.2f}{r.signal_usd:>10,.2f}"
            f"{r.naive_usd:>10,.2f}{r.uplift_usd:>10,.2f}{old.uplift_usd:>19,.2f}"
        )
    print(f"\ncorrected (day-ahead and last settled print only): {headline(summarize(results))}")
    print(f"as first scored (same-interval price):             {headline(summarize(lookahead))}")


if __name__ == "__main__":
    main()
