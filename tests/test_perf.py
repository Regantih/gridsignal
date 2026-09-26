"""Speed and scale: the benchmark itself, and a guard that the hot paths stay fixed.

The absolute budget is deliberately loose (CI runners vary by several times), so it
only fires on an accidental O(n^2). The guard that matters is relative: the same work,
in the same process, against the implementations Q9 replaced.
"""

import pytest

from gridsignal import perf, perf_before
from gridsignal.control_room import ControlRoomEngine
from gridsignal.fleet import FOCUS_DEVICE_ID
from gridsignal.mesh.cards import AgentCard, AgentKind, Health, derived_signing_key
from gridsignal.prices import load_scenario

#: Fleet-scale benchmarks: the slow half of the suite.
pytestmark = pytest.mark.slow

GUARD_DEVICES = 2_000
GUARD_REPEATS = 3
#: One whole pass at 2,000 devices. Roughly 0.1 s here; a slow shared runner gets 6 s.
GUARD_BUDGET_MS = 6_000.0
#: The signing and allocation work must stay clearly ahead of the code it replaced.
MIN_SPEEDUP = 1.15


def test_percentile_interpolates_and_survives_short_samples():
    assert perf.percentile([], 0.5) == 0.0
    assert perf.percentile([4.0], 0.95) == 4.0
    assert perf.percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    assert perf.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == pytest.approx(4.8)


def test_one_pass_times_every_stage_and_still_covers_the_call():
    run = perf.one_pass(500)
    assert set(run.timings_ms) == {
        "build fleet",
        "detect incident",
        "recover after approval",
        "publish signed cards",
        "heartbeat sweep",
        "negotiate one call",
    }
    assert all(ms > 0 for ms in run.timings_ms.values())
    assert run.devices == 500
    assert run.agents > run.devices  # gateway and zone agents too
    assert run.bids > 0 and run.awards > 0
    assert run.coverage_pct == pytest.approx(100.0, abs=0.5)


def test_the_report_carries_p50_p95_throughput_and_memory():
    result = perf.measure(500, repeats=2)
    assert result.peak_mib > 0
    for stage in result.stages:
        assert stage.p95_ms >= stage.p50_ms > 0
        assert stage.throughput_per_s > 0
    text = "\n".join(perf.lines([result]))
    assert "p50 ms" in text and "p95 ms" in text and "throughput" in text
    assert "peak heap" in text
    assert "no network" in text


def test_the_new_signature_still_covers_every_field():
    """Faster canonical bytes, same tamper surface: each field still signs."""
    key = derived_signing_key(7)
    card = AgentCard(
        agent_id="DEV-0001",
        kind=AgentKind.BATTERY,
        zone="LZ_HOUSTON",
        capabilities={"kw_available": 4.5, "kwh_available": 9.0},
        health=Health.HEALTHY,
        last_heartbeat_s=12,
        controller="base",
    ).signed(key)
    assert card.verifies(key)

    edits = [
        {"agent_id": "DEV-0002"},
        {"zone": "LZ_WEST"},
        {"capabilities": {"kw_available": 50.0, "kwh_available": 9.0}},
        {"health": Health.DEGRADED},
        {"last_heartbeat_s": 13},
        {"controller": "utility_partner"},
        {"kind": AgentKind.GATEWAY},
    ]
    for edit in edits:
        tampered = AgentCard(
            **{
                "agent_id": card.agent_id,
                "kind": card.kind,
                "zone": card.zone,
                "capabilities": card.capabilities,
                "health": card.health,
                "last_heartbeat_s": card.last_heartbeat_s,
                "controller": card.controller,
                "signature": card.signature,
                **edit,
            }
        )
        assert not tampered.verifies(key), edit


def test_the_new_signature_and_the_old_one_agree_on_what_differs():
    """Both serialisations separate the same cards; only the bytes got cheaper."""
    key = derived_signing_key(7)
    a = AgentCard(agent_id="DEV-1", kind=AgentKind.BATTERY, zone="LZ_HOUSTON", capabilities={})
    b = AgentCard(agent_id="DEV-1", kind=AgentKind.BATTERY, zone="LZ_WEST", capabilities={})
    assert (a.canonical() == b.canonical()) == (
        perf_before.canonical(a) == perf_before.canonical(b)
    )
    assert a.signed(key).signature == perf_before.signed(a, key).signature


def test_cached_dispatch_hours_match_the_uncached_allocation():
    """The cache is an allocation detail: every kW comes out where it did before."""
    trace = load_scenario("scarcity")
    fast = ControlRoomEngine(price_trace=trace, fleet_size=1_000)
    slow = ControlRoomEngine(price_trace=trace, fleet_size=1_000)
    with perf_before.slow_paths():
        slow.reset()

    for step in (0, 900, 1_800):
        if step:
            fast._tick(step)
            slow._tick(step)
            fast._allocate_dispatch()
            with perf_before.slow_paths():
                slow._allocate_dispatch()
        assert fast.remaining_hours() == pytest.approx(slow.remaining_hours())
        for left, right in zip(fast.devices, slow.devices, strict=True):
            assert left.assigned_kw == pytest.approx(right.assigned_kw)
            assert fast.discharge_headroom_kw(left) == pytest.approx(
                perf_before.discharge_headroom_kw(slow, right)
            )


def test_recovery_after_a_failure_is_unchanged_by_the_hot_path_fixes():
    trace = load_scenario("scarcity")
    fast = ControlRoomEngine(price_trace=trace, fleet_size=1_000)
    incident = fast.trigger_device_failure(FOCUS_DEVICE_ID)
    fast.approve_recovery()

    with perf_before.slow_paths():
        slow = ControlRoomEngine(price_trace=trace, fleet_size=1_000)
        was = slow.trigger_device_failure(FOCUS_DEVICE_ID)
        slow.approve_recovery()

    assert incident.dollars_at_risk == pytest.approx(was.dollars_at_risk)
    assert incident.dollars_recovered == pytest.approx(was.dollars_recovered)
    assert fast.snapshot().coverage_pct == pytest.approx(slow.snapshot().coverage_pct)


def test_benchmark_guard_one_pass_stays_within_budget():
    """Catches an accidental O(n^2): the whole pass, at 2,000 devices, in one budget."""
    result = perf.measure(GUARD_DEVICES, repeats=GUARD_REPEATS)
    assert result.total_p50_ms < GUARD_BUDGET_MS, "\n".join(perf.lines([result]))
    assert result.coverage_pct == pytest.approx(100.0, abs=0.5)


def test_benchmark_guard_the_hot_paths_stay_faster_than_what_they_replaced():
    """Machine-independent: the same stages, both implementations, same process."""
    after = perf.measure(GUARD_DEVICES, repeats=GUARD_REPEATS)
    before = perf.measure_before(GUARD_DEVICES, repeats=GUARD_REPEATS)
    speedups = {
        stage.name: was.p50_ms / stage.p50_ms
        for stage, was in zip(after.stages, before.stages, strict=True)
    }
    assert speedups["publish signed cards"] > MIN_SPEEDUP, speedups
    assert speedups["heartbeat sweep"] > MIN_SPEEDUP, speedups
    assert speedups["recover after approval"] > MIN_SPEEDUP, speedups
