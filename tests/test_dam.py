"""The day-ahead plan and the real-time deviations layered on top of it."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from gridsignal import dam, holdout
from gridsignal.prices import dam_path_for, load_scenario
from gridsignal.signals import Signal


def curve(prices: list[float], date: str = "2026-09-22") -> pd.DataFrame:
    start = pd.date_range(f"{date} 00:00", periods=len(prices), freq="h")
    return pd.DataFrame(
        {"interval_start": start, "interval_end": start + pd.Timedelta("1h"), "spp": prices}
    )


def rtm(prices: list[float], date: str = "2026-09-22") -> pd.DataFrame:
    start = pd.date_range(f"{date} 00:00", periods=len(prices), freq="15min")
    return pd.DataFrame(
        {"interval_start": start, "interval_end": start + pd.Timedelta("15min"), "spp": prices}
    )


def test_plan_buys_the_cheapest_hours_and_sells_the_dearest_later_ones() -> None:
    plan = dam.hourly_plan(curve([10, 10, 90, 90]), charge_hours=2, export_hours=2)
    assert list(plan["planned"]) == [
        Signal.CHARGE.value,
        Signal.CHARGE.value,
        Signal.EXPORT.value,
        Signal.EXPORT.value,
    ]


def test_export_hours_always_follow_the_charge_hours() -> None:
    plan = dam.hourly_plan(curve([90, 90, 10, 10]), charge_hours=2, export_hours=2)
    charge = plan.index[plan["planned"] == Signal.CHARGE.value]
    export = plan.index[plan["planned"] == Signal.EXPORT.value]
    assert not len(export) or min(export) > max(charge)


def test_a_flat_day_is_planned_as_do_nothing() -> None:
    plan = dam.hourly_plan(curve([20, 21, 22, 23]), charge_hours=1, export_hours=1, min_spread=1.4)
    assert set(plan["planned"]) == {Signal.HOLD.value}


def test_alignment_broadcasts_each_hour_onto_its_four_intervals() -> None:
    plan = dam.hourly_plan(curve([10, 10, 90, 90]), charge_hours=2, export_hours=2)
    aligned = dam.align_to_intervals(plan, rtm([1.0] * 16)["interval_start"])
    assert len(aligned) == 16
    assert list(aligned["planned"][:4]) == [Signal.CHARGE.value] * 4
    assert list(aligned["planned"][-4:]) == [Signal.EXPORT.value] * 4
    assert aligned["dam_mwh"].iloc[-1] == 90


def test_intervals_with_no_day_ahead_hour_fall_back_to_hold() -> None:
    plan = dam.hourly_plan(curve([10, 10, 90, 90]), charge_hours=2, export_hours=2)
    aligned = dam.align_to_intervals(plan, rtm([1.0] * 24)["interval_start"])
    assert list(aligned["planned"][-4:]) == [Signal.HOLD.value] * 4


def test_a_real_time_spike_overrides_the_plan_on_the_interval_after_it_settles() -> None:
    prices = rtm([10.0] * 16)
    prices.loc[9, "spp"] = 4000.0
    prob = pd.Series([0.0] * 16)
    prob[10] = 1.0
    plan = dam.deviate_from_plan(
        prices, prob, curve([10, 10, 10, 10]), charge_hours=1, export_hours=1
    )
    assert plan.loc[10, "planned"] == Signal.HOLD.value
    assert plan.loc[10, "signal"] == Signal.EXPORT.value
    assert "sell into the spike" in plan.loc[10, "reason"]


def test_a_planned_charge_is_abandoned_when_real_time_runs_hot() -> None:
    prices = rtm([10.0] * 16)
    prices.loc[1, "spp"] = 500.0
    plan = dam.deviate_from_plan(
        prices,
        pd.Series([0.0] * 16),
        curve([10, 10, 90, 90]),
        charge_hours=2,
        export_hours=2,
        charge_ceiling=2.0,
    )
    assert plan.loc[1, "signal"] == Signal.CHARGE.value
    assert plan.loc[2, "signal"] == Signal.HOLD.value


def test_a_weak_planned_export_waits_but_the_last_one_still_sells() -> None:
    prices = rtm([10.0] * 8 + [1.0] * 8)
    plan = dam.deviate_from_plan(
        prices,
        pd.Series([0.0] * 16),
        curve([10, 10, 90, 90]),
        charge_hours=2,
        export_hours=2,
        export_floor=0.5,
    )
    exported = plan.index[plan["signal"] == Signal.EXPORT.value]
    # Interval 8 is decided on interval 7's print, which was still strong; every later
    # export sees the collapse and waits, except the last one of the day.
    assert list(exported) == [8, 15]


def test_deviation_needs_both_a_spike_forecast_and_a_price_gap() -> None:
    prices = rtm([10.0] * 16)
    prices.loc[8, "spp"] = 4000.0
    quiet = dam.deviate_from_plan(
        prices, pd.Series([0.0] * 16), curve([10, 10, 10, 10]), charge_hours=1, export_hours=1
    )
    assert quiet.loc[9, "signal"] == Signal.HOLD.value


def test_the_plan_never_reads_a_later_interval() -> None:
    """Truncating the day must not change any signal before the cut."""
    trace = load_scenario("scarcity")
    frame = trace.frame
    prob = pd.Series([0.6] * len(frame))
    full = dam.deviate_from_plan(frame, prob, trace.dam)
    cut = 60
    partial = dam.deviate_from_plan(frame.iloc[:cut], prob.iloc[:cut], trace.dam)
    assert list(full["signal"][:cut]) == list(partial["signal"][:cut])


def test_an_intervals_own_print_cannot_change_its_own_decision() -> None:
    """Rewrite one interval's settled price; the decision made before it must not move."""
    trace = load_scenario("scarcity")
    prob = pd.Series([0.6] * len(trace.frame))
    base = dam.deviate_from_plan(trace.frame, prob, trace.dam)

    changed = 0
    for i in (20, 40, 60, 80):
        tampered = trace.frame.copy()
        tampered.loc[i, "spp"] = 9_000.0
        plan = dam.deviate_from_plan(tampered, prob, trace.dam)
        assert plan.loc[i, "signal"] == base.loc[i, "signal"], i
        changed += int(plan.loc[i + 1, "signal"] != base.loc[i + 1, "signal"])
    assert changed, "a $9,000 print should still move the *next* interval's decision"


