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

    # 5 kW for 15 minutes = 1.25 kWh bought at $100/MWh, 1.125 kWh stored after losses.
    assert ledger.loc[0, "signal_usd"] == pytest.approx(-0.125)
    assert ledger.loc[0, "signal_soc_kwh"] == pytest.approx(1.125)
    assert ledger.loc[1, "signal_usd"] == pytest.approx(1.125)
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

    # 2 x 1.25 kWh bought at $100/MWh, 1.25 kWh sold at $1,000/MWh.
    assert summary.signal_usd == pytest.approx(1.25 - 0.25, abs=0.01)
    assert summary.naive_usd == 0.0
    assert summary.uplift_usd == summary.signal_usd
    assert summary.charged_kwh == pytest.approx(2.25)
    assert summary.exported_kwh == pytest.approx(1.25)
    assert summary.fleet_usd(10_000) == pytest.approx(summary.uplift_usd * 10_000)


def test_mismatched_plan_length_is_rejected():
    with pytest.raises(ValueError, match="rows"):
        backtest.value_captured(plan_of([Signal.HOLD.value]), trace([20.0, 21.0]))


@pytest.mark.parametrize("scenario", ["normal", "scarcity"])
def test_the_signals_beat_the_naive_schedule_on_both_bundled_days(scenario):
    summary = pipeline.run(scenario).summary

    assert summary.signal_usd > summary.naive_usd
    assert summary.uplift_usd > 0
