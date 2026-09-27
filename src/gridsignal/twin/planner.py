"""Commitment planner: how much of this fleet's headroom to promise, and under which policy.

This is where the twin meets the Control Room. It reads the operator's live fleet from a
:class:`~gridsignal.control_room.engine.ControlRoomEngine` (unit mix, state of charge,
house loads, degraded units, member reserve), scales it to a fleet the stress test can
say something about, runs that fleet through simulated years of ERCOT prices from the
world model, and returns the largest commitment that still keeps the promise on the
target share of days, with and without a pre-approved recovery playbook.

What is real: the prices the world model learned from (ERCOT NP6-905-CD, 2021 to 2025)
and the dispatch rule (parity-tested against the engine). What is assumed: failure rates
and feeder outages (:class:`~gridsignal.twin.sim.FleetAssumptions`). The plan says so.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from statistics import fmean, pstdev
from typing import TYPE_CHECKING

import numpy as np

from gridsignal.twin import feed, stress
from gridsignal.twin.data import PriceDays, load_days
from gridsignal.twin.sim import FleetAssumptions
from gridsignal.twin.world import PriceWorldModel

if TYPE_CHECKING:
    from gridsignal.control_room.engine import ControlRoomEngine
    from gridsignal.twin.learn import Calibration

#: The world model prices three ERCOT load zones. The engine's Austin homes are priced as
#: South, the nearest modelled zone; the partner tenant's West units are not ours to
#: commit and are left out. Reliability barely depends on price (see ``docs/twin``), so
#: this mapping moves the value estimate, not the safe commitment.
TWIN_ZONE = {"LZ_HOUSTON": 0, "LZ_NORTH": 1, "LZ_SOUTH": 2, "LZ_AUSTIN": 2, "LZ_AEN": 2}

#: Scenarios that need the live feed's days to exist. Offered only when they do.
LIVE_SCENARIO = "This year so far (live feed)"

SCENARIOS: dict[str, dict] = {
    "2025-like (latest year)": {"regime": 2025},
    "2025-like, spikes halved": {"regime": 2025, "scarcity": 0.5},
    "2023-like (scarce summer)": {"regime": 2023},
    "2021-like (includes Winter Storm Uri)": {"regime": 2021},
    "Any past year (drawn)": {"regime": "draw"},
}
DEFAULT_SCENARIO = "2025-like (latest year)"
POLICY_LABEL = {
    "naive": "No recovery",
    "gridsignal": "Approval on every incident",
    "gridsignal_auto": "Pre-approved playbook",
}


@lru_cache(maxsize=1)
def _history() -> PriceDays:
    return load_days()


@lru_cache(maxsize=2)
def _live_history(last_day: str | None) -> PriceDays:
    """Bundled 2021 to 2025 plus every settled live day, cached per newest settled day."""
    return feed.combined_days()


def _history_with_live() -> PriceDays:
    return _live_history(feed.last_full_day())


@lru_cache(maxsize=1)
def _frozen_model() -> PriceWorldModel:
    return PriceWorldModel().fit(_history())


@lru_cache(maxsize=2)
def _live_model(last_day: str | None) -> PriceWorldModel:
    return PriceWorldModel().fit(_live_history(last_day))


def world_model(live: bool = False) -> PriceWorldModel:
    """The price world model, fitted once per process (and once per new live day).

    ``live=False`` fits the frozen bundled history, so every number in ``docs/twin``
    reproduces. ``live=True`` refits on bundled plus live days, so the newest regime is
    this year's real prices; it refits by itself when the feed settles a new day.
    """
    return _live_model(feed.last_full_day()) if live else _frozen_model()


def live_regime() -> int | None:
    """The newest year the live feed has enough settled days of, or None."""
    days = feed.live_days()
    if days is None or len(days) < 60:
        return None
    years, counts = np.unique(days.dates.year.to_numpy(), return_counts=True)
    ok = years[counts >= 60]
    return int(ok.max()) if len(ok) else None


def scenarios(live: bool = False) -> dict[str, dict]:
    """The scenario menu. With ``live``, adds this year's real regime when it exists."""
    out = dict(SCENARIOS)
    regime = live_regime() if live else None
    if regime is not None:
        out[LIVE_SCENARIO] = {"regime": regime}
    return out


def assumptions_from_engine(engine: ControlRoomEngine, homes: int = 1_000) -> FleetAssumptions:
    """The engine's operator-controlled fleet, described the way the twin simulates it.

    Unit mix, state of charge, house load, degraded share and the member reserve come
    from the engine's devices; failure behaviour stays at the twin's documented
    defaults. ``homes`` scales the fleet up so a 48-device demo still gets a stable
    answer (the answer is a *share* of headroom, so it carries back to 48 devices).
    """
    mine = [d for d in engine.mine if TWIN_ZONE.get(d.zone) is not None]
    if not mine:
        raise ValueError("the engine has no operator-controlled devices in a modelled zone")
    counts = np.bincount([TWIN_ZONE[d.zone] for d in mine], minlength=3).astype(float)
    counts = np.maximum(counts, 1e-6)
    large = [d for d in mine if d.unit_type.value == "base_core"]
    small = [d for d in mine if d.unit_type.value != "base_core"]
    socs = [d.state_of_charge for d in mine]
    base = FleetAssumptions()
    return replace(
        base,
        homes=homes,
        zone_share=tuple(float(c) for c in counts / counts.sum()),
        large_share=len(large) / len(mine),
        large_kwh=fmean(d.capacity_kwh for d in large) if large else base.large_kwh,
        large_kw=fmean(d.power_kw for d in large) if large else base.large_kw,
        small_kwh=tuple(sorted({d.capacity_kwh for d in small})) or base.small_kwh,
        small_kw=tuple(sorted({d.power_kw for d in small})) or base.small_kw,
        soc_mean=fmean(socs),
        soc_spread=max(pstdev(socs), 0.02),
        reserve_fraction=engine.reserve_fraction,
        home_load_kw=max(fmean(d.home_load_kw for d in mine), 0.1),
        degraded_share=sum(d.status.value == "degraded" for d in mine) / len(mine),
    )


