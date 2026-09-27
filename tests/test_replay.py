"""The full-fleet scarcity replay: three faults at the peak, one human approval."""

from __future__ import annotations

import dataclasses

import pytest

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
    assert 0 < result.dollars_recovered < result.dollars_at_risk
    assert result.fault_minutes > 0
    # Recovery is priced over the hours left after the fix, so the degraded minutes are
    # never sold twice and the headline can never round its way past what was at risk.
    assert result.recovery_hours < result.window_hours
    assert result.dollars_recovered == pytest.approx(
        result.dollars_at_risk * result.recovery_hours / result.window_hours, rel=0.02
    )


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


def test_cli_reports_runtime_and_what_the_recovery_does_not_cover(capsys):
    assert replay.main(["--devices", str(SMALL)]) == 0
    out = capsys.readouterr().out
    assert "simulated minutes the fleet spent degraded did not" in out
    assert "wall clock" in out
    assert "spoofed agents" in out


def test_near_full_recovery_is_reported_with_the_headroom_that_allowed_it():
    """What comes back is a fact about spare capacity, not a property of the orchestrator."""
    result = replay.run()
    # Never all of it: the kW are reassigned, the degraded minutes are gone.
    assert 0.9 < result.recovered_share < 1.0
    assert result.spare_kw_at_fault >= result.kw_lost
    assert result.headroom_cover == round(result.spare_kw_at_fault / result.kw_lost, 2)
    text = "\n".join(replay.lines(result))
    assert f"{result.spare_kw_at_fault:,.0f} kW" in text
    assert "not guaranteed by orchestration" in text
    assert f"{result.spare_kw_at_fault:,.0f} kW" in replay.summary(result)


def test_headroom_cover_is_zero_when_nothing_was_lost():
    result = replay.run()
    nothing_lost = dataclasses.replace(result, kw_lost=0.0)
    assert nothing_lost.headroom_cover == 0.0


def test_the_replay_never_recovers_more_than_it_put_at_risk():
    """Same invariant as the Control Room and the operator workflow, at fleet scale."""
    result = replay.run(SMALL)
    assert result.dollars_recovered <= result.dollars_at_risk
    assert result.recovered_share <= 1.0
    assert result.dollars_unprotected >= 0.0
