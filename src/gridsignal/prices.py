"""Read the cached ERCOT price trace and price the capacity at stake in a grid event.

The Control Room runs offline from a bundled Parquet sample; ``gridsignal.ingest``
refreshes it from ERCOT's public MIS reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from gridsignal import paths

PROCESSED = paths.PROCESSED_DIR
SAMPLE_PRICES = PROCESSED / "lz_houston_rtm_spp_sample.parquet"
SCARCITY_PRICES = PROCESSED / "lz_houston_rtm_spp_scarcity_sample.parquet"
DAM_SUFFIX = "_dam"


@dataclass(frozen=True)
class PriceScenario:
    """A bundled day of real prices the demo can be replayed against."""

    key: str
    label: str
    blurb: str
    path: Path


SCENARIOS: dict[str, PriceScenario] = {
    "normal": PriceScenario(
        key="normal",
        label="Normal peak day",
        blurb="An ordinary summer evening ramp: prices firm but far from scarcity.",
        path=SAMPLE_PRICES,
    ),
    "scarcity": PriceScenario(
        key="scarcity",
        label="Scarcity day",
        blurb="A real ERCOT scarcity event with settlement prices near the offer cap.",
        path=SCARCITY_PRICES,
    ),
}
DEFAULT_SCENARIO = "normal"


def available_scenarios() -> list[PriceScenario]:
    """Scenarios whose Parquet sample is actually bundled on this machine."""
    return [s for s in SCENARIOS.values() if s.path.exists()]


@dataclass(frozen=True)
class PriceTrace:
    """One day of settlement point prices for a single load zone."""

    location: str
    market: str
    date: str
    source: str
    frame: pd.DataFrame
    # Day-ahead hourly curve for the same date, when one is bundled alongside the
    # real-time trace. Published the afternoon before, so planning from it is not
    # lookahead.
    dam: pd.DataFrame | None = None

    @property
    def peak_mwh(self) -> float:
        return round(float(self.frame["spp"].max()), 2)

    @property
    def mean_mwh(self) -> float:
        return round(float(self.frame["spp"].mean()), 2)

    def window_price_mwh(self, start: datetime, end: datetime) -> float:
        """Average $/MWh across the intervals overlapping ``[start, end)``."""
        if end <= start:
            return 0.0
        frame = self.frame
        overlap = frame[(frame["interval_end"] > start) & (frame["interval_start"] < end)]
        if overlap.empty:
            return self.mean_mwh
        return round(float(overlap["spp"].mean()), 2)

    def peak_window(self, hours: float = 2.0) -> tuple[datetime, datetime, float]:
        """Highest-priced contiguous window of ``hours``, as (start, end, mean $/MWh)."""
        starts = list(self.frame["interval_start"])
        best = (starts[0], starts[0] + timedelta(hours=hours), 0.0)
        for start in starts:
            end = start + timedelta(hours=hours)
            if end > self.frame["interval_end"].iloc[-1]:
                break
            price = self.window_price_mwh(start, end)
            if price > best[2]:
                best = (start, end, price)
        return best


def energy_value_usd(kw: float, hours: float, price_mwh: float) -> float:
    """Dollar value of holding ``kw`` for ``hours`` at ``price_mwh`` ($/MWh)."""
    return round(kw * hours * price_mwh / 1000.0, 2)


def dam_path_for(path: Path) -> Path:
    """Where the day-ahead curve for a real-time trace is cached."""
    return path.with_name(f"{path.stem}{DAM_SUFFIX}{path.suffix}")


def _read_trace_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    frame["interval_start"] = pd.to_datetime(frame["interval_start"]).dt.tz_localize(None)
    frame["interval_end"] = pd.to_datetime(frame["interval_end"]).dt.tz_localize(None)
    return frame.sort_values("interval_start").reset_index(drop=True)


def load_price_trace(path: Path | None = None) -> PriceTrace:
    """Load the cached price trace, its provenance sidecar and any day-ahead curve."""
    path = path or SAMPLE_PRICES
    if not path.exists():
        raise FileNotFoundError(
            f"no cached price trace at {path}; run python -m gridsignal.ingest --date <YYYY-MM-DD>"
        )

    frame = _read_trace_frame(path)
    dam_path = dam_path_for(path)
    dam = _read_trace_frame(dam_path) if dam_path.exists() else None

    meta_path = path.with_suffix(".json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return PriceTrace(
        location=meta.get("location", "LZ_HOUSTON"),
        market=meta.get("market", "REAL_TIME_15_MIN"),
        date=meta.get("date", str(frame["interval_start"].iloc[0].date())),
        source=meta.get("source", "ERCOT MIS"),
        frame=frame,
        dam=dam,
    )


def load_scenario(key: str = DEFAULT_SCENARIO) -> PriceTrace:
    """Load the bundled price trace for a named scenario."""
    try:
        scenario = SCENARIOS[key]
    except KeyError:
        raise KeyError(f"unknown price scenario {key!r}; known: {sorted(SCENARIOS)}") from None
    return load_price_trace(scenario.path)
