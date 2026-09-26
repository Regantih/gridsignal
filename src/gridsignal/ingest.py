"""Pull ERCOT prices, load and generation and store them as Parquet."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

try:  # gridstatus only ships with the optional [ercot] extra
    import gridstatus
except ImportError:  # pragma: no cover - exercised only without the extra
    gridstatus = None

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

DEFAULT_LOCATION = "LZ_HOUSTON"
DEFAULT_MARKET = "REAL_TIME_15_MIN"
SOURCE_URL = "https://www.ercot.com/mp/data-products/data-product-details?id=NP6-905-CD"
# The daily MIS report above only retains about a week, so scarcity days come from the
# historical RTM settlement point price archive.
HISTORICAL_SOURCE_URL = "https://www.ercot.com/mp/data-products/data-product-details?id=NP6-785-ER"


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
