"""Correlated-risk map: which homes fail together, and what each group would cost.

Independent failures are cheap: the playbook re-shares one unit's kW in the same
interval. What breaks a promise is a group of homes that fails *together*. The fleet has
two kinds of shared fate, and this module names every group of each kind:

* **Gateway firmware ring** (``gridsignal.fleet.gateway_ring``): devices on one firmware
  channel. An uplink regression takes the whole ring dark at once, in every zone.
* **Feeder** (simulated): homes on one distribution feeder lose power together in a
  storm or a feeder trip. The fleet is simulated, so feeders are a stand-in: each zone's
  homes are split into four quadrants around the zone's centre. A real deployment would
  read the utility's feeder ID instead; nothing else changes.

For every group the map answers three questions from the live engine state: how many
committed kW go with it, whether the recovery playbook may recover it without a person
(its kW and device limits), and whether the healthy homes left in the same zone have the
spare headroom to cover it at all. A learned :class:`~gridsignal.twin.learn.Calibration`
adds how often that group has actually failed together in the telemetry history.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gridsignal.fleet import GATEWAY_RING_SIZE, ZONES

if TYPE_CHECKING:
    from gridsignal.control_room.engine import ControlRoomEngine
    from gridsignal.twin.learn import Calibration

CENTRES = {zone: (lat, lon) for zone, _city, lat, lon in ZONES}
QUADRANT = {(True, True): "NE", (True, False): "NW", (False, True): "SE", (False, False): "SW"}

PLAYBOOK = "Playbook recovers it"
PERSON = "Waits for a person"
UNCOVERED = "Promise at risk"
STATUS_ORDER = (UNCOVERED, PERSON, PLAYBOOK)


def feeder_of(zone: str, lat: float, lon: float) -> str:
    """Simulated feeder: the zone's quadrant the home sits in (e.g. ``LZ_NORTH-NE``)."""
    clat, clon = CENTRES.get(zone, (lat, lon))
    return f"{zone}-{QUADRANT[(lat >= clat, lon >= clon)]}"


def ring_of(device_id: str) -> str:
    return f"GW-{int(device_id.split('-')[1]) % GATEWAY_RING_SIZE:02d}"


@dataclass(frozen=True)
class RiskGroup:
    kind: str  # "feeder" or "gateway ring"
    key: str
    devices: tuple[str, ...]
    zones: tuple[str, ...]
    lost_kw: float
    share_of_target: float
    worst_zone_share: float  # largest share of one zone's committed kW in this group
    spare_kw: float  # spare headroom left in the group's zones outside the group
    uncovered_kw: float
    playbook_refusal: str | None
    lat: float
    lon: float
    seen_together: int = 0

    @property
    def covering_ratio(self) -> float:
        """Highest commitment at which the rest of the zone could still cover this group.

        Work is shared by headroom, so a group holding share ``s`` of a zone's headroom
        carries ``r * s`` of it at commitment ``r``, and the rest of the zone has
        ``(1 - r)(1 - s)`` spare. Those meet at ``r = 1 - s``.
        """
        return max(1.0 - self.worst_zone_share, 0.0)

    @property
    def status(self) -> str:
        if self.uncovered_kw > 0.05:
            return UNCOVERED
        if self.playbook_refusal is not None:
            return PERSON
        return PLAYBOOK


