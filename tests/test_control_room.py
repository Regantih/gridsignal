from dataclasses import replace
from datetime import timedelta

import pytest

from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.engine import MIN_DISPATCH_HOURS, ApprovalError
from gridsignal.control_room.models import DeviceStatus, IncidentStatus, Role, Severity, TaskStatus
from gridsignal.fleet import FOCUS_DEVICE_ID, build_fleet
from gridsignal.home import reserve_kwh
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


# ------------------------------------------------------- headroom units and reserve


def test_headroom_takes_the_binding_limit_in_kw():
    """Both terms are kW: the inverter, and the energy above reserve over hours left."""
    eng = ControlRoomEngine()
    device = eng.device(FOCUS_DEVICE_ID)
    hours = max(eng.remaining_hours(), MIN_DISPATCH_HOURS)
    reserve = reserve_kwh(device, eng.reserve_fraction)

    # Full: headroom is whichever of the two kW limits is smaller.
    device.state_of_charge = 1.0
    full = min(device.power_kw, (device.capacity_kwh - reserve) / hours)
    assert eng.discharge_headroom_kw(device) == pytest.approx(full, abs=0.01)

    # Nearly empty: the energy above reserve binds, and it is strictly smaller.
    device.state_of_charge = (reserve + 1.0) / device.capacity_kwh
    energy_limit = max(0.0, device.available_kwh - reserve) / hours
    assert energy_limit < device.power_kw
    assert eng.discharge_headroom_kw(device) == pytest.approx(energy_limit, abs=0.01)

    # At the reserve floor there is nothing to offer at all.
    device.state_of_charge = reserve / device.capacity_kwh
    assert eng.discharge_headroom_kw(device) == 0.0


def test_a_shorter_window_raises_headroom_up_to_the_inverter_only():
    """Same stored energy over fewer hours is more kW — but never past the inverter."""
    eng = ControlRoomEngine()
    device = eng.device(FOCUS_DEVICE_ID)
    device.state_of_charge = 0.55
    long_window = eng.discharge_headroom_kw(device)

    eng._clock = eng.grid_event.ends_at - timedelta(minutes=30)
    short_window = eng.discharge_headroom_kw(device)

    assert short_window > long_window
    assert short_window <= device.power_kw + 1e-9


def test_a_full_event_at_the_dispatched_plan_never_eats_the_reserve():
    """Run the committed plan for the whole window and check the floor holds."""
    eng = ControlRoomEngine()
    eng.trigger_device_failure()
    eng.approve_recovery()
    hours = max(eng.remaining_hours(), MIN_DISPATCH_HOURS)

    violations = []
    for device in eng.mine:
        if device.assigned_kw <= 0:
            continue
        # Discharged energy is what the grid gets plus what the house takes.
        discharged_kwh = (device.assigned_kw + device.home_load_kw) * hours
        left = device.available_kwh - reserve_kwh(device, eng.reserve_fraction) - discharged_kwh
        if left < -1e-6:
            violations.append((device.device_id, round(left, 3)))
    assert not violations, violations
    assert sum(d.assigned_kw for d in eng.mine) > 0  # the check had something to bite on


# ------------------------------------------------------- idle capacity in a high-price window


def test_the_fleet_offers_its_spare_kw_when_the_price_clears_the_wear_floor():
    """Spare headroom is offered on top of the target, and the dollars are reported."""
    eng = ControlRoomEngine()
    before = eng.surplus_offer()

    assert before.price_mwh > eng.offer_floor_usd_mwh()
    assert before.offerable_kw > 0
    assert before.revenue_usd == pytest.approx(
        energy_value_usd(before.offerable_kw, before.hours, before.price_mwh), abs=0.01
    )
    assert before.net_usd < before.revenue_usd  # wear is priced in

    committed_before = sum(d.assigned_kw for d in eng.mine)
    eng.offer_surplus()
    committed_after = sum(d.assigned_kw for d in eng.mine)

    assert committed_after > committed_before
    assert committed_after - committed_before == pytest.approx(before.offerable_kw, abs=1.0)
    assert eng.surplus_offer().offerable_kw < before.offerable_kw
    assert any(e.kind == "surplus_offered" for e in eng.audit)


