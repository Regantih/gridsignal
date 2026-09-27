"""Cache every ERCOT load zone plus the hub average for each bundled day.

The LZ_HOUSTON traces elsewhere in ``data/`` answer "when was power expensive".
These answer "expensive *where*", which is the congestion question: West Texas
generation and the four big load centres do not settle at the same price when the
lines between them are constrained.

One Parquet per trade day, tidy as ``interval_start``/``interval_end``/
``location``/``spp``, with a provenance sidecar next to it.

    python scripts/fetch_zones.py            # needs the [ercot] extra and network
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd

from gridsignal import ingest
from gridsignal.congestion import HUB_AVERAGE, ZONE_DIR, ZONES, zone_path_for

RECENT_FROM = 2026  # the yearly archive does not cover these yet; use the daily report
WANTED = [*ZONES, HUB_AVERAGE]


def _tidy(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.rename(
        columns={
            "Interval Start": "interval_start",
            "Interval End": "interval_end",
            "Location": "location",
            "SPP": "spp",
        }
    )[["interval_start", "interval_end", "location", "spp"]]
    frame = frame[frame["location"].isin(WANTED)]
    for column in ("interval_start", "interval_end"):
        frame[column] = pd.to_datetime(frame[column]).dt.tz_localize(None)
    return (
        frame.groupby(["interval_start", "interval_end", "location"], as_index=False)["spp"]
        .mean()
        .round({"spp": 2})
        .sort_values(["interval_start", "location"])
        .reset_index(drop=True)
    )


def fetch_day(date: str) -> tuple[pd.DataFrame, str]:
    """All zones and the hub average for one recent trade date, from the daily report."""
    ercot = ingest.gridstatus.Ercot()
    frames = [
        ercot.get_spp(date=date, market=ingest.DEFAULT_MARKET, location_type=kind)
        for kind in ("Load Zone", "Trading Hub")
    ]
    return _tidy(pd.concat(frames, ignore_index=True)), ingest.SOURCE_URL


def fetch_year(year: int) -> pd.DataFrame:
    """A whole year of every location from the historical RTM archive."""
    return _tidy(ingest.gridstatus.Ercot().get_rtm_spp(year))


def save(frame: pd.DataFrame, date: str, source: str) -> None:
    ZONE_DIR.mkdir(parents=True, exist_ok=True)
    path = zone_path_for(date)
    frame.to_parquet(path, index=False)
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "market": ingest.DEFAULT_MARKET,
                "location": sorted(frame["location"].unique()),
                "date": date,
                "source": source,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "intervals": int(frame["interval_start"].nunique()),
                "units": "$/MWh",
                "note": (
                    "Public ERCOT settlement point prices, read-only. Load zones and the "
                    "hub average for one trade day."
                ),
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"{date}: {frame['location'].nunique()} locations, "
        f"{frame['interval_start'].nunique()} intervals -> {path}"
    )


def bundled_dates() -> list[str]:
    from gridsignal.insight import bundled_traces

    return [t.date for t in bundled_traces()]


def main() -> None:
    dates = bundled_dates()
    by_year: dict[int, list[str]] = {}
    for date in dates:
        by_year.setdefault(int(date[:4]), []).append(date)

    for year, days in sorted(by_year.items()):
        if year >= RECENT_FROM:
            for date in days:
                frame, source = fetch_day(date)
                save(frame, date, source)
            continue
        archive = fetch_year(year)
        stamps = archive["interval_start"].dt.date.astype(str)
        for date in days:
            save(archive[stamps == date].reset_index(drop=True), date, ingest.HISTORICAL_SOURCE_URL)


if __name__ == "__main__":
    main()
