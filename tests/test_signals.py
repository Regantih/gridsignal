"""Charge / hold / export policy."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from gridsignal import signals
from gridsignal.signals import Signal


def trace(prices: list[float]) -> pd.DataFrame:
    start = datetime(2023, 9, 6)
    return pd.DataFrame(
        {
            "interval_start": [start + timedelta(minutes=15 * i) for i in range(len(prices))],
            "interval_end": [start + timedelta(minutes=15 * (i + 1)) for i in range(len(prices))],
            "spp": prices,
        }
    )


def calm_probability(n: int) -> pd.Series:
    return pd.Series([0.0] * n)


def test_cheap_intervals_charge_and_scarcity_intervals_export():
    prices = [25.0] * 20 + [4_000.0] * 4 + [30.0] * 20
    plan = signals.make_signals(trace(prices), calm_probability(len(prices)))

    assert plan.loc[0, "signal"] == Signal.CHARGE.value
    assert (plan.loc[20:23, "signal"] == Signal.EXPORT.value).all()


def test_a_merely_good_morning_price_does_not_empty_the_battery():
    """$35 after a $25 night clears no reservation price; the battery waits."""
    prices = [25.0] * 10 + [35.0] * 4 + [25.0] * 10
    plan = signals.make_signals(trace(prices), calm_probability(len(prices)))

    assert Signal.EXPORT.value not in set(plan.loc[10:13, "signal"])


def test_a_confident_spike_forecast_opens_the_export_gate_early():
    prices = [25.0] * 10 + [500.0] + [25.0] * 5
    prob = pd.Series([0.0] * 10 + [0.95] + [0.0] * 5)

    without = signals.make_signals(trace(prices), calm_probability(len(prices)))
    with_forecast = signals.make_signals(trace(prices), prob)

    assert with_forecast.loc[10, "signal"] == Signal.EXPORT.value
    assert with_forecast.loc[10, "spike_prob"] == 0.95
    # The price alone is enough here; the forecast must not *stop* the export either.
    assert without.loc[10, "signal"] == Signal.EXPORT.value


def test_the_reservation_price_declines_across_the_horizon():
    prices = [25.0] * 40
    reservation = signals.reservation_price(pd.Series(prices), opening=30.0, closing=1.5)

    assert reservation.iloc[0] == pytest.approx(25.0 * 30.0)
    assert reservation.iloc[-1] == pytest.approx(25.0 * 1.5)
    assert reservation.is_monotonic_decreasing


def test_every_interval_gets_a_signal_and_a_reason():
    prices = [25.0, 30.0, 900.0, 40.0]
    plan = signals.make_signals(trace(prices), calm_probability(4))

    assert set(plan["signal"]) <= {s.value for s in Signal}
    assert plan["reason"].str.len().gt(10).all()


def test_mismatched_forecast_length_is_rejected():
    with pytest.raises(ValueError, match="rows"):
        signals.make_signals(trace([25.0, 26.0]), pd.Series([0.1]))
