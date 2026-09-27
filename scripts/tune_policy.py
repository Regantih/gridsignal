"""Fit the DAM-anchored policy parameters on the tuning split only.

Search space is the day-ahead plan shape (how many hours to buy and sell, and the
day-ahead spread below which the battery sits out) and the three real-time deviation
gates. Scored on the two bundled scenario days plus ``data/tuning`` — never on
``data/holdout``, which is read exactly once, afterwards, by ``gridsignal.holdout``.

The objective is the *median* uplift per battery per day rather than the mean, so one
scarcity day cannot buy a parameter set that loses money on ordinary days.

    python scripts/tune_policy.py
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from gridsignal import backtest, dam, detect, forecast, holdout
from gridsignal.prices import PriceTrace, load_scenario

CHARGE_HOURS = (2, 3, 4, 5, 6)
EXPORT_HOURS = (2, 3, 4, 6, 8)
MIN_SPREAD = (1.2, 1.4, 1.8, 2.5)
DEVIATION_MULTIPLE = (1.5, 2.0, 3.0, 5.0)
CHARGE_CEILING = (1.5, 2.0, 5.0)
EXPORT_FLOOR = (0.0, 0.5, 0.8, 0.9)


@dataclass(frozen=True)
class Params:
    charge_hours: int
    export_hours: int
    min_spread: float
    deviation_multiple: float
    charge_ceiling: float
    export_floor: float


def tuning_traces() -> list[PriceTrace]:
    return [load_scenario("normal"), load_scenario("scarcity"), *holdout.load_tuning()]


def score(params: Params, traces: list[PriceTrace]) -> list[float]:
    uplifts = []
    for trace in traces:
        detections = detect.detect_spikes(trace.frame)
        prob = forecast.forecast_spike_probability(forecast.build_features(detections))
        plan = dam.deviate_from_plan(
            detections,
            prob,
            trace.dam,
            charge_hours=params.charge_hours,
            export_hours=params.export_hours,
            min_spread=params.min_spread,
            deviation_multiple=params.deviation_multiple,
            charge_ceiling=params.charge_ceiling,
            export_floor=params.export_floor,
        )
        summary = backtest.summarize(backtest.value_captured(plan, trace.frame))
        uplifts.append(summary.uplift_usd)
    return uplifts


def median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def main() -> None:
    traces = [t for t in tuning_traces() if t.dam is not None]
    print(f"tuning on {len(traces)} days: {', '.join(t.date for t in traces)}")

    ranked = []
    for combo in itertools.product(
        CHARGE_HOURS, EXPORT_HOURS, MIN_SPREAD, DEVIATION_MULTIPLE, CHARGE_CEILING, EXPORT_FLOOR
    ):
        params = Params(*combo)
        uplifts = score(params, traces)
        ranked.append((median(uplifts), sum(uplifts) / len(uplifts), min(uplifts), params))

    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    print(f"{'median $':>10}{'mean $':>10}{'worst $':>10}  params")
    for med, mean, worst, params in ranked[:10]:
        print(f"{med:>10.2f}{mean:>10.2f}{worst:>10.2f}  {params}")


if __name__ == "__main__":
    main()