def test_every_idle_kw_has_a_named_reason():
    """Nothing is simply idle: each held block says why, and none of it is unexplained."""
    eng = ControlRoomEngine()
    offer = eng.surplus_offer()

    reasons = {h.reason for h in offer.held}
    assert "member backup reserve" in reasons
    assert "serving the member's own home" in reasons
    assert all(h.kw > 0 for h in offer.held)
    assert offer.idle_kw == pytest.approx(sum(h.kw for h in offer.held), abs=0.01)


def test_offering_the_surplus_still_leaves_every_member_their_reserve():
    """Take the offer, run the whole window at it, and check the floor holds."""
    eng = ControlRoomEngine()
    eng.offer_surplus()
    hours = max(eng.remaining_hours(), MIN_DISPATCH_HOURS)

    violations = []
    for device in eng.mine:
        drawn = (device.assigned_kw + device.home_load_kw) * hours
        left = device.available_kwh - reserve_kwh(device, eng.reserve_fraction) - drawn
        if left < -1e-6:
            violations.append((device.device_id, round(left, 3)))
    assert not violations, violations


def test_a_cheap_window_holds_the_surplus_and_says_the_price_is_the_reason():
    """Below the wear floor the answer is "held", with the price named, not a dispatch."""
    eng = ControlRoomEngine()
    eng.prices = replace(eng.prices, frame=eng.prices.frame.assign(spp=0.0))

    offer = eng.surplus_offer()
    assert offer.offerable_kw == 0.0
    assert offer.revenue_usd == 0.0
    assert any("wear floor" in h.reason for h in offer.held)

    committed_before = sum(d.assigned_kw for d in eng.mine)
    eng.offer_surplus()
    assert sum(d.assigned_kw for d in eng.mine) == pytest.approx(committed_before)
    assert any(e.kind == "surplus_held" for e in eng.audit)


def test_no_zone_is_offered_past_its_simulated_feeder_cap():
    """Deliverability binds the offer and is reported as a reason when it does."""
    eng = ControlRoomEngine()
    eng.offer_surplus()

    for zone in {d.zone for d in eng.mine}:
        committed = sum(d.assigned_kw for d in eng.mine if d.zone == zone)
        assert committed <= eng._zone_export_cap_kw(zone) + 1e-6, zone


def test_a_later_loss_joins_the_open_incident_so_recovery_never_beats_the_exposure():
    """A wave that lands while an incident is open is the same work, so it is merged.

    Before this, ``dollars_at_risk`` was frozen at detection while the approval went
    on to restore the merged wave's kW too, and an incident could report recovering
    more than it ever said was at risk.
    """
    eng = ControlRoomEngine(fleet_size=1_000)
    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    ring_kw, ring_risk = incident.lost_kw, incident.dollars_at_risk

    dropped = eng.inject_stale_telemetry()

    assert dropped > 0
    assert incident.lost_kw == pytest.approx(ring_kw + dropped, abs=0.01)
    assert incident.dollars_at_risk > ring_risk
    assert f"${incident.dollars_at_risk:,.2f}" in incident.impact
    assert any(e.kind == "incident_merged" for e in eng.audit)

    eng.approve_recovery()

    assert incident.restored_kw <= incident.lost_kw
    assert incident.dollars_recovered <= incident.dollars_at_risk
    assert incident.dollars_recovered > ring_risk  # the merged wave really was recovered


def test_no_incident_ever_recovers_more_than_it_put_at_risk():
    for size, wave in ((48, False), (1_000, True), (1_000, False)):
        eng = ControlRoomEngine(fleet_size=size)
        eng.trigger_device_failure(FOCUS_DEVICE_ID)
        if wave:
            eng.inject_stale_telemetry()
        eng.approve_recovery()
        for incident in eng.incidents:
            assert incident.dollars_recovered <= incident.dollars_at_risk
            assert incident.restored_kw <= incident.lost_kw
