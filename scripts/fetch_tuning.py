"""Cache the LZ_HOUSTON RTM days the policy is allowed to be tuned on.

The held-out set (``scripts/fetch_holdout.py``) takes each year's highest-priced day and
its median day. The tuning set takes the days either side of those — the 75th and 25th
percentile of daily peak — so the two splits never share a date and neither split is
hand-picked. Parameters may be fitted on these days and on the two bundled scenario
days; nothing else.

    python scripts/fetch_tuning.py            # needs the [ercot] extra and network
"""

from __future__ import annotations

import pandas as pd

from gridsignal import ingest
from gridsignal.holdout import TUNING_DIR, slug_for

# 2026 contributes the bundled normal scenario day (2026-09-22), which is already part
# of the tuning split, so no extra recent day is pulled here.
QUANTILES = (0.25, 0.75)


def pick_days(frame: pd.DataFrame) -> list[str]:
    daily_peak = frame.groupby(frame["interval_start"].dt.date)["spp"].max().sort_values()
    full = frame.groupby(frame["interval_start"].dt.date)["spp"].size() == 96
    daily_peak = daily_peak[full[daily_peak.index].to_numpy()]
    return [str(daily_peak.index[int(q * (len(daily_peak) - 1))]) for q in QUANTILES]


def cache(date: str, day: pd.DataFrame, source: str) -> None:
    path = ingest.save_price_trace(
        day, date=date, path=TUNING_DIR / f"{slug_for(date)}.parquet", source=source
    )
    print(f"{date}: {len(day)} intervals, peak ${day['spp'].max():,.2f}/MWh -> {path}")


def main() -> None:
    TUNING_DIR.mkdir(parents=True, exist_ok=True)
    for year in (2023, 2024, 2025):
        try:
            frame = ingest.fetch_rtm_year(year)
        except Exception as exc:  # noqa: BLE001 - the archive may not cover a year yet
            print(f"{year}: skipped ({exc})")
            continue
        for date in pick_days(frame):
            day = frame[frame["interval_start"].dt.date.astype(str) == date].reset_index(drop=True)
            cache(date, day, ingest.HISTORICAL_SOURCE_URL)


if __name__ == "__main__":
    main()
