"""Home-first dispatch: the house is served before anything is exported.

Every battery in this repo backs its own home first and sells only what is left, so
exported kW is battery discharge minus the home's load. That load, the members'
backup reserve, the mixed hardware and the neighbour mutual-aid sharing modelled here
are all simulated; no real home, meter or member is involved.

Assumptions, not measurements:

* ``HOURLY_LOAD_KW`` is a synthetic summer weekday load shape for one Texas home.
* ``ESSENTIAL_LOAD_KW`` is what a home draws on backup once heavy loads are shed.
* A member's portable generator, when they have one, tops the battery back up at
  ``generator_kw`` for ``generator_fuel_h`` hours.
"""

from __future__ import annotations

from dataclasses import dataclass

from gridsignal.control_room.models import Controller, Device, UnitType
from gridsignal.load import ESSENTIAL_LOAD_KW, HOURLY_LOAD_KW, home_load_kw, load_multiplier

__all__ = [
    "ESSENTIAL_LOAD_KW",
    "HOURLY_LOAD_KW",
    "home_load_kw",
    "load_multiplier",
    "DEFAULT_RESERVE_FRACTION",
    "STORM_RESERVE_FRACTION",
    "backup_estimate",
    "by_tenant",
    "by_unit_type",
    "mutual_aid_plan",
    "apply_aid",
    "reserve_kwh",
    "spare_backup_kwh",
]

#: Stored energy held back for the member, as a share of nameplate capacity.
DEFAULT_RESERVE_FRACTION = 0.20
#: What the operator raises the floor to before a forecast storm or high-risk day.
STORM_RESERVE_FRACTION = 0.50
#: Smallest surplus worth moving between two neighbours during an island.
MIN_SHARE_KWH = 0.25
#: Ceiling on what one member gives a neighbour in a single island.
MAX_SHARE_KWH = 2.0
#: Backup a member running a medical device is helped up to during an island.
MEDICAL_BACKUP_HOURS = 12.0


def reserve_kwh(device: Device, fraction: float = DEFAULT_RESERVE_FRACTION) -> float:
    """Stored energy the member keeps for themselves, never bid into an event."""
    return round(device.capacity_kwh * fraction, 2)


def generator_kwh(device: Device) -> float:
    """Energy a member could put back into the battery from their own generator."""
    return round(device.generator_kw * device.generator_fuel_h, 2)


def spare_backup_kwh(device: Device, fraction: float = DEFAULT_RESERVE_FRACTION) -> float:
    """Stored energy above the member's reserve floor, which is theirs to lend."""
    return round(max(device.available_kwh - reserve_kwh(device, fraction), 0.0), 2)


@dataclass(frozen=True)
class BackupEstimate:
    """How long one home can run itself, with and without the member's generator."""

    device_id: str
    stored_kwh: float
    committed_kwh: float
    reserve_kwh: float
    backup_kwh: float
    generator_kwh: float
    load_kw: float
    hours: float
    hours_with_generator: float

    @property
    def generator_hours(self) -> float:
        return round(self.hours_with_generator - self.hours, 1)


def backup_estimate(
    device: Device,
    hours_left: float,
    fraction: float = DEFAULT_RESERVE_FRACTION,
    load_kw: float = ESSENTIAL_LOAD_KW,
) -> BackupEstimate:
    """Backup hours from state of charge, the member's own load and their generator.

    The export commitment is what can still be sold; everything under it backs up the
    home. A generator only extends the estimate up to what the battery can hold.
    """
    stored = round(device.capacity_kwh * device.state_of_charge, 2)
    committed = round(min(device.assigned_kw * hours_left, stored), 2)
    backup = round(max(stored - committed, 0.0), 2)
    topped = round(min(generator_kwh(device), max(device.capacity_kwh - backup, 0.0)), 2)
    load = max(load_kw, 0.1)
    return BackupEstimate(
        device_id=device.device_id,
        stored_kwh=stored,
        committed_kwh=committed,
        reserve_kwh=reserve_kwh(device, fraction),
        backup_kwh=backup,
        generator_kwh=topped,
        load_kw=round(load, 2),
        hours=round(backup / load, 1),
        hours_with_generator=round((backup + topped) / load, 1),
    )


@dataclass(frozen=True)
class UnitTypeRow:
    """Revenue, export and backup by simulated hardware generation."""

    unit_type: str
    devices: int
    capacity_kwh: float
    export_kw: float
    home_load_kw: float
    revenue_usd: float
    backup_hours: float


