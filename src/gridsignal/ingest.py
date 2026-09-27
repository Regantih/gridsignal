"""Pull ERCOT prices, load and generation and store them as Parquet."""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

from gridsignal import paths

try:  # gridstatus only ships with the optional [ercot] extra
    import gridstatus
except ImportError:  # pragma: no cover - exercised only without the extra
    gridstatus = None

ROOT = paths.ROOT
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

DEFAULT_LOCATION = "LZ_HOUSTON"
DEFAULT_MARKET = "REAL_TIME_15_MIN"
DAM_MARKET = "DAY_AHEAD_HOURLY"
DAM_SOURCE_URL = "https://www.ercot.com/mp/data-products/data-product-details?id=NP4-190-CD"
SOURCE_URL = "https://www.ercot.com/mp/data-products/data-product-details?id=NP6-905-CD"
# The daily MIS report above only retains about a week, so scarcity days come from the
# historical RTM settlement point price archive.
HISTORICAL_SOURCE_URL = "https://www.ercot.com/mp/data-products/data-product-details?id=NP6-785-ER"

# Ancillary service market clearing prices for capacity, cleared in the day-ahead market.
AS_MARKET = "DAM_ANCILLARY"
AS_SOURCE_URL = "https://www.ercot.com/mp/data-products/data-product-details?id=NP4-188-CD"
# The daily report keeps about a month; older trade dates come from the yearly archive.
AS_HISTORICAL_SOURCE_URL = (
    "https://www.ercot.com/mp/data-products/data-product-details?id=NP4-181-ER"
)
AS_HISTORICAL_REPORT_ID = 13091
#: ERCOT ancillary products, keyed by the column name this repo uses.
AS_PRODUCTS = ("regup", "regdn", "rrs", "ecrs", "nonspin")
_AS_DAILY_COLUMNS = {
    "Regulation Up": "regup",
    "Regulation Down": "regdn",
    "Responsive Reserves": "rrs",
    "ERCOT Contingency Reserve Service": "ecrs",
    "Non-Spinning Reserves": "nonspin",
}
_AS_ARCHIVE_COLUMNS = {
    "REGUP": "regup",
    "REGDN": "regdn",
    "RRS": "rrs",
    "ECRS": "ecrs",
    "NSPIN": "nonspin",
}


class MissingDependencyError(RuntimeError):
    """Raised when a live-data helper is used without the [ercot] extra installed."""


def fetch_prices(
    date: str,
    location: str = DEFAULT_LOCATION,
    market: str = DEFAULT_MARKET,
) -> pd.DataFrame:
    """Real-time settlement point prices for one ERCOT load zone on one day.

    Hits the public ERCOT MIS reports through gridstatus, no credentials needed.
    Returns tidy columns: ``interval_start``, ``interval_end``, ``spp``.
    """
    if gridstatus is None:
        raise MissingDependencyError('install the live-data extra: pip install -e ".[ercot]"')

    raw = gridstatus.Ercot().get_spp(date=date, market=market, location_type="Load Zone")
    zone = raw[raw["Location"] == location]
    if zone.empty:
        raise ValueError(f"no {market} prices for {location} on {date}")

    return normalize_prices(
        zone.rename(
            columns={
                "Interval Start": "interval_start",
                "Interval End": "interval_end",
                "SPP": "spp",
            }
        )
    )


def normalize_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Tidy to ``interval_start``/``interval_end``/``spp``, one row per interval.

    ERCOT publishes some intervals twice (a correction restates the same interval a
    cent or two apart), so intervals are collapsed to their mean.
    """
    return (
        df[["interval_start", "interval_end", "spp"]]
        .groupby(["interval_start", "interval_end"], as_index=False)["spp"]
        .mean()
        .round({"spp": 2})
        .sort_values("interval_start")
        .reset_index(drop=True)
    )


def fetch_dam_prices(
    date: str,
    location: str = DEFAULT_LOCATION,
) -> pd.DataFrame:
    """Day-ahead hourly settlement point prices for one load zone on one trade date.

    DAM results are published the afternoon before the trade day, so a plan built from
    this curve is information the operator genuinely has in advance.
    """
    if gridstatus is None:
        raise MissingDependencyError('install the live-data extra: pip install -e ".[ercot]"')

    raw = gridstatus.Ercot().get_spp(date=date, market=DAM_MARKET, location_type="Load Zone")
    return _zone_frame(raw, location, f"no {DAM_MARKET} prices for {location} on {date}")


def fetch_rtm_year(year: int, location: str = DEFAULT_LOCATION) -> pd.DataFrame:
    """A whole year of real-time settlement prices from ERCOT's historical archive."""
    if gridstatus is None:
        raise MissingDependencyError('install the live-data extra: pip install -e ".[ercot]"')

    raw = gridstatus.Ercot().get_rtm_spp(year)
    return _zone_frame(raw, location, f"no historical RTM prices for {location} in {year}")


