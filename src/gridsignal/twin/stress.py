"""Stress test: GridSignal's recovery rule versus a naive split, on simulated years.

Prices come from the world model (learned from real ERCOT history); the fleet is
simulated (see :mod:`gridsignal.twin.sim`). Each simulated day the fleet commits a share of
its measured headroom for one two-hour evening window per zone, then units drop out
at random and in feeder-level clusters that are more likely on stressed days.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np

from gridsignal.twin.data import PriceDays
from gridsignal.twin.sim import POLICIES, FleetAssumptions, build_fleet, draw_days, run_policy
from gridsignal.twin.world import PriceWorldModel

STEPS = 8  # two hours
HOT_PRICE = 250.0
RATIOS = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0)


def window_start(train: PriceDays, months: np.ndarray) -> np.ndarray:
    """Planned window start per day: the 2-hour window with the best average price
    in that calendar month of the training data. Planned from history only, so the
    stress test never peeks at the day it is scoring."""
    best = {}
    for m in range(1, 13):
        prof = train.prices[train.months == m].mean((0, 1))
        c = np.cumsum(np.r_[0.0, prof])
        best[m] = int(np.argmax(c[STEPS:] - c[:-STEPS]))
    return np.array([best[int(m)] for m in months])


@dataclass
class StressResult:
    policy: str
    commit_ratio: float
    days: int
    kept_day_rate: float  # every zone-interval delivered >= tolerance
    worst_interval_ratio_p1: float
    shortfall_mwh_per_year: float
    delivered_mwh_per_year: float
    value_usd_per_year: float
    shortfall_value_usd_per_year: float
    hot_day_kept_rate: float
    min_reserve_margin_kwh: float
    kept_interval_rate: float

    def row(self) -> dict:
        return asdict(self)


def simulate_prices(model: PriceWorldModel, years: int, rng: np.random.Generator, **kw):
    months = np.repeat(np.arange(1, 13), [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
    out = [model.sample(months, rng, **kw) for _ in range(years)]
    return np.concatenate(out), np.tile(months, years)


def run(
    model: PriceWorldModel,
    train: PriceDays,
    years: int = 30,
    commit_ratios: tuple[float, ...] = RATIOS,
    assumptions: FleetAssumptions | None = None,
    seed: int = 2026,
    chunk: int = 730,
    world_kw: dict | None = None,
    real: PriceDays | None = None,
) -> list[StressResult]:
    """Run every policy at every commit ratio on the same simulated days.

    Pass ``real`` to score on actual historical days instead of simulated ones
    (``years`` is then taken from the data), the reality check for the twin.
    """
    assumptions = assumptions or FleetAssumptions()
    rng = np.random.default_rng(seed)
    if real is not None:
        prices, months = real.prices, real.months
        years = len(real) / 365.0
    else:
        prices, months = simulate_prices(model, years, rng, **(world_kw or {}))
    start = window_start(train, months)
    idx = start[:, None] + np.arange(STEPS)
    win = np.take_along_axis(prices, idx[:, None, :].repeat(3, 1), axis=2)  # (D, 3, STEPS)
    hot = win.max((1, 2)) > HOT_PRICE
    fleet = build_fleet(assumptions, np.random.default_rng(seed + 1))

    acc: dict[tuple[str, float], dict[str, list]] = {}
    for c0 in range(0, len(hot), chunk):
        sl = slice(c0, c0 + chunk)
        # Same random fleet state for every policy and ratio: a paired comparison.
        day = draw_days(assumptions, hot[sl], STEPS, np.random.default_rng(seed + 100 + c0), fleet)
        for policy in POLICIES:
            for r in commit_ratios:
                o = run_policy(policy, day, assumptions, r, STEPS)
                ratio = o.zone_interval_ratio  # (d, steps, 3)
                kept = (ratio >= assumptions.kept_tolerance).all((1, 2))
                short_mw = np.maximum(o.committed_kw[:, None, :] - o.delivered_kw, 0) / 1000
                p = win[sl].transpose(0, 2, 1)  # (d, steps, 3)
                a = acc.setdefault(
                    (policy, r),
                    {
                        k: []
                        for k in (
                            "kept",
                            "worst",
                            "short",
                            "deliv",
                            "val",
                            "sval",
                            "margin",
                            "ikept",
                        )
                    },
                )
                a["kept"].append(kept)
                a["worst"].append(ratio.min((1, 2)))
                a["short"].append(short_mw.sum((1, 2)) * 0.25)
                a["deliv"].append(o.delivered_kw.sum((1, 2)) / 1000 * 0.25)
                a["val"].append((o.delivered_kw / 1000 * 0.25 * p).sum((1, 2)))
                a["sval"].append((short_mw * 0.25 * np.maximum(p, 0)).sum((1, 2)))
                a["margin"].append(np.array([o.min_reserve_margin_kwh]))
                a["ikept"].append((ratio >= assumptions.kept_tolerance).mean((1, 2)))

    out = []
    for (policy, r), a in acc.items():
        kept = np.concatenate(a["kept"])
        out.append(
            StressResult(
                policy,
                r,
                len(kept),
                float(kept.mean()),
                float(np.percentile(np.concatenate(a["worst"]), 1)),
                float(np.concatenate(a["short"]).sum() / years),
                float(np.concatenate(a["deliv"]).sum() / years),
                float(np.concatenate(a["val"]).sum() / years),
                float(np.concatenate(a["sval"]).sum() / years),
                float(kept[hot].mean()) if hot.any() else float("nan"),
                float(np.min(np.concatenate(a["margin"]))),
                float(np.concatenate(a["ikept"]).mean()),
            )
        )
    return out


def max_safe_ratio(results: list[StressResult], policy: str, target: float = 0.99) -> float:
    ok = [r.commit_ratio for r in results if r.policy == policy and r.kept_day_rate >= target]
    return max(ok) if ok else 0.0


def sensitivity(model, train, base: FleetAssumptions, years: int = 10, **kw) -> list[dict]:
    """Re-run with each key fleet assumption doubled and halved."""
    rows = []
    for name in (
        "device_hazard_per_h",
        "feeder_event_p_hot",
        "feeder_event_share",
        "home_load_kw",
        "recovery_delay_steps",
    ):
        for mult in (0.5, 2.0):
            v = getattr(base, name) * mult
            a = replace(base, **{name: int(round(v)) if name == "recovery_delay_steps" else v})
            res = run(model, train, years=years, assumptions=a, **kw)
            rows.append(
                {
                    "assumption": name,
                    "value": getattr(a, name),
                    **{f"{p}_safe": max_safe_ratio(res, p) for p in POLICIES},
                    **{f"{r.policy}_kept_{r.commit_ratio}": r.kept_day_rate for r in res},
                }
            )
    return rows
