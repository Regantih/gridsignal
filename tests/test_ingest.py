import json
from datetime import datetime, timedelta

import pandas as pd

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
