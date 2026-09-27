"""Q7 operator workflow: grouped alarms, the incident timeline, logged overrides."""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.workflow import (
    CAPACITY_DROPPED,
    GROUP_WINDOW_S,
    TELEMETRY_LOST,
    Alarm,
    OverrideError,
    group_alarms,
    timeline,
)
from gridsignal.fleet import UTILITY_PARTNER
from gridsignal.home import reserve_kwh
from gridsignal.workflow import main

AT = datetime(2026, 9, 26, 18, 0, 0)


def alarm(offset_s: int, device: str, ring: int, kind: str = TELEMETRY_LOST, kw: float = 0.0):
    return Alarm(at=AT + timedelta(seconds=offset_s), device_id=device, ring=ring, kind=kind, kw=kw)


def test_one_ring_failing_raises_one_alarm_per_device_per_symptom():
    eng = ControlRoomEngine(fleet_size=480)
    eng.trigger_device_failure()
    devices = {a.device_id for a in eng.alarms}
    assert len(devices) > 1
    assert {a.kind for a in eng.alarms} == {TELEMETRY_LOST, CAPACITY_DROPPED}
    assert len(eng.alarms) > len(devices), "a device that lost capacity pages twice"


def test_alarms_from_one_cause_group_into_one_incident():
    eng = ControlRoomEngine()
    eng.trigger_device_failure()
    report = group_alarms(eng.alarms)
    assert report.incidents == 1
    assert report.alarms == len(eng.alarms)
    group = report.groups[0]
    assert group.kinds == (CAPACITY_DROPPED, TELEMETRY_LOST)
    assert group.kw > 0


def test_alarms_per_incident_before_and_after_grouping():
    alarms = [alarm(0, f"BAT-{i:03d}", 7) for i in range(20)]
    report = group_alarms(alarms)
    assert report.before == 1.0  # every alarm is its own page
    assert report.after == 20.0  # one incident holds all twenty
    assert "20 raw alarms grouped into 1 incident" in report.headline


def test_grouping_is_deterministic_and_order_independent():
    alarms = [alarm(0, "BAT-001", 3), alarm(1, "BAT-002", 4), alarm(2, "BAT-003", 3)]
    first = group_alarms(alarms)
    second = group_alarms(list(reversed(alarms)))
    assert [g.device_ids for g in first.groups] == [g.device_ids for g in second.groups]
    assert first.incidents == 2


def test_a_later_failure_on_the_same_ring_is_not_hidden_inside_the_first():
    alarms = [alarm(0, "BAT-001", 3), alarm(GROUP_WINDOW_S + 1, "BAT-002", 3)]
    assert group_alarms(alarms).incidents == 2


def test_empty_alarm_list_reports_nothing_rather_than_dividing_by_zero():
    report = group_alarms([])
    assert (report.incidents, report.after) == (0, 0.0)


def test_timeline_runs_detect_to_recover_with_dollars():
    eng = ControlRoomEngine()
    eng.trigger_device_failure()
    incident = eng.approve_recovery()
    steps = timeline(eng.audit, incident)
    stages = [s.stage for s in steps]
    assert stages[0] == "detect"
    for stage in ("diagnose", "approve", "reassign", "recover"):
        assert stage in stages
    assert stages.index("detect") < stages.index("reassign") < stages.index("recover")
    assert [s.seconds_from_open for s in steps] == sorted(s.seconds_from_open for s in steps)
    assert steps[0].elapsed == "+0s"
    assert incident.dollars_at_risk > 0 and incident.dollars_recovered > 0


def test_timeline_is_empty_before_anything_happens():
    assert timeline([]) == []


def test_override_needs_a_reason():
    eng = ControlRoomEngine()
    device = next(d for d in eng.devices if d.is_operator_controlled)
    with pytest.raises(OverrideError):
        eng.override_award(device.device_id, 1.0, "   ")
    assert eng.overrides == []


