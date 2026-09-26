"""Deterministic state machine behind the GridSignal Control Room.

Lifecycle of the demo:

    stable -> telemetry failure detected -> incident opened (awaiting human approval)
           -> operator approves -> work reassigned, device quarantined -> recovered

The engine never dispatches anything: it mutates an in-memory simulation and records
an append-only audit trail. Recovery only happens after an explicit human approval.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from gridsignal import home
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
from gridsignal.fleet import (
    DEFAULT_SEED,
    FLEET_SIZE,
    FOCUS_DEVICE_ID,
    UTILITY_PARTNER,
    build_fleet,
    gateway_ring,
)
from gridsignal.mesh.negotiation import WEAR_USD_PER_KW
from gridsignal.prices import PriceTrace, energy_value_usd, load_price_trace

# Each home commits this many *exported* kW to the event — what is left after its own
# load is served — so the target scales with the operator-controlled fleet.
TARGET_KW_PER_DEVICE = 4.5
GRID_EVENT_TARGET_KW = TARGET_KW_PER_DEVICE * FLEET_SIZE
GRID_EVENT_HOURS = 2.0
TELEMETRY_STALE_SECONDS = 120
#: Floor on the hours left in the event, so headroom never divides by zero at the
#: closing bell; with a window this short the inverter is the binding limit anyway.
MIN_DISPATCH_HOURS = 0.25
#: Simulated export ceiling per operator-controlled home on one zone's feeder, the
#: stand-in for deliverability. An assumption of this simulation, not a utility limit.
DELIVERABILITY_KW_PER_DEVICE = 6.0

OWNERS: dict[Role, str] = {
    Role.FLEET_OPERATOR: "M. Alvarez (Fleet Operator)",
    Role.RELIABILITY_ENGINEER: "T. Okafor (Reliability Engineer)",
    Role.FIELD_SUPPORT: "J. Nguyen (Field Support)",
}


class ApprovalError(RuntimeError):
    """Raised when a recovery plan is approved out of order."""


@dataclass(frozen=True)
class ReserveOutcome:
    """The trade the operator is making when they move the member reserve floor."""

    fraction: float
    committed_kw_before: float
    committed_kw_after: float
    revenue_given_up_usd: float
    backup_hours_before: float
    backup_hours_after: float

    @property
    def backup_hours_gained(self) -> float:
        return round(self.backup_hours_after - self.backup_hours_before, 1)


@dataclass(frozen=True)
class HeldCapacity:
    """kW the fleet is deliberately not offering, and the reason it is held."""

    reason: str
    kw: float


@dataclass(frozen=True)
class SurplusOffer:
    """Spare capacity beyond the event commitment: what is offered, what is held."""

    target_kw: float
    committed_kw: float
    offerable_kw: float
    held: tuple[HeldCapacity, ...]
    price_mwh: float
    hours: float
    revenue_usd: float
    wear_usd: float

    @property
    def idle_kw(self) -> float:
        """Spare kW that is not being offered right now."""
        return round(sum(h.kw for h in self.held), 2)

    @property
    def net_usd(self) -> float:
        """Simulated revenue from the surplus after the modelled cycle wear."""
        return round(self.revenue_usd - self.wear_usd, 2)

    @property
    def held_summary(self) -> str:
        return "; ".join(f"{h.kw:,.0f} kW {h.reason}" for h in self.held)


def subject_for(device_id: str, ring_size: int) -> str:
    """Name the thing being quarantined: one device, or its whole gateway ring."""
    if ring_size == 1:
        return device_id
    return f"the {ring_size} devices on {device_id}'s gateway ring"


class ControlRoomEngine:
    """In-memory, deterministic simulation of a battery-fleet control room."""

    def __init__(
        self,
        seed: int = DEFAULT_SEED,
        price_trace: PriceTrace | None = None,
        fleet_size: int = FLEET_SIZE,
    ) -> None:
        self.seed = seed
        self.fleet_size = fleet_size
        self.prices = price_trace or load_price_trace()
        self.priority_zone: str | None = None
        self.reserve_fraction = home.DEFAULT_RESERVE_FRACTION
        self.reset()

    # ------------------------------------------------------------------ setup

    def reset(self) -> None:
        """Return the simulation to its stable starting state."""
        window_start, window_end, window_price = self.prices.peak_window(GRID_EVENT_HOURS)
        self._clock = window_start
        self.devices: list[Device] = build_fleet(self.seed, self.fleet_size)
        self._by_id = {d.device_id: d for d in self.devices}
        self.incidents: list[Incident] = []
        self.audit: list[AuditEvent] = []
        self._incident_seq = 0
        self.mine: list[Device] = [d for d in self.devices if d.is_operator_controlled]
        self.grid_event = GridEvent(
            name="ERCOT peak-demand response window",
            zone=self.prices.location,
            status="active",
            target_kw=round(TARGET_KW_PER_DEVICE * len(self.mine), 1),
            price_mwh=window_price,
            started_at=window_start,
            ends_at=window_end,
            price_source=(
                f"ERCOT {self.prices.market} settlement point prices, "
                f"{self.prices.location} {self.prices.date} (cached Parquet)"
            ),
        )
        self._allocate_dispatch()
        self._log(
            actor="system",
            kind="baseline",
            summary="Fleet stable, dispatch plan committed for grid event",
            detail=(
                f"{self.snapshot().committed_kw:.0f} kW exported against a "
                f"{self.grid_event.target_kw:.0f} kW target across "
                f"{len([d for d in self.mine if d.is_dispatchable])} dispatchable devices, "
                f"after serving {self.snapshot().home_load_kw:.0f} kW of member load first, "
                f"at ${self.grid_event.price_mwh:.2f}/MWh ({self.grid_event.price_source}). "
                f"{len(self.devices) - len(self.mine)} units are controlled by "
                f"{UTILITY_PARTNER} and are not ours to dispatch."
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
        return self._by_id[device_id]

    def snapshot(self) -> FleetSnapshot:
        return FleetSnapshot(
            devices=self.devices,
            grid_event=self.grid_event,
            incidents=self.incidents,
            audit=self.audit,
            now=self._clock,
        )

    def remaining_hours(self) -> float:
        """Hours left in the grid event from the current simulated time."""
        return max((self.grid_event.ends_at - self._clock).total_seconds() / 3600.0, 0.0)

    def remaining_price_mwh(self) -> float:
        """Average real settlement price across the rest of the event window."""
        return self.prices.window_price_mwh(self._clock, self.grid_event.ends_at)

    def discharge_headroom_kw(self, device: Device) -> float:
        """How many kW this battery can discharge for the rest of the event.

        Two limits, both in kW: the inverter (derated on a degraded device) and the
        energy above the member's reserve spread over the hours left::

            headroom_kw = min(power_kw * trust,
                              max(0, available_kwh - reserve_kwh) / hours_left)

        The reserve is held back here and never appears in any later step, so no
        dispatch path can spend it. Another tenant's batteries are not ours and read
        zero.
        """
        if not device.is_dispatchable or not device.is_operator_controlled:
            return 0.0
        # A degraded device is only trusted with half of its nameplate power.
        trust = 0.5 if device.status is DeviceStatus.DEGRADED else 1.0
        hours_left = max(self.remaining_hours(), MIN_DISPATCH_HOURS)
        reserve_kwh = home.reserve_kwh(device, self.reserve_fraction)
        energy_kw = max(0.0, device.available_kwh - reserve_kwh) / hours_left
        return round(max(min(device.power_kw * trust, energy_kw), 0.0), 3)

    def _headroom_kw(self, device: Device) -> float:
        """Exportable kW: the discharge headroom left once the house is served.

        The home is on the same side of the meter, so its load comes out of the
        discharge before anything reaches the grid.
        """
        return round(max(self.discharge_headroom_kw(device) - device.home_load_kw, 0.0), 3)

    def _share(self, pool: list[Device], target: float) -> float:
        """Split ``target`` kW across ``pool`` in proportion to headroom."""
        total_headroom = sum(self._headroom_kw(d) for d in pool)
        if total_headroom <= 0 or target <= 0:
            return 0.0
        share = min(target, total_headroom)
        for device in pool:
            device.assigned_kw = round(share * self._headroom_kw(device) / total_headroom, 2)
        return round(sum(d.assigned_kw for d in pool), 2)

    def _allocate_dispatch(self) -> float:
        """Share the grid-event target across dispatchable devices by headroom.

        With a ``priority_zone`` set the operator has chosen to lean on one congested
        zone first: its devices are filled to their headroom before the rest of the
        fleet shares what is left. Returns the total kW committed; devices that are not
        dispatchable get 0 kW.
        """
        for device in self.mine:
            device.assigned_kw = 0.0

        pool = [d for d in self.mine if self._headroom_kw(d) > 0]
        total_headroom = sum(self._headroom_kw(d) for d in pool)
        if total_headroom <= 0:
            return 0.0

        target = min(self.grid_event.target_kw, total_headroom)
        first = [d for d in pool if d.zone == self.priority_zone]
        if not first:
            return self._share(pool, target)

        committed = self._share(first, target)
        rest = [d for d in pool if d.zone != self.priority_zone]
        if not rest:
            return committed
        return round(committed + self._share(rest, target - committed), 2)

    def set_priority_zone(self, zone: str | None) -> float:
        """Discharge one zone's batteries first, and reallocate the open commitment.

        Simulated dispatch preference only: the target and the homeowner reserve rules
        do not move, only which zone carries the commitment first.
        """
        if zone == self.priority_zone:
            return self.snapshot().committed_kw
        self.priority_zone = zone
        committed = self._allocate_dispatch()
        self._log(
            actor=OWNERS[Role.FLEET_OPERATOR],
            kind="dispatch_priority",
            summary=(
                f"Congestion priority set to {zone}"
                if zone
                else "Congestion priority cleared, fleet shares the target by headroom"
            ),
            detail=(
                f"{committed:.0f} kW committed of {self.grid_event.target_kw:.0f} kW; "
                f"{len([d for d in self.devices if d.zone == zone and d.assigned_kw > 0])} "
                f"devices in {zone} discharge first."
                if zone
                else f"{committed:.0f} kW committed of {self.grid_event.target_kw:.0f} kW."
            ),
        )
        return committed

    # ------------------------------------------------------------------ surplus

    def _zone_export_cap_kw(self, zone: str) -> float:
        """Simulated feeder export ceiling for one zone.

        A real fleet cannot push every kW it owns onto one distribution feeder. This
        stands in for that limit at a flat kW per operator-controlled home; it is an
        assumption of this simulation, not a measured ERCOT or utility limit.
        """
        return DELIVERABILITY_KW_PER_DEVICE * sum(1 for d in self.mine if d.zone == zone)

    def offer_floor_usd_mwh(self) -> float:
        """Price below which cycling a battery costs more wear than the kWh earns.

        ``WEAR_USD_PER_KW`` of modelled wear over ``hours_left`` of discharge, in
        $/MWh. Above it the spare kW is worth offering; below it holding is the
        cheaper answer and the Control Room says so instead of dispatching.
        """
        hours = max(self.remaining_hours(), MIN_DISPATCH_HOURS)
        return round(WEAR_USD_PER_KW * 1000.0 / hours, 2)

    def _spare_kw(self, device: Device) -> float:
        """Export headroom this battery has not already promised to the event."""
        return round(max(self._headroom_kw(device) - device.assigned_kw, 0.0), 3)

    def surplus_offer(self) -> SurplusOffer:
        """What the fleet could still offer beyond its commitment, and what it holds.

        Nothing is mutated: this is the answer to "you are sitting on tens of MW, why
        is it idle?" — every kW of the fleet's nameplate is either committed, offered,
        or held for a named reason.
        """
        hours = max(self.remaining_hours(), MIN_DISPATCH_HOURS)
        price = self.remaining_price_mwh()
        floor = self.offer_floor_usd_mwh()

        committed = round(sum(d.assigned_kw for d in self.mine), 2)
        spare_by_zone: dict[str, float] = defaultdict(float)
        for device in self.mine:
            spare_by_zone[device.zone] += self._spare_kw(device)

        deliverable = 0.0
        for zone, spare in spare_by_zone.items():
            zone_committed = sum(d.assigned_kw for d in self.mine if d.zone == zone)
            allowance = max(self._zone_export_cap_kw(zone) - zone_committed, 0.0)
            deliverable += min(spare, allowance)
        spare_total = round(sum(spare_by_zone.values()), 2)
        deliverable = round(deliverable, 2)

        held: list[HeldCapacity] = []
        reserve_kw = sum(
            home.reserve_kwh(d, self.reserve_fraction) / hours
            for d in self.mine
            if d.is_dispatchable
        )
        held.append(HeldCapacity("member backup reserve", round(reserve_kw, 2)))
        held.append(
            HeldCapacity(
                "serving the member's own home",
                round(sum(d.home_load_kw for d in self.mine if d.is_dispatchable), 2),
            )
        )
        offline_kw = sum(d.power_kw for d in self.mine if not d.is_dispatchable)
        if offline_kw:
            held.append(HeldCapacity("offline, degraded or quarantined", round(offline_kw, 2)))
        other_tenant = sum(d.power_kw for d in self.devices if not d.is_operator_controlled)
        if other_tenant:
            held.append(
                HeldCapacity(
                    f"another tenant's batteries ({UTILITY_PARTNER} controls them)",
                    round(other_tenant, 2),
                )
            )
        if spare_total - deliverable > 0.01:
            held.append(
                HeldCapacity(
                    "deliverability: simulated feeder export cap",
                    round(spare_total - deliverable, 2),
                )
            )
        offerable = deliverable if price >= floor else 0.0
        if offerable == 0.0 and deliverable > 0:
            held.append(
                HeldCapacity(
                    f"price ${price:,.2f}/MWh is under the ${floor:,.2f}/MWh wear floor",
                    deliverable,
                )
            )

        return SurplusOffer(
            target_kw=self.grid_event.target_kw,
            committed_kw=committed,
            offerable_kw=round(offerable, 2),
            held=tuple(held),
            price_mwh=price,
            hours=round(hours, 3),
            revenue_usd=energy_value_usd(offerable, hours, price),
            wear_usd=round(offerable * WEAR_USD_PER_KW, 2),
        )

    def offer_surplus(self, approver: str = OWNERS[Role.FLEET_OPERATOR]) -> SurplusOffer:
        """Commit the offerable surplus on top of the event target, and log it.

        Idempotent in the sense that matters: the second call finds no spare left
        within the feeder caps and offers 0 kW. The member reserve is untouched,
        because the surplus is measured from :meth:`discharge_headroom_kw`, which
        already holds it back.
        """
        offer = self.surplus_offer()
        if offer.offerable_kw <= 0:
            self._log(
                actor=approver,
                kind="surplus_held",
                summary=f"{offer.idle_kw:,.0f} kW of spare capacity held, not offered",
                detail=offer.held_summary,
            )
            return offer

        for zone in sorted({d.zone for d in self.mine}):
            in_zone = [d for d in self.mine if d.zone == zone and self._spare_kw(d) > 0]
            spare = sum(self._spare_kw(d) for d in in_zone)
            if spare <= 0:
                continue
            zone_committed = sum(d.assigned_kw for d in self.mine if d.zone == zone)
            allowance = max(self._zone_export_cap_kw(zone) - zone_committed, 0.0)
            taken = min(spare, allowance)
            for device in in_zone:
                # Round the added kW *down*: a rounded-up commitment would be a kW the
                # battery does not have, and would show as a reserve breach on paper.
                extra = math.floor(taken * self._spare_kw(device) / spare * 100) / 100
                device.assigned_kw = round(device.assigned_kw + extra, 2)

        self._log(
            actor=approver,
            kind="surplus_offered",
            summary=(
                f"{offer.offerable_kw:,.0f} kW of spare capacity offered at "
                f"${offer.price_mwh:,.2f}/MWh"
            ),
            detail=(
                f"${offer.revenue_usd:,.2f} of simulated revenue over "
                f"{offer.hours:.2f} h, less ${offer.wear_usd:,.2f} of modelled wear. "
                f"Held back: {offer.held_summary}"
            ),
        )
        return offer

    # ------------------------------------------------------------------ reserve

    def reserve_outcome(self, fraction: float) -> ReserveOutcome:
        """What raising the member reserve floor to ``fraction`` would cost and buy.

        Compares the current allocation with the one the higher floor produces:
        revenue given up over the rest of the event against the backup hours it
        holds back for members. Nothing is applied.
        """
        before_fraction = self.reserve_fraction
        before_kw = self.snapshot().committed_kw
        before_hours = self._mean_backup_hours(before_fraction)
        self.reserve_fraction = fraction
        try:
            after_kw = self._allocate_dispatch()
            after_hours = self._mean_backup_hours(fraction)
        finally:
            self.reserve_fraction = before_fraction
            self._allocate_dispatch()
        hours = self.remaining_hours()
        price = self.remaining_price_mwh()
        given_up = round(max(before_kw - after_kw, 0.0), 2)
        return ReserveOutcome(
            fraction=fraction,
            committed_kw_before=before_kw,
            committed_kw_after=round(after_kw, 2),
            revenue_given_up_usd=energy_value_usd(given_up, hours, price),
            backup_hours_before=before_hours,
            backup_hours_after=after_hours,
        )

    def _mean_backup_hours(self, fraction: float) -> float:
        pool = [d for d in self.mine if d.is_dispatchable]
        if not pool:
            return 0.0
        hours_left = self.remaining_hours()
        estimates = [home.backup_estimate(d, hours_left, fraction).hours for d in pool]
        return round(sum(estimates) / len(estimates), 1)

    def set_reserve_floor(self, fraction: float) -> ReserveOutcome:
        """Raise or lower the member reserve floor and reallocate what is left.

        The operator does this before a forecast storm or other high-risk day: it
        sells less and holds more backup for members. Simulated policy only.
        """
        outcome = self.reserve_outcome(fraction)
        self.reserve_fraction = fraction
        committed = self._allocate_dispatch()
        self._log(
            actor=OWNERS[Role.FLEET_OPERATOR],
            kind="reserve_policy",
            summary=f"Member backup reserve floor set to {fraction:.0%} of capacity",
            detail=(
                f"{committed:.0f} kW exported of a {self.grid_event.target_kw:.0f} kW target. "
                f"${outcome.revenue_given_up_usd:,.2f} of event revenue given up to hold "
                f"{outcome.backup_hours_gained:.1f} more hours of backup per member."
            ),
        )
        return outcome

    # ------------------------------------------------------------------ failure

    def trigger_device_failure(self, device_id: str = FOCUS_DEVICE_ID) -> Incident:
        """Simulate a telemetry blackout on one gateway ring and open an incident.

        At the 48-device demo scale the ring is just ``device_id``; at fleet scale the
        same gateway regression takes out every device on that firmware ring, so the
        dollars at stake scale with the fleet.

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

        # Another tenant's batteries can sit on the same ring, but their kW was never
        # ours to lose or to reassign, so the incident only covers our own.
        ring = [
            self.device(i)
            for i in gateway_ring(device_id, self.fleet_size)
            if self.device(i).is_operator_controlled
        ]
        lost_kw = round(sum(d.assigned_kw for d in ring), 2)
        self._tick(45)
        window_hours = self.remaining_hours()
        price_mwh = self.remaining_price_mwh()
        dollars_at_risk = energy_value_usd(lost_kw, window_hours, price_mwh)
        for member in ring:
            member.status = DeviceStatus.OFFLINE
            member.last_telemetry_s = TELEMETRY_STALE_SECONDS + 18
            member.assigned_kw = 0.0
        others = (
            ""
            if len(ring) == 1
            else f" {len(ring) - 1} more devices on the same gateway firmware ring went dark too."
        )
        self._log(
            actor="telemetry-monitor",
            kind="detection",
            summary=f"{device_id} telemetry lost during active grid event",
            detail=(
                f"No heartbeat for {device.last_telemetry_s}s (threshold "
                f"{TELEMETRY_STALE_SECONDS}s) at {device.site}.{others}"
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
                + (
                    ""
                    if len(ring) == 1
                    else f" The blackout follows the gateway firmware ring ({len(ring)} devices "
                    "across five load zones), which points at the uplink stack, not the sites."
                )
            ),
            cohort=[d.device_id for d in ring],
            impact=(
                f"{lost_kw:.1f} kW of committed capacity dropped out of a "
                f"{self.grid_event.target_kw:.0f} kW commitment while the grid event is active. "
                f"At ${price_mwh:.2f}/MWh across the remaining {window_hours:.2f} h of the "
                f"window that is ${dollars_at_risk:,.2f} at risk if the fleet under-delivers."
            ),
            lost_kw=lost_kw,
            window_hours=round(window_hours, 3),
            price_mwh=price_mwh,
            dollars_at_risk=dollars_at_risk,
            recommended_action=(
                f"Quarantine {subject_for(device_id, len(ring))} "
                "(mark unavailable, stop counting its capacity) and "
                f"reassign {lost_kw:.1f} kW across healthy devices with headroom to recover "
                f"the ${dollars_at_risk:,.2f} at risk, then dispatch Field Support to "
                "inspect the gateway."
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

        ring = [self.device(i) for i in (incident.cohort or [incident.device_id])]
        for member in ring:
            member.status = DeviceStatus.UNAVAILABLE
            member.assigned_kw = 0.0
        self._tick(25)
        self._log(
            actor="orchestrator",
            kind="quarantine",
            summary=(
                f"{subject_for(incident.device_id, len(ring))} "
                "marked unavailable and removed from capacity"
            ),
            detail="Excluded from dispatch until Field Support clears the gateway ring.",
        )

        committed_before = self.snapshot().committed_kw
        incident.assigned_kw_before_recovery = {d.device_id: d.assigned_kw for d in self.devices}
        committed = self._allocate_dispatch()
        incident.restored_kw = round(self.snapshot().committed_kw - committed_before, 2)
        hours = self.remaining_hours()
        price_mwh = self.remaining_price_mwh()
        incident.dollars_recovered = energy_value_usd(incident.restored_kw, hours, price_mwh)
        self._tick(20)
        self._log(
            actor="orchestrator",
            kind="reassignment",
            summary=f"Dispatch reassigned: {committed:.0f} kW committed",
            detail=(
                f"Target {self.grid_event.target_kw:.0f} kW covered by "
                f"{len([d for d in self.devices if d.assigned_kw > 0])} devices. "
                f"{incident.restored_kw:.1f} kW restored over the remaining {hours:.2f} h "
                f"at ${price_mwh:.2f}/MWh recovers ${incident.dollars_recovered:,.2f}."
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
            f"Grid event: {snap.grid_event.name} ({snap.grid_event.zone} at "
            f"${snap.grid_event.price_mwh:.2f}/MWh), "
            f"{snap.committed_kw:.0f} kW committed of {snap.grid_event.target_kw:.0f} kW "
            f"target ({snap.coverage_pct:.0f}% coverage).",
        ]
        resolved = [i for i in self.incidents if i.status is IncidentStatus.RESOLVED]
        pending = self.pending_incident
        if pending is not None:
            parts.append(
                f"{pending.incident_id} is awaiting operator approval with "
                f"${pending.dollars_at_risk:,.2f} at risk; no dispatch change has been made."
            )
        elif resolved:
            last = resolved[-1]
            parts.append(
                f"{last.incident_id} ({last.device_id}) was approved by {last.approved_by} "
                f"and reassigning healthy devices recovered "
                f"${last.dollars_recovered:,.2f} of the ${last.dollars_at_risk:,.2f} at risk."
            )
        else:
            parts.append("No open incidents.")
        return " ".join(parts)
