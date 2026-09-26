import pytest

from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.engine import ApprovalError
from gridsignal.control_room.models import DeviceStatus, IncidentStatus, Role, Severity, TaskStatus
from gridsignal.fleet import FOCUS_DEVICE_ID, build_fleet


def test_fleet_is_deterministic():
    a = build_fleet()
    b = build_fleet()
    assert [d.device_id for d in a] == [d.device_id for d in b]
    assert [d.state_of_charge for d in a] == [d.state_of_charge for d in b]
    assert any(d.device_id == FOCUS_DEVICE_ID for d in a)


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
