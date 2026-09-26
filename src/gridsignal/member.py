"""What a Base member sees at home while the operator works the same incident.

The operator view is about the fleet; this is about one house. It answers the three
questions a homeowner actually has during a grid event: will my lights stay on, what
did my battery earn, and is something wrong with my equipment.

Assumptions (not measured data): a home draws ``ESSENTIAL_LOAD_KW`` on backup and the
member keeps ``MEMBER_REVENUE_SHARE`` of what their battery earns in the event.
"""

from __future__ import annotations

from dataclasses import dataclass

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import Device, DeviceStatus, Incident, IncidentStatus
from gridsignal.fleet import FOCUS_DEVICE_ID
from gridsignal.prices import energy_value_usd

ESSENTIAL_LOAD_KW = 1.2  # fridge, lights, internet, a few outlets
MEMBER_REVENUE_SHARE = 0.6  # member's cut of the grid-event value their battery creates


@dataclass(frozen=True)
class MemberSummary:
    """One home's view of the event: backup, earnings, and a plain-English notice."""

    device_id: str
    site: str
    # Backup protection
    stored_kwh: float
    committed_kwh: float
    backup_kwh: float
    backup_hours: float
    # Money
    grid_value_usd: float
    earned_usd: float
    protected_usd: float
    # Notice
    is_affected: bool
    headline: str
    body: str
    next_step: str

    @property
    def total_usd(self) -> float:
        return round(self.earned_usd + self.protected_usd, 2)


def _incident_for(engine: ControlRoomEngine, device_id: str) -> Incident | None:
    """The most recent incident this home was caught up in, if any."""
    for incident in reversed(engine.incidents):
        if device_id in (incident.cohort or [incident.device_id]):
            return incident
    return None


def _backup(device: Device, hours_left: float) -> tuple[float, float, float, float]:
    """Split stored energy into what the grid event may take and what backs up the home."""
    stored = round(device.capacity_kwh * device.state_of_charge, 2)
    committed = round(min(device.assigned_kw * hours_left, stored), 2)
    backup_kwh = round(max(stored - committed, 0.0), 2)
    return stored, committed, backup_kwh, round(backup_kwh / ESSENTIAL_LOAD_KW, 1)


def _notice(
    incident: Incident | None,
    affected: bool,
    backup_hours: float,
    degraded: bool = False,
) -> tuple[str, str, str]:
    """Plain-English status for the homeowner: no jargon, no incident IDs."""
    if (incident is None or not affected) and degraded:
        return (
            "Your battery is reporting slowly",
            "Your system is online and still backing up your home, but it is sending us "
            "readings less often than usual, so we have it on a watch list and are holding "
            "back some of its grid participation until it settles.",
            f"Nothing to do. You have about {backup_hours:.1f} hours of backup held in reserve.",
        )

    if incident is None or not affected:
        return (
            "Your battery is healthy",
            "Your system is online and taking part in today's grid event. "
            "Your home keeps priority over the grid: backup energy is reserved first.",
            f"Nothing to do. You have about {backup_hours:.1f} hours of backup held in reserve.",
        )

    if incident.status is IncidentStatus.RESOLVED:
        return (
            "Resolved: your battery is back under our watch",
            "The internet gateway at your home stopped reporting to us during a grid event. "
            "Your battery itself was never at fault and kept protecting your home the whole "
            "time. We paused its grid participation and other batteries covered your share, "
            "so the neighbourhood commitment was still met.",
            "A technician visit is scheduled to replace the gateway. "
            "Your backup protection is unaffected in the meantime.",
        )

    return (
        "We've lost contact with your battery",
        "Your home's gateway stopped sending us data during a grid event. Your battery is "
        "still running and still protecting your home — we just cannot see it right now, so "
        "we have paused its grid participation while an operator reviews what happened.",
        f"No action needed from you. You still have about {backup_hours:.1f} hours of "
        "backup available if the power goes out.",
    )


def member_summary(
    engine: ControlRoomEngine,
    device_id: str = FOCUS_DEVICE_ID,
    share: float = MEMBER_REVENUE_SHARE,
) -> MemberSummary:
    """Build one home's summary from the same simulation the operator is looking at."""
    device = engine.device(device_id)
    hours_left = engine.remaining_hours()
    stored, committed_kwh, backup_kwh, backup_hours = _backup(device, hours_left)

    price = engine.grid_event.price_mwh
    grid_value = energy_value_usd(device.assigned_kw, engine.grid_event.duration_hours, price)
    earned = round(grid_value * share, 2)

    incident = _incident_for(engine, device_id)
    affected = incident is not None and device.status in (
        DeviceStatus.OFFLINE,
        DeviceStatus.UNAVAILABLE,
    )
    # Dollars this home helped protect: its slice of what the fleet clawed back after the
    # operator approved a recovery anywhere in the fleet. Knocked-out homes protect nothing.
    protected = 0.0
    recovery = engine.incidents[-1] if engine.incidents else None
    if recovery is not None and not affected and recovery.restored_kw > 0:
        before = recovery.assigned_kw_before_recovery.get(device_id)
        if before is not None:
            extra_kw = max(device.assigned_kw - before, 0.0)
            protected = round(recovery.dollars_recovered * extra_kw / recovery.restored_kw, 2)

    degraded = device.status is DeviceStatus.DEGRADED
    headline, body, next_step = _notice(incident, affected, backup_hours, degraded)
    return MemberSummary(
        device_id=device.device_id,
        site=device.site,
        stored_kwh=stored,
        committed_kwh=committed_kwh,
        backup_kwh=backup_kwh,
        backup_hours=backup_hours,
        grid_value_usd=grid_value,
        earned_usd=earned,
        protected_usd=protected,
        is_affected=affected,
        headline=headline,
        body=body,
        next_step=next_step,
    )


def neighbours(
    engine: ControlRoomEngine, device_id: str = FOCUS_DEVICE_ID, count: int = 3
) -> list[str]:
    """Device ids of nearby homes, for the 'your neighbours covered for you' line."""
    zone = engine.device(device_id).zone
    return [d.device_id for d in engine.devices if d.zone == zone and d.device_id != device_id][
        :count
    ]
