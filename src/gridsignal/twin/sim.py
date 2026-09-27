"""Home-battery fleet world model and the dispatch policies it stress-tests.

Everything here is SIMULATED. The failure rates, loads and unit mix are assumptions
written down in :class:`FleetAssumptions`, not measurements from any real fleet. They
are there so a policy can be stress-tested today and re-run the day real telemetry
replaces them. ``sensitivity`` in :mod:`gridsignal.twin.stress` sweeps the ones that matter.

The dispatch rule is GridSignal's, vectorised: each home's exportable kW is

    min(power_kw * trust, max(0, available_kwh - reserve_kwh) / hours_left) - home_load_kw

floored at zero (``gridsignal.control_room.engine.ControlRoomEngine.exportable_kw``),
and a zone's commitment is shared across its homes in proportion to that headroom
(``ControlRoomEngine._share``). ``tests/test_twin.py`` checks both against the
GridSignal code itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

STEP_H = 0.25


@dataclass(frozen=True)
class FleetAssumptions:
    homes: int = 1_000
    zone_share: tuple[float, float, float] = (0.40, 0.35, 0.25)  # Houston, North, South
    # Unit mix mirrors GridSignal's simulated fleet: every 4th home a larger unit.
    large_share: float = 0.25
    large_kwh: float = 39.2  # Base Core, as announced Aug 2026
    large_kw: float = 20.0  # simulated, not an official spec
    small_kwh: tuple[float, ...] = (25.0, 27.5, 30.0)
    small_kw: tuple[float, ...] = (5.0, 7.5, 10.0)
    soc_mean: float = 0.80
    soc_spread: float = 0.10
    reserve_fraction: float = 0.20
    home_load_kw: float = 1.6  # evening mean, lognormal across homes
    hot_day_load_factor: float = 1.35  # homes draw more on the days prices spike
    degraded_share: float = 0.04  # start the event derated to half power
    device_hazard_per_h: float = 0.004  # chance an online unit drops per hour
    feeder_event_p: float = 0.01  # per zone-day, a cluster outage...
    feeder_event_p_hot: float = 0.06  # ...more likely on stressed days
    feeder_event_share: float = 0.25  # ...taking out this share of the zone
    recovery_delay_steps: int = 1  # detect + human approval before reassignment
    kept_tolerance: float = 0.97  # a zone-interval counts as kept at >= 97%


@dataclass
class FleetDay:
    """One simulated evening for every home, batched over days: arrays are (D, N)."""

    zone: np.ndarray  # (N,)
    power_kw: np.ndarray  # (N,)
    capacity_kwh: np.ndarray  # (N,)
    soc: np.ndarray  # (D, N)
    home_kw: np.ndarray  # (D, N)
    trust: np.ndarray  # (D, N)
    fail_step: np.ndarray  # (D, N) step at which the unit drops; 99 = never
    extra: dict = field(default_factory=dict)


def build_fleet(
    a: FleetAssumptions, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = a.homes
    zone = rng.choice(3, size=n, p=np.array(a.zone_share) / sum(a.zone_share))
    large = rng.random(n) < a.large_share
    cap = np.where(large, a.large_kwh, rng.choice(a.small_kwh, size=n))
    kw = np.where(large, a.large_kw, rng.choice(a.small_kw, size=n))
    return zone, kw, cap


def draw_days(
    a: FleetAssumptions,
    hot: np.ndarray,
    steps: int,
    rng: np.random.Generator,
    fleet: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
) -> FleetDay:
    """Draw the random state of the fleet for ``len(hot)`` event windows."""
    zone, kw, cap = fleet if fleet is not None else build_fleet(a, rng)
    d, n = len(hot), len(zone)
    soc = np.clip(rng.normal(a.soc_mean, a.soc_spread, (d, n)), a.reserve_fraction + 0.05, 1.0)
    load = rng.lognormal(np.log(a.home_load_kw) - 0.18, 0.6, (d, n))
    load *= np.where(hot[:, None], a.hot_day_load_factor, 1.0)
    trust = np.where(rng.random((d, n)) < a.degraded_share, 0.5, 1.0)

    p_step = 1 - (1 - a.device_hazard_per_h) ** STEP_H
    fail = np.where(rng.random((d, n, steps)) < p_step, np.arange(steps), 99).min(-1)
    p_event = np.where(hot, a.feeder_event_p_hot, a.feeder_event_p)
    for z in range(3):
        hit_days = rng.random(d) < p_event
        at = rng.integers(0, steps, d)
        members = (zone == z)[None, :] & (rng.random((d, n)) < a.feeder_event_share)
        cluster = np.where(hit_days[:, None] & members, at[:, None], 99)
        fail = np.minimum(fail, cluster)
    return FleetDay(zone, kw, cap, soc, load, trust, fail, {"feeder_days": p_event})


def exportable_kw(power_kw, trust, available_kwh, reserve_kwh, hours_left, home_kw):
    """GridSignal's home-first, reserve-protected export headroom (vectorised)."""
    energy_kw = np.maximum(available_kwh - reserve_kwh, 0.0) / np.maximum(hours_left, 0.25)
    headroom = np.maximum(np.minimum(power_kw * trust, energy_kw), 0.0)
    return np.maximum(headroom - home_kw, 0.0)


