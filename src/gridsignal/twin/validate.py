"""Out-of-sample checks for the price world model.

Rolling origin: fit on one calendar year, generate many simulated copies of the next
year, and ask whether the real next year falls inside the simulated range on each
metric that matters to a battery fleet. The same test runs on a plain bootstrap of
real days, so the world model has to earn its place against the obvious yardstick.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from gridsignal.twin.data import PriceDays
from gridsignal.twin.world import PriceWorldModel, bootstrap_baseline

EVENING_START = 17 * 4  # 5 PM
WINDOW = 8  # two hours of 15-minute intervals


def best_window_value(p: np.ndarray) -> np.ndarray:
    """$/MWh average of the best 2-hour window in each day (perfect hindsight)."""
    c = np.cumsum(np.pad(p, [(0, 0)] * (p.ndim - 1) + [(1, 0)]), axis=-1)
    return ((c[..., WINDOW:] - c[..., :-WINDOW]) / WINDOW).max(-1)


def year_metrics(prices: np.ndarray) -> dict[str, float]:
    """Metrics on a (days, zones, 96) array, Houston zone unless stated."""
    h = prices[:, 0]
    mx = h.max(1)
    return {
        "median_price": float(np.median(h)),
        "p99_price": float(np.percentile(h, 99)),
        "days_over_250": float((mx > 250).sum()),
        "days_over_1000": float((mx > 1000).sum()),
        "evening_peak_share": float(np.mean(h.argmax(1) >= EVENING_START)),
        "best_2h_window_mean": float(best_window_value(h).mean()),
        "zone_spread_p95": float(np.percentile(np.abs(prices[:, 0] - prices[:, 1]), 95)),
    }


@dataclass
class MetricCheck:
    metric: str
    actual: float
    world_lo: float
    world_hi: float
    boot_lo: float
    boot_hi: float

    @property
    def world_covers(self) -> bool:
        return self.world_lo <= self.actual <= self.world_hi

    @property
    def boot_covers(self) -> bool:
        return self.boot_lo <= self.actual <= self.boot_hi


def check_year(
    train: PriceDays,
    test: PriceDays,
    sims: int = 60,
    seed: int = 11,
    regime: int | str | None = "recent",
    level_risk: bool = True,
    boot_last_year: bool = True,
    **model_kw,
) -> list[MetricCheck]:
    model = PriceWorldModel(**model_kw).fit(train)
    boot_train = (
        train.between(f"{train.dates.year.max()}-01-01", f"{train.dates.year.max()}-12-31")
        if boot_last_year
        else train
    )
    actual = year_metrics(test.prices)
    world = [
        year_metrics(
            model.sample(
                test.months, np.random.default_rng(seed + i), regime=regime, level_risk=level_risk
            )
        )
        for i in range(sims)
    ]
    boot = [
        year_metrics(bootstrap_baseline(boot_train, test.months, np.random.default_rng(seed + i)))
        for i in range(sims)
    ]
    out = []
    for k, v in actual.items():
        w = [x[k] for x in world]
        b = [x[k] for x in boot]
        out.append(
            MetricCheck(
                k,
                v,
                float(np.percentile(w, 5)),
                float(np.percentile(w, 95)),
                float(np.percentile(b, 5)),
                float(np.percentile(b, 95)),
            )
        )
    return out


def rolling_origin(
    days: PriceDays, years: tuple[int, ...] = (2023, 2024, 2025), expanding: bool = True, **kw
) -> list[dict]:
    rows = []
    for y in years:
        first = days.dates.year.min() if expanding else y - 1
        train = days.between(f"{first}-01-01", f"{y - 1}-12-31")
        test = days.between(f"{y}-01-01", f"{y}-12-31")
        for c in check_year(train, test, **kw):
            rows.append(
                {
                    "train": f"{first}-{y - 1}",
                    "test": y,
                    **asdict(c),
                    "world_covers": c.world_covers,
                    "boot_covers": c.boot_covers,
                }
            )
    return rows


def reproduce(
    days: PriceDays,
    years: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025),
    sims: int = 60,
    seed: int = 21,
    **model_kw,
) -> list[dict]:
    """In-sample fidelity: can the model regenerate a year it was told to imitate?

    This is not a forecast test (``rolling_origin`` is). It checks the thing the stress
    scenarios rely on: that asking for a "2023-like" world gives a world that looks
    like 2023 on the metrics a battery fleet cares about, without copying 2023's days.
    """
    model = PriceWorldModel(**model_kw).fit(days)
    rows = []
    for y in years:
        real = days.between(f"{y}-01-01", f"{y}-12-31")
        actual = year_metrics(real.prices)
        sims_ = [
            year_metrics(
                model.sample(
                    real.months, np.random.default_rng(seed + i), regime=y, level_risk=False
                )
            )
            for i in range(sims)
        ]
        for k, v in actual.items():
            w = [s[k] for s in sims_]
            lo, hi = float(np.percentile(w, 5)), float(np.percentile(w, 95))
            rows.append(
                {
                    "year": y,
                    "metric": k,
                    "actual": v,
                    "world_lo": lo,
                    "world_hi": hi,
                    "world_median": float(np.median(w)),
                    "covers": lo <= v <= hi,
                }
            )
    return rows
