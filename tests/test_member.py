"""The homeowner-facing summary: backup hours, dollars, and the notice wording."""

from __future__ import annotations

import pytest

from gridsignal import member
from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.models import DeviceStatus
from gridsignal.fleet import FOCUS_DEVICE_ID


@pytest.fixture
def eng() -> ControlRoomEngine:
    return ControlRoomEngine()


def test_backup_hours_are_stored_energy_not_committed_to_the_grid(eng: ControlRoomEngine) -> None:
    view = member.member_summary(eng)
    device = eng.device(FOCUS_DEVICE_ID)

    assert view.stored_kwh == pytest.approx(device.capacity_kwh * device.state_of_charge, abs=0.01)
    assert view.backup_kwh == pytest.approx(view.stored_kwh - view.committed_kwh, abs=0.01)
    assert view.backup_hours == pytest.approx(view.backup_kwh / member.ESSENTIAL_LOAD_KW, abs=0.05)
    assert view.committed_kwh > 0  # the home is taking part in the event


def test_backup_never_goes_negative_when_the_grid_asks_for_everything() -> None:
    eng = ControlRoomEngine()
    device = eng.device(FOCUS_DEVICE_ID)
    device.assigned_kw = device.capacity_kwh * 100

    view = member.member_summary(eng)

    assert view.backup_kwh == 0.0
    assert view.backup_hours == 0.0
    assert view.committed_kwh <= view.stored_kwh


def test_earnings_are_the_member_share_of_the_grid_value(eng: ControlRoomEngine) -> None:
    view = member.member_summary(eng)
    device = eng.device(FOCUS_DEVICE_ID)
    event = eng.grid_event
    expected = device.assigned_kw * event.duration_hours * event.price_mwh / 1000.0

    assert view.grid_value_usd == pytest.approx(expected, abs=0.01)
    assert view.earned_usd == pytest.approx(expected * member.MEMBER_REVENUE_SHARE, abs=0.01)
    assert view.total_usd == pytest.approx(view.earned_usd + view.protected_usd, abs=0.01)


def test_share_parameter_scales_earnings_only(eng: ControlRoomEngine) -> None:
    full = member.member_summary(eng, share=1.0)
    half = member.member_summary(eng, share=0.5)

    assert half.earned_usd == pytest.approx(full.earned_usd / 2, abs=0.01)
    assert half.grid_value_usd == pytest.approx(full.grid_value_usd, abs=0.01)


def test_offline_home_keeps_full_backup_and_stops_earning(eng: ControlRoomEngine) -> None:
    before = member.member_summary(eng)
    eng.trigger_device_failure()
    after = member.member_summary(eng)

    assert after.is_affected
    assert after.earned_usd == 0.0
    assert after.committed_kwh == 0.0
    # Losing the gateway does not touch the battery, so backup protection goes up.
    assert after.backup_kwh == pytest.approx(after.stored_kwh, abs=0.01)
    assert after.backup_hours > before.backup_hours


def test_notice_walks_the_member_through_detection_and_resolution(eng: ControlRoomEngine) -> None:
    calm = member.member_summary(eng)
    assert "healthy" in calm.headline.lower()
    assert not calm.is_affected

    eng.trigger_device_failure()
    during = member.member_summary(eng)
    assert "lost contact" in during.headline.lower()
    assert "no action needed" in during.next_step.lower()
    assert "still protecting your home" in during.body

    eng.approve_recovery()
    after = member.member_summary(eng)
    assert after.headline.startswith("Resolved")
    assert "technician" in after.next_step.lower()
    assert after.is_affected  # the device stays quarantined until the visit


def test_notice_stays_plain_english(eng: ControlRoomEngine) -> None:
    eng.trigger_device_failure()
    view = member.member_summary(eng)
    text = f"{view.headline} {view.body} {view.next_step}"

    for jargon in ("INC-", "kW", "quarantine", "dispatch", "telemetry", "gateway ring"):
        assert jargon not in text


def test_neighbours_absorb_the_outage_and_are_credited(eng: ControlRoomEngine) -> None:
    eng.trigger_device_failure()
    incident = eng.approve_recovery()
    peers = member.neighbours(eng)
    assert peers and FOCUS_DEVICE_ID not in peers

    protected = [member.member_summary(eng, d.device_id).protected_usd for d in eng.devices]

    # Every dollar the fleet recovered is attributed to the homes that took the load on.
    assert sum(protected) == pytest.approx(incident.dollars_recovered, abs=0.05)
    assert all(p >= 0 for p in protected)
    assert member.member_summary(eng, FOCUS_DEVICE_ID).protected_usd == 0.0


def test_no_dollars_are_credited_before_a_human_approves(eng: ControlRoomEngine) -> None:
    eng.trigger_device_failure()

    assert all(member.member_summary(eng, d.device_id).protected_usd == 0.0 for d in eng.devices)


def test_reset_returns_every_home_to_the_calm_state(eng: ControlRoomEngine) -> None:
    eng.trigger_device_failure()
    eng.approve_recovery()
    eng.reset()

    view = member.member_summary(eng)

    assert not view.is_affected
    assert eng.device(FOCUS_DEVICE_ID).status is DeviceStatus.ONLINE
    assert view.protected_usd == 0.0
    assert view.earned_usd > 0