def by_unit_type(
    devices: list[Device],
    hours: float,
    price_mwh: float,
    fraction: float = DEFAULT_RESERVE_FRACTION,
) -> list[UnitTypeRow]:
    """Split the fleet's export, revenue and backup hours by unit type."""
    rows: list[UnitTypeRow] = []
    for unit_type in (UnitType.LEGACY, UnitType.BASE_CORE):
        group = [d for d in devices if d.unit_type is unit_type]
        if not group:
            continue
        export = round(sum(d.export_kw for d in group), 1)
        backups = [backup_estimate(d, hours, fraction).hours for d in group]
        rows.append(
            UnitTypeRow(
                unit_type=unit_type.value,
                devices=len(group),
                capacity_kwh=round(sum(d.capacity_kwh for d in group), 1),
                export_kw=export,
                home_load_kw=round(sum(d.home_load_kw for d in group if d.is_dispatchable), 1),
                revenue_usd=round(export * hours * price_mwh / 1000.0, 2),
                backup_hours=round(sum(backups) / len(backups), 1),
            )
        )
    return rows


@dataclass(frozen=True)
class TenantRow:
    """One control authority's slice of the fleet."""

    controller: str
    devices: int
    export_kw: float
    dispatchable: int


def by_tenant(devices: list[Device]) -> list[TenantRow]:
    """Who controls what: this mesh may only dispatch the operator's own tenant."""
    rows: list[TenantRow] = []
    for controller in (Controller.BASE, Controller.UTILITY):
        group = [d for d in devices if d.controller is controller]
        if not group:
            continue
        rows.append(
            TenantRow(
                controller=controller.value,
                devices=len(group),
                export_kw=round(sum(d.export_kw for d in group), 1),
                dispatchable=sum(1 for d in group if d.is_dispatchable),
            )
        )
    return rows


@dataclass(frozen=True)
class AidTransfer:
    """One simulated neighbour-to-neighbour share during an island."""

    from_device: str
    to_device: str
    kwh: float
    reason: str


@dataclass(frozen=True)
class AidPlan:
    """Who helped whom, and what it bought the member who needed it."""

    recipient: str
    transfers: list[AidTransfer]
    hours_before: float
    hours_after: float

    @property
    def shared_kwh(self) -> float:
        return round(sum(t.kwh for t in self.transfers), 2)

    @property
    def hours_gained(self) -> float:
        return round(self.hours_after - self.hours_before, 1)


def mutual_aid_plan(
    devices: list[Device],
    recipient_id: str,
    fraction: float = DEFAULT_RESERVE_FRACTION,
    load_kw: float = ESSENTIAL_LOAD_KW,
    max_share_kwh: float = MAX_SHARE_KWH,
    target_hours: float = MEDICAL_BACKUP_HOURS,
) -> AidPlan | None:
    """Share surplus with an opted-in neighbour who runs a medical device.

    The recipient is topped up towards ``target_hours`` of essential load. Only
    opted-in members give, only their energy above their own reserve floor moves, and
    a giver never drops below their own reserve. Simulated: no energy is transferred
    anywhere, this is a plan a member would be asked to confirm.
    """
    by_id = {d.device_id: d for d in devices}
    recipient = by_id.get(recipient_id)
    if recipient is None or not recipient.medical_device or not recipient.mutual_aid:
        return None

    load = max(load_kw, 0.1)
    target_kwh = min(target_hours * load, recipient.capacity_kwh)
    need_kwh = round(max(target_kwh - recipient.available_kwh, 0.0), 2)
    transfers: list[AidTransfer] = []
    if need_kwh > 0:
        givers = sorted(
            (
                d
                for d in devices
                if d.device_id != recipient_id
                and d.mutual_aid
                and d.zone == recipient.zone
                and d.is_dispatchable
                and spare_backup_kwh(d, fraction) >= MIN_SHARE_KWH
            ),
            key=lambda d: (-spare_backup_kwh(d, fraction), d.device_id),
        )
        remaining = need_kwh
        for giver in givers:
            if remaining < MIN_SHARE_KWH:
                break
            share = round(min(spare_backup_kwh(giver, fraction), max_share_kwh, remaining), 2)
            if share < MIN_SHARE_KWH:
                continue
            transfers.append(
                AidTransfer(
                    from_device=giver.device_id,
                    to_device=recipient_id,
                    kwh=share,
                    reason="surplus above own backup reserve",
                )
            )
            remaining = round(remaining - share, 2)

    shared = round(sum(t.kwh for t in transfers), 2)
    hours_before = round(recipient.available_kwh / load, 1)
    return AidPlan(
        recipient=recipient_id,
        transfers=transfers,
        hours_before=hours_before,
        hours_after=round((recipient.available_kwh + shared) / load, 1),
    )


def apply_aid(devices: list[Device], plan: AidPlan) -> None:
    """Move the planned energy in the simulation, keeping every giver above reserve."""
    by_id = {d.device_id: d for d in devices}
    for transfer in plan.transfers:
        giver = by_id[transfer.from_device]
        taker = by_id[transfer.to_device]
        giver.state_of_charge = round(
            max(giver.state_of_charge - transfer.kwh / giver.capacity_kwh, 0.0), 4
        )
        taker.state_of_charge = round(
            min(taker.state_of_charge + transfer.kwh / taker.capacity_kwh, 1.0), 4
        )
