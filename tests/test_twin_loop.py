"""The three loops around the Planner: live ERCOT feed, learned failure rates, correlated risk.

No test here touches the network: the feed is exercised against a fake ERCOT page in the
exact HTML layout ercot.com serves, and a temporary store.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from gridsignal import live
from gridsignal.control_room.engine import ControlRoomEngine
from gridsignal.twin import feed, learn, planner, risk
from gridsignal.twin.data import ZONES
from gridsignal.twin.sim import FleetAssumptions

# ------------------------------------------------------------------ live feed


def ercot_page(day: date, intervals: int = 96, base: float = 30.0) -> str:
    cols = ["HB_HOUSTON", *ZONES, "LZ_WEST"]
    head = "".join(f"<th>{c}</th>" for c in ["Oper Day", "Interval Ending", *cols])
    rows = []
    for i in range(1, intervals + 1):
        end = i * 15
        hhmm = f"{end // 60:02d}{end % 60:02d}"
        cells = "".join(f"<td>{base + i + k:.2f}</td>" for k in range(len(cols)))
        rows.append(f"<tr><td>{day:%m/%d/%Y}</td><td>{hhmm}</td>{cells}</tr>")
    return f"<html><table><tr>{head}</tr>{''.join(rows)}</table></html>"


class FakeErcot:
    def __init__(self, today: date, today_intervals: int = 40, fail: set[date] | None = None):
        self.today, self.today_intervals, self.fail = today, today_intervals, fail or set()
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        ymd = url.rsplit("/", 1)[-1][:8]
        d = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
        if d in self.fail:
            raise live.LiveDataError("could not reach ERCOT (ConnectError)")
        n = self.today_intervals if d == self.today else 96
        return ercot_page(d, n)


def test_parse_day_reads_all_three_zones_in_central_time():
    frame = feed.parse_day(ercot_page(date(2026, 9, 1)))
    assert set(frame["Location"]) == set(ZONES)
    assert len(frame) == 96 * 3
    first = frame["Interval Start"].min()
    assert str(first.tz) == "US/Central" and first.hour == 0 and first.minute == 0


def test_refresh_backfills_then_only_rereads_today(tmp_path):
    store = tmp_path / "live.parquet"
    today = date(2026, 1, 5)
    fake = FakeErcot(today)
    s = feed.refresh(store, today=today, get=fake, first_day=date(2026, 1, 1))
    assert s.days_fetched == 5 and not s.errors
    assert s.last_full_day == "2026-01-04"
    assert s.intervals_today == 40
    assert s.latest_interval == "2026-01-05 10:00"

    fake.calls.clear()
    fake.today_intervals = 41  # fifteen minutes later
    s = feed.refresh(store, today=today, get=fake, first_day=date(2026, 1, 1))
    assert len(fake.calls) == 1, "settled days must not be fetched again"
    assert s.intervals_today == 41 and s.revised_intervals == 0


def test_a_failed_day_is_reported_and_retried(tmp_path):
    store = tmp_path / "live.parquet"
    today = date(2026, 1, 4)
    bad = date(2026, 1, 2)
    s = feed.refresh(
        store, today=today, get=FakeErcot(today, fail={bad}), first_day=date(2026, 1, 1)
    )
    assert len(s.errors) == 1 and "2026-01-02" in s.errors[0]
    fake = FakeErcot(today)
    feed.refresh(store, today=today, get=fake, first_day=date(2026, 1, 1))
    fetched = {u.rsplit("/", 1)[-1][:8] for u in fake.calls}
    assert fetched == {"20260102", "20260104"}


def test_nothing_reachable_is_an_error_not_an_empty_success(tmp_path):
    today = date(2026, 1, 2)
    with pytest.raises(feed.FeedError):
        feed.refresh(
            tmp_path / "live.parquet",
            today=today,
            get=FakeErcot(today, fail={date(2026, 1, 1), today}),
            first_day=date(2026, 1, 1),
        )


def test_live_days_join_the_history_without_duplicates(tmp_path):
    store = tmp_path / "live.parquet"
    today = date(2026, 1, 4)
    feed.refresh(store, today=today, get=FakeErcot(today), first_day=date(2026, 1, 1))
    days = feed.live_days(store)
    assert len(days) == 3  # today is partial, so it is not a day yet
    both = feed.combined_days(store)
    assert both.dates.is_unique and both.dates.is_monotonic_increasing
    assert both.dates.max() == pd.Timestamp("2026-01-03")


def test_the_bundled_live_store_is_real_2026_ercot():
    days = feed.live_days()
    assert days is not None and len(days) >= 200
    assert days.dates.min() == pd.Timestamp("2026-01-01")
    assert (days.dates.year == 2026).all()
    assert np.isfinite(days.prices).all() and days.prices.max() > 250


def test_the_live_scenario_is_this_years_regime():
    menu = planner.scenarios(live=True)
    assert planner.LIVE_SCENARIO in menu
    assert menu[planner.LIVE_SCENARIO]["regime"] == 2026
    assert planner.LIVE_SCENARIO not in planner.scenarios(live=False)
    # The frozen study's model never sees 2026, so docs/twin reproduces.
    assert 2026 not in planner.world_model().regimes_
    assert 2026 in planner.world_model(live=True).regimes_


def test_today_is_checked_against_the_models_band():
    model = planner.world_model()
    actual = model.sample(np.array([9]), np.random.default_rng(3), regime=2025, level_risk=False)[0]
    check = feed.check_today(model, actual[:, :40], month=9)
    assert check.intervals == 40
    assert check.inside_share > 0.6
    silly = feed.check_today(model, np.full((3, 40), 50_000.0), month=9)
    assert silly.inside_share == 0 and "missing something" in silly.verdict


# ------------------------------------------------------------------ learning loop


@pytest.fixture(scope="module")
def fleet():
    eng = ControlRoomEngine(fleet_size=1_000)
    groups = risk.device_groups(eng)
    return {
        "eng": eng,
        "feeders": {k: v["feeder"] for k, v in groups.items()},
        "rings": {k: v["ring"] for k, v in groups.items()},
        "zones": {d.device_id: d.zone for d in eng.mine},
    }


def _learn(fleet, truth, days):
    lines = learn.synthetic_history(
        list(fleet["eng"].mine),
        days=days,
        truth=truth,
        feeders=fleet["feeders"],
        ring_of=fleet["rings"],
    )
    frame, rejected = learn.frame_from_lines(lines)
    assert rejected == 0
    return learn.learn(frame, fleet["zones"], fleet["feeders"])


@pytest.mark.slow
def test_the_loop_recovers_known_rates(fleet):
    truth = learn.SyntheticTruth()
    cal = _learn(fleet, truth, days=120)
    e = cal.estimates
    assert e["device_hazard_per_h"].low <= truth.device_hazard_per_h * 1.05
    assert e["device_hazard_per_h"].high >= truth.device_hazard_per_h * 0.9
    assert abs(e["degraded_share"].learned - truth.degraded_share) < 0.005
    # Feeder outages: rate within its range, share near what one quadrant holds.
    assert e["feeder_event_rate"].low <= truth.feeder_event_p <= e["feeder_event_rate"].high * 1.2
    assert 0.12 < e["feeder_event_share"].learned < 0.25
    kinds = {c.key.split()[0] for c in cal.clusters}
    assert {"feeder", "gateway"} <= kinds


def test_no_failures_in_the_history_moves_hazard_down_but_keeps_the_prior(fleet):
    quiet = learn.SyntheticTruth(
        device_hazard_per_h=0.0, feeder_event_p=0.0, gateway_event_p=0.0, degraded_share=0.0
    )
    cal = _learn(fleet, quiet, days=10)
    e = cal.estimates
    assert 0 < e["device_hazard_per_h"].learned < FleetAssumptions().device_hazard_per_h
    assert e["feeder_event_share"].learned == FleetAssumptions().feeder_event_share
    assert any("lean on the assumption" in n for n in cal.notes)


def test_a_thin_history_barely_moves_the_assumption(fleet):
    thin = _learn(fleet, learn.SyntheticTruth(), days=3).estimates["device_hazard_per_h"]
    thick = _learn(fleet, learn.SyntheticTruth(), days=40).estimates["device_hazard_per_h"]
    assert (thin.high - thin.low) > (thick.high - thick.low)


def test_learned_rates_replace_only_failure_behaviour(fleet):
    cal = _learn(fleet, learn.SyntheticTruth(), days=20)
    base = planner.assumptions_from_engine(fleet["eng"])
    after = cal.apply(base)
    assert after.device_hazard_per_h == cal.estimates["device_hazard_per_h"].learned
    assert after.feeder_event_share == cal.estimates["feeder_event_share"].learned
    for kept in ("homes", "zone_share", "soc_mean", "reserve_fraction", "home_load_kw"):
        assert getattr(after, kept) == getattr(base, kept)
    ratio = after.feeder_event_p_hot / after.feeder_event_p
    assert ratio == pytest.approx(base.feeder_event_p_hot / base.feeder_event_p)


def test_the_importer_guards_the_loop():
    good = (
        '{"device_id": "BAT-001", "ts": "2026-09-01T22:00:00Z", "soc_kwh": 10, "power_kw": 1,'
        ' "status": "online", "firmware": "2.4.1", "gateway": "GW-01"}'
    )
    frame, rejected = learn.frame_from_lines([good, "{not json", '{"device_id": "BAT-002"}'])
    assert len(frame) == 1 and rejected == 2
    with pytest.raises(ValueError, match="known zone"):
        learn.learn(frame, {"BAT-999": "LZ_NORTH"})


def test_a_drop_across_a_long_gap_is_not_counted_as_observed():
    rows = [
        ("BAT-001", "2026-09-01T22:00:00Z", "online"),
        ("BAT-001", "2026-09-02T22:00:00Z", "offline"),  # a day later: not observed failing
        ("BAT-002", "2026-09-01T22:00:00Z", "online"),
        ("BAT-002", "2026-09-01T22:15:00Z", "offline"),
    ]
    frame = pd.DataFrame(rows, columns=["device_id", "ts", "status"])
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame["gateway"] = frame["firmware"] = ""
    cal = learn.learn(frame, {"BAT-001": "LZ_NORTH", "BAT-002": "LZ_NORTH"})
    assert cal.failures == 1
    assert cal.exposure_h == pytest.approx(0.25)


# ------------------------------------------------------------------ correlated risk


def test_every_device_sits_on_one_feeder_and_one_ring():
    eng = ControlRoomEngine(fleet_size=1_000)
    groups = risk.groups(eng)
    for kind in ("feeder", "gateway ring"):
        seen = [d for g in groups if g.kind == kind for d in g.devices]
        assert (
            len(seen) == len(set(seen)) == len([d for d in eng.mine if d.status.value != "offline"])
        )


def test_rings_match_the_control_rooms_own_gateway_ring():
    from gridsignal.fleet import gateway_ring

    eng = ControlRoomEngine(fleet_size=1_000)
    ring = next(g for g in risk.groups(eng) if g.key == risk.ring_of("BAT-042"))
    mine = {d.device_id for d in eng.mine}
    assert set(ring.devices) == {d for d in gateway_ring("BAT-042", 1_000) if d in mine}


def test_the_playbook_decides_person_or_automatic():
    eng = ControlRoomEngine(fleet_size=48)
    before = risk.summary(risk.groups(eng))
    assert before[risk.PLAYBOOK] == 0, "no playbook approved: nothing recovers without a person"
    eng.approve_playbook("M. Alvarez (Fleet Operator)")
    groups = risk.groups(eng)
    auto = [g for g in groups if g.status == risk.PLAYBOOK]
    assert auto and all(
        g.lost_kw <= eng.playbook.max_kw and len(g.devices) <= eng.playbook.max_devices
        for g in auto
    )
    for g in groups:
        if g.status == risk.PERSON:
            assert g.playbook_refusal is not None


def test_uncovered_means_the_zone_really_cannot_absorb_it():
    eng = ControlRoomEngine(fleet_size=1_000)
    for g in risk.groups(eng):
        if g.status == risk.UNCOVERED:
            assert g.uncovered_kw > 0
        assert 0 <= g.covering_ratio <= 1


def test_lowering_the_commitment_covers_the_feeders_the_map_flags():
    eng = ControlRoomEngine(fleet_size=1_000)
    worst = max(
        (g for g in risk.groups(eng) if g.kind == "feeder"), key=lambda g: g.worst_zone_share
    )
    assert worst.status == risk.UNCOVERED
    eng.set_commitment(worst.covering_ratio - 0.02, "M. Alvarez (Fleet Operator)", basis="test")
    again = next(g for g in risk.groups(eng) if g.key == worst.key)
    assert again.uncovered_kw == pytest.approx(0, abs=0.05)


def test_history_counts_how_often_a_group_failed_together(fleet):
    cal = _learn(fleet, learn.SyntheticTruth(), days=30)
    groups = risk.groups(fleet["eng"], cal)
    total_seen = sum(g.seen_together for g in groups)
    keyed = sum(1 for c in cal.clusters if c.key.split(" ", 1)[0] in ("feeder", "gateway"))
    assert total_seen == keyed > 0


def test_the_fleet_scale_feeder_share_is_what_the_stress_test_assumes():
    eng = ControlRoomEngine(fleet_size=1_000)
    share = risk.worst_feeder_share(risk.groups(eng))
    assert abs(share - FleetAssumptions().feeder_event_share) <= 0.05