def fetch_dam_year(year: int, location: str = DEFAULT_LOCATION) -> pd.DataFrame:
    """A whole year of day-ahead settlement prices from ERCOT's historical archive."""
    if gridstatus is None:
        raise MissingDependencyError('install the live-data extra: pip install -e ".[ercot]"')

    raw = gridstatus.Ercot().get_dam_spp(year)
    return _zone_frame(raw, location, f"no historical DAM prices for {location} in {year}")


def _zone_frame(raw: pd.DataFrame, location: str, message: str) -> pd.DataFrame:
    zone = raw[raw["Location"] == location]
    if zone.empty:
        raise ValueError(message)
    return normalize_prices(
        zone.rename(
            columns={
                "Interval Start": "interval_start",
                "Interval End": "interval_end",
                "SPP": "spp",
            }
        )
    )


def fetch_scarcity_day(
    year: int,
    location: str = DEFAULT_LOCATION,
) -> tuple[str, pd.DataFrame]:
    """Highest-priced day of ``year`` for one load zone, from ERCOT's historical archive.

    Returns ``(date, frame)`` so the caller can cache it with the right provenance.
    """
    if gridstatus is None:
        raise MissingDependencyError('install the live-data extra: pip install -e ".[ercot]"')

    raw = gridstatus.Ercot().get_rtm_spp(year)
    zone = raw[raw["Location"] == location]
    if zone.empty:
        raise ValueError(f"no historical RTM prices for {location} in {year}")

    frame = normalize_prices(
        zone.rename(
            columns={
                "Interval Start": "interval_start",
                "Interval End": "interval_end",
                "SPP": "spp",
            }
        )
    )
    days = frame["interval_start"].dt.date
    peak_day = days[frame["spp"].idxmax()]
    return str(peak_day), frame[days == peak_day].reset_index(drop=True)


def normalize_as_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Tidy ancillary clearing prices to ``interval_start`` plus one column per product.

    Prices are $/MW per hour of capacity held. ECRS only exists from June 2023, so its
    column is zero-filled on earlier days rather than dropped.
    """
    frame = df.copy()
    for column in AS_PRODUCTS:
        if column not in frame:
            frame[column] = 0.0
    frame["interval_start"] = pd.to_datetime(frame["interval_start"]).dt.tz_localize(None)
    frame[list(AS_PRODUCTS)] = (
        frame[list(AS_PRODUCTS)].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    )
    return (
        frame[["interval_start", *AS_PRODUCTS]]
        .groupby("interval_start", as_index=False)
        .mean()
        .round(2)
        .sort_values("interval_start")
        .reset_index(drop=True)
    )


def fetch_as_prices(date: str) -> pd.DataFrame:
    """Day-ahead ancillary clearing prices for one recent trade date (public MIS report)."""
    if gridstatus is None:
        raise MissingDependencyError('install the live-data extra: pip install -e ".[ercot]"')

    raw = gridstatus.Ercot().get_as_prices(date=date)
    if raw.empty:
        raise ValueError(f"no ancillary clearing prices published for {date}")
    return normalize_as_prices(
        raw.rename(columns={"Interval Start": "interval_start", **_AS_DAILY_COLUMNS})
    )


def fetch_as_year(year: int) -> pd.DataFrame:
    """A whole year of ancillary clearing prices from ERCOT's historical archive."""
    if gridstatus is None:
        raise MissingDependencyError('install the live-data extra: pip install -e ".[ercot]"')

    doc = gridstatus.Ercot()._get_document(  # noqa: SLF001 - no public historical AS helper
        report_type_id=AS_HISTORICAL_REPORT_ID,
        constructed_name_contains=f"{year}.zip",
    )

    payload = requests.get(doc.url, timeout=180)
    payload.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(payload.content))
    raw = pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])))
    raw.columns = [c.strip() for c in raw.columns]
    # "Hour Ending" is 01:00..24:00 local, so the interval starts an hour earlier.
    hour_end = raw["Hour Ending"].astype(str).str.slice(0, 2).astype(int)
    raw["interval_start"] = pd.to_datetime(
        raw["Delivery Date"], format="%m/%d/%Y"
    ) + pd.to_timedelta(hour_end - 1, unit="h")
    return normalize_as_prices(raw.rename(columns=_AS_ARCHIVE_COLUMNS))


