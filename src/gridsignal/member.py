"""What a Base member sees at home while the operator works the same incident.

The operator view is about the fleet; this is about one house. It answers the three
questions a member actually has during a grid event: will my lights stay on, what did
my battery earn, and is something wrong with my equipment.

The battery serves the house first and exports only the surplus, so what this member
earns is priced on exported kW, never on the energy their own home just used.

Assumptions (not measured data): a home draws ``ESSENTIAL_LOAD_KW`` on backup and the
member keeps ``MEMBER_REVENUE_SHARE`` of what their battery earns in the event.
"""

from __future__ import annotations

from dataclasses import dataclass

from gridsignal import home
from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import DeviceStatus, Incident, IncidentStatus
from gridsignal.fleet import FOCUS_DEVICE_ID
from gridsignal.load import ESSENTIAL_LOAD_KW
from gridsignal.prices import energy_value_usd

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
    backup_hours_with_generator: float
    generator_kwh: float
    reserve_kwh: float
    # Home-first dispatch: discharge = what the house takes + what is exported
    home_load_kw: float
    export_kw: float
    discharge_kw: float
    unit_type: str
    controller: str
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

    @property
    def generator_hours(self) -> float:
        return round(self.backup_hours_with_generator - self.backup_hours, 1)


def _incident_for(engine: ControlRoomEngine, device_id: str) -> Incident | None:
    """The most recent incident this home was caught up in, if any."""
    for incident in reversed(engine.incidents):
        if device_id in (incident.cohort or [incident.device_id]):
            return incident
    return None


def _notice(
    incident: Incident | None,
    affected: bool,
    backup_hours: float,
    degraded: bool = False,
) -> tuple[str, str, str]:
    """Plain-English status for the member: no jargon, no incident IDs."""
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
            "Resolved: your battery is reporting to us again",
            "The internet gateway at your home stopped reporting during a grid event, so for "
            "that period we could not confirm the state of your battery. Its readings are "
            "back now and nothing is wrong with the battery itself. We paused its grid "
            "participation while it was dark and other batteries covered your share, so the "
            "neighbourhood commitment was still met.",
            "A technician visit is scheduled to replace the gateway. "
            "Your backup protection is unaffected now that readings are back.",
        )

    return (
        "We've lost contact with your battery",
        "Your home's gateway stopped sending us data during a grid event, so we cannot "
        "currently confirm whether your battery is charged, discharging or able to back up "
        "your home. We have paused its grid participation while an operator reviews what "
        "happened.",
        f"No action needed from you. The last reading we received showed about "
        f"{backup_hours:.1f} hours of backup, but we cannot confirm that until contact "
        "is restored — if your power is out and the battery is not carrying the house, "
        "call us.",
    )


def member_summary(
    engine: ControlRoomEngine,
    device_id: str = FOCUS_DEVICE_ID,
    share: float = MEMBER_REVENUE_SHARE,
) -> MemberSummary:
    """Build one home's summary from the same simulation the operator is looking at."""
    device = engine.device(device_id)
    hours_left = engine.remaining_hours()
    estimate = home.backup_estimate(
        device, hours_left, engine.reserve_fraction, load_kw=ESSENTIAL_LOAD_KW
    )
    backup_hours = estimate.hours

    price = engine.grid_event.price_mwh
    grid_value = energy_value_usd(device.export_kw, engine.grid_event.duration_hours, price)
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
        stored_kwh=estimate.stored_kwh,
        committed_kwh=estimate.committed_kwh,
        backup_kwh=estimate.backup_kwh,
        backup_hours=backup_hours,
        backup_hours_with_generator=estimate.hours_with_generator,
        generator_kwh=estimate.generator_kwh,
        reserve_kwh=estimate.reserve_kwh,
        home_load_kw=device.home_load_kw if device.is_dispatchable else 0.0,
        export_kw=device.export_kw,
        discharge_kw=device.discharge_kw,
        unit_type=device.unit_type.value,
        controller=device.controller.value,
        grid_value_usd=grid_value,
        earned_usd=earned,
        protected_usd=protected,
        is_affected=affected,
        headline=headline,
        body=body,
        next_step=next_step,
    )


def aid_candidate(engine: ControlRoomEngine) -> str | None:
    """A member in this simulation who would be offered neighbour mutual aid.

    The recipient runs a medical device, has opted in, and would run out before the
    island is expected to end — the only case where neighbours are asked to share.
    """
    short = [
        d
        for d in engine.mine
        if d.medical_device
        and d.mutual_aid
        and d.available_kwh < home.MEDICAL_BACKUP_HOURS * ESSENTIAL_LOAD_KW
    ]
    if not short:
        return None
    return min(short, key=lambda d: (d.available_kwh, d.device_id)).device_id


def aid_plan(engine: ControlRoomEngine, recipient_id: str) -> home.AidPlan | None:
    """Simulated mutual-aid plan for one recipient, nothing applied to the fleet."""
    return home.mutual_aid_plan(engine.mine, recipient_id, engine.reserve_fraction)


def neighbours(
    engine: ControlRoomEngine, device_id: str = FOCUS_DEVICE_ID, count: int = 3
) -> list[str]:
    """Device ids of nearby homes, for the 'your neighbours covered for you' line."""
    zone = engine.device(device_id).zone
    return [d.device_id for d in engine.devices if d.zone == zone and d.device_id != device_id][
        :count
    ]
