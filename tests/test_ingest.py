import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from gridsignal import ingest
from gridsignal.prices import load_price_trace


def test_saved_trace_round_trips_with_provenance(tmp_path):
    start = datetime(2026, 9, 22, 0, 0)
    frame = pd.DataFrame(
        [
            {
                "interval_start": start + timedelta(minutes=15 * i),
                "interval_end": start + timedelta(minutes=15 * (i + 1)),
                "spp": 40.0 + i,
            }
            for i in range(4)
        ]
    )

    path = ingest.save_price_trace(
        frame, date="2026-09-22", location="LZ_TEST", path=tmp_path / "trace.parquet"
    )

    meta = json.loads(path.with_suffix(".json").read_text())
    assert meta["location"] == "LZ_TEST"
    assert meta["date"] == "2026-09-22"
    assert meta["units"] == "$/MWh"
    assert "ercot.com" in meta["source"]

    trace = load_price_trace(path)
    assert trace.location == "LZ_TEST"
    assert trace.peak_mwh == 43.0


def test_restated_intervals_collapse_to_one_row():
    start = datetime(2023, 9, 6, 17, 0)
    frame = pd.DataFrame(
        [
            {"interval_start": start, "interval_end": start + timedelta(minutes=15), "spp": 25.88},
            # ERCOT restates the same interval a cent apart in the historical archive.
            {"interval_start": start, "interval_end": start + timedelta(minutes=15), "spp": 25.86},
            {
                "interval_start": start + timedelta(minutes=15),
                "interval_end": start + timedelta(minutes=30),
                "spp": 5000.0,
            },
        ]
    )

    tidy = ingest.normalize_prices(frame)

    assert list(tidy.columns) == ["interval_start", "interval_end", "spp"]
    assert len(tidy) == 2
    assert tidy["spp"].tolist() == [25.87, 5000.0]


def fake_gridstatus(monkeypatch, **frames):
    """Stand in for the ERCOT client so the tidy-up logic is tested without network."""
    client = SimpleNamespace(
        **{name: (lambda *a, frame=frame, **k: frame) for name, frame in frames.items()}
    )
    monkeypatch.setattr(ingest, "gridstatus", SimpleNamespace(Ercot=lambda: client))


def test_load_is_tidied_to_interval_and_megawatts(monkeypatch):
    start = datetime(2026, 9, 22, 18, 0)
    raw = pd.DataFrame(
        [
            {"Interval Start": start + timedelta(hours=1), "Load": 71_200.0, "Time": None},
            {"Interval Start": start, "Load": 70_100.0, "Time": None},
        ]
    )
    fake_gridstatus(monkeypatch, get_load=raw)

    tidy = ingest.fetch_load("2026-09-22", "2026-09-22")

    assert list(tidy.columns) == ["interval_start", "load_mw"]
    assert tidy["load_mw"].tolist() == [70_100.0, 71_200.0]


def test_fuel_mix_is_melted_to_one_row_per_fuel(monkeypatch):
    start = datetime(2026, 9, 22, 18, 0)
    raw = pd.DataFrame(
        [{"Interval Start": start, "Interval End": start, "Wind": 9_000.0, "Solar": 4_000.0}]
    )
    fake_gridstatus(monkeypatch, get_fuel_mix=raw)

    tidy = ingest.fetch_fuel_mix("2026-09-22", "2026-09-22")

    assert list(tidy.columns) == ["interval_start", "fuel", "mw"]
    assert tidy["fuel"].tolist() == ["Solar", "Wind"]
    assert tidy["mw"].tolist() == [4_000.0, 9_000.0]


def test_live_helpers_explain_the_missing_extra(monkeypatch):
    monkeypatch.setattr(ingest, "gridstatus", None)

    with pytest.raises(ingest.MissingDependencyError, match="ercot"):
        ingest.fetch_load("2026-09-22", "2026-09-22")
    with pytest.raises(ingest.MissingDependencyError, match="ercot"):
        ingest.fetch_fuel_mix("2026-09-22", "2026-09-22")
