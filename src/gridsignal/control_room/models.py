"""Dataclasses describing the simulated fleet, incidents, tasks and audit log."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DeviceStatus(StrEnum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNAVAILABLE = "unavailable"


class UnitType(StrEnum):
    """Simulated hardware generations in the fleet.

    ``BASE_CORE`` models the larger unit Base describes in public interviews
    (40 kWh, 20 kW). Simulated, and not an official specification.
    """

    LEGACY = "legacy"
    BASE_CORE = "base_core"


class Controller(StrEnum):
    """Who is allowed to dispatch a battery.

    In retail-choice markets the operator controls the unit; elsewhere a utility
    partner controls it and the operator must never bid or reassign it. Simulated
    tenancy model, not a description of any real commercial arrangement.
    """

    BASE = "base"
    UTILITY = "utility"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    DETECTED = "detected"
    AWAITING_APPROVAL = "awaiting_approval"
    RECOVERING = "recovering"
    RESOLVED = "resolved"


class TaskStatus(StrEnum):
    BLOCKED = "blocked"
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class Role(StrEnum):
    FLEET_OPERATOR = "Fleet Operator"
    RELIABILITY_ENGINEER = "Reliability Engineer"
    FIELD_SUPPORT = "Field Support"


@dataclass
class Device:
    """A simulated home battery."""

    device_id: str
    site: str
    zone: str
    lat: float
    lon: float
    capacity_kwh: float
    state_of_charge: float
    power_kw: float
    status: DeviceStatus = DeviceStatus.ONLINE
    last_telemetry_s: int = 0
    #: Exported kW committed to the grid event, on top of the home's own load.
    assigned_kw: float = 0.0
    unit_type: UnitType = UnitType.LEGACY
    controller: Controller = Controller.BASE
    #: Simulated household draw the battery serves before anything is exported.
    home_load_kw: float = 0.0
    #: Member opted this home into sharing surplus during a neighbourhood island.
    mutual_aid: bool = False
    #: A member who has told us they run a medical device at home.
    medical_device: bool = False
    #: Output of the member's own portable generator, 0 kW when they have none.
    generator_kw: float = 0.0
    #: Hours of fuel the member keeps for that generator.
    generator_fuel_h: float = 0.0
    #: Firmware build the device last reported, empty until telemetry says otherwise.
    firmware: str = ""
    #: Gateway the device last reported through.
    gateway: str = ""
    #: Output the device last measured, positive discharging. Read from telemetry and
    #: shown as measured; the plan the engine commits is ``assigned_kw``.
    measured_power_kw: float | None = None

    @property
    def is_dispatchable(self) -> bool:
        return self.status in (DeviceStatus.ONLINE, DeviceStatus.DEGRADED)

    @property
    def is_operator_controlled(self) -> bool:
        """Whether this tenant's batteries may be bid, awarded or reassigned here."""
        return self.controller is Controller.BASE

    @property
    def available_kwh(self) -> float:
        if not self.is_dispatchable:
            return 0.0
        return round(self.capacity_kwh * self.state_of_charge, 2)

    @property
    def discharge_kw(self) -> float:
        """Total battery output: the home is served first, the rest is exported."""
        if not self.is_dispatchable:
            return 0.0
        return round(self.home_load_kw + self.assigned_kw, 2)

    @property
    def export_kw(self) -> float:
        """kW leaving the house, which is what the grid event counts."""
        return self.assigned_kw if self.is_dispatchable else 0.0


@dataclass(frozen=True)
class Reading:
    """One validated telemetry row, ready to apply to a device.

    The wire format and the validation live in :mod:`gridsignal.telemetry`; this is
    what survives it.
    """

    line_no: int
    device_id: str
    ts: datetime
    soc_kwh: float
    power_kw: float
    status: DeviceStatus
    firmware: str
    gateway: str


@dataclass(frozen=True)
class Rejection:
    """A telemetry row that was not applied, and the reason an operator can act on."""

    line_no: int
    reason: str
    device_id: str = ""

    def __str__(self) -> str:
        who = f" [{self.device_id}]" if self.device_id else ""
        return f"line {self.line_no}{who}: {self.reason}"


@dataclass
class GridEvent:
    """A simulated ERCOT-style grid event the fleet is responding to."""

    name: str
    zone: str
    status: str
    target_kw: float
    price_mwh: float
    started_at: datetime
    ends_at: datetime
    price_source: str = ""

    @property
    def duration_hours(self) -> float:
        return (self.ends_at - self.started_at).total_seconds() / 3600.0


@dataclass
class Task:
    task_id: str
    role: Role
    owner: str
    title: str
    detail: str
    status: TaskStatus = TaskStatus.OPEN


@dataclass
class AuditEvent:
    at: datetime
    actor: str
    kind: str
    summary: str
    detail: str = ""


