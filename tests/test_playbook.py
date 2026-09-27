"""The recovery playbook: a person approves the rule once, inside limits, in advance."""

from datetime import timedelta

import pytest

from gridsignal import home
from gridsignal.control_room.engine import ApprovalError, ControlRoomEngine
from gridsignal.control_room.models import DeviceStatus, IncidentStatus
from gridsignal.fleet import FOCUS_DEVICE_ID


def kinds(eng: ControlRoomEngine) -> list[str]:
    return [a.kind for a in eng.audit]


def test_without_a_playbook_every_incident_still_waits_for_a_person() -> None:
    eng = ControlRoomEngine()
    incident = eng.trigger_device_failure()
    assert incident.status is IncidentStatus.AWAITING_APPROVAL
    assert incident.executed_under is None
    eng.approve_recovery()
    assert incident.status is IncidentStatus.RESOLVED
    assert "human_approval" in kinds(eng)


def test_inside_its_limits_the_playbook_recovers_at_once_and_says_who_approved_it() -> None:
    eng = ControlRoomEngine()
    pb = eng.approve_playbook()
    target = eng.grid_event.target_kw
    incident = eng.trigger_device_failure()
    assert incident.status is IncidentStatus.RESOLVED
    assert incident.approval_required is False
    assert pb.playbook_id in incident.executed_under and pb.approved_by in incident.executed_under
    assert incident.restored_kw == pytest.approx(incident.lost_kw)
    assert eng.snapshot().committed_kw == pytest.approx(target)
    assert eng.device(FOCUS_DEVICE_ID).status is DeviceStatus.UNAVAILABLE
    assert pb.executions == 1
    seq = kinds(eng)
    assert seq.index("playbook_approved") < seq.index("detection") < seq.index("playbook_execution")
    assert "human_approval" not in seq
    with pytest.raises(ApprovalError):
        eng.approve_recovery()  # nothing left for a person to approve


def test_a_correlated_outage_wider_than_the_playbook_goes_to_a_person() -> None:
    eng = ControlRoomEngine(fleet_size=1_000)
    pb = eng.approve_playbook()
    incident = eng.trigger_device_failure()
    assert len(incident.cohort) > pb.max_devices
    assert incident.status is IncidentStatus.AWAITING_APPROVAL
    assert "correlated outage" in incident.escalation_reason
    assert "playbook_escalation" in kinds(eng)
    assert pb.executions == 0
    eng.approve_recovery()
    assert incident.status is IncidentStatus.RESOLVED
    assert incident.executed_under is None


def test_a_loss_bigger_than_the_kw_limit_goes_to_a_person() -> None:
    eng = ControlRoomEngine()
    eng.approve_playbook(max_kw=1.0)
    incident = eng.trigger_device_failure()
    assert incident.status is IncidentStatus.AWAITING_APPROVAL
    assert "kW limit" in incident.escalation_reason


def test_a_revoked_or_expired_playbook_executes_nothing() -> None:
    eng = ControlRoomEngine()
    eng.approve_playbook()
    eng.revoke_playbook()
    assert eng.trigger_device_failure().status is IncidentStatus.AWAITING_APPROVAL

    eng = ControlRoomEngine()
    eng.approve_playbook(expires_at=eng.event_clock - timedelta(minutes=1))
    incident = eng.trigger_device_failure()
    assert incident.status is IncidentStatus.AWAITING_APPROVAL
    assert "expired" in incident.escalation_reason


def test_a_new_playbook_replaces_the_old_one_on_the_record() -> None:
    eng = ControlRoomEngine()
    first = eng.approve_playbook()
    second = eng.approve_playbook(max_kw=5.0)
    assert first.revoked_at is not None and eng.playbook is second
    assert kinds(eng).count("playbook_revoked") == 1


def test_playbook_limits_must_be_real() -> None:
    eng = ControlRoomEngine()
    with pytest.raises(ValueError):
        eng.approve_playbook(max_kw=0)
    with pytest.raises(ValueError):
        eng.approve_playbook(max_devices=0)


def test_no_recovery_path_spends_a_member_reserve() -> None:
    eng = ControlRoomEngine()
    eng.set_commitment(1.0)
    eng.approve_playbook(max_kw=10_000, max_devices=100)
    eng.trigger_device_failure()
    eng.inject_stale_telemetry(0.1)
    hours = eng.remaining_hours()
    for d in eng.mine:
        if d.assigned_kw > 0:
            reserve = home.reserve_kwh(d, eng.reserve_fraction)
            left = d.available_kwh - (d.assigned_kw + d.home_load_kw) * hours
            assert left >= reserve - 0.05, d.device_id


def test_a_stale_wave_with_no_open_incident_is_re_shared_under_the_playbook() -> None:
    plain, covered = ControlRoomEngine(), ControlRoomEngine()
    covered.approve_playbook(max_kw=10_000, max_devices=100)
    plain.inject_stale_telemetry(0.05)
    covered.inject_stale_telemetry(0.05)
    assert covered.snapshot().committed_kw > plain.snapshot().committed_kw
    assert "playbook_execution" in kinds(covered)


def test_a_stale_wave_outside_the_playbook_is_logged_and_left_for_a_person() -> None:
    eng = ControlRoomEngine()
    eng.approve_playbook()  # one device at 48 homes; the wave is wider
    eng.inject_stale_telemetry(0.1)
    assert "playbook_escalation" in kinds(eng)
    assert "playbook_execution" not in kinds(eng)


def test_the_summary_says_the_playbook_recovered_it_not_a_person() -> None:
    eng = ControlRoomEngine()
    eng.approve_playbook()
    eng.trigger_device_failure()
    assert "recovered under playbook PB-001" in eng.human_summary()


# ------------------------------------------------------------------ commitment


def test_the_flat_target_is_about_77_percent_of_headroom() -> None:
    eng = ControlRoomEngine()
    assert 0.74 < eng.current_commit_ratio() < 0.80


def test_setting_the_commitment_moves_the_target_and_logs_why() -> None:
    eng = ControlRoomEngine()
    committed = eng.set_commitment(0.75, basis="Planner said so.")
    assert eng.current_commit_ratio() == pytest.approx(0.75, abs=0.002)
    assert committed == pytest.approx(eng.grid_event.target_kw, abs=0.05)
    last = eng.audit[-1]
    assert last.kind == "commitment_set" and "Planner said so." in last.detail


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.2])
def test_the_commitment_is_a_share_of_headroom(bad: float) -> None:
    with pytest.raises(ValueError):
        ControlRoomEngine().set_commitment(bad)


def test_reset_returns_to_the_starting_state() -> None:
    eng = ControlRoomEngine()
    start = eng.grid_event.target_kw
    eng.set_commitment(0.6)
    eng.approve_playbook()
    eng.reset()
    assert eng.grid_event.target_kw == start
    assert eng.playbook is None and eng.commit_ratio is None
