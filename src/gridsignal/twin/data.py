"""Real ERCOT real-time settlement point prices, reshaped into day-by-interval arrays.

Source: ERCOT real-time 15-minute settlement point prices (report NP6-905-CD) for the
Houston, North and South load zones, fetched with ``gridstatus``. Every price in this
package that is described as real comes from that file; nothing is invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from gridsignal.paths import DATA_DIR

ZONES: tuple[str, ...] = ("LZ_HOUSTON", "LZ_NORTH", "LZ_SOUTH")
INTERVALS_PER_DAY = 96
TWIN_DIR = DATA_DIR / "twin"
DATA_FILE = TWIN_DIR / "ercot_rtm_lz_2021_2025.parquet"


@dataclass(frozen=True)
class PriceDays:
    """Prices as ``(days, zones, 96)`` in $/MWh, with the local date of each day."""

    dates: pd.DatetimeIndex
    prices: np.ndarray
    zones: tuple[str, ...] = ZONES

    def __post_init__(self) -> None:
        if self.prices.shape != (len(self.dates), len(self.zones), INTERVALS_PER_DAY):
            raise ValueError(f"prices shape {self.prices.shape} does not match dates and zones")
        if not np.isfinite(self.prices).all():
            raise ValueError("prices contain NaN or inf")

    def __len__(self) -> int:
        return len(self.dates)

    def between(self, start: str, end: str) -> PriceDays:
        """Days with ``start <= date <= end`` (inclusive, ISO dates)."""
        mask = (self.dates >= pd.Timestamp(start)) & (self.dates <= pd.Timestamp(end))
        return PriceDays(self.dates[mask], self.prices[mask], self.zones)

    @property
    def months(self) -> np.ndarray:
        return self.dates.month.to_numpy()


def frame_to_days(frame: pd.DataFrame, zones: tuple[str, ...] = ZONES) -> PriceDays:
    """Pivot a long price frame into full days.

    Daylight-saving days have 92 or 100 intervals; they are dropped rather than
    stretched, so every kept day is exactly 96 real 15-minute prices per zone.
    """
    needed = {"Interval Start", "Location", "SPP"}
    missing = needed - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    df = frame[frame["Location"].isin(zones)].copy()
    ts = pd.to_datetime(df["Interval Start"])
    if ts.dt.tz is None:
        raise ValueError("Interval Start must be timezone-aware (US/Central)")
    local = ts.dt.tz_convert("US/Central")
    df["date"] = local.dt.tz_localize(None).dt.normalize()
    df["slot"] = local.dt.hour * 4 + local.dt.minute // 15
    df = df.drop_duplicates(["date", "Location", "slot"])

    counts = df.groupby(["date", "Location"]).size().unstack()
    full = counts.reindex(columns=list(zones)).eq(INTERVALS_PER_DAY).all(axis=1)
    keep = full[full].index
    df = df[df["date"].isin(keep)]

    cube = df.pivot_table(index=["date", "Location"], columns="slot", values="SPP").reindex(
        columns=range(INTERVALS_PER_DAY)
    )
    dates = pd.DatetimeIndex(sorted(keep))
    arr = np.stack([cube.xs(z, level="Location").reindex(dates).to_numpy() for z in zones], axis=1)
    return PriceDays(dates, arr.astype(float), zones)


def load_days(path: Path = DATA_FILE) -> PriceDays:
    return frame_to_days(pd.read_parquet(path))
