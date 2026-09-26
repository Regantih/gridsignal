"""Simulation-only control room for a distributed home-battery fleet.

Nothing in this package talks to real devices, utilities or operational systems.
All data is deterministic and generated locally.

The engine is exported lazily. It reads :mod:`gridsignal.fleet`, which reads the models
in this package, so importing it here eagerly would make ``import gridsignal.fleet``
depend on which module the process happened to import first.
"""

from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from gridsignal.control_room.engine import ControlRoomEngine


def __getattr__(name: str) -> object:
    if name == "ControlRoomEngine":
        from gridsignal.control_room.engine import ControlRoomEngine

        return ControlRoomEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
