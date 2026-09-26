"""Put the Control Room's BAT-042 incident to Jev, in the same shape the mesh uses.

The operator view and the agent mesh share one decision layer: the same four questions,
the same confidence gate, the same fixtures. Only the state differs.
"""

from __future__ import annotations

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.control_room.models import Device, DeviceStatus, Incident
from gridsignal.fleet import GATEWAY_RING_SIZE
from gridsignal.jev import rules
from gridsignal.jev.client import JevClient, JevResponse
from gridsignal.jev.policy import ApprovalDecision, ApprovalPolicy, decide
from gridsignal.jev.questions import IncidentSnapshot, Suspect, incident_questions
from gridsignal.mesh.build import gateway_id
from gridsignal.mesh.negotiation import BACKUP_RESERVE_KWH

SUSPECTS = 3


def fixture_name(engine: ControlRoomEngine) -> str:
    return f"control_room_{engine.prices.date}_{engine.fleet_size}"


def _headroom_kw(device: Device) -> float:
    if not device.is_dispatchable:
        return 0.0
    trust = 0.5 if device.status is DeviceStatus.DEGRADED else 1.0
    return max(min(device.power_kw * trust, device.available_kwh) - device.assigned_kw, 0.0)


def snapshot_from_engine(engine: ControlRoomEngine, incident: Incident) -> IncidentSnapshot:
    """Describe the open incident as simulated state, with no member identifiers."""
    devices = engine.devices
    offline = [d for d in devices if d.status is DeviceStatus.OFFLINE]
    healthy = [d for d in devices if d.is_dispatchable]
    hours = engine.remaining_hours()
    headroom = sorted(healthy, key=lambda d: (-_headroom_kw(d), d.device_id))
    helpers = [d for d in headroom if _headroom_kw(d) > 0]
    spare_kwh = [d.available_kwh - d.assigned_kw * hours for d in helpers]
    by_zone: dict[str, list[Device]] = {}
    for device in devices:
        by_zone.setdefault(device.zone, []).append(device)

    median_kw = 0.0
    if helpers:
        median_kw = round(sorted(_headroom_kw(d) for d in helpers)[len(helpers) // 2], 3)

    return IncidentSnapshot(
        scenario=f"control_room_{engine.prices.date}",
        agents=len(devices),
        batteries=len(devices),
        offline_agents=len(offline),
        offline_zones=tuple(
            sorted(z for z, group in by_zone.items() if group and all(d in offline for d in group))
        ),
        offline_gateway_rings=len({gateway_id(d.device_id) for d in offline}),
        largest_offline_group_in_one_ring=len(offline),
        gateway_ring_size=GATEWAY_RING_SIZE,
        rejected_cards=0,
        stale_agents=len(offline),
        max_telemetry_age_s=max((d.last_telemetry_s for d in offline), default=0),
        lost_kw=incident.lost_kw,
        price_usd_mwh=incident.price_mwh,
        event_hours=incident.window_hours,
        dollars_at_risk=incident.dollars_at_risk,
        bidders=len(helpers),
        proposed_kw=round(min(sum(_headroom_kw(d) for d in helpers), incident.lost_kw), 2),
        uncovered_kw=round(max(incident.lost_kw - sum(_headroom_kw(d) for d in helpers), 0.0), 2),
        plan_agents=len(helpers),
        plan_mean_soc=(
            round(sum(d.state_of_charge for d in helpers) / len(helpers), 4) if helpers else 1.0
        ),
        plan_min_spare_kwh=round(min(spare_kwh), 3) if spare_kwh else BACKUP_RESERVE_KWH,
        backup_reserve_kwh=BACKUP_RESERVE_KWH,
        suspects=tuple(
            Suspect(
                agent_id=d.device_id,
                card_status="verified",
                signature_valid=True,
                heartbeat_age_s=d.last_telemetry_s,
                claimed_kw=round(_headroom_kw(d), 3),
                claimed_kwh=round(d.available_kwh, 3),
                soc=d.state_of_charge,
                bid_kw=round(_headroom_kw(d), 3),
                fleet_median_kw=median_kw,
            )
            for d in helpers[:SUSPECTS]
        ),
    )


def ask(
    engine: ControlRoomEngine,
    incident: Incident,
    client: JevClient | None = None,
    policy: ApprovalPolicy | None = None,
) -> tuple[JevResponse, ApprovalDecision]:
    """Ask Jev about the open incident and route it: auto-approve, or the human gate."""
    jev = client or JevClient.for_scenario(fixture_name(engine), fallback=rules.answers)
    if jev.fallback is None:
        jev.fallback = rules.answers
    snapshot = snapshot_from_engine(engine, incident)
    response = jev.ask(snapshot.as_state(), incident_questions(snapshot))
    jev.flush()
    decision = decide(
        response,
        dollars=incident.dollars_at_risk,
        covered_fully=snapshot.uncovered_kw <= 0.0,
        human_approver=incident.owner,
        policy=policy,
    )
    return response, decision
