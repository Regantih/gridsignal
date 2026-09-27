"""Cache the ERCOT day-ahead curve for every bundled real-time trace.

For each ``*.parquet`` price trace under ``data/processed``, ``data/tuning`` and
``data/holdout``, this fetches the LZ_HOUSTON DAM hourly settlement prices for the same
trade date and writes a ``*_dam.parquet`` sibling plus provenance JSON. DAM results are
published the afternoon before the trade day, so planning against them is not lookahead.

    python scripts/fetch_dam.py            # needs the [ercot] extra and network
"""

from __future__ import annotations

import pandas as pd

from gridsignal import ingest
from gridsignal.holdout import HOLDOUT_DIR, TUNING_DIR, rtm_paths
from gridsignal.prices import PROCESSED, dam_path_for, load_price_trace

# The daily MIS report keeps about a week; older trade dates come from the yearly
# archive, which is fetched once per year and sliced.
DAILY_REPORT_YEARS = (2026,)


def dam_for(date: str, cache: dict[int, pd.DataFrame]) -> tuple[pd.DataFrame, str]:
    year = int(date[:4])
    if year in DAILY_REPORT_YEARS:
        return ingest.fetch_dam_prices(date), ingest.DAM_SOURCE_URL
    if year not in cache:
        cache[year] = ingest.fetch_dam_year(year)
    frame = cache[year]
    day = frame[frame["interval_start"].dt.date.astype(str) == date].reset_index(drop=True)
    return day, ingest.HISTORICAL_SOURCE_URL


def main() -> None:
    cache: dict[int, pd.DataFrame] = {}
    for directory in (PROCESSED, TUNING_DIR, HOLDOUT_DIR):
        for path in rtm_paths(directory):
            date = load_price_trace(path).date
            day, source = dam_for(date, cache)
            if day.empty:
                print(f"{date}: no DAM prices found, skipped")
                continue
            out = ingest.save_price_trace(
                day,
                date=date,
                market=ingest.DAM_MARKET,
                path=dam_path_for(path),
                source=source,
            )
            print(f"{date}: {len(day)} hours, peak ${day['spp'].max():,.2f}/MWh -> {out}")


if __name__ == "__main__":
    main()
