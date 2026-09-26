"""Staged firmware rollout: rings, health gates, halt and rollback, human promotion.

Everything under test is simulated: no firmware, no device, no temperature is real.
"""

from dataclasses import replace

import pytest

from gridsignal import rollout
from gridsignal.mesh.messages import MessageKind, read_jsonl
from gridsignal.mesh.scenarios import SCENARIO_DIR

BAD = SCENARIO_DIR / "rollout_bad_build.yaml"
GOOD = SCENARIO_DIR / "rollout_good_build.yaml"


def test_rings_cover_the_fleet_once_and_only_grow():
    plan = rollout.ring_plan(10_000)
    assert [name for name, _ in plan] == [name for name, _ in rollout.RING_SHARES]
    sizes = [len(cohort) for _, cohort in plan]
    assert sizes[0] == rollout.LAB_DEVICES
    assert sum(sizes) == 10_000

    seen: list[str] = []
    for _, cohort in plan:
        seen.extend(cohort)
    assert len(set(seen)) == 10_000  # every device updated exactly once


def test_a_good_build_walks_every_ring_and_needs_two_human_approvals():
    result = rollout.run_rollout(rollout.load_rollout(GOOD), trace=None)
    m = result.metrics

    assert not m.halted
    assert m.rings_completed == len(rollout.RING_SHARES)
    assert m.homes_touched == m.devices
    assert m.homes_affected == 0
    assert m.reserve_violations == 0
    # 50% and 100% are the rings above the 10% threshold.
    assert m.human_approvals == 2
    approved = [r.ring for r in result.rings if r.approved_by]
    assert approved == ["ring_50pct", "ring_100pct"]


def test_a_silent_bad_build_is_caught_by_the_canary_response_gate():
    result = rollout.run_rollout(rollout.load_rollout(BAD), trace=None)
    m = result.metrics

    assert m.halted
    assert m.halted_ring == "canary_1pct"
    # The heartbeat keeps arriving; only the charge/discharge gate sees it.
    assert m.failed_gate == "charge_discharge_response"
    canary = next(r for r in result.rings if r.ring == "canary_1pct")
    assert next(g for g in canary.gates if g.gate == "telemetry_heartbeat").passed

    assert m.homes_touched <= 0.02 * m.devices  # stopped inside the 1% ring
    assert 0 < m.homes_affected < m.homes_touched
    assert m.rolled_back == m.homes_touched
    assert m.time_to_detect_s is not None and m.time_to_detect_s < 900
    assert m.reserve_violations == 0


def test_the_defect_only_touches_hot_devices_and_is_about_three_percent_of_the_fleet():
    scenario = rollout.load_rollout(BAD)
    ids = rollout.device_ids(10_000)
    broken = [d for d in ids if rollout.is_broken(scenario, d)]

    assert all(rollout.ambient_c(scenario, d) >= scenario.fault.high_temp_c for d in broken)
    assert 0.02 <= len(broken) / len(ids) <= 0.04


def test_no_ring_advances_while_a_grid_event_is_live():
    scenario = replace(rollout.load_rollout(GOOD), grid_event_s=(0, 300))
    result = rollout.run_rollout(scenario, trace=None)

    lab = result.rings[0]
    assert lab.held_s >= 300
    assert lab.started_at_s >= 300
    holds = [m for m in result.bus.of_kind(MessageKind.ESCALATION) if "grid event" in m.summary]
    assert holds


def test_no_ring_advances_while_a_home_in_it_is_islanded():
    scenario = replace(
        rollout.load_rollout(GOOD),
        grid_event_s=None,
        islanded=("BAT-00001",),
    )
    result = rollout.run_rollout(scenario, trace=None)

    # The islanded home sits in the lab ring, which never clears, so nothing ships.
    assert result.metrics.homes_touched == 0
    assert result.metrics.halted
    assert result.metrics.failed_gate == "blocked"
    assert result.rings == []
    assert result.metrics.held_s >= rollout.HOLD_GIVE_UP_S
    assert any("islanded" in m.summary for m in result.bus.of_kind(MessageKind.ESCALATION))


def test_promotion_past_ten_percent_stops_without_a_human():
    scenario = replace(rollout.load_rollout(GOOD), approver=None, grid_event_s=None)
    result = rollout.run_rollout(scenario, trace=None)
    m = result.metrics

    assert m.halted
    assert m.halted_ring == "ring_50pct"
    assert m.failed_gate == "human_approval"
    assert m.human_approvals == 0
    assert m.homes_touched == 1_000  # nothing past the 10% ring


def test_a_reserve_breaking_build_fails_the_backup_gate():
    scenario = rollout.load_rollout(BAD)
    scenario = replace(
        scenario,
        fault=replace(scenario.fault, breaks_reserve=True, silent_failure_share=1.0),
        grid_event_s=None,
    )
    result = rollout.run_rollout(scenario, trace=None)

    assert result.metrics.failed_gate in {"charge_discharge_response", "backup_reserve_held"}
    lab = result.rings[0]
    assert not next(g for g in lab.gates if g.gate == "backup_reserve_held").passed
    assert result.metrics.reserve_violations > 0


def test_runs_are_deterministic_and_replayable_from_the_trace(tmp_path):
    scenario = rollout.load_rollout(BAD)
    first = rollout.run_rollout(scenario, trace=tmp_path / "bad.jsonl")
    second = rollout.run_rollout(scenario, trace=None)

    assert first.metrics == second.metrics
    trace = read_jsonl(tmp_path / "bad.jsonl")
    assert len(trace) == len(first.bus.messages)
    assert any(r["kind"] == MessageKind.ESCALATION.value for r in trace)
    assert trace[-1]["payload"]["failed_gate"] == "charge_discharge_response"


def test_ten_thousand_device_rollout_detects_the_bad_build_fast(capsys):
    import time

    scenario = rollout.load_rollout(BAD)
    assert scenario.devices == 10_000

    started = time.perf_counter()
    result = rollout.run_rollout(scenario, trace=None)
    elapsed_ms = (time.perf_counter() - started) * 1_000

    m = result.metrics
    with capsys.disabled():
        print(
            f"\n  rollout benchmark: {m.devices:,} devices in {elapsed_ms:.0f} ms — "
            f"halted at {m.halted_ring} after {m.time_to_detect_s}s simulated, "
            f"{m.homes_touched:,} homes touched, {m.homes_affected:,} affected"
        )
    assert elapsed_ms < 5_000
    assert m.homes_touched == 100


def test_the_cli_writes_a_trace(tmp_path):
    scenario = rollout.load_rollout(GOOD)
    path = rollout.trace_path(scenario, directory=tmp_path)
    rollout.run_rollout(scenario, trace=path)
    assert path.exists()
    assert read_jsonl(path)


def test_gate_thresholds_come_from_the_scenario():
    scenario = rollout.load_rollout(BAD)
    assert scenario.heartbeat_gate == pytest.approx(0.99)
    assert scenario.response_gate == pytest.approx(0.99)
    lenient = replace(scenario, response_gate=0.5, grid_event_s=None)
    assert not rollout.run_rollout(lenient, trace=None).metrics.halted
