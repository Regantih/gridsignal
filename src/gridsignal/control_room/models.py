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
    assigned_kw: float = 0.0

    @property
    def is_dispatchable(self) -> bool:
        return self.status in (DeviceStatus.ONLINE, DeviceStatus.DEGRADED)

    @property
    def available_kwh(self) -> float:
        if not self.is_dispatchable:
            return 0.0
        return round(self.capacity_kwh * self.state_of_charge, 2)


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
    approval_required: bool = True
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
        return round(sum(d.assigned_kw for d in self.devices if d.is_dispatchable), 1)

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
