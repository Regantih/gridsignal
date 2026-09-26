import pytest

from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.engine import ApprovalError
from gridsignal.control_room.models import DeviceStatus, IncidentStatus, Role, Severity, TaskStatus
from gridsignal.fleet import FOCUS_DEVICE_ID, build_fleet
from gridsignal.prices import energy_value_usd


def test_fleet_is_deterministic():
    a = build_fleet()
    b = build_fleet()
    assert [d.device_id for d in a] == [d.device_id for d in b]
    assert [d.state_of_charge for d in a] == [d.state_of_charge for d in b]
    assert any(d.device_id == FOCUS_DEVICE_ID for d in a)


def test_grid_event_is_priced_from_the_real_ercot_trace():
    eng = ControlRoomEngine()
    event = eng.grid_event
    trace = eng.prices
    assert event.zone == trace.location
    assert trace.date in event.price_source
    # The event sits on the most expensive window of the real trading day.
    assert (event.started_at, event.ends_at, event.price_mwh) == trace.peak_window(
        event.duration_hours
    )
    assert event.price_mwh > 0


def test_baseline_state_is_stable_and_covered():
    eng = ControlRoomEngine()
    snap = eng.snapshot()
    assert snap.open_incidents == 0
    assert snap.offline == 0 and snap.unavailable == 0
    assert snap.coverage_pct == pytest.approx(100.0, abs=0.5)
    assert eng.device(FOCUS_DEVICE_ID).assigned_kw > 0


def test_failure_opens_incident_and_waits_for_human():
    eng = ControlRoomEngine()
    before = eng.snapshot().committed_kw

    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)

    assert incident.severity is Severity.HIGH
    assert incident.status is IncidentStatus.AWAITING_APPROVAL
    assert incident.approval_required and incident.approved_by is None
    assert eng.device(FOCUS_DEVICE_ID).status is DeviceStatus.OFFLINE

    snap = eng.snapshot()
    # Nothing is reassigned before approval: the fleet is visibly short.
    assert snap.committed_kw < before
    assert snap.coverage_pct < 100.0
    assert snap.open_incidents == 1
    assert {t.role for t in incident.tasks} == {
        Role.FLEET_OPERATOR,
        Role.RELIABILITY_ENGINEER,
        Role.FIELD_SUPPORT,
    }
    # Dollars at risk are priced off the real trace: kW x remaining hours x $/MWh.
    assert incident.lost_kw == pytest.approx(before - snap.committed_kw, abs=0.2)
    assert incident.price_mwh == eng.prices.window_price_mwh(
        incident.opened_at, eng.grid_event.ends_at
    )
    assert incident.dollars_at_risk == energy_value_usd(
        incident.lost_kw, incident.window_hours, incident.price_mwh
    )
    assert incident.dollars_at_risk > 0
    assert incident.dollars_recovered == 0.0
    assert f"${incident.dollars_at_risk:,.2f}" in incident.impact

    field_task = next(t for t in incident.tasks if t.role is Role.FIELD_SUPPORT)
    assert field_task.status is TaskStatus.BLOCKED
    assert [e.kind for e in eng.audit][-3:] == ["detection", "incident_opened", "recommendation"]


def test_approval_recovers_capacity_and_quarantines_device():
    eng = ControlRoomEngine()
    eng.trigger_device_failure(FOCUS_DEVICE_ID)

    incident = eng.approve_recovery("M. Alvarez (Fleet Operator)")

    assert incident.status is IncidentStatus.RESOLVED
    assert incident.approved_by == "M. Alvarez (Fleet Operator)"
    assert incident.approved_at is not None and incident.resolved_at is not None

    device = eng.device(FOCUS_DEVICE_ID)
    assert device.status is DeviceStatus.UNAVAILABLE
    assert device.assigned_kw == 0.0
    assert device.available_kwh == 0.0

    snap = eng.snapshot()
    assert snap.coverage_pct == pytest.approx(100.0, abs=0.5)
    assert snap.open_incidents == 0
    assert {t.status for t in incident.tasks} == {TaskStatus.DONE, TaskStatus.IN_PROGRESS}

    kinds = [e.kind for e in eng.audit]
    assert kinds.index("human_approval") < kinds.index("reassignment") < kinds.index("recovered")
    assert "quarantine" in kinds


