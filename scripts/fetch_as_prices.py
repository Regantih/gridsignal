"""Cache ERCOT ancillary service clearing prices for every bundled trade date.

For each ``*.parquet`` real-time price trace under ``data/processed``, ``data/tuning``
and ``data/holdout``, this fetches the day-ahead ancillary market clearing prices for
capacity (Reg Up, Reg Down, RRS, ECRS, Non-Spin) for the same trade date and writes a
``*_as.parquet`` sibling plus a provenance JSON. Recent dates come from the daily MIS
report; older ones from the yearly historical archive.

    python scripts/fetch_as_prices.py      # needs the [ercot] extra and network
"""

from __future__ import annotations

import pandas as pd

from gridsignal import ingest
from gridsignal.ancillary import as_path_for
from gridsignal.holdout import HOLDOUT_DIR, TUNING_DIR, rtm_paths
from gridsignal.prices import PROCESSED, load_price_trace

# The daily report retains about a month; anything older comes from the yearly archive.
DAILY_REPORT_YEARS = (2026,)


def as_for(date: str, cache: dict[int, pd.DataFrame]) -> tuple[pd.DataFrame, str]:
    year = int(date[:4])
    if year in DAILY_REPORT_YEARS:
        return ingest.fetch_as_prices(date), ingest.AS_SOURCE_URL
    if year not in cache:
        cache[year] = ingest.fetch_as_year(year)
    frame = cache[year]
    day = frame[frame["interval_start"].dt.date.astype(str) == date].reset_index(drop=True)
    return day, ingest.AS_HISTORICAL_SOURCE_URL


def main() -> None:
    cache: dict[int, pd.DataFrame] = {}
    for directory in (PROCESSED, TUNING_DIR, HOLDOUT_DIR):
        for path in rtm_paths(directory):
            date = load_price_trace(path).date
            day, source = as_for(date, cache)
            if day.empty:
                print(f"{date}: no ancillary clearing prices found, skipped")
                continue
            out = ingest.save_as_prices(day, date=date, path=as_path_for(path), source=source)
            peak = day[list(ingest.AS_PRODUCTS)].max().max()
            print(f"{date}: {len(day)} hours, peak ${peak:,.2f}/MW-h -> {out}")


if __name__ == "__main__":
    main()
