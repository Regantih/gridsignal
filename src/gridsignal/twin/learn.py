"""The learning loop: replace the stress test's assumed failure rates with measured ones.

The Planner's weakest input is :class:`~gridsignal.twin.sim.FleetAssumptions`: how often a
unit drops, how often a cluster of units drops together, and how much of a zone one
cluster takes. This module reads a telemetry history in the documented JSON-lines format
(:mod:`gridsignal.telemetry`, one row per device per report) and estimates each of them
with its evidence, so the plan moves the moment real reports arrive.

How the estimate works, in plain terms:

* **A failure** is a device whose status goes from ``online``/``degraded`` to
  ``offline``/``unavailable`` between two reports.
* **Exposure** is the time a device was reporting as up, with gaps capped at
  ``MAX_GAP_H`` so a device that went silent overnight does not count as a night online.
* **Together** means at least ``MIN_CLUSTER`` failures in the same 15-minute slot that
  share a gateway, a firmware build or a feeder. Those are the correlated failures; the
  rest are independent.
* **Each rate starts from the current assumption** as a prior worth ``PRIOR_*`` of
  evidence (a Gamma or Beta prior), and the history moves it. A thin history barely
  moves it; a long one decides it. The 90% range says how sure the estimate is.

Nothing here is a claim about a real fleet: the bundled history is SYNTHETIC
(:func:`synthetic_history`), generated with known rates so ``tests/test_learn.py`` can
check the estimator recovers them. Point it at a real export and the same code runs.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from gridsignal.twin.sim import FleetAssumptions

UP = {"online", "degraded"}
DOWN = {"offline", "unavailable"}
MIN_CLUSTER = 3
MAX_GAP_H = 0.5
SLOT = "15min"
#: Prior strength: how much evidence the current assumption counts for.
PRIOR_DEVICE_HOURS = 5_000.0
PRIOR_ZONE_DAYS = 60.0
PRIOR_SNAPSHOTS = 200.0
PRIOR_CLUSTERS = 3.0
#: The stress test's split between ordinary and stressed-day cluster rates. The history
#: rarely has enough clusters to learn the split, so the learned overall rate scales both.
HOT_DAY_SHARE = 0.08


@dataclass(frozen=True)
class Estimate:
    name: str
    label: str
    prior: float
    learned: float
    low: float
    high: float
    evidence: str
    observed: float | None = None

    @property
    def moved(self) -> float:
        return self.learned / self.prior if self.prior else float("inf")


@dataclass
class Cluster:
    slot: pd.Timestamp
    key: str  # e.g. "gateway GW-07" or "feeder LZ_NORTH-F2"
    devices: tuple[str, ...]
    zone_share: dict[str, float]

    @property
    def worst_zone_share(self) -> float:
        return max(self.zone_share.values(), default=0.0)


@dataclass
class Calibration:
    """What the history says, next to what the stress test assumed."""

    estimates: dict[str, Estimate]
    clusters: list[Cluster]
    devices: int
    reports: int
    days: int
    first: str | None
    last: str | None
    failures: int
    independent_failures: int
    exposure_h: float
    source: str
    notes: list[str] = field(default_factory=list)

    def apply(self, base: FleetAssumptions) -> FleetAssumptions:
        """The stress test's assumptions with every learned rate swapped in."""
        e = self.estimates
        scale = e["feeder_event_rate"].learned / max(_overall_event_rate(base), 1e-12)
        return replace(
            base,
            device_hazard_per_h=e["device_hazard_per_h"].learned,
            degraded_share=e["degraded_share"].learned,
            feeder_event_p=min(base.feeder_event_p * scale, 1.0),
            feeder_event_p_hot=min(base.feeder_event_p_hot * scale, 1.0),
            feeder_event_share=e["feeder_event_share"].learned,
        )

    def rows(self) -> list[dict]:
        return [
            {
                "What": est.label,
                "Assumed": est.prior,
                "Learned": est.learned,
                "90% range": (est.low, est.high),
                "Evidence": est.evidence,
            }
            for est in self.estimates.values()
        ]


def _overall_event_rate(a: FleetAssumptions) -> float:
    return (1 - HOT_DAY_SHARE) * a.feeder_event_p + HOT_DAY_SHARE * a.feeder_event_p_hot


