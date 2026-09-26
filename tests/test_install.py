"""Install wave: several hundred simulated new units joining the mesh mid-event."""

import time
from dataclasses import replace

from gridsignal import install
from gridsignal.mesh.cards import CardStatus
from gridsignal.mesh.messages import MessageKind, read_jsonl
from gridsignal.mesh.scenarios import SCENARIO_DIR

WAVE = SCENARIO_DIR / "install_wave.yaml"


def scenario() -> install.InstallScenario:
    return install.load_install(WAVE)


def test_the_wave_registers_signed_units_and_turns_the_rest_away():
    result = install.run_install_wave(scenario(), trace=None)
    m = result.metrics

    assert m.arrived == 400
    assert m.registered + m.rejected_cards == m.arrived
    assert m.rejected_cards > 0
    rejected = {
        msg.payload["unit"]
        for msg in result.bus.of_kind(MessageKind.REJECT)
        if "unit" in msg.payload
    }
    assert len(rejected) == m.rejected_cards
    for unit in (f"NEW-{i:04d}" for i in range(1, m.arrived + 1)):
        want = CardStatus.REJECTED if unit in rejected else CardStatus.VERIFIED
        assert result.registry.status(unit) is want
    assert any(
        "failed the installer check" in msg.summary
        for msg in result.bus.of_kind(MessageKind.REJECT)
    )


def test_a_probationary_unit_advertises_no_biddable_capacity():
    card = install.new_unit_card("NEW-0001", "LZ_HOUSTON", biddable=False)
    assert card.capabilities["kw_available"] == 0.0
    assert card.capabilities["probation"] == 1.0

    cleared = install.new_unit_card("NEW-0001", "LZ_HOUSTON", biddable=True)
    assert cleared.capabilities["kw_available"] == install.NEW_UNIT_KW
    assert cleared.capabilities["probation"] == 0.0


def test_no_award_ever_reaches_an_unverified_or_probationary_unit():
    result = install.run_install_wave(scenario(), trace=None)
    m = result.metrics

    assert m.awards_to_unverified == 0
    assert m.awards_to_probation == 0
    awarded = {
        award.agent_id
        for award_set in result.coordinator.awards.values()
        for award in award_set.awards
    }
    for agent_id in awarded:
        assert result.registry.status(agent_id) is CardStatus.VERIFIED
        assert result.registry.card(agent_id).capability("probation") == 0.0


def test_units_that_fail_the_health_gate_never_become_eligible():
    result = install.run_install_wave(scenario(), trace=None)
    m = result.metrics

    assert m.failed_health > 0
    assert m.promoted + m.failed_health == m.registered
    failed = [
        msg.payload["unit"]
        for msg in result.bus.of_kind(MessageKind.ESCALATION)
        if "probation health check" in msg.summary
    ]
    assert len(failed) == m.failed_health
    for unit in failed:
        assert result.registry.card(unit).capability("probation") == 1.0
        assert result.registry.card(unit).capability("kw_available") == 0.0


def test_new_units_do_win_work_once_they_are_eligible():
    result = install.run_install_wave(scenario(), trace=None)
    m = result.metrics

    assert m.new_unit_awards > 0
    assert m.new_unit_kw > 0
    assert m.time_to_first_eligible_award_s is not None
    # No unit can be eligible before its own probation check finishes.
    assert m.time_to_first_eligible_award_s >= scenario().health_check_s


def test_the_wave_never_takes_capacity_away_from_an_existing_commitment():
    result = install.run_install_wave(scenario(), trace=None)
    assert result.metrics.existing_kw_lost == 0.0


def test_join_throughput_tracks_the_scenario_rate():
    slow = replace(scenario(), units_per_hour=50.0, joining=100)
    fast = replace(scenario(), units_per_hour=200.0, joining=100)

    assert install.run_install_wave(slow, trace=None).metrics.joins_per_hour < (
        install.run_install_wave(fast, trace=None).metrics.joins_per_hour
    )


def test_the_run_is_deterministic_and_writes_a_replayable_trace(tmp_path):
    path = install.trace_path(scenario(), directory=tmp_path)
    first = install.run_install_wave(scenario(), trace=path)
    second = install.run_install_wave(scenario(), trace=None)

    assert first.metrics == second.metrics
    trace = read_jsonl(path)
    assert len(trace) == len(first.bus.messages)


def test_ten_thousand_device_install_wave_benchmark(capsys):
    big = replace(scenario(), existing=10_000, joining=400)

    started = time.perf_counter()
    result = install.run_install_wave(big, trace=None)
    elapsed_ms = (time.perf_counter() - started) * 1_000

    m = result.metrics
    with capsys.disabled():
        print(
            f"\n  install-wave benchmark: {m.arrived:,} units joining a "
            f"{m.existing_agents:,}-device mesh in {elapsed_ms:.0f} ms — "
            f"{m.promoted:,} eligible at {m.joins_per_hour:,.0f} joins/h simulated, "
            f"first eligible award at {m.time_to_first_eligible_award_s}s, "
            f"{m.awards_to_unverified} awards to unverified units"
        )
    assert elapsed_ms < 30_000
    assert m.awards_to_unverified == 0
    assert m.existing_kw_lost == 0.0
