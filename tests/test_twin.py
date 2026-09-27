"""GridSignal Twin: real ERCOT data, the price world model, the fleet simulation, the planner."""

import time

import numpy as np
import pandas as pd
import pytest

from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.twin import planner, stress
from gridsignal.twin.data import ZONES, frame_to_days, load_days
from gridsignal.twin.sim import FleetAssumptions, draw_days, exportable_kw, run_policy, share
from gridsignal.twin.validate import best_window_value, year_metrics
from gridsignal.twin.world import PriceWorldModel, bootstrap_baseline, squash, unsquash


@pytest.fixture(scope="module")
def days():
    return load_days()


@pytest.fixture(scope="module")
def model(days):
    return PriceWorldModel().fit(days)


def test_real_history_is_complete(days):
    # Five calendar years, minus the ten daylight-saving days that are not 96 intervals.
    assert days.dates.min() == pd.Timestamp("2021-01-01")
    assert days.dates.max() == pd.Timestamp("2025-12-31")
    assert 1815 <= len(days) <= 1826
    assert days.prices.shape[1:] == (3, 96)


def test_known_real_events_are_in_the_data(days):
    uri = days.between("2021-02-15", "2021-02-18").prices
    assert uri.max() > 8_000  # Winter Storm Uri: ERCOT's $9,000 cap binding
    assert np.median(days.between("2025-01-01", "2025-12-31").prices) < 40


def test_dst_days_are_dropped_not_stretched():
    idx = pd.date_range("2025-03-08", "2025-03-11", freq="15min", tz="US/Central", inclusive="left")
    f = pd.DataFrame(
        [(t, z, 30.0) for t in idx for z in ZONES], columns=["Interval Start", "Location", "SPP"]
    )
    d = frame_to_days(f)
    assert pd.Timestamp("2025-03-09") not in d.dates  # 92 intervals
    assert len(d) == 2


def test_naive_timestamps_are_rejected():
    f = pd.DataFrame(
        {
            "Interval Start": pd.date_range("2025-01-01", periods=96, freq="15min"),
            "Location": "LZ_HOUSTON",
            "SPP": 1.0,
        }
    )
    with pytest.raises(ValueError, match="timezone"):
        frame_to_days(f)


def test_squash_round_trips_negative_and_capped_prices():
    p = np.array([-250.0, -5.0, 0.0, 25.0, 5000.0, 9000.0])
    assert np.allclose(unsquash(squash(p)), p)


def test_same_seed_same_world(model):
    months = np.full(30, 8)
    a = model.sample(months, np.random.default_rng(3), regime=2025)
    b = model.sample(months, np.random.default_rng(3), regime=2025)
    assert np.array_equal(a, b)
    assert a.shape == (30, 3, 96)
    assert np.isfinite(a).all()


def test_generated_days_are_new_not_copies(model, days):
    sim = model.sample(np.full(50, 7), np.random.default_rng(4), regime=2025, level_risk=False)
    real = days.prices.reshape(len(days), -1)
    nearest = np.min(np.abs(sim.reshape(50, -1)[:, None, :] - real[None]).mean(-1), axis=1)
    assert (nearest > 0.5).all()  # no generated day equals any real day


def test_month_ordering_follows_real_history(model, days):
    # August 2023 was scarce (best-window mean ~$1,185/MWh) and April 2023 calm (~$58).
    # In 2025 April beat August. The model must follow each year, not a rule of thumb.
    rng = np.random.default_rng(5)

    def med(m, y):
        return np.median(
            best_window_value(model.sample(np.full(300, m), rng, regime=y, level_risk=False)[:, 0])
        )

    assert med(8, 2023) > 3 * med(4, 2023)

    def real(m, y):
        sel = (days.months == m) & (days.dates.year == y)
        return np.median(best_window_value(days.prices[sel][:, 0]))

    assert (med(8, 2023) > med(4, 2023)) == (real(8, 2023) > real(4, 2023))


def test_evening_peak_is_learned_not_imposed(model):
    sim = model.sample(np.full(365, 8), np.random.default_rng(6), regime=2025)
    assert year_metrics(sim)["evening_peak_share"] > 0.4


def test_regime_changes_the_world(model):
    months = np.repeat(np.arange(1, 13), 30)
    calm = [
        year_metrics(model.sample(months, np.random.default_rng(i), regime=2025))["days_over_250"]
        for i in range(8)
    ]
    wild = [
        year_metrics(model.sample(months, np.random.default_rng(i), regime=2023))["days_over_250"]
        for i in range(8)
    ]
    assert np.mean(wild) > np.mean(calm)


def test_scarcity_knob_only_touches_spikes(model):
    months = np.full(60, 8)
    a = model.sample(months, np.random.default_rng(8), regime=2023)
    b = model.sample(months, np.random.default_rng(8), regime=2023, scarcity=0.5)
    low = a <= 100
    assert np.allclose(a[low], b[low])
    assert (b[~low] < a[~low]).all()
    with pytest.raises(ValueError):
        model.sample(months, np.random.default_rng(8), scarcity=-1)


