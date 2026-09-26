"""Detect price spikes, scarcity windows and zone spreads."""

from __future__ import annotations

import numpy as np
import pandas as pd

# 4 hours of 15-minute settlement intervals: long enough to describe "normal right now",
# short enough that an evening ramp does not look like the overnight trough.
BASELINE_INTERVALS = 16
# $/MWh floor on the baseline spread so a flat overnight hour cannot divide by ~0.
MIN_SPREAD_MWH = 2.0
# Median absolute deviation to standard-deviation conversion for a normal distribution.
MAD_TO_SIGMA = 1.4826


def detect_spikes(
    prices: pd.DataFrame,
    z: float = 3.0,
    window: int = BASELINE_INTERVALS,
) -> pd.DataFrame:
    """Flag intervals sitting ``z`` robust deviations above their rolling baseline.

    The baseline is a *trailing* median (the interval being judged is excluded) and the
    spread is a median absolute deviation, so a single $5,000/MWh print cannot inflate
    the baseline and mask the rest of the event.

    Adds ``baseline``, ``spread``, ``z`` and ``is_spike`` to a copy of ``prices``.
    """
    if "spp" not in prices.columns:
        raise KeyError("prices needs an 'spp' column of $/MWh settlement prices")

    out = prices.copy().reset_index(drop=True)
    spp = out["spp"].astype(float)
    trailing = spp.shift(1).rolling(window, min_periods=2)

    baseline = trailing.median()
    mad = trailing.apply(lambda w: np.median(np.abs(w - np.median(w))), raw=True)
    spread = (MAD_TO_SIGMA * mad).clip(lower=MIN_SPREAD_MWH)

    out["baseline"] = baseline.round(2)
    out["spread"] = spread.round(2)
    out["z"] = ((spp - baseline) / spread).fillna(0.0).round(2)
    out["is_spike"] = out["z"] >= z
    return out


def spike_windows(detections: pd.DataFrame) -> pd.DataFrame:
    """Collapse consecutive spike intervals into windows a human can read.

    Columns: ``start``, ``end``, ``intervals``, ``peak_mwh``, ``mean_mwh``, ``peak_z``.
    """
    flagged = detections[detections["is_spike"]]
    if flagged.empty:
        return pd.DataFrame(columns=["start", "end", "intervals", "peak_mwh", "mean_mwh", "peak_z"])

    # A gap in the row index means the spike stopped and a new window started.
    groups = (flagged.index.to_series().diff() != 1).cumsum()
    windows = flagged.groupby(groups).agg(
        start=("interval_start", "first"),
        end=("interval_end", "last"),
        intervals=("spp", "size"),
        peak_mwh=("spp", "max"),
        mean_mwh=("spp", "mean"),
        peak_z=("z", "max"),
    )
    return windows.round({"peak_mwh": 2, "mean_mwh": 2, "peak_z": 2}).reset_index(drop=True)
