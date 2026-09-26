"""Fleet-scale behaviour: the same outage, priced and recovered at 48 to 10,000 devices."""

import time

import pytest

from gridsignal.control_room import ControlRoomEngine
from gridsignal.control_room.engine import TARGET_KW_PER_DEVICE
from gridsignal.fleet import FOCUS_DEVICE_ID, GATEWAY_RING_SIZE, gateway_ring
from gridsignal.prices import load_scenario

# Generous enough to be stable on CI, tight enough to catch an accidental O(n^2).
RUNTIME_BUDGET_S = 5.0


def test_gateway_ring_scales_with_the_fleet():
    assert gateway_ring(FOCUS_DEVICE_ID, 48) == [FOCUS_DEVICE_ID]
    assert len(gateway_ring(FOCUS_DEVICE_ID, 1_000)) == 1_000 // GATEWAY_RING_SIZE
    ring = gateway_ring(FOCUS_DEVICE_ID, 10_000)
    assert ring[0] == FOCUS_DEVICE_ID
    assert len(ring) == 10_000 // GATEWAY_RING_SIZE
    assert len(set(ring)) == len(ring)


@pytest.mark.parametrize("fleet_size", [48, 1_000, 10_000])
def test_dollars_scale_with_the_fleet_and_recovery_still_covers_the_target(fleet_size):
    eng = ControlRoomEngine(fleet_size=fleet_size)
    assert eng.grid_event.target_kw == TARGET_KW_PER_DEVICE * fleet_size

    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    assert len(incident.cohort) == max(fleet_size // GATEWAY_RING_SIZE, 1)
    assert incident.dollars_at_risk > 0
    assert eng.snapshot().coverage_pct < 100.0  # nothing moves before approval

    eng.approve_recovery()
    snap = eng.snapshot()
    assert snap.unavailable == len(incident.cohort)
    assert snap.coverage_pct == pytest.approx(100.0, abs=0.5)
    assert incident.dollars_recovered > 0


def test_bigger_fleets_put_more_money_on_the_line():
    def at_risk(fleet_size: int) -> float:
        eng = ControlRoomEngine(fleet_size=fleet_size)
        return eng.trigger_device_failure(FOCUS_DEVICE_ID).dollars_at_risk

    small, large = at_risk(48), at_risk(10_000)
    assert large > 100 * small


def test_scarcity_prices_multiply_the_exposure_at_fleet_scale():
    normal = ControlRoomEngine(price_trace=load_scenario("normal"), fleet_size=10_000)
    scarcity = ControlRoomEngine(price_trace=load_scenario("scarcity"), fleet_size=10_000)

    calm = normal.trigger_device_failure(FOCUS_DEVICE_ID)
    tight = scarcity.trigger_device_failure(FOCUS_DEVICE_ID)

    assert tight.lost_kw == pytest.approx(calm.lost_kw, rel=0.01)
    assert tight.dollars_at_risk > 10 * calm.dollars_at_risk
    assert tight.dollars_at_risk > 1_000  # a number leadership cares about


def test_ten_thousand_device_failure_and_reallocation_is_fast(capsys):
    """Benchmark: detection plus reallocation across a 10,000-device fleet."""
    trace = load_scenario("scarcity")

    build_start = time.perf_counter()
    eng = ControlRoomEngine(price_trace=trace, fleet_size=10_000)
    build_s = time.perf_counter() - build_start

    detect_start = time.perf_counter()
    incident = eng.trigger_device_failure(FOCUS_DEVICE_ID)
    detect_s = time.perf_counter() - detect_start

    recover_start = time.perf_counter()
    eng.approve_recovery()
    recover_s = time.perf_counter() - recover_start

    with capsys.disabled():
        print(
            f"\n[benchmark] 10,000 devices | build {build_s * 1000:.0f} ms | "
            f"detect {detect_s * 1000:.0f} ms | approve+reallocate {recover_s * 1000:.0f} ms | "
            f"{len(incident.cohort)} devices out, ${incident.dollars_at_risk:,.2f} at risk, "
            f"${incident.dollars_recovered:,.2f} recovered"
        )

    assert build_s + detect_s + recover_s < RUNTIME_BUDGET_S
    assert eng.snapshot().coverage_pct == pytest.approx(100.0, abs=0.5)
