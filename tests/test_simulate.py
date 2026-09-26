"""Chaos scenarios: deterministic replay, JSONL traces and the 10k-agent benchmark."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.mesh.build import heartbeat_all, register_fleet
from gridsignal.mesh.cards import derived_signing_key
from gridsignal.mesh.messages import MessageBus, MessageKind, read_jsonl
from gridsignal.mesh.negotiation import Coordinator
from gridsignal.mesh.registry import AgentRegistry
from gridsignal.mesh.scenarios import (
    SCENARIO_DIR,
    Injection,
    available_scenarios,
    load_scenario,
)
from gridsignal.prices import load_scenario as load_price_scenario
from gridsignal.simulate import main, run_file, run_scenario

REQUIRED_INJECTIONS = {
    Injection.DEVICE_FAILURE,
    Injection.GATEWAY_OUTAGE,
    Injection.ZONE_OUTAGE,
    Injection.STALE_TELEMETRY,
    Injection.LYING_AGENT,
    Injection.SILENT_AFTER_AWARD,
}


def test_at_least_four_scenarios_are_bundled() -> None:
    files = available_scenarios()
    assert len(files) >= 4
    assert (SCENARIO_DIR / "zone_outage.yaml") in files


def test_the_bundled_scenarios_cover_every_failure_mode() -> None:
    kinds = {f.kind for path in available_scenarios() for f in load_scenario(path).failures}
    assert REQUIRED_INJECTIONS <= kinds


def test_a_scenario_loads_its_seed_population_and_injections() -> None:
    scenario = load_scenario("zone_outage.yaml")

    assert scenario.seed == 42
    assert scenario.batteries == 1_000
    assert scenario.llm_coordinator is False
    assert scenario.of_kind(Injection.ZONE_OUTAGE)[0].zone == "LZ_HOUSTON"


def test_single_device_scenario_covers_the_gap_behind_a_human_approval() -> None:
    result = run_scenario(load_scenario("single_device.yaml"))
    kinds = [m.kind for m in result.bus.messages]

    assert result.metrics.covered_pct == 100.0
    assert not result.metrics.escalated
    assert kinds.index(MessageKind.APPROVAL) < kinds.index(MessageKind.AWARD_EXECUTED)
    # The CLI has no human at the keyboard, so the YAML name is labelled as scripted.
    assert result.awards[0].approved_by == "M. Alvarez (Fleet Operator) (scripted approver)"
    assert result.metrics.dollars_recovered > 0.0


def test_zone_outage_covers_partially_and_escalates() -> None:
    result = run_scenario(load_scenario("zone_outage.yaml"))

    assert result.metrics.escalated
    assert 0.0 < result.metrics.covered_pct < 100.0
    assert result.bus.of_kind(MessageKind.ESCALATION)
    assert result.metrics.dollars_at_risk > result.metrics.dollars_recovered > 0.0


def test_lying_agent_and_silent_agents_are_visible_in_the_metrics() -> None:
    result = run_scenario(load_scenario("lying_agent.yaml"))

    assert result.metrics.rejected_cards >= 1
    assert result.metrics.stale_agents >= 4
    assert result.bus.of_kind(MessageKind.REJECT)


def test_a_silent_awardee_forces_a_second_round() -> None:
    result = run_scenario(load_scenario("silent_bidder.yaml"))
    executed = result.bus.of_kind(MessageKind.AWARD_EXECUTED)

    assert result.metrics.rounds == 2
    assert len(executed) == 2
    assert result.bus.of_kind(MessageKind.HEARTBEAT_LOST)
    # Both rounds went through the human gate.
    assert len(result.bus.of_kind(MessageKind.APPROVAL)) == 2


def test_replay_is_deterministic(tmp_path: Path) -> None:
    first = run_file("zone_outage.yaml", tmp_path / "a.jsonl")
    second = run_file("zone_outage.yaml", tmp_path / "b.jsonl")

    assert (tmp_path / "a.jsonl").read_text() == (tmp_path / "b.jsonl").read_text()
    assert first.metrics == second.metrics


def test_the_trace_is_jsonl_the_dashboard_can_read(tmp_path: Path) -> None:
    out = tmp_path / "trace.jsonl"
    result = run_file("single_device.yaml", out)
    records = read_jsonl(out)

    assert len(records) == len(result.bus.messages)
    assert {"t_s", "kind", "sender", "recipient", "summary", "payload"} <= set(records[0])
    assert records[-1]["kind"] == MessageKind.METRICS.value
    assert json.loads(out.read_text().splitlines()[0])["kind"] == MessageKind.REGISTER.value


def test_the_cli_runs_a_scenario_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli.jsonl"
    assert main([str(SCENARIO_DIR / "single_device.yaml"), "--out", str(out)]) == 0

    printed = capsys.readouterr().out
    assert "kW recovered" in printed
    assert out.exists()


def test_the_cli_needs_a_scenario() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_ten_thousand_agent_negotiation_benchmark(capsys: pytest.CaptureFixture[str]) -> None:
    """The mesh has to stay usable at fleet scale, not just at demo scale."""
    engine = ControlRoomEngine(price_trace=load_price_scenario("scarcity"), fleet_size=10_000)
    hours = engine.remaining_hours()
    devices = engine.devices
    registry = AgentRegistry(key=derived_signing_key(42))
    bus = MessageBus()

    started = time.perf_counter()
    register_fleet(registry, devices, hours, bus)
    registered_s = time.perf_counter() - started

    started = time.perf_counter()
    heartbeat_all(registry)
    heartbeat_s = time.perf_counter() - started

    coordinator = Coordinator(registry, bus)
    started = time.perf_counter()
    call = coordinator.call_for_capacity(500.0, hours)
    bids = coordinator.collect_bids(call, log_each=False)
    award_set = coordinator.propose(call, bids)
    coordinator.approve(call.call_id, "M. Alvarez (Fleet Operator)")
    negotiate_s = time.perf_counter() - started

    with capsys.disabled():
        print(
            f"\n10,000-agent mesh: register {registered_s * 1000:.0f} ms, "
            f"heartbeat {heartbeat_s * 1000:.0f} ms, negotiate {negotiate_s * 1000:.0f} ms "
            f"({len(bids):,} bids, {len(award_set.awards)} awards, "
            f"{award_set.covered_kw:.0f} kW covered)"
        )

    assert len(registry) > 10_000  # batteries plus gateway and zone agents
    assert len(bids) > 1_000
    assert award_set.coverage_pct == 100.0
    assert award_set.executed
    assert registered_s + heartbeat_s + negotiate_s < 30.0


# ------------------------------------------- distrusted bids and the human click


def test_distrusted_bidders_are_dropped_and_the_award_recomputed() -> None:
    """A valid signature is not enough: the inflated bidders lose their award."""
    result = run_scenario(load_scenario(SCENARIO_DIR / "holdout" / "large_load_squeeze.yaml"))
    revised = [a for a in result.awards if a.covered_kw_before_revision is not None]

    assert revised, "the squeeze drill has validly signed bidders Jev distrusts"
    award = revised[-1]
    assert award.dropped  # named, not just flagged
    assert not {a.agent_id for a in award.awards} & set(award.dropped)
    assert award.covered_kw < (award.covered_kw_before_revision or 0.0)
    assert result.metrics.distrusted_kw_dropped > 0.0

    revisions = [m for m in result.bus.messages if m.kind is MessageKind.AWARD_REVISED]
    assert revisions and revisions[-1].payload["dropped"] == list(award.dropped)
    # The revision happens before anyone is asked to approve the plan.
    kinds = [m.kind for m in result.bus.messages]
    assert kinds.index(MessageKind.AWARD_REVISED) < kinds.index(MessageKind.APPROVAL)


def test_the_cli_labels_its_approver_as_scripted() -> None:
    result = run_scenario(load_scenario("single_device.yaml"))
    assert result.awards[0].approved_by.endswith("(scripted approver)")


def test_without_a_click_nothing_is_committed() -> None:
    """The Control Room path: propose, wait, then execute on the operator's click."""
    pending = run_scenario(load_scenario("single_device.yaml"), approve=False)
    assert pending.metrics.pending_approval
    assert pending.awards[-1].approved_by is None
    assert not pending.awards[-1].executed

    clicked = run_scenario(
        load_scenario("single_device.yaml"), approver="M. Alvarez (Fleet Operator)"
    )
    assert clicked.awards[-1].approved_by == "M. Alvarez (Fleet Operator)"
    assert clicked.awards[-1].executed
    assert "scripted" not in (clicked.awards[-1].approved_by or "")
