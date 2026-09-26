from datetime import datetime, timedelta

import pandas as pd
import pytest

from gridsignal.prices import (
    SAMPLE_PRICES,
    PriceTrace,
    energy_value_usd,
    load_price_trace,
)


def make_trace(prices: list[float], start: datetime = datetime(2026, 9, 22, 0, 0)) -> PriceTrace:
    rows = [
        {
            "interval_start": start + timedelta(minutes=15 * i),
            "interval_end": start + timedelta(minutes=15 * (i + 1)),
            "spp": price,
        }
        for i, price in enumerate(prices)
    ]
    return PriceTrace(
        location="LZ_TEST",
        market="REAL_TIME_15_MIN",
        date="2026-09-22",
        source="test",
        frame=pd.DataFrame(rows),
    )


def test_bundled_sample_loads_offline():
    assert SAMPLE_PRICES.exists(), "the demo ships a cached ERCOT price trace"
    trace = load_price_trace()
    assert trace.location == "LZ_HOUSTON"
    assert len(trace.frame) == 96  # 15-minute intervals over one day
    assert trace.peak_mwh > trace.mean_mwh > 0
    assert list(trace.frame["interval_start"]) == sorted(trace.frame["interval_start"])


def test_missing_trace_points_at_the_refresh_command(tmp_path):
    with pytest.raises(FileNotFoundError, match="gridsignal.ingest"):
        load_price_trace(tmp_path / "nope.parquet")


def test_window_price_averages_overlapping_intervals():
    trace = make_trace([10.0, 20.0, 60.0, 100.0])
    start = datetime(2026, 9, 22, 0, 0)
    assert trace.window_price_mwh(start, start + timedelta(minutes=30)) == 15.0
    assert trace.window_price_mwh(start, start) == 0.0
    # Outside the trace we fall back to the day average rather than zero.
    assert trace.window_price_mwh(start + timedelta(days=2), start + timedelta(days=3)) == 47.5


def test_peak_window_picks_the_most_expensive_hour():
    trace = make_trace([10.0, 10.0, 10.0, 10.0, 200.0, 200.0, 200.0, 200.0])
    start, end, price = trace.peak_window(hours=1.0)
    assert (start, end) == (datetime(2026, 9, 22, 1, 0), datetime(2026, 9, 22, 2, 0))
    assert price == 200.0


def test_energy_value_converts_kw_hours_and_mwh_price():
    # 100 kW held for 2 h at $150/MWh = 0.2 MWh * ... = $30
    assert energy_value_usd(100.0, 2.0, 150.0) == 30.0
    assert energy_value_usd(0.0, 2.0, 150.0) == 0.0