def share(target: np.ndarray, headroom: np.ndarray, zone: np.ndarray) -> np.ndarray:
    """Split each zone's ``target`` (D, 3) across its homes by ``headroom`` (D, N)."""
    out = np.zeros_like(headroom)
    for z in range(3):
        m = zone == z
        h = headroom[:, m]
        tot = h.sum(1, keepdims=True)
        frac = np.divide(h, tot, out=np.zeros_like(h), where=tot > 0)
        out[:, m] = frac * np.minimum(target[:, z : z + 1], tot)
    return out


POLICIES = ("naive", "gridsignal", "gridsignal_auto")


@dataclass
class Outcome:
    committed_kw: np.ndarray  # (D, 3)
    delivered_kw: np.ndarray  # (D, steps, 3)
    min_reserve_margin_kwh: float
    policy: str

    @property
    def zone_interval_ratio(self) -> np.ndarray:
        c = self.committed_kw[:, None, :]
        return np.divide(self.delivered_kw, c, out=np.ones_like(self.delivered_kw), where=c > 0)


def run_policy(
    policy: str, day: FleetDay, a: FleetAssumptions, commit_ratio: float, steps: int
) -> Outcome:
    """Simulate one event window per day under ``policy``.

    * ``naive``: split the commitment by headroom at the start and never move it. A
      unit that drops takes its share with it; one that runs low delivers what it can.
    * ``gridsignal``: same start, then every 15 minutes after a unit drops (plus
      ``recovery_delay_steps`` for detection and human approval) re-share each zone's
      commitment over the homes still online, by current headroom. Never touches the
      reserve, never moves kW across zones (each zone is its own commitment).
    * ``gridsignal_auto``: the same rule with a pre-approved playbook, so the re-share
      lands inside the interval the loss happens in (no approval wait). Humans still
      approve the playbook once, and everything the playbook does is logged.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}")
    d, n = day.soc.shape
    reserve = day.capacity_kwh * a.reserve_fraction
    avail = day.capacity_kwh * day.soc
    hours = steps * STEP_H
    head0 = exportable_kw(day.power_kw, day.trust, avail, reserve, hours, day.home_kw)
    committed = np.stack([head0[:, day.zone == z].sum(1) for z in range(3)], 1) * commit_ratio
    assigned = share(committed, head0, day.zone)
    delivered = np.zeros((d, steps, 3))
    min_margin = np.inf
    last_fail = np.full(d, -99)

    for t in range(steps):
        online = day.fail_step > t
        just_failed = (day.fail_step == t).any(1)
        last_fail = np.where(just_failed, t, last_fail)
        head = exportable_kw(
            day.power_kw, day.trust, avail, reserve, hours - t * STEP_H, day.home_kw
        )
        head = np.where(online, head, 0.0)
        if policy in ("gridsignal", "gridsignal_auto"):
            # Re-share once the loss has been detected and approved.
            delay = 0 if policy == "gridsignal_auto" else a.recovery_delay_steps
            act = (t - last_fail) >= delay
            re = share(committed, head, day.zone)
            assigned = np.where(act[:, None] & (last_fail >= 0)[:, None], re, assigned)
        out = np.minimum(assigned, head)
        out = np.where(online, out, 0.0)
        for z in range(3):
            delivered[:, t, z] = out[:, day.zone == z].sum(1)
        draw = np.where(online, out + day.home_kw, 0.0) * STEP_H
        avail = np.maximum(avail - draw, 0.0)
        # The member's promise: exporting never takes a battery below its reserve.
        # (Serving the member's own house may; that energy is theirs.)
        exporting = online & (out > 1e-9)
        margin = np.where(exporting, avail - reserve, np.inf)
        min_margin = min(min_margin, float(margin.min()))
    return Outcome(committed, delivered, min_margin, policy)