def save_as_prices(df: pd.DataFrame, date: str, path: Path, source: str) -> Path:
    """Cache one day of ancillary clearing prices with a provenance sidecar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "location": "ERCOT system-wide",
                "market": AS_MARKET,
                "date": date,
                "source": source,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "intervals": int(len(df)),
                "products": list(AS_PRODUCTS),
                "units": "$/MW per hour of capacity held",
            },
            indent=2,
        )
        + "\n"
    )
    return path


def fetch_load(start: str, end: str) -> pd.DataFrame:
    """ERCOT system load over ``[start, end]``, as ``interval_start``/``load_mw``."""
    if gridstatus is None:
        raise MissingDependencyError('install the live-data extra: pip install -e ".[ercot]"')

    raw = gridstatus.Ercot().get_load(date=start, end=end)
    return (
        raw.rename(columns={"Interval Start": "interval_start", "Load": "load_mw"})[
            ["interval_start", "load_mw"]
        ]
        .sort_values("interval_start")
        .reset_index(drop=True)
    )


def fetch_fuel_mix(start: str, end: str) -> pd.DataFrame:
    """ERCOT generation by fuel type, one tidy row per interval and fuel (MW)."""
    if gridstatus is None:
        raise MissingDependencyError('install the live-data extra: pip install -e ".[ercot]"')

    raw = gridstatus.Ercot().get_fuel_mix(date=(pd.Timestamp(start), pd.Timestamp(end)))
    fuels = [c for c in raw.columns if c not in {"Time", "Interval Start", "Interval End"}]
    return (
        raw.rename(columns={"Interval Start": "interval_start"})
        .melt(
            id_vars="interval_start",
            value_vars=fuels,
            var_name="fuel",
            value_name="mw",
        )
        .sort_values(["interval_start", "fuel"])
        .reset_index(drop=True)
    )


def save(df: pd.DataFrame, name: str) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{name}.parquet"
    df.to_parquet(path)
    return path


def save_price_trace(
    df: pd.DataFrame,
    date: str,
    location: str = DEFAULT_LOCATION,
    market: str = DEFAULT_MARKET,
    path: Path | None = None,
    source: str = SOURCE_URL,
) -> Path:
    """Cache a price trace as Parquet plus a sidecar JSON describing its provenance."""
    PROCESSED.mkdir(parents=True, exist_ok=True)
    path = path or PROCESSED / f"{location.lower()}_rtm_spp_sample.parquet"
    df.to_parquet(path, index=False)
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "location": location,
                "market": market,
                "date": date,
                "source": source,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "intervals": int(len(df)),
                "units": "$/MWh",
            },
            indent=2,
        )
        + "\n"
    )
    return path


def main() -> None:
    p = argparse.ArgumentParser(description="Refresh a cached ERCOT price trace")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="recent trade date, e.g. 2026-09-22")
    group.add_argument(
        "--scarcity-year",
        type=int,
        help="cache the highest-priced day of this year as the scarcity scenario",
    )
    p.add_argument("--location", default=DEFAULT_LOCATION)
    p.add_argument("--market", default=DEFAULT_MARKET)
    args = p.parse_args()

    if args.scarcity_year:
        date, df = fetch_scarcity_day(args.scarcity_year, location=args.location)
        path = save_price_trace(
            df,
            date=date,
            location=args.location,
            path=PROCESSED / f"{args.location.lower()}_rtm_spp_scarcity_sample.parquet",
            source=HISTORICAL_SOURCE_URL,
        )
    else:
        date = args.date
        df = fetch_prices(date, location=args.location, market=args.market)
        path = save_price_trace(df, date=date, location=args.location, market=args.market)

    print(f"{date}: {len(df)} intervals, peak ${df['spp'].max():,.2f}/MWh -> {path}")


if __name__ == "__main__":
    main()