def test_unknown_regime_is_an_error(model):
    with pytest.raises(ValueError, match="unknown regime"):
        model.sample(np.full(3, 1), np.random.default_rng(0), regime=1999)


def test_bootstrap_only_replays_real_days(days):
    out = bootstrap_baseline(days, np.full(5, 2), np.random.default_rng(1))
    flat = days.prices.reshape(len(days), -1)
    for d in out.reshape(5, -1):
        assert (np.abs(flat - d).sum(1) == 0).any()


def test_fit_is_fast_and_explains_most_variance(days):

    t = time.time()
    m = PriceWorldModel().fit(days)
    assert time.time() - t < 5
    assert m.explained_ > 0.8


A = FleetAssumptions(homes=300)


def _day(hot=False, n=40, **kw):
    a = FleetAssumptions(homes=300, **kw)
    return a, draw_days(a, np.full(n, hot), 8, np.random.default_rng(1))


def test_export_is_home_first_and_reserve_protected():
    # 10 kWh available, 5 kWh reserve, 2 h left: at most 2.5 kW, minus 1 kW of house.
    assert exportable_kw(10.0, 1.0, 10.0, 5.0, 2.0, 1.0) == pytest.approx(1.5)
    assert exportable_kw(10.0, 1.0, 4.0, 5.0, 2.0, 1.0) == 0.0  # below reserve
    assert exportable_kw(10.0, 0.5, 100.0, 5.0, 2.0, 1.0) == pytest.approx(4.0)  # derated


def test_share_respects_zone_and_headroom():
    zone = np.array([0, 0, 1])
    head = np.array([[3.0, 1.0, 5.0]])
    out = share(np.array([[2.0, 10.0, 0.0]]), head, zone)
    assert out[0].tolist() == pytest.approx([1.5, 0.5, 5.0])  # zone 1 capped at headroom


@pytest.mark.parametrize("policy", ["naive", "gridsignal", "gridsignal_auto"])
def test_no_policy_exports_below_reserve(policy):
    a, day = _day(hot=True, device_hazard_per_h=0.05)
    o = run_policy(policy, day, a, 1.0, 8)
    assert o.min_reserve_margin_kwh >= -1e-6


def test_no_failures_means_every_policy_keeps_the_commitment():
    a, day = _day(device_hazard_per_h=0.0, feeder_event_p=0.0, feeder_event_p_hot=0.0)
    for p in ("naive", "gridsignal", "gridsignal_auto"):
        r = run_policy(p, day, a, 0.9, 8).zone_interval_ratio
        assert r.min() >= 0.999


def test_recovery_beats_naive_on_the_same_failures():
    a, day = _day(hot=True, n=200, device_hazard_per_h=0.02)
    naive = run_policy("naive", day, a, 0.8, 8)
    gs = run_policy("gridsignal", day, a, 0.8, 8)
    auto = run_policy("gridsignal_auto", day, a, 0.8, 8)
    assert gs.delivered_kw.sum() > naive.delivered_kw.sum()
    assert auto.delivered_kw.sum() >= gs.delivered_kw.sum()


def test_nobody_can_beat_physics_at_full_commitment():
    # Committing 100% of headroom leaves no spare: recovery has nothing to move.
    a, day = _day(hot=True, n=100, device_hazard_per_h=0.05)
    n = run_policy("naive", day, a, 1.0, 8).delivered_kw.sum()
    g = run_policy("gridsignal_auto", day, a, 1.0, 8).delivered_kw.sum()
    assert g == pytest.approx(n, rel=1e-6)


def test_unknown_policy():
    a, day = _day(n=2)
    with pytest.raises(ValueError):
        run_policy("yolo", day, a, 0.8, 8)


@pytest.fixture(scope="module")
def eng():
    return ControlRoomEngine()


def test_exportable_kw_matches_gridsignal(eng):
    hours = eng._hours_left()
    checked = 0
    for d in eng.mine:
        if not d.is_dispatchable:
            continue
        trust = 0.5 if d.status.value == "degraded" else 1.0
        reserve = d.capacity_kwh * eng.reserve_fraction
        mine = exportable_kw(d.power_kw, trust, d.available_kwh, reserve, hours, d.home_load_kw)
        assert mine == pytest.approx(eng.exportable_kw(d), abs=0.002), d.device_id
        checked += 1
    assert checked >= 30


def test_share_matches_gridsignal_allocation(eng):
    head = {d.device_id: eng.exportable_kw(d) for d in eng.mine}
    pool = [d for d in eng.mine if head[d.device_id] > 0]
    target = min(eng.grid_event.target_kw, sum(head.values()))
    eng._share(pool, target, head)
    theirs = np.array([d.assigned_kw for d in pool])
    ours = share(
        np.array([[target, 0, 0]]),
        np.array([[head[d.device_id] for d in pool]]),
        np.zeros(len(pool), int),
    )[0]
    # GridSignal floors to the cent and hands out leftover cents; we keep full precision.
    assert np.abs(theirs - ours).max() <= 0.011
    assert theirs.sum() == pytest.approx(ours.sum(), abs=0.01)


