"""Dollars captured by the signals versus the naive fixed schedule."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from gridsignal import backtest, pipeline
from gridsignal.signals import Signal


def trace(prices: list[float], start_hour: int = 0) -> pd.DataFrame:
    start = datetime(2023, 9, 6, start_hour)
    return pd.DataFrame(
        {
            "interval_start": [start + timedelta(minutes=15 * i) for i in range(len(prices))],
            "interval_end": [start + timedelta(minutes=15 * (i + 1)) for i in range(len(prices))],
            "spp": prices,
        }
    )


def plan_of(actions: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"signal": actions})


def test_charging_costs_money_and_exporting_earns_it():
    prices = trace([100.0, 1_000.0])
    ledger = backtest.value_captured(plan_of([Signal.CHARGE.value, Signal.EXPORT.value]), prices)

    # 20 kW for 15 minutes = 5 kWh bought at $100/MWh, 4.5 kWh stored after losses.
    assert ledger.loc[0, "signal_usd"] == pytest.approx(-0.5)
    assert ledger.loc[0, "signal_soc_kwh"] == pytest.approx(4.5)
    assert ledger.loc[1, "signal_usd"] == pytest.approx(4.5)
    assert ledger.loc[1, "signal_soc_kwh"] == 0.0


def test_an_empty_battery_cannot_export():
    ledger = backtest.value_captured(plan_of([Signal.EXPORT.value] * 2), trace([5_000.0] * 2))

    assert ledger["signal_usd"].sum() == 0.0


def test_charging_stops_at_usable_capacity():
    actions = plan_of([Signal.CHARGE.value] * 40)
    ledger = backtest.value_captured(actions, trace([20.0] * 40), kwh=13.5)

    assert ledger["signal_soc_kwh"].max() == pytest.approx(13.5)


def test_the_naive_schedule_charges_overnight_and_exports_in_the_evening():
    prices = trace([30.0] * 96)
    ledger = backtest.value_captured(plan_of([Signal.HOLD.value] * 96), prices)
    hours = ledger["interval_start"].dt.hour

    assert (ledger.loc[hours == 2, "naive_action"] == Signal.CHARGE.value).all()
    assert (ledger.loc[hours == 18, "naive_action"] == Signal.EXPORT.value).all()
    assert (ledger.loc[hours == 12, "naive_action"] == Signal.HOLD.value).all()


def test_summary_reports_uplift_and_energy_moved():
    prices = trace([100.0, 100.0, 1_000.0])
    actions = [Signal.CHARGE.value, Signal.CHARGE.value, Signal.EXPORT.value]
    summary = backtest.summarize(backtest.value_captured(plan_of(actions), prices))

    # 2 x 5 kWh bought at $100/MWh, 5 kWh sold at $1,000/MWh.
    assert summary.signal_usd == pytest.approx(5.0 - 1.0, abs=0.01)
    assert summary.naive_usd == 0.0
    assert summary.uplift_usd == summary.signal_usd
    assert summary.charged_kwh == pytest.approx(9.0)
    assert summary.exported_kwh == pytest.approx(5.0)
    assert summary.fleet_usd(10_000) == pytest.approx(summary.uplift_usd * 10_000)


def test_mismatched_plan_length_is_rejected():
    with pytest.raises(ValueError, match="rows"):
        backtest.value_captured(plan_of([Signal.HOLD.value]), trace([20.0, 21.0]))


@pytest.mark.parametrize("scenario", ["normal", "scarcity"])
def test_the_signals_beat_the_naive_schedule_on_both_bundled_days(scenario):
    summary = pipeline.run(scenario).summary

    assert summary.signal_usd > summary.naive_usd
    assert summary.uplift_usd > 0


def peak_day() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A cheap night, an ordinary afternoon and a dear evening, with the plan to match."""
    spp = [20.0] * 96
    for i in range(72, 80):  # 18:00 to 20:00
        spp[i] = 900.0
    prices = trace(spp)
    planned = [Signal.HOLD.value] * 96
    for i in range(8):  # 00:00 to 02:00
        planned[i] = Signal.CHARGE.value
    for i in range(72, 80):
        planned[i] = Signal.EXPORT.value
    plan = pd.DataFrame({"signal": planned, "planned": planned, "dam_mwh": spp})
    return plan, prices


