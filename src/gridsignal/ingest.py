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

    return (
        zone.rename(
            columns={
                "Interval Start": "interval_start",
                "Interval End": "interval_end",
                "SPP": "spp",
            }
        )[["interval_start", "interval_end", "spp"]]
        .sort_values("interval_start")
        .reset_index(drop=True)
    )


def fetch_load(start: str, end: str) -> pd.DataFrame:
    """System load. TODO."""
    raise NotImplementedError


def fetch_fuel_mix(start: str, end: str) -> pd.DataFrame:
    """Generation by fuel type. TODO."""
    raise NotImplementedError


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
                "source": SOURCE_URL,
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
    p = argparse.ArgumentParser(description="Refresh the cached ERCOT price trace")
    p.add_argument("--date", required=True, help="trade date, e.g. 2026-09-22")
    p.add_argument("--location", default=DEFAULT_LOCATION)
    p.add_argument("--market", default=DEFAULT_MARKET)
    args = p.parse_args()

    df = fetch_prices(args.date, location=args.location, market=args.market)
    path = save_price_trace(df, date=args.date, location=args.location, market=args.market)
    print(f"{len(df)} intervals, peak ${df['spp'].max():.2f}/MWh -> {path}")


if __name__ == "__main__":
    main()