def test_approval_recovers_the_dollars_that_were_at_risk():
    eng = ControlRoomEngine()
    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    at_risk = incident.dollars_at_risk

    eng.approve_recovery()

    assert incident.restored_kw == pytest.approx(incident.lost_kw, abs=0.3)
    assert incident.dollars_recovered > 0
    # Recovery lands later in the window, so it recovers slightly less than the exposure.
    assert incident.dollars_recovered == pytest.approx(at_risk, rel=0.15)
    assert incident.dollars_recovered <= at_risk
    assert incident.dollars_at_risk == at_risk  # the original exposure is not rewritten
    assert f"${incident.dollars_recovered:,.2f}" in eng.human_summary()


def test_approval_requires_a_pending_plan():
    eng = ControlRoomEngine()
    with pytest.raises(ApprovalError):
        eng.approve_recovery()

    eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()
    with pytest.raises(ApprovalError):
        eng.approve_recovery()
    with pytest.raises(ApprovalError):
        eng.trigger_device_failure(FOCUS_DEVICE_ID)


def test_audit_trail_is_append_only_across_the_flow():
    eng = ControlRoomEngine()
    baseline = len(eng.audit)
    eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()
    assert len(eng.audit) > baseline
    timestamps = [e.at for e in eng.audit]
    assert timestamps == sorted(timestamps)
    assert eng.audit[0].kind == "baseline"


def test_reset_replays_the_demo_without_leftovers():
    eng = ControlRoomEngine()
    eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()

    eng.reset()
    snap = eng.snapshot()
    assert snap.incidents == [] and len(eng.audit) == 1
    assert eng.device(FOCUS_DEVICE_ID).status is DeviceStatus.ONLINE
    assert snap.coverage_pct == pytest.approx(100.0, abs=0.5)

    # The whole story runs again identically.
    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    assert incident.incident_id == "INC-001"
    eng.approve_recovery()
    assert eng.snapshot().coverage_pct == pytest.approx(100.0, abs=0.5)


def test_human_summary_mentions_approval_state():
    eng = ControlRoomEngine()
    eng.trigger_device_failure(FOCUS_DEVICE_ID)
    assert "awaiting operator approval" in eng.human_summary()
    eng.approve_recovery()
    assert "approved by" in eng.human_summary()


def test_priority_zone_discharges_that_zone_first_without_changing_the_target():
    eng = ControlRoomEngine()
    before = eng.snapshot().committed_kw
    zone = "LZ_HOUSTON"
    committed = eng.set_priority_zone(zone)

    in_zone = [d for d in eng.devices if d.zone == zone and d.is_dispatchable]
    # Every dispatchable battery in the chosen zone is pushed to its full headroom.
    for device in in_zone:
        assert device.assigned_kw == pytest.approx(eng._headroom_kw(device), abs=0.02)
    assert committed == pytest.approx(before, abs=0.5)
    assert eng.snapshot().coverage_pct == pytest.approx(100.0, abs=0.5)
    assert eng.audit[-1].kind == "dispatch_priority"


def test_priority_zone_is_reversible_and_idempotent():
    eng = ControlRoomEngine()
    baseline = {d.device_id: d.assigned_kw for d in eng.devices}
    eng.set_priority_zone("LZ_WEST")
    logged = len(eng.audit)
    eng.set_priority_zone("LZ_WEST")
    assert len(eng.audit) == logged

    eng.set_priority_zone(None)
    assert {d.device_id: d.assigned_kw for d in eng.devices} == baseline


def test_priority_zone_survives_the_failure_and_recovery_flow():
    eng = ControlRoomEngine()
    eng.set_priority_zone("LZ_NORTH")
    eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()

    quarantined = eng.device(FOCUS_DEVICE_ID)
    assert quarantined.assigned_kw == 0.0
    assert eng.snapshot().coverage_pct == pytest.approx(100.0, abs=0.5)
    # Reserve is untouched: nothing we control is asked for more than its headroom.
    # The utility tenant's units carry their partner's own schedule, not our award.
    for device in eng.mine:
        assert device.assigned_kw <= eng._headroom_kw(device) + 0.02


def test_unknown_priority_zone_falls_back_to_sharing_by_headroom():
    eng = ControlRoomEngine()
    baseline = {d.device_id: d.assigned_kw for d in eng.devices}
    eng.set_priority_zone("LZ_NOWHERE")
    assert {d.device_id: d.assigned_kw for d in eng.devices} == baseline
