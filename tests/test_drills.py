"""Held-out chaos drills: they parse, they run keyless, and they never spend backup."""

from __future__ import annotations

import httpx
import pytest

from gridsignal import drills
from gridsignal.mesh.messages import MessageKind
from gridsignal.mesh.scenarios import HOLDOUT_DIR, Injection, load_scenario
from gridsignal.simulate import (
    FFR_DEADLINE_CYCLES,
    FFR_TRIGGER_HZ,
    NOMINAL_HZ,
    run_scenario,
)

DRILLS = (
    "cascade_spain_style",
    "frequency_dip_coordinator_down",
    "neighborhood_island",
    "large_load_squeeze",
)
KEYS = ("AI_GATEWAY_API_KEY", "TYPESAFE_API_KEY")


@pytest.fixture(autouse=True)
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every drill is scored the way a judge sees it: no key, no network."""
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("the drills must run without touching the network")

    monkeypatch.setattr(httpx, "post", refuse)


def test_the_four_drills_are_bundled_and_labelled() -> None:
    files = drills.available_drills()

    assert [p.stem for p in files] == sorted(DRILLS)
    for path in files:
        scenario = load_scenario(path)
        assert scenario.held_out
        assert scenario.declared_root_cause, f"{path.stem} has no ground-truth label"
        assert scenario.ground_truth_note.strip()


def test_drills_live_apart_from_the_tuned_scenarios() -> None:
    from gridsignal.mesh.scenarios import available_scenarios

    assert HOLDOUT_DIR.is_dir()
    assert not set(available_scenarios()) & set(drills.available_drills())


def test_the_cascade_arrives_in_waves_so_the_faults_interact() -> None:
    scenario = load_scenario(HOLDOUT_DIR / "cascade_spain_style.yaml")

    kinds = [f.kind for f in sorted(scenario.failures, key=lambda f: f.at_s)]

    assert kinds[0] is Injection.GENERATION_TRIP
    assert Injection.GATEWAY_OUTAGE in kinds and Injection.STALE_TELEMETRY in kinds
    assert len({f.at_s for f in scenario.failures}) == len(scenario.failures)


def test_the_frequency_drill_dips_below_the_ffr_trigger_while_the_coordinator_is_down() -> None:
    scenario = load_scenario(HOLDOUT_DIR / "frequency_dip_coordinator_down.yaml")

    result = run_scenario(scenario)
    metrics = result.metrics

    assert metrics.min_frequency_hz < FFR_TRIGGER_HZ
    assert metrics.response_cycles is not None
    assert any(m.kind is MessageKind.GRID_STRESS for m in result.bus.messages)
    assert [f.for_s for f in scenario.of_kind(Injection.COORDINATOR_DOWN)] == [180]


def test_islanded_homes_keep_their_own_energy_and_are_never_awarded() -> None:
    scenario = load_scenario(HOLDOUT_DIR / "neighborhood_island.yaml")

    result = run_scenario(scenario)

    assert result.metrics.islanded_agents > 0
    assert result.metrics.min_frequency_hz == NOMINAL_HZ
    islanded = {
        agent
        for message in result.bus.of_kind(MessageKind.ISLANDED)
        for agent in [str(message.payload.get("zone"))]
    }
    assert islanded == {"LZ_AUSTIN"}
    awarded = {a.agent_id for s in result.awards for a in s.awards}
    zone_of = {d.agent_id: d.zone for d in [result.registry.card(i) for i in awarded]}
    assert "LZ_AUSTIN" not in set(zone_of.values())


def test_conflicting_cards_are_signed_yet_inconsistent() -> None:
    scenario = load_scenario(HOLDOUT_DIR / "large_load_squeeze.yaml")

    result = run_scenario(scenario)

    assert result.metrics.conflicting_cards == 3
    assert result.metrics.extra_demand_kw == 1200.0
    conflicts = result.bus.of_kind(MessageKind.CONFLICT)
    assert len(conflicts) == 3
    for message in conflicts:
        assert result.registry.status(message.sender).value == "verified"


def test_batteries_self_deploy_from_their_own_cards_inside_the_15_cycle_window() -> None:
    """After tuning on held-out: the local card rule acts without the coordinator."""
    scenario = load_scenario(HOLDOUT_DIR / "frequency_dip_coordinator_down.yaml")

    result = run_scenario(scenario)
    metrics = result.metrics

    assert metrics.self_deployed_kw > 0
    assert metrics.response_cycles is not None and metrics.response_cycles <= FFR_DEADLINE_CYCLES
    assert metrics.within_ffr_deadline
    deploys = result.bus.of_kind(MessageKind.SELF_DEPLOY)
    assert len(deploys) == 1
    # The fleet acted while the coordinator was still unreachable.
    calls = result.bus.of_kind(MessageKind.CALL_FOR_CAPACITY)
    assert deploys[0].t_s < min(m.t_s for m in calls)


def test_self_deployed_kw_is_reconciled_once_the_coordinator_returns() -> None:
    scenario = load_scenario(HOLDOUT_DIR / "frequency_dip_coordinator_down.yaml")

    result = run_scenario(scenario)
    metrics = result.metrics

    reconciles = result.bus.of_kind(MessageKind.RECONCILE)
    assert len(reconciles) == 1
    remaining = float(reconciles[0].payload["remaining_kw"])
    assert remaining == pytest.approx(metrics.lost_kw - metrics.self_deployed_kw, abs=0.05)
    # Locally deployed kW plus awarded kW never exceeds what was lost: no double count.
    awarded = sum(a.kw for s in result.awards for a in s.awards)
    assert metrics.self_deployed_kw + awarded <= metrics.lost_kw + 0.05
    assert metrics.covered_kw <= metrics.lost_kw + 0.05


def test_islanded_homes_resync_when_the_feeder_is_restored() -> None:
    scenario = load_scenario(HOLDOUT_DIR / "neighborhood_island.yaml")

    result = run_scenario(scenario)

    assert result.metrics.resynced_agents == result.metrics.islanded_agents > 0
    assert len(result.bus.of_kind(MessageKind.RECONCILE)) == 1


def test_a_plain_component_failure_still_sends_jev_the_state_it_always_did() -> None:
    """Grid conditions are only added to the state when a drill actually has them."""
    from gridsignal.mesh.scenarios import SCENARIO_DIR

    result = run_scenario(load_scenario(SCENARIO_DIR / "single_device.yaml"))

    assert result.metrics.self_deployed_kw == 0.0
    assert result.metrics.min_frequency_hz == NOMINAL_HZ
    assert not result.bus.of_kind(MessageKind.SELF_DEPLOY)


@pytest.mark.parametrize("name", DRILLS)
def test_every_drill_runs_keyless_and_spends_no_homeowner_backup(name: str) -> None:
    scenario = load_scenario(HOLDOUT_DIR / f"{name}.yaml")

    result = run_scenario(scenario)

    assert result.metrics.backup_violations == 0
    assert result.metrics.auto_approvals == 0
    assert result.metrics.human_approvals >= 1
    assert result.metrics.covered_kw > 0


@pytest.mark.parametrize("name", DRILLS)
def test_drills_replay_deterministically(name: str) -> None:
    scenario = load_scenario(HOLDOUT_DIR / f"{name}.yaml")

    first = run_scenario(scenario).metrics
    second = run_scenario(scenario).metrics

    assert first == second


def test_the_report_scores_both_decision_layers_on_every_drill() -> None:
    report = drills.run()

    assert len(report.rows) == 2 * len(DRILLS)
    assert report.backup_violations == 0
    for mode in (drills.RULES, drills.JEV):
        _, total = report.accuracy(mode)
        assert total == len(DRILLS)
    table = drills.markdown(report)
    assert "Backup reserve violations" in table
    for name in DRILLS:
        assert name in table
