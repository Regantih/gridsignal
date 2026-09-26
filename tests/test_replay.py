"""The full-fleet scarcity replay: three faults at the peak, one human approval."""

from __future__ import annotations

from gridsignal import replay
from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.models import DeviceStatus
from gridsignal.prices import load_scenario

SMALL = 1_000


def test_replay_runs_the_real_scarcity_day():
    result = replay.run(SMALL)
    assert result.date == "2023-09-06"
    assert result.location == "LZ_HOUSTON"
    assert result.price_mwh > 1_000  # a scarcity print, not an ordinary evening


def test_all_three_faults_land_at_the_peak():
    result = replay.run(SMALL)
    names = [f.name for f in result.faults]
    assert names == ["stale telemetry wave", "gateway outage", "spoofed agents"]
    assert all(f.kw > 0 for f in result.faults if f.name != "spoofed agents")


def test_spoofed_cards_never_enter_the_plan():
    result = replay.run(SMALL)
    assert result.rejected_cards == replay.SPOOFED_AGENTS
    assert result.phantom_kw_rejected > 0
    # The phantom kW is refused on signature, so it is never part of the commitment.
    assert result.committed_kw_after <= result.target_kw


def test_orchestration_protects_dollars_it_can_account_for():
    result = replay.run(SMALL)
    assert result.dollars_at_risk > 0
    assert 0 < result.dollars_recovered <= result.dollars_at_risk
    assert result.fault_minutes > 0
    expected = round(result.dollars_recovered / result.fault_minutes, 2)
    assert result.protected_usd_per_fault_minute == expected


def test_no_orchestration_is_the_whole_loss():
    """The counterfactual is the kW that never came back, priced at the same print."""
    result = replay.run(SMALL)
    assert result.dollars_unprotected == round(result.dollars_at_risk - result.dollars_recovered, 2)


def test_replay_is_deterministic():
    first, second = replay.run(SMALL), replay.run(SMALL)
    assert first.kw_lost == second.kw_lost
    assert first.dollars_recovered == second.dollars_recovered


def test_stale_wave_removes_capacity_rather_than_assuming_it_is_good():
    engine = ControlRoomEngine(price_trace=load_scenario("scarcity"), fleet_size=SMALL)
    before = engine.snapshot().committed_kw
    dropped = engine.inject_stale_telemetry(replay.STALE_SHARE)
    assert dropped > 0
    stale = [d for d in engine.devices if d.status is DeviceStatus.OFFLINE]
    assert stale and all(d.assigned_kw == 0.0 for d in stale)
    assert engine.snapshot().committed_kw == round(before - dropped, 1)


def test_cli_reports_runtime_and_the_per_minute_figure(capsys):
    assert replay.main(["--devices", str(SMALL)]) == 0
    out = capsys.readouterr().out
    assert "protected per minute of fault" in out
    assert "wall clock" in out
    assert "spoofed agents" in out
