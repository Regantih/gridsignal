"""Home-first dispatch: the house is served before anything is exported.

Everything here is simulated — the load shape, the hardware mix, the tenancy split
and the neighbour sharing are modelling assumptions, not measurements.
"""

import pytest

from gridsignal import home, load, member
from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.models import Controller, Device, DeviceStatus, UnitType
from gridsignal.fleet import (
    BASE_CORE_KWH,
    BASE_CORE_POWER_KW,
    FOCUS_DEVICE_ID,
    UTILITY_ZONE,
    build_fleet,
)


@pytest.fixture
def eng() -> ControlRoomEngine:
    return ControlRoomEngine()


def test_every_home_draws_its_own_simulated_load():
    fleet = build_fleet(size=48)
    assert all(d.home_load_kw > 0 for d in fleet)
    # the same home always draws the same, two homes rarely draw the same
    assert load.home_load_kw("BAT-001", 18) == load.home_load_kw("BAT-001", 18)
    assert len({d.home_load_kw for d in fleet}) > 10


def test_export_is_discharge_minus_the_home_load(eng: ControlRoomEngine):
    for device in eng.mine:
        if not device.is_dispatchable:
            continue
        assert device.export_kw == pytest.approx(
            device.discharge_kw - device.home_load_kw, abs=0.01
        )
        assert device.discharge_kw <= device.power_kw + 1e-6


def test_the_fleet_serves_homes_before_it_exports(eng: ControlRoomEngine):
    snap = eng.snapshot()
    assert snap.home_load_kw > 0
    assert snap.discharge_kw == pytest.approx(
        snap.home_load_kw + snap.committed_kw + snap.partner_kw, abs=0.5
    )
    assert snap.committed_kw < snap.discharge_kw


def test_no_export_ever_digs_into_the_member_reserve(eng: ControlRoomEngine):
    hours = eng.grid_event.duration_hours
    for device in eng.mine:
        if not device.is_dispatchable:
            continue
        sold = device.assigned_kw * hours
        assert device.available_kwh - sold >= home.reserve_kwh(device, eng.reserve_fraction) - 0.01


def test_the_fleet_is_a_mix_of_legacy_and_base_core_units():
    fleet = build_fleet(size=48)
    cores = [d for d in fleet if d.unit_type is UnitType.BASE_CORE]
    assert 0 < len(cores) < len(fleet)
    assert all(d.capacity_kwh == BASE_CORE_KWH for d in cores)
    assert all(d.power_kw == BASE_CORE_POWER_KW for d in cores)


def test_unit_type_and_tenant_rows_add_up(eng: ControlRoomEngine):
    rows = home.by_unit_type(eng.mine, eng.grid_event.duration_hours, eng.grid_event.price_mwh)
    assert {r.unit_type for r in rows} == {"legacy", "base_core"}
    assert sum(r.devices for r in rows) == len(eng.mine)

    tenants = home.by_tenant(eng.devices)
    assert {t.controller for t in tenants} == {"base", "utility"}
    assert sum(t.devices for t in tenants) == len(eng.devices)


def test_the_operator_never_dispatches_another_tenants_battery(eng: ControlRoomEngine):
    partner = [d for d in eng.devices if not d.is_operator_controlled]
    assert partner and all(d.zone == UTILITY_ZONE for d in partner)
    assert all(d not in eng.mine for d in partner)
    # the partner schedule is theirs; it is never counted towards our commitment
    assert eng.snapshot().partner_kw > 0
    assert eng.snapshot().committed_kw == pytest.approx(sum(d.export_kw for d in eng.mine), abs=0.1)
    before = {d.device_id: d.assigned_kw for d in partner}
    eng.trigger_device_failure(FOCUS_DEVICE_ID)
    eng.approve_recovery()
    assert {d.device_id: d.assigned_kw for d in partner} == before


def test_raising_the_reserve_trades_revenue_for_backup_hours(eng: ControlRoomEngine):
    outcome = eng.set_reserve_floor(home.STORM_RESERVE_FRACTION)
    assert outcome.committed_kw_after <= outcome.committed_kw_before
    assert outcome.revenue_given_up_usd >= 0
    assert outcome.backup_hours_after >= outcome.backup_hours_before
    assert eng.snapshot().committed_kw == pytest.approx(outcome.committed_kw_after, abs=0.1)
    assert any(e.kind == "reserve_policy" for e in eng.audit)


