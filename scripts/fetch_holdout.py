"""Cache the held-out LZ_HOUSTON RTM days used to score the frozen signal policy.

Day selection is a rule, not a hand-pick: for each year the archive covers, take the
highest-priced day (the scarcity case) and the day whose daily peak is the median of
that year (the ordinary case). 2023's peak day is excluded because the policy was
written against it, so it is not held out. The archive does not cover 2026 yet, so
those days come from the daily MIS report instead (``RECENT_DAYS``).

    python scripts/fetch_holdout.py            # needs the [ercot] extra and network
"""

from __future__ import annotations

import gridstatus
import pandas as pd

from gridsignal import ingest
from gridsignal.holdout import HOLDOUT_DIR, slug_for

TUNED_ON = "2023-09-06"
# gridstatus cannot parse the 2026 yearly archive yet; these are the trade days the
# daily report still retained, excluding the tuned-on 2026-09-22.
RECENT_DAYS = ("2026-09-23", "2026-09-24")


def year_frame(year: int) -> pd.DataFrame:
    raw = gridstatus.Ercot().get_rtm_spp(year)
    zone = raw[raw["Location"] == ingest.DEFAULT_LOCATION]
    return ingest.normalize_prices(
        zone.rename(
            columns={
                "Interval Start": "interval_start",
                "Interval End": "interval_end",
                "SPP": "spp",
            }
        )
    )


def pick_days(frame: pd.DataFrame) -> list[str]:
    daily_peak = frame.groupby(frame["interval_start"].dt.date)["spp"].max().sort_values()
    full = frame.groupby(frame["interval_start"].dt.date)["spp"].size() == 96
    daily_peak = daily_peak[full[daily_peak.index].to_numpy()]
    chosen = [str(daily_peak.index[-1]), str(daily_peak.index[len(daily_peak) // 2])]
    return [d for d in chosen if d != TUNED_ON]


def cache(date: str, day: pd.DataFrame, source: str) -> None:
    path = ingest.save_price_trace(
        day, date=date, path=HOLDOUT_DIR / f"{slug_for(date)}.parquet", source=source
    )
    print(f"{date}: {len(day)} intervals, peak ${day['spp'].max():,.2f}/MWh -> {path}")


def main() -> None:
    HOLDOUT_DIR.mkdir(parents=True, exist_ok=True)
    for year in (2023, 2024, 2025):
        try:
            frame = year_frame(year)
        except Exception as exc:  # noqa: BLE001 - the archive may not cover a year yet
            print(f"{year}: skipped ({exc})")
            continue
        for date in pick_days(frame):
            day = frame[frame["interval_start"].dt.date.astype(str) == date].reset_index(drop=True)
            cache(date, day, ingest.HISTORICAL_SOURCE_URL)

    for date in RECENT_DAYS:
        cache(date, ingest.fetch_prices(date), ingest.SOURCE_URL)


if __name__ == "__main__":
    main()