def groups(engine: ControlRoomEngine, calibration: Calibration | None = None) -> list[RiskGroup]:
    """Every feeder and gateway ring in the operator's fleet, worst first."""
    mine = [d for d in engine.mine if d.status.value != "offline"]
    target = max(engine.grid_event.target_kw, 1e-9)
    committed_by_zone: dict[str, float] = defaultdict(float)
    spare_by_device: dict[str, float] = {}
    for d in mine:
        committed_by_zone[d.zone] += d.assigned_kw
        spare_by_device[d.device_id] = max(engine.exportable_kw(d) - d.assigned_kw, 0.0)

    members: dict[tuple[str, str], list] = defaultdict(list)
    for d in mine:
        members[("feeder", feeder_of(d.zone, d.lat, d.lon))].append(d)
        members[("gateway ring", ring_of(d.device_id))].append(d)

    seen: dict[str, int] = defaultdict(int)
    if calibration is not None:
        for c in calibration.clusters:
            kind, _, key = c.key.partition(" ")
            seen[key] += 1

    playbook = engine.playbook
    now = engine.event_clock
    out = []
    for (kind, key), devs in members.items():
        ids = {d.device_id for d in devs}
        lost = sum(d.assigned_kw for d in devs)
        zones = sorted({d.zone for d in devs})
        lost_by_zone: dict[str, float] = defaultdict(float)
        for d in devs:
            lost_by_zone[d.zone] += d.assigned_kw
        spare = 0.0
        uncovered = 0.0
        for z in zones:
            zone_spare = sum(
                spare_by_device[d.device_id] for d in mine if d.zone == z and d.device_id not in ids
            )
            spare += zone_spare
            uncovered += max(lost_by_zone[z] - zone_spare, 0.0)
        worst = max(
            (lost_by_zone[z] / committed_by_zone[z] for z in zones if committed_by_zone[z] > 0),
            default=0.0,
        )
        if playbook is None:
            refusal = "no playbook approved"
        else:
            refusal = playbook.refusal(lost, len(devs), now)
        out.append(
            RiskGroup(
                kind=kind,
                key=key,
                devices=tuple(sorted(ids)),
                zones=tuple(zones),
                lost_kw=round(lost, 2),
                share_of_target=lost / target,
                worst_zone_share=worst,
                spare_kw=round(spare, 2),
                uncovered_kw=round(uncovered, 2),
                playbook_refusal=refusal,
                lat=sum(d.lat for d in devs) / len(devs),
                lon=sum(d.lon for d in devs) / len(devs),
                seen_together=seen.get(key, 0),
            )
        )
    return sorted(out, key=lambda g: (STATUS_ORDER.index(g.status), -g.lost_kw))


def device_groups(engine: ControlRoomEngine) -> dict[str, dict[str, str]]:
    """Device -> {feeder, ring}, for the map and for the learning loop."""
    return {
        d.device_id: {"feeder": feeder_of(d.zone, d.lat, d.lon), "ring": ring_of(d.device_id)}
        for d in engine.mine
    }


def worst_feeder_share(risk: list[RiskGroup]) -> float:
    """Largest share of one zone's commitment that sits on a single feeder."""
    return max((g.worst_zone_share for g in risk if g.kind == "feeder"), default=0.0)


def summary(risk: list[RiskGroup]) -> dict[str, int]:
    counts = {s: 0 for s in STATUS_ORDER}
    for g in risk:
        counts[g.status] += 1
    return counts


@dataclass(frozen=True)
class StormResult:
    """What one storm cell does to tonight's promise if every home under it drops."""

    lat: float
    lon: float
    radius_km: float
    devices: tuple[str, ...]
    lost_kw: float
    spare_kw: float
    uncovered_kw: float
    target_kw: float
    kept_share: float
    zones: tuple[str, ...]
    by_zone: dict[str, dict[str, float]]

    @property
    def holds(self) -> bool:
        return self.uncovered_kw <= 1e-6


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def storm(engine: ControlRoomEngine, lat: float, lon: float, radius_km: float) -> StormResult:
    """Drop every operator home within ``radius_km`` of a point and see who covers the loss.

    Same arithmetic as :func:`groups`: the lost kW is what those homes were assigned; only
    healthy homes in the same zone and outside the storm can pick it up, each up to its spare.
    """
    if not (0 < radius_km <= 800):
        raise ValueError("radius_km must be between 0 and 800")
    mine = [d for d in engine.mine if d.status.value != "offline"]
    target = max(engine.grid_event.target_kw, 1e-9)
    hit = {d.device_id for d in mine if _km(lat, lon, d.lat, d.lon) <= radius_km}
    by_zone: dict[str, dict[str, float]] = {}
    for d in mine:
        z = by_zone.setdefault(d.zone, {"lost_kw": 0.0, "spare_kw": 0.0})
        if d.device_id in hit:
            z["lost_kw"] += d.assigned_kw
        else:
            z["spare_kw"] += max(engine.exportable_kw(d) - d.assigned_kw, 0.0)
    lost = spare = uncovered = 0.0
    zones = []
    for name, z in sorted(by_zone.items()):
        if z["lost_kw"] <= 0:
            continue
        zones.append(name)
        z["uncovered_kw"] = max(z["lost_kw"] - z["spare_kw"], 0.0)
        lost += z["lost_kw"]
        spare += z["spare_kw"]
        uncovered += z["uncovered_kw"]
    return StormResult(
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        devices=tuple(sorted(hit)),
        lost_kw=round(lost, 2),
        spare_kw=round(spare, 2),
        uncovered_kw=round(uncovered, 2),
        target_kw=round(target, 2),
        kept_share=max(0.0, 1.0 - uncovered / target),
        zones=tuple(zones),
        by_zone={
            k: {a: round(b, 2) for a, b in v.items()} for k, v in by_zone.items() if k in zones
        },
    )