def frame_from_readings(readings: Iterable) -> pd.DataFrame:
    rows = [(r.device_id, r.ts, r.status.value, r.gateway, r.firmware) for r in readings]
    frame = pd.DataFrame(rows, columns=["device_id", "ts", "status", "gateway", "firmware"])
    if frame.empty:
        return frame
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    return frame.sort_values(["device_id", "ts"]).reset_index(drop=True)


def frame_from_lines(lines: Iterable[str]) -> tuple[pd.DataFrame, int]:
    """Parse telemetry JSON lines with the Control Room's own validating importer.

    Returns the frame and the number of rejected rows. Every row here went through the
    same checks as a live import, so the loop learns from nothing the importer refuses.
    """
    from gridsignal.telemetry import read_lines

    parsed = read_lines(lines)
    return frame_from_readings(parsed.readings), len(parsed.rejected)


def _gamma_interval(a: float, b: float, rng: np.random.Generator) -> tuple[float, float]:
    s = rng.gamma(a, 1.0 / b, 20_000)
    return float(np.percentile(s, 5)), float(np.percentile(s, 95))


def _beta_interval(a: float, b: float, rng: np.random.Generator) -> tuple[float, float]:
    s = rng.beta(a, b, 20_000)
    return float(np.percentile(s, 5)), float(np.percentile(s, 95))