@dataclass(frozen=True)
class CommitmentPlan:
    """The planner's answer for one fleet and one price scenario."""

    scenario: str
    target: float
    years: int
    homes: int
    #: Largest commit ratio meeting ``target`` per policy (0.0 when none does).
    safe_ratio: dict[str, float]
    #: Kept-day rate per policy at each tested ratio.
    curve: dict[str, dict[float, float]]
    #: Energy value per year at each ratio (the same delivered MWh under all policies
    #: to within the shortfall), for the playbook policy.
    value_usd_per_year: dict[float, float]
    shortfall_mwh_per_year: dict[str, dict[float, float]]
    min_reserve_margin_kwh: float
    assumptions: FleetAssumptions
    #: True when failure rates came from telemetry instead of the documented defaults.
    calibrated: bool = False
    #: True when the price model was refitted to include the live feed's days.
    live_prices: bool = False

    @property
    def recommended_ratio(self) -> float:
        """The playbook-safe commitment: what the planner recommends promising."""
        return self.safe_ratio["gridsignal_auto"]

    def kept_at(self, policy: str, ratio: float) -> float:
        """Kept-day rate at ``ratio``, interpolated between the tested ratios."""
        pts = sorted(self.curve[policy].items())
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return float(np.interp(ratio, xs, ys))

    def headline(self, current_ratio: float | None = None) -> str:
        rec = self.recommended_ratio
        parts = []
        if rec > 0:
            parts.append(
                f"Commit {rec:.0%} of measured headroom with the recovery playbook on: "
                f"{self.kept_at('gridsignal_auto', rec):.1%} of days kept over "
                f"{self.years} simulated years in the {self.scenario} scenario."
            )
        else:
            parts.append(
                f"No tested commitment keeps {self.target:.0%} of days, even with the playbook."
            )
        if self.safe_ratio["gridsignal"] == 0:
            parts.append(
                f"With approval on every incident no tested level reaches {self.target:.0%}: "
                "the fix lands an interval late."
            )
        if current_ratio is not None:
            parts.append(
                f"Today's plan commits {current_ratio:.0%}: "
                f"{self.kept_at('gridsignal', current_ratio):.1%} of days with approval each time, "
                f"{self.kept_at('gridsignal_auto', current_ratio):.1%} with the playbook."
            )
        return " ".join(parts)


def plan_commitment(
    engine: ControlRoomEngine | None = None,
    scenario: str = DEFAULT_SCENARIO,
    target: float = 0.99,
    years: int = 4,
    homes: int = 1_000,
    ratios: tuple[float, ...] = stress.RATIOS,
    assumptions: FleetAssumptions | None = None,
    seed: int = 2026,
    live: bool | None = None,
    calibration: Calibration | None = None,
) -> CommitmentPlan:
    """Stress-test a fleet on simulated years and return the safe commitment.

    Pass an ``engine`` to plan for its live fleet, or ``assumptions`` directly. Every
    policy is scored on the same simulated days and failures (a paired comparison).
    """
    if live is None:
        live = scenario == LIVE_SCENARIO
    menu = scenarios(live=live)
    if scenario not in menu:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {list(menu)}")
    if assumptions is None:
        assumptions = (
            assumptions_from_engine(engine, homes)
            if engine is not None
            else FleetAssumptions(homes=homes)
        )
    if calibration is not None:
        # Measured failure behaviour replaces the documented assumptions.
        assumptions = calibration.apply(assumptions)
    history = _history_with_live() if live else _history()
    results = stress.run(
        world_model(live),
        history,
        years=years,
        commit_ratios=ratios,
        assumptions=assumptions,
        seed=seed,
        world_kw=menu[scenario],
    )
    curve: dict[str, dict[float, float]] = {p: {} for p in stress.POLICIES}
    short: dict[str, dict[float, float]] = {p: {} for p in stress.POLICIES}
    value: dict[float, float] = {}
    for r in results:
        curve[r.policy][r.commit_ratio] = r.kept_day_rate
        short[r.policy][r.commit_ratio] = r.shortfall_mwh_per_year
        if r.policy == "gridsignal_auto":
            value[r.commit_ratio] = r.value_usd_per_year
    return CommitmentPlan(
        scenario=scenario,
        target=target,
        years=years,
        homes=assumptions.homes,
        safe_ratio={p: stress.max_safe_ratio(results, p, target) for p in stress.POLICIES},
        curve=curve,
        value_usd_per_year=value,
        shortfall_mwh_per_year=short,
        min_reserve_margin_kwh=min(r.min_reserve_margin_kwh for r in results),
        assumptions=assumptions,
        calibrated=calibration is not None,
        live_prices=live,
    )
