"""Deterministic state machine behind the GridSignal Control Room.

Lifecycle of the demo:

    stable -> telemetry failure detected -> incident opened (awaiting human approval)
           -> operator approves -> work reassigned, device quarantined -> recovered

The engine never dispatches anything: it mutates an in-memory simulation and records
an append-only audit trail. Recovery only happens after an explicit human approval.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from gridsignal.control_room.models import (
    AuditEvent,
    Device,
    DeviceStatus,
    FleetSnapshot,
    GridEvent,
    Incident,
    IncidentStatus,
    Role,
    Severity,
    Task,
    TaskStatus,
)
from gridsignal.fleet import DEFAULT_SEED, FOCUS_DEVICE_ID, build_fleet

SIM_START = datetime(2026, 9, 26, 17, 5, 0)
GRID_EVENT_TARGET_KW = 240.0
TELEMETRY_STALE_SECONDS = 120

OWNERS: dict[Role, str] = {
    Role.FLEET_OPERATOR: "M. Alvarez (Fleet Operator)",
    Role.RELIABILITY_ENGINEER: "T. Okafor (Reliability Engineer)",
    Role.FIELD_SUPPORT: "J. Nguyen (Field Support)",
}


class ApprovalError(RuntimeError):
    """Raised when a recovery plan is approved out of order."""


class ControlRoomEngine:
    """In-memory, deterministic simulation of a battery-fleet control room."""

    def __init__(self, seed: int = DEFAULT_SEED) -> None:
        self.seed = seed
        self.reset()

    # ------------------------------------------------------------------ setup

    def reset(self) -> None:
        """Return the simulation to its stable starting state."""
        self._clock = SIM_START
        self.devices: list[Device] = build_fleet(self.seed)
        self.incidents: list[Incident] = []
        self.audit: list[AuditEvent] = []
        self._incident_seq = 0
        self.grid_event = GridEvent(
            name="ERCOT peak-demand response window",
            zone="ERCOT, all load zones",
            status="active",
            target_kw=GRID_EVENT_TARGET_KW,
            price_mwh=412.0,
            started_at=self._clock,
            ends_at=self._clock + timedelta(hours=2),
        )
        self._allocate_dispatch()
        self._log(
            actor="system",
            kind="baseline",
            summary="Fleet stable, dispatch plan committed for grid event",
            detail=(
                f"{self.snapshot().committed_kw:.0f} kW committed against a "
                f"{self.grid_event.target_kw:.0f} kW target across "
                f"{len([d for d in self.devices if d.is_dispatchable])} dispatchable devices."
            ),
        )

    # ------------------------------------------------------------------ helpers

    def _tick(self, seconds: int) -> datetime:
        self._clock += timedelta(seconds=seconds)
        return self._clock

    def _log(self, actor: str, kind: str, summary: str, detail: str = "") -> AuditEvent:
        event = AuditEvent(at=self._clock, actor=actor, kind=kind, summary=summary, detail=detail)
        self.audit.append(event)
        return event

    def device(self, device_id: str) -> Device:
        for device in self.devices:
            if device.device_id == device_id:
                return device
        raise KeyError(device_id)

    def snapshot(self) -> FleetSnapshot:
        return FleetSnapshot(
            devices=self.devices,
            grid_event=self.grid_event,
            incidents=self.incidents,
            audit=self.audit,
            now=self._clock,
        )

    def _headroom_kw(self, device: Device) -> float:
        if not device.is_dispatchable:
            return 0.0
        # A degraded device is only trusted with half of its nameplate power.
        trust = 0.5 if device.status is DeviceStatus.DEGRADED else 1.0
        return min(device.power_kw * trust, device.available_kwh)

    def _allocate_dispatch(self) -> float:
        """Share the grid-event target across dispatchable devices by headroom.

        Returns the total kW committed. Devices that are not dispatchable get 0 kW.
        """
        for device in self.devices:
            device.assigned_kw = 0.0

        pool = [d for d in self.devices if self._headroom_kw(d) > 0]
        total_headroom = sum(self._headroom_kw(d) for d in pool)
        if total_headroom <= 0:
            return 0.0

        target = min(self.grid_event.target_kw, total_headroom)
        for device in pool:
            device.assigned_kw = round(target * self._headroom_kw(device) / total_headroom, 2)
        return round(sum(d.assigned_kw for d in pool), 2)

    # ------------------------------------------------------------------ failure

    def trigger_device_failure(self, device_id: str = FOCUS_DEVICE_ID) -> Incident:
        """Simulate a telemetry blackout on one device and open an incident.

        Detection and planning happen automatically; execution does not. The incident
        is parked in ``awaiting_approval`` until a human approves the plan.
        """
        device = self.device(device_id)
        if any(
            i.device_id == device_id and i.status is not IncidentStatus.RESOLVED
            for i in self.incidents
        ):
            raise ApprovalError(f"{device_id} already has an open incident")
        if not device.is_dispatchable:
            raise ApprovalError(f"{device_id} is already out of service, reset the demo first")

        lost_kw = device.assigned_kw
        self._tick(45)
        device.status = DeviceStatus.OFFLINE
        device.last_telemetry_s = TELEMETRY_STALE_SECONDS + 18
        device.assigned_kw = 0.0
        self._log(
            actor="telemetry-monitor",
            kind="detection",
            summary=f"{device_id} telemetry lost during active grid event",
            detail=(
                f"No heartbeat for {device.last_telemetry_s}s (threshold "
                f"{TELEMETRY_STALE_SECONDS}s) at {device.site}."
            ),
        )

        self._incident_seq += 1
        self._tick(20)
        incident = Incident(
            incident_id=f"INC-{self._incident_seq:03d}",
            device_id=device_id,
            severity=Severity.HIGH,
            status=IncidentStatus.DETECTED,
            opened_at=self._clock,
            title=f"{device_id} offline during peak-demand response",
            root_cause_hypothesis=(
                "Site gateway lost its uplink: the inverter reported nominal state of charge "
                "in the last good frame and neighbouring devices on the same feeder are healthy, "
                "so a device fault is unlikely."
            ),
            impact=(
                f"{lost_kw:.1f} kW of committed capacity dropped out of a "
                f"{self.grid_event.target_kw:.0f} kW commitment while the grid event is active. "
                "Without reassignment the fleet under-delivers on the window."
            ),
            recommended_action=(
                f"Quarantine {device_id} (mark unavailable, stop counting its capacity) and "
                f"reassign {lost_kw:.1f} kW across healthy devices with headroom, "
                "then dispatch Field Support to inspect the gateway."
            ),
            owner=OWNERS[Role.FLEET_OPERATOR],
        )
        incident.tasks = self._build_tasks(incident, lost_kw)
        self.incidents.append(incident)
        self._log(
            actor="orchestrator",
            kind="incident_opened",
            summary=f"{incident.incident_id} opened at severity {incident.severity.value}",
            detail=incident.impact,
        )

        self._tick(15)
        incident.status = IncidentStatus.AWAITING_APPROVAL
        self._log(
            actor="orchestrator",
            kind="recommendation",
            summary="Recovery plan proposed, waiting for human approval",
            detail=incident.recommended_action,
        )
        return incident

    def _build_tasks(self, incident: Incident, lost_kw: float) -> list[Task]:
        return [
            Task(
                task_id=f"{incident.incident_id}-T1",
                role=Role.FLEET_OPERATOR,
                owner=OWNERS[Role.FLEET_OPERATOR],
                title="Review and approve the recovery plan",
                detail=(
                    f"Approve reassignment of {lost_kw:.1f} kW away from "
                    f"{incident.device_id}. Nothing executes until this is approved."
                ),
                status=TaskStatus.OPEN,
            ),
            Task(
                task_id=f"{incident.incident_id}-T2",
                role=Role.RELIABILITY_ENGINEER,
                owner=OWNERS[Role.RELIABILITY_ENGINEER],
                title="Confirm root-cause hypothesis from telemetry history",
                detail="Compare feeder-mates and last good frames to rule out an inverter fault.",
                status=TaskStatus.IN_PROGRESS,
            ),
            Task(
                task_id=f"{incident.incident_id}-T3",
                role=Role.FIELD_SUPPORT,
                owner=OWNERS[Role.FIELD_SUPPORT],
                title=f"Schedule site visit for {incident.device_id}",
                detail="Blocked until the operator approves the recovery plan.",
                status=TaskStatus.BLOCKED,
            ),
        ]

    # ------------------------------------------------------------------ recovery

    @property
    def pending_incident(self) -> Incident | None:
        for incident in self.incidents:
            if incident.status is IncidentStatus.AWAITING_APPROVAL:
                return incident
        return None

    def approve_recovery(self, approver: str = OWNERS[Role.FLEET_OPERATOR]) -> Incident:
        """Human-in-the-loop gate: execute the plan only once a person approves it."""
        incident = self.pending_incident
        if incident is None:
            raise ApprovalError("no recovery plan is awaiting approval")

        self._tick(30)
        incident.approved_by = approver
        incident.approved_at = self._clock
        incident.status = IncidentStatus.RECOVERING
        self._log(
            actor=approver,
            kind="human_approval",
            summary=f"Recovery plan approved for {incident.incident_id}",
            detail="Operator approval recorded. Execution starts from this point only.",
        )

        device = self.device(incident.device_id)
        device.status = DeviceStatus.UNAVAILABLE
        device.assigned_kw = 0.0
        self._tick(25)
        self._log(
            actor="orchestrator",
            kind="quarantine",
            summary=f"{device.device_id} marked unavailable and removed from capacity",
            detail="Device is excluded from dispatch until Field Support clears it.",
        )

        committed = self._allocate_dispatch()
        self._tick(20)
        self._log(
            actor="orchestrator",
            kind="reassignment",
            summary=f"Dispatch reassigned: {committed:.0f} kW committed",
            detail=(
                f"Target {self.grid_event.target_kw:.0f} kW covered by "
                f"{len([d for d in self.devices if d.assigned_kw > 0])} devices."
            ),
        )

        for task in incident.tasks:
            if task.role is Role.FIELD_SUPPORT:
                task.status = TaskStatus.IN_PROGRESS
                task.detail = "Site visit scheduled for the next business morning."
            else:
                task.status = TaskStatus.DONE

        self._tick(20)
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = self._clock
        self._log(
            actor="orchestrator",
            kind="recovered",
            summary=f"{incident.incident_id} recovered, grid commitment held",
            detail=self.human_summary(),
        )
        return incident

    # ------------------------------------------------------------------ summary

    def human_summary(self) -> str:
        snap = self.snapshot()
        parts = [
            f"Fleet: {snap.online} online, {snap.degraded} degraded, "
            f"{snap.offline} offline, {snap.unavailable} quarantined "
            f"of {snap.total_devices} simulated devices.",
            f"Grid event: {snap.grid_event.name} ({snap.grid_event.zone}), "
            f"{snap.committed_kw:.0f} kW committed of {snap.grid_event.target_kw:.0f} kW "
            f"target ({snap.coverage_pct:.0f}% coverage).",
        ]
        resolved = [i for i in self.incidents if i.status is IncidentStatus.RESOLVED]
        pending = self.pending_incident
        if pending is not None:
            parts.append(
                f"{pending.incident_id} is awaiting operator approval; no dispatch change "
                "has been made."
            )
        elif resolved:
            last = resolved[-1]
            parts.append(
                f"{last.incident_id} ({last.device_id}) was approved by {last.approved_by} "
                "and the commitment was held by reassigning healthy devices."
            )
        else:
            parts.append("No open incidents.")
        return " ".join(parts)