def learn(
    frame: pd.DataFrame,
    zones: dict[str, str],
    feeders: dict[str, str] | None = None,
    base: FleetAssumptions | None = None,
    source: str = "telemetry history",
    seed: int = 5,
) -> Calibration:
    """Estimate the stress test's failure behaviour from a telemetry history.

    ``zones`` maps device to load zone (telemetry rows do not carry it); ``feeders``
    optionally maps device to feeder, so spatial clusters count as well as gateway and
    firmware ones.
    """
    base = base or FleetAssumptions()
    rng = np.random.default_rng(seed)
    feeders = feeders or {}
    if frame.empty:
        raise ValueError("the telemetry history has no usable rows")
    f = frame[frame["device_id"].isin(zones)].copy()
    if f.empty:
        raise ValueError("no telemetry row belongs to a device with a known zone")
    notes = []
    dropped = len(frame) - len(f)
    if dropped:
        notes.append(f"{dropped:,} rows skipped: device not in this fleet")

    f["prev_status"] = f.groupby("device_id")["status"].shift()
    f["prev_ts"] = f.groupby("device_id")["ts"].shift()
    gap_h = (f["ts"] - f["prev_ts"]).dt.total_seconds() / 3600
    was_up = f["prev_status"].isin(UP)
    # A gap longer than MAX_GAP_H (overnight, a silent device) is not time observed up.
    exposure_h = float(gap_h[was_up & (gap_h <= MAX_GAP_H)].sum())
    fail = f[was_up & f["status"].isin(DOWN) & (gap_h <= MAX_GAP_H)].copy()
    fail["slot"] = fail["ts"].dt.floor(SLOT)
    fail["feeder"] = fail["device_id"].map(feeders).fillna("")

    # Devices per zone that were reporting that day: the denominator for "share of a zone".
    f["day"] = f["ts"].dt.tz_convert("US/Central").dt.date
    f["zone"] = f["device_id"].map(zones)
    per_zone_day = f.groupby(["day", "zone"])["device_id"].nunique()

    clusters: list[Cluster] = []
    in_cluster: set[int] = set()
    for key_col, label in (("gateway", "gateway"), ("feeder", "feeder"), ("firmware", "firmware")):
        # Firmware builds are fleet-wide; a build only counts as the shared cause when
        # enough of its devices drop in one slot that chance cannot explain it.
        min_size = MIN_CLUSTER if key_col != "firmware" else max(MIN_CLUSTER, 6)
        for (slot, key), grp in fail.groupby(["slot", key_col]):
            if not key or len(grp) < min_size:
                continue
            idx = set(grp.index) - in_cluster
            if len(idx) < min_size:
                continue
            members = tuple(sorted(fail.loc[list(idx), "device_id"]))
            day = slot.tz_convert("US/Central").date()
            share: dict[str, float] = defaultdict(float)
            for dev in members:
                z = zones[dev]
                share[z] += 1.0 / max(per_zone_day.get((day, z), 1), 1)
            clusters.append(Cluster(slot, f"{label} {key}", members, dict(share)))
            in_cluster |= idx

    # A cluster smaller than the day's tolerance (3% of a zone by default) cannot break
    # a promise on its own; count it, but keep it out of the zone-outage rate and size.
    small = 1.0 - base.kept_tolerance
    minor = [c for c in clusters if c.worst_zone_share < small]
    if minor:
        notes.append(
            f"{len(minor)} correlated drop(s) took under {small:.0%} of any zone: counted, "
            "but too small to break a day's promise, so left out of the outage rate"
        )
    major = [c for c in clusters if c.worst_zone_share >= small]

    n_fail = len(fail)
    n_indep = n_fail - len(in_cluster)
    days = int(f["day"].nunique())
    zone_days = int(per_zone_day.size)

    est: dict[str, Estimate] = {}
    a0 = base.device_hazard_per_h * PRIOR_DEVICE_HOURS
    a, b = a0 + n_indep, PRIOR_DEVICE_HOURS + exposure_h
    lo, hi = _gamma_interval(a, b, rng)
    est["device_hazard_per_h"] = Estimate(
        "device_hazard_per_h",
        "A unit drops on its own (per hour)",
        base.device_hazard_per_h,
        a / b,
        lo,
        hi,
        f"{n_indep:,} independent drops in {exposure_h:,.0f} device-hours",
        n_indep / exposure_h if exposure_h else None,
    )

    # Degraded share: the first report of each device on each day.
    first = f.sort_values("ts").groupby(["device_id", "day"]).head(1)
    k, n = int((first["status"] == "degraded").sum()), len(first)
    pa, pb = base.degraded_share * PRIOR_SNAPSHOTS, (1 - base.degraded_share) * PRIOR_SNAPSHOTS
    lo, hi = _beta_interval(pa + k + 1e-3, pb + n - k + 1e-3, rng)
    est["degraded_share"] = Estimate(
        "degraded_share",
        "A unit starts the event derated",
        base.degraded_share,
        (pa + k) / (pa + pb + n),
        lo,
        hi,
        f"{k:,} of {n:,} device-days started degraded",
        k / n if n else None,
    )

    # Cluster rate per zone-day: a zone is hit when a cluster takes >= MIN_CLUSTER of it.
    hits = {
        (c.slot.tz_convert("US/Central").date(), z)
        for c in major
        for z, sh in c.zone_share.items()
        if sh >= small
    }
    prior_rate = _overall_event_rate(base)
    pa, pb = prior_rate * PRIOR_ZONE_DAYS, (1 - prior_rate) * PRIOR_ZONE_DAYS
    k = len(hits)
    lo, hi = _beta_interval(pa + k, pb + zone_days - k, rng)
    est["feeder_event_rate"] = Estimate(
        "feeder_event_rate",
        "A zone has a cluster outage (per zone-day)",
        prior_rate,
        (pa + k) / (pa + pb + zone_days),
        lo,
        hi,
        f"{len(major)} outages hit {k} of {zone_days:,} zone-days",
        k / zone_days if zone_days else None,
    )

    # How much of a zone one cluster takes: shrink the observed mean toward the prior.
    shares = np.array([c.worst_zone_share for c in major])
    w = len(shares) / (len(shares) + PRIOR_CLUSTERS)
    obs = float(shares.mean()) if len(shares) else base.feeder_event_share
    learned = w * obs + (1 - w) * base.feeder_event_share
    if len(shares) >= 2:
        boots = rng.choice(shares, (5_000, len(shares))).mean(1)
        boots = w * boots + (1 - w) * base.feeder_event_share
        lo, hi = float(np.percentile(boots, 5)), float(np.percentile(boots, 95))
    else:
        lo, hi = min(learned, base.feeder_event_share), max(learned, base.feeder_event_share)
    est["feeder_event_share"] = Estimate(
        "feeder_event_share",
        "Share of a zone one cluster takes out",
        base.feeder_event_share,
        learned,
        lo,
        hi,
        f"{len(shares)} clusters, worst {shares.max():.0%}" if len(shares) else "no clusters seen",
        obs if len(shares) else None,
    )
    if len(major) < 5:
        notes.append(
            f"only {len(major)} zone outage(s) in the history: the cluster "
            "estimates still lean on the assumption"
        )

    ts = f["ts"]
    return Calibration(
        estimates=est,
        clusters=sorted(clusters, key=lambda c: -len(c.devices)),
        devices=int(f["device_id"].nunique()),
        reports=len(f),
        days=days,
        first=str(ts.min().date()),
        last=str(ts.max().date()),
        failures=n_fail,
        independent_failures=n_indep,
        exposure_h=exposure_h,
        source=source,
        notes=notes,
    )