def test_a_generator_only_extends_backup_for_members_who_have_one():
    fleet = build_fleet(size=48)
    with_gen = next(d for d in fleet if d.generator_kw > 0)
    without = next(d for d in fleet if d.generator_kw == 0)
    assert home.backup_estimate(with_gen, 2.0).generator_hours > 0
    assert home.backup_estimate(without, 2.0).generator_hours == 0


def _home(device_id: str, soc: float, **kwargs: object) -> Device:
    return Device(
        device_id=device_id,
        site="123 Test St, Houston",
        zone="LZ_HOUSTON",
        lat=29.76,
        lon=-95.37,
        capacity_kwh=30.0,
        state_of_charge=soc,
        power_kw=7.5,
        status=DeviceStatus.ONLINE,
        controller=Controller.BASE,
        home_load_kw=1.5,
        **kwargs,  # type: ignore[arg-type]
    )


def test_mutual_aid_helps_a_medical_member_without_spending_anyone_else_reserve():
    recipient = _home("BAT-A", 0.15, mutual_aid=True, medical_device=True)
    givers = [_home(f"BAT-{c}", 0.95, mutual_aid=True) for c in "BCD"]
    fleet = [recipient, *givers]
    before = {d.device_id: d.available_kwh for d in givers}

    plan = home.mutual_aid_plan(fleet, "BAT-A")
    assert plan is not None and plan.transfers
    assert plan.hours_gained > 0
    home.apply_aid(fleet, plan)

    for giver in givers:
        assert giver.available_kwh >= home.reserve_kwh(giver) - 0.01
        assert giver.available_kwh <= before[giver.device_id]
    assert recipient.available_kwh > 4.5


def test_mutual_aid_is_refused_without_a_medical_flag_or_opt_in():
    givers = [_home(f"BAT-{c}", 0.95, mutual_aid=True) for c in "BCD"]
    no_flag = _home("BAT-A", 0.15, mutual_aid=True)
    assert home.mutual_aid_plan([no_flag, *givers], "BAT-A") is None
    opted_out = _home("BAT-A", 0.15, medical_device=True)
    assert home.mutual_aid_plan([opted_out, *givers], "BAT-A") is None
    # a neighbour who never opted in is never asked to give
    closed = [_home(f"BAT-{c}", 0.95) for c in "BCD"]
    recipient = _home("BAT-A", 0.15, mutual_aid=True, medical_device=True)
    plan = home.mutual_aid_plan([recipient, *closed], "BAT-A")
    assert plan is not None and plan.transfers == []


def test_the_member_view_splits_home_load_from_export(eng: ControlRoomEngine):
    view = member.member_summary(eng, FOCUS_DEVICE_ID)
    assert view.home_load_kw > 0
    assert view.discharge_kw == pytest.approx(view.home_load_kw + view.export_kw, abs=0.01)
    assert view.backup_hours_with_generator >= view.backup_hours
    assert view.unit_type in ("legacy", "base_core")
    assert view.controller == "base"


def test_the_other_tenants_units_are_a_stand_in_held_to_the_member_reserve() -> None:
    """This mesh writes their kW, so it obeys the same backup floor ours does."""
    eng = ControlRoomEngine(fleet_size=1_000)
    theirs = [d for d in eng.devices if not d.is_operator_controlled]
    assert theirs

    assert any(d.export_kw > 0 for d in theirs)
    for fraction in (eng.reserve_fraction, home.STORM_RESERVE_FRACTION):
        if fraction != eng.reserve_fraction:
            eng.set_reserve_floor(fraction)
        hours = eng.remaining_hours()
        for device in theirs:
            spare_kw = home.spare_backup_kwh(device, fraction) / hours
            assert device.export_kw <= max(spare_kw - device.home_load_kw, 0.0) + 1e-6
            assert device.export_kw >= 0.0
    # Raising the floor takes kW off their units too; it never adds any.
    assert all(d.export_kw <= 3.0 for d in theirs)