def test_stress_is_paired_and_reproducible(model, days):
    a = FleetAssumptions(homes=200)
    kw = dict(years=1, commit_ratios=(0.7, 1.0), assumptions=a, world_kw={"regime": 2025})
    r1 = stress.run(model, days, **kw)
    r2 = stress.run(model, days, **kw)
    assert [x.row() for x in r1] == [x.row() for x in r2]
    by = {(x.policy, x.commit_ratio): x for x in r1}
    # Same days, same failures: at 100% nobody has spare, so all policies tie.
    assert by[("naive", 1.0)].shortfall_mwh_per_year == pytest.approx(
        by[("gridsignal_auto", 1.0)].shortfall_mwh_per_year
    )
    assert by[("gridsignal_auto", 0.7)].kept_day_rate >= by[("naive", 0.7)].kept_day_rate
    for x in r1:
        assert x.min_reserve_margin_kwh >= -1e-6


def test_window_is_planned_from_history_only(days):
    train = days.between("2021-01-01", "2024-12-31")
    w = stress.window_start(train, np.array([8, 1]))
    assert 60 <= w[0] <= 84  # an evening window in August


def test_real_day_mode(model, days):
    real = days.between("2025-08-01", "2025-08-31")
    r = stress.run(
        model,
        days.between("2021-01-01", "2024-12-31"),
        real=real,
        assumptions=FleetAssumptions(homes=200),
        commit_ratios=(0.8,),
    )
    assert r[0].days == len(real)


def test_max_safe_ratio():
    def mk(p, c, k):
        return stress.StressResult(p, c, 1, k, 0, 0, 0, 0, 0, 0, 0, 0)

    res = [mk("a", 0.7, 0.995), mk("a", 0.8, 0.991), mk("a", 0.9, 0.95)]
    assert stress.max_safe_ratio(res, "a") == 0.8
    assert stress.max_safe_ratio(res, "b") == 0.0


# ------------------------------------------------------------------ planner


def test_the_planner_reads_the_fleet_the_control_room_is_running(eng):
    a = planner.assumptions_from_engine(eng, homes=500)
    mine = [d for d in eng.mine if d.zone in planner.TWIN_ZONE]
    assert a.homes == 500
    assert sum(a.zone_share) == pytest.approx(1.0)
    assert a.large_share == pytest.approx(
        sum(d.unit_type.value == "base_core" for d in mine) / len(mine)
    )
    assert a.soc_mean == pytest.approx(np.mean([d.state_of_charge for d in mine]))
    assert a.reserve_fraction == eng.reserve_fraction
    assert a.degraded_share > 0  # the demo fleet starts with two degraded units


def test_the_partner_tenants_units_are_never_planned_as_ours():
    eng = ControlRoomEngine()
    west = [d for d in eng.devices if d.zone == "LZ_WEST"]
    assert west and all(not d.is_operator_controlled for d in west)
    assert "LZ_WEST" not in planner.TWIN_ZONE


def test_a_small_plan_is_paired_and_honest():
    plan = planner.plan_commitment(ControlRoomEngine(), years=1, homes=300, ratios=(0.6, 0.8, 1.0))
    assert set(plan.safe_ratio) == set(stress.POLICIES)
    assert plan.min_reserve_margin_kwh >= -1e-6
    for r in (0.6, 0.8):
        assert plan.curve["gridsignal_auto"][r] >= plan.curve["naive"][r]
    # Committing everything leaves no spare to recover with: every policy ties.
    assert plan.curve["gridsignal_auto"][1.0] == pytest.approx(plan.curve["naive"][1.0])
    assert 0.0 <= plan.kept_at("gridsignal", 0.7) <= 1.0
    assert "Today's plan commits 77%" in plan.headline(0.77)


def test_unknown_scenarios_are_refused():
    with pytest.raises(ValueError, match="unknown scenario"):
        planner.plan_commitment(scenario="2099-like")


def test_the_shipped_study_matches_the_claims_the_app_makes():
    import json

    from gridsignal.twin.data import TWIN_DIR

    s = json.loads((TWIN_DIR / "stress.json").read_text())
    head = s["scenarios"]["2025-like (latest year)"]
    assert s["years_per_scenario"] >= 30
    assert head["safe_ratio"]["gridsignal"] == 0.0
    assert 0.7 <= head["safe_ratio"]["gridsignal_auto"] <= 0.8
    worst = min(s["sensitivity"], key=lambda x: x["gridsignal_auto_safe"])
    assert worst["assumption"] == "feeder_event_share" and worst["gridsignal_auto_safe"] == 0.5