def test_the_same_interval_rule_is_still_reachable_for_the_published_number() -> None:
    """The old, optimistic rule survives behind a flag so it can be reported as such."""
    trace = load_scenario("scarcity")
    prob = pd.Series([0.6] * len(trace.frame))
    tampered = trace.frame.copy()
    tampered.loc[40, "spp"] = 9_000.0

    lookahead = dam.deviate_from_plan(tampered, prob, trace.dam, same_interval_price=True)
    assert lookahead.loc[40, "signal"] == Signal.EXPORT.value

    corrected = holdout.summarize(holdout.evaluate())
    as_first_scored = holdout.summarize(holdout.evaluate(same_interval_price=True))
    assert corrected.mean_uplift_usd <= as_first_scored.mean_uplift_usd


def test_signals_for_falls_back_to_the_real_time_policy_without_a_curve() -> None:
    prices = rtm([10.0] * 16)
    plan = dam.signals_for(prices, pd.Series([0.0] * 16), None)
    assert "planned" not in plan.columns
    assert set(plan["signal"]) <= {s.value for s in Signal}


def test_spike_probability_must_line_up_with_prices() -> None:
    with pytest.raises(ValueError, match="spike_prob"):
        dam.deviate_from_plan(rtm([10.0] * 16), pd.Series([0.0] * 4), curve([10, 10, 90, 90]))


def test_every_bundled_day_ships_a_day_ahead_curve_with_provenance() -> None:
    paths = [
        *(p for p in holdout.holdout_paths()),
        *(p for p in holdout.tuning_paths()),
    ]
    assert paths
    for path in paths:
        meta = json.loads(dam_path_for(path).with_suffix(".json").read_text())
        assert meta["market"] == "DAY_AHEAD_HOURLY"
        assert meta["location"] == "LZ_HOUSTON"
        assert meta["units"] == "$/MWh"
        assert meta["intervals"] == 24
        assert meta["source"].startswith("https://www.ercot.com/")


def test_scenario_days_carry_their_day_ahead_curve() -> None:
    for name in ("normal", "scarcity"):
        trace = load_scenario(name)
        assert trace.dam is not None
        assert len(trace.dam) == 24
        assert trace.dam["interval_start"].dt.date.nunique() == 1
