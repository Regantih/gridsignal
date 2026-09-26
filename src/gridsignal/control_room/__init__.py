"""Simulation-only control room for a distributed home-battery fleet.

Nothing in this package talks to real devices, utilities or operational systems.
All data is deterministic and generated locally.
"""

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import (
    AuditEvent,
    Device,
    DeviceStatus,
    FleetSnapshot,
    GridEvent,
    Incident,
    IncidentStatus,
    Severity,
    Task,
    TaskStatus,
)

__all__ = [
    "AuditEvent",
    "ControlRoomEngine",
    "Device",
    "DeviceStatus",
    "FleetSnapshot",
    "GridEvent",
    "Incident",
    "IncidentStatus",
    "Severity",
    "Task",
    "TaskStatus",
]