def test_the_house_is_carried_by_the_grid_while_the_battery_is_held_for_the_peak():
    plan, prices = peak_day()
    held = backtest.value_captured(plan, prices, serve_home=True)
    drained = backtest.value_captured(plan, prices, serve_home=True, hold_for_peak=False)

    # Afternoon load is real in both runs, but only one of them pays for it out of storage.
    assert held.loc[40:71, "signal_home_kwh"].sum() == pytest.approx(0.0)
    assert drained.loc[40:71, "signal_home_kwh"].sum() > 0
    # So the battery arrives at the evening with the energy it was charged for.
    assert held.loc[72, "signal_soc_kwh"] > drained.loc[72, "signal_soc_kwh"]
    assert backtest.summarize(held).signal_usd > backtest.summarize(drained).signal_usd


def test_storage_serves_the_house_when_nothing_later_pays_more():
    """The hold is not a blanket refusal: at the top of the day-ahead curve, the house eats."""
    plan, prices = peak_day()
    ledger = backtest.value_captured(plan, prices, serve_home=True)

    assert ledger.loc[72:79, "signal_home_kwh"].sum() > 0
    # Nothing is invented or lost: every kWh the house drew came from one side or the other.
    load = backtest._home_load_kwh(ledger, backtest._interval_hours(ledger))
    assert ledger["signal_home_kwh"].sum() + ledger["signal_grid_kwh"].sum() == pytest.approx(
        sum(load), abs=0.01
    )


def test_the_naive_schedule_holds_for_its_own_peak_like_the_signals_do():
    """The baseline was exempt from the hold, so the policy was beating the rule, not it."""
    plan, prices = peak_day()
    held = backtest.value_captured(plan, prices, serve_home=True)
    drained = backtest.value_captured(plan, prices, serve_home=True, hold_for_peak=False)

    # The clock schedule exports 17:00 to 21:00, so its afternoon load goes to the grid too.
    assert held.loc[40:67, "naive_home_kwh"].sum() == pytest.approx(0.0)
    assert drained.loc[40:67, "naive_home_kwh"].sum() > 0
    assert held.loc[68, "naive_soc_kwh"] > drained.loc[68, "naive_soc_kwh"]
    # Both strategies are settled under the same rule, so the naive day earns more too.
    assert backtest.summarize(held).naive_usd > backtest.summarize(drained).naive_usd


def test_a_plan_without_a_day_ahead_curve_reserves_nothing():
    plan, prices = peak_day()
    bare = backtest.value_captured(plan[["signal"]].copy(), prices, serve_home=True)
    drained = backtest.value_captured(plan, prices, serve_home=True, hold_for_peak=False)

    assert bare["signal_home_kwh"].sum() == pytest.approx(drained["signal_home_kwh"].sum())


def test_the_household_view_prices_the_do_nothing_battery_at_zero():
    plan, prices = peak_day()
    summary = backtest.summarize(backtest.value_captured(plan, prices, serve_home=True))

    assert summary.member_savings_usd > 0
    assert summary.household_usd == pytest.approx(summary.signal_usd + summary.member_savings_usd)
    # A battery that never moves earns nothing and saves nothing, so the gap is the value.
    assert summary.uplift_vs_nothing_usd == summary.household_usd


def test_the_default_unit_is_the_base_core_style_one_with_legacy_kept_for_comparison():
    assert (backtest.DEFAULT_KWH, backtest.DEFAULT_POWER_KW) == (40.0, 20.0)
    assert (backtest.LEGACY_KWH, backtest.LEGACY_POWER_KW) == (13.5, 5.0)

    plan, prices = peak_day()
    default = backtest.summarize(backtest.value_captured(plan, prices, serve_home=True))
    legacy = backtest.summarize(
        backtest.value_captured(
            plan,
            prices,
            kwh=backtest.LEGACY_KWH,
            power_kw=backtest.LEGACY_POWER_KW,
            serve_home=True,
        )
    )
    assert default.signal_usd > legacy.signal_usd