@dataclass
class Playbook:
    """Recovery an operator has approved in advance, inside written limits.

    The Control Room normally waits for a person before it reassigns any capacity. The
    twin's stress test (``gridsignal.twin``) found that wait costs the very interval a
    unit drops in: over 30 simulated 2025-like years it keeps a 75% commitment on about
    96.6% of days, against 99.8% when the same recovery runs the moment the loss is seen.
    A playbook keeps the human decision and moves it earlier: the operator approves the
    rule once, with limits, and every incident outside the limits still waits for them.
    """

    playbook_id: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime
    #: Largest single loss (kW) the playbook may recover without asking.
    max_kw: float
    #: Largest number of devices one incident may take out and still be covered. A
    #: failure wider than this is correlated (a gateway ring, a feeder) and is exactly
    #: what the twin says the fleet cannot absorb blind, so it goes to a person.
    max_devices: int
    executions: int = 0
    revoked_at: datetime | None = None

    def refusal(self, lost_kw: float, devices: int, now: datetime) -> str | None:
        """Why this playbook cannot execute a recovery, or ``None`` if it can."""
        if self.revoked_at is not None:
            return f"playbook {self.playbook_id} was revoked at {self.revoked_at:%H:%M:%S}"
        if now > self.expires_at:
            return f"playbook {self.playbook_id} expired at {self.expires_at:%H:%M:%S}"
        if devices > self.max_devices:
            return (
                f"{devices:,} devices out is wider than the playbook's {self.max_devices:,}-device "
                "limit: a correlated outage goes to a person"
            )
        if lost_kw > self.max_kw + 1e-9:
            return f"{lost_kw:,.1f} kW lost is more than the playbook's {self.max_kw:,.1f} kW limit"
        return None


@dataclass
class Incident:
    incident_id: str
    device_id: str
    severity: Severity
    status: IncidentStatus
    opened_at: datetime
    title: str
    root_cause_hypothesis: str
    impact: str
    recommended_action: str
    owner: str
    lost_kw: float = 0.0
    window_hours: float = 0.0
    price_mwh: float = 0.0
    dollars_at_risk: float = 0.0
    restored_kw: float = 0.0
    dollars_recovered: float = 0.0
    # Every device knocked out by this failure (one gateway firmware ring).
    cohort: list[str] = field(default_factory=list)
    # Dispatch each device held just before the approved reallocation, so a home can be
    # told how much of the recovery its own battery absorbed.
    assigned_kw_before_recovery: dict[str, float] = field(default_factory=dict)
    approval_required: bool = True
    #: Set when a pre-approved recovery playbook executed the plan instead of a person
    #: approving this incident: the playbook id and who approved the playbook.
    executed_under: str | None = None
    #: Why the playbook did not cover this incident, so it waited for a person.
    escalation_reason: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    resolved_at: datetime | None = None
    tasks: list[Task] = field(default_factory=list)


@dataclass
class FleetSnapshot:
    """Everything the dashboard renders, computed from engine state."""

    devices: list[Device]
    grid_event: GridEvent
    incidents: list[Incident]
    audit: list[AuditEvent]
    now: datetime

    @property
    def total_devices(self) -> int:
        return len(self.devices)

    @property
    def online(self) -> int:
        return sum(1 for d in self.devices if d.status is DeviceStatus.ONLINE)

    @property
    def degraded(self) -> int:
        return sum(1 for d in self.devices if d.status is DeviceStatus.DEGRADED)

    @property
    def offline(self) -> int:
        return sum(1 for d in self.devices if d.status is DeviceStatus.OFFLINE)

    @property
    def unavailable(self) -> int:
        return sum(1 for d in self.devices if d.status is DeviceStatus.UNAVAILABLE)

    @property
    def available_capacity_kwh(self) -> float:
        return round(sum(d.available_kwh for d in self.devices), 1)

    @property
    def committed_kw(self) -> float:
        """Exported kW counting towards the operator's commitment.

        Utility-controlled units belong to another tenant and are never part of this
        number. What they export here is a simulated stand-in, not a partner schedule.
        """
        return round(
            sum(
                d.export_kw for d in self.devices if d.is_dispatchable and d.is_operator_controlled
            ),
            1,
        )

    @property
    def home_load_kw(self) -> float:
        """Simulated household draw the fleet's batteries are serving right now."""
        return round(sum(d.home_load_kw for d in self.devices if d.is_dispatchable), 1)

    @property
    def discharge_kw(self) -> float:
        """Everything the batteries are putting out: home load plus exports."""
        return round(sum(d.discharge_kw for d in self.devices if d.is_dispatchable), 1)

    @property
    def partner_kw(self) -> float:
        """Exported kW of the separate, utility-controlled tenant. Simulated stand-in."""
        return round(
            sum(
                d.export_kw
                for d in self.devices
                if d.is_dispatchable and not d.is_operator_controlled
            ),
            1,
        )

    @property
    def open_incidents(self) -> int:
        return sum(1 for i in self.incidents if i.status is not IncidentStatus.RESOLVED)

    @property
    def coverage_pct(self) -> float:
        if self.grid_event.target_kw <= 0:
            return 100.0
        return round(100.0 * self.committed_kw / self.grid_event.target_kw, 1)

    @property
    def tasks(self) -> list[Task]:
        return [t for incident in self.incidents for t in incident.tasks]