# ---------------------------------------------------------------------------------------
# SYNTHETIC history with known rates, so the estimator can be checked end to end.


@dataclass(frozen=True)
class SyntheticTruth:
    """The rates the synthetic history was generated with. Not a real fleet's rates."""

    device_hazard_per_h: float = 0.007
    degraded_share: float = 0.06
    feeder_event_p: float = 0.03  # per zone-day
    feeder_event_feeder_share: float = 0.8  # of the struck feeder's devices drop
    gateway_event_p: float = 0.02  # per day, one gateway ring's uplink regresses
    window_start_h: int = 17
    window_steps: int = 8  # 2 hours of 15-minute reports


def synthetic_history(
    devices: list,
    days: int = 45,
    truth: SyntheticTruth | None = None,
    feeders: dict[str, str] | None = None,
    ring_of: dict[str, str] | None = None,
    seed: int = 20260927,
    end: datetime | None = None,
) -> list[str]:
    """Telemetry JSON lines for ``devices`` over ``days`` evening windows.

    Every value is SIMULATED with ``truth``'s rates: independent drops, feeder outages
    (a share of one feeder's devices at once) and gateway-ring regressions (every
    device on one ring at once). A dropped device stays down for the rest of that
    evening and is back the next day.
    """
    import json

    truth = truth or SyntheticTruth()
    rng = np.random.default_rng(seed)
    feeders = feeders or {}
    ring_of = ring_of or {}
    ids = [d.device_id for d in devices]
    n, steps = len(ids), truth.window_steps
    cap = np.array([d.capacity_kwh for d in devices])
    zone_of = [d.zone for d in devices]
    ring_ids = sorted(set(ring_of.values()))
    firmware = [("2.4.1" if i % 3 else "2.4.0") for i in range(n)]
    end = end or datetime(2026, 9, 26, tzinfo=UTC)
    p_step = 1 - (1 - truth.device_hazard_per_h) ** 0.25
    lines: list[str] = []
    for day in range(days):
        d0 = (end - timedelta(days=days - 1 - day)).replace(
            hour=truth.window_start_h + 5, minute=0, second=0, microsecond=0
        )  # 17:00 CDT is 22:00 UTC
        # The first report of the evening is the baseline; drops happen between reports.
        draws = rng.random((n, steps - 1)) < p_step
        fail_at = np.where(draws, np.arange(1, steps), 99).min(1)
        for z in sorted(set(zone_of)):
            if rng.random() < truth.feeder_event_p:
                zone_feeders = sorted(
                    {
                        feeders[i]
                        for i, zz in zip(ids, zone_of, strict=True)
                        if zz == z and i in feeders
                    }
                )
                if zone_feeders:
                    hit = zone_feeders[int(rng.integers(len(zone_feeders)))]
                    at = int(rng.integers(1, steps))
                    for j, dev in enumerate(ids):
                        struck = rng.random() < truth.feeder_event_feeder_share
                        if feeders.get(dev) == hit and struck:
                            fail_at[j] = min(fail_at[j], at)
        if ring_ids and rng.random() < truth.gateway_event_p:
            hit = ring_ids[int(rng.integers(len(ring_ids)))]
            at = int(rng.integers(1, steps))
            for j, dev in enumerate(ids):
                if ring_of.get(dev) == hit:
                    fail_at[j] = min(fail_at[j], at)
        degraded = rng.random(n) < truth.degraded_share
        soc = rng.uniform(0.6, 0.95, n) * cap
        for t in range(steps):
            ts = (d0 + timedelta(minutes=15 * t)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for j, dev in enumerate(ids):
                if fail_at[j] < t:
                    continue  # already down: a dropped unit stops reporting
                status = "offline" if fail_at[j] == t else ("degraded" if degraded[j] else "online")
                lines.append(
                    json.dumps(
                        {
                            "device_id": dev,
                            "ts": ts,
                            "soc_kwh": round(float(soc[j] * (1 - 0.04 * t)), 2),
                            "power_kw": 0.0 if status == "offline" else 3.0,
                            "status": status,
                            "firmware": firmware[j],
                            "gateway": ring_of.get(dev, ""),
                        }
                    )
                )
    return lines
