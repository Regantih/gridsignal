"""Rolling-baseline spike detection on synthetic and real bundled prices."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from gridsignal import detect
from gridsignal.prices import load_scenario


def trace(prices: list[float]) -> pd.DataFrame:
    start = datetime(2023, 9, 6)
    return pd.DataFrame(
        {
            "interval_start": [start + timedelta(minutes=15 * i) for i in range(len(prices))],
            "interval_end": [start + timedelta(minutes=15 * (i + 1)) for i in range(len(prices))],
            "spp": prices,
        }
    )


def test_a_calm_market_has_no_spikes():
    detections = detect.detect_spikes(trace([25.0, 26.0, 24.0, 25.5, 26.5] * 6))

    assert not detections["is_spike"].any()


def test_a_price_far_above_the_trailing_baseline_is_flagged():
    prices = [25.0] * 20 + [900.0] + [25.0] * 5
    detections = detect.detect_spikes(trace(prices))

    assert detections.loc[20, "is_spike"]
    assert detections.loc[20, "z"] > 3.0
    # The baseline is trailing, so the spike itself does not raise its own bar.
    assert detections.loc[20, "baseline"] == pytest.approx(25.0)
    assert detections["is_spike"].sum() == 1


def test_the_baseline_ignores_an_earlier_spike_when_judging_the_next_one():
    """One $900 print must not desensitise the detector to the next one."""
    prices = [25.0] * 20 + [900.0, 25.0, 25.0, 900.0]
    detections = detect.detect_spikes(trace(prices))

    assert detections.loc[[20, 23], "is_spike"].all()


def test_spike_windows_group_consecutive_intervals():
    prices = [25.0] * 20 + [900.0, 950.0] + [25.0] * 4 + [800.0]
    windows = detect.spike_windows(detect.detect_spikes(trace(prices)))

    assert len(windows) == 2
    assert windows.loc[0, "intervals"] == 2
    assert windows.loc[0, "peak_mwh"] == 950.0
    assert windows.loc[1, "intervals"] == 1


def test_no_spikes_gives_an_empty_window_table():
    windows = detect.spike_windows(detect.detect_spikes(trace([25.0] * 30)))

    assert windows.empty
    assert list(windows.columns) == ["start", "end", "intervals", "peak_mwh", "mean_mwh", "peak_z"]


def test_the_real_scarcity_day_is_detected_and_the_normal_day_is_quieter():
    scarcity = detect.detect_spikes(load_scenario("scarcity").frame)
    normal = detect.detect_spikes(load_scenario("normal").frame)

    assert scarcity["is_spike"].sum() > 0
    assert scarcity.loc[scarcity["is_spike"], "spp"].max() > 5_000
    assert scarcity["z"].max() > normal["z"].max()


def test_prices_without_an_spp_column_are_rejected():
    with pytest.raises(KeyError):
        detect.detect_spikes(pd.DataFrame({"price": [1.0, 2.0]}))