def test_override_changes_the_award_and_logs_the_reason():
    eng = ControlRoomEngine()
    device = next(d for d in eng.devices if d.is_operator_controlled and d.assigned_kw > 0)
    before = device.assigned_kw
    record = eng.override_award(device.device_id, 0.0, "member called: medical device on site")
    assert eng.device(device.device_id).assigned_kw == 0.0
    assert (record.kw_before, record.kw_after) == (before, 0.0)
    assert record.delta_kw == -before
    event = eng.audit[-1]
    assert event.kind == "override"
    assert "medical device on site" in event.detail
    assert eng.overrides == [record]


def test_override_cannot_touch_the_utility_partners_tenant():
    eng = ControlRoomEngine()
    device = next(d for d in eng.devices if not d.is_operator_controlled)
    with pytest.raises(OverrideError, match=re.escape(UTILITY_PARTNER)):
        eng.override_award(device.device_id, 0.0, "tempting")
    assert eng.overrides == []


def test_override_cannot_spend_the_members_backup_reserve():
    eng = ControlRoomEngine()
    device = next(d for d in eng.devices if d.is_operator_controlled)
    with pytest.raises(OverrideError):
        eng.override_award(device.device_id, device.power_kw * 10, "give me everything")
    with pytest.raises(OverrideError):
        eng.override_award(device.device_id, -1.0, "negative")
    assert eng.device(device.device_id).assigned_kw == device.assigned_kw


def test_overrides_leave_every_battery_above_its_reserve():
    eng = ControlRoomEngine()
    eng.trigger_device_failure()
    eng.approve_recovery()
    hours = eng.grid_event.duration_hours
    for device in [d for d in eng.devices if d.is_operator_controlled][:40]:
        headroom = eng.exportable_kw(device)
        if headroom <= 0:
            continue
        eng.override_award(device.device_id, headroom, "push every battery to its limit")
    for device in eng.devices:
        drawn_kwh = device.assigned_kw * hours
        available = device.capacity_kwh * device.state_of_charge
        assert available - drawn_kwh >= reserve_kwh(device) - 1e-6


def test_cli_prints_the_grouping_the_timeline_and_the_override(capsys):
    assert main(["--devices", "200"]) == 0
    out = capsys.readouterr().out
    assert "raw alarms grouped into" in out
    for stage in ("detect", "diagnose", "approve", "reassign", "recover"):
        assert stage in out
    assert "reason (required, logged)" in out


def test_cli_stale_wave_is_several_incidents_not_one():
    eng = ControlRoomEngine()
    eng.trigger_device_failure()
    eng.inject_stale_telemetry()
    report = group_alarms(eng.alarms)
    assert report.incidents > 1
    assert report.after < report.alarms


def test_readme_alarm_numbers_come_from_the_code():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    eng = ControlRoomEngine(fleet_size=10_000)
    eng.trigger_device_failure()
    ring_only = group_alarms(eng.alarms)
    eng.inject_stale_telemetry()
    with_wave = group_alarms(eng.alarms)
    assert (
        f"**{ring_only.alarms:,} raw alarms from "
        f"{len(ring_only.groups[0].device_ids):,} batteries**" in readme
    )
    assert (
        f"**{with_wave.alarms:,} alarms \u2192 {with_wave.incidents} incidents "
        f"({with_wave.after:,.1f} per incident)**" in readme
    )


def test_the_workflow_screen_never_shows_more_recovered_than_at_risk():
    """The stale wave merges into the incident, so both numbers move together."""
    eng = ControlRoomEngine(fleet_size=1_000)
    eng.trigger_device_failure()
    eng.inject_stale_telemetry()
    incident = eng.approve_recovery()
    assert incident.dollars_recovered <= incident.dollars_at_risk
    steps = timeline(eng.audit, incident)
    assert any("merged into" in s.summary for s in steps)


def test_cli_reports_recovered_within_at_risk(capsys):
    assert main(["--devices", "1000", "--stale-wave"]) == 0
    out = capsys.readouterr().out
    at_risk, recovered = re.search(r"\$([\d,]+) at risk, \$([\d,]+) recovered", out).groups()
    assert float(recovered.replace(",", "")) <= float(at_risk.replace(",", ""))
