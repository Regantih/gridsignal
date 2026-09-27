"""Live ERCOT price feed for the world model: every settled 15-minute interval, kept.

The bundled study is frozen on 2021 to 2025 so its numbers reproduce. This module keeps
a second store, ``data/twin/ercot_rtm_live.parquet``, with every real-time settlement
point price ERCOT has published since, for the same three load zones, read from ERCOT's
public daily display pages (``gridsignal.live``). A refresh

* backfills every missing day up to yesterday, one page per operating day;
* re-reads today's page, which grows by one interval every 15 minutes;
* never rewrites a settled interval with a different value without saying so.

The Planner refits a second world model on bundled plus live days, so the scenario
"2026 so far" is learned from this year's real prices, and checks today's intervals
against the band that model draws for this month.

    python -m gridsignal.twin feed            # backfill and refresh once
    python -m gridsignal.twin feed --watch    # refresh every 15 minutes until stopped
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from gridsignal import live
from gridsignal.twin.data import (
    DATA_FILE,
    INTERVALS_PER_DAY,
    TWIN_DIR,
    ZONES,
    PriceDays,
    frame_to_days,
    load_days,
)

LIVE_FILE = TWIN_DIR / "ercot_rtm_live.parquet"
#: Where refreshes go when the checkout is read-only (some hosts mount it that way).
FALLBACK_FILE = Path(tempfile.gettempdir()) / "gridsignal" / "ercot_rtm_live.parquet"
#: The bundled history ends here; the live store starts the day after.
FIRST_LIVE_DAY = date(2026, 1, 1)
REFRESH_S = 15 * 60
COLUMNS = ["Interval Start", "Location", "SPP"]


def store_path() -> Path:
    """The live store to read and write: the bundled file, or a writable copy of it."""
    if FALLBACK_FILE.exists():
        return FALLBACK_FILE
    if os.access(LIVE_FILE.parent, os.W_OK) and (
        not LIVE_FILE.exists() or os.access(LIVE_FILE, os.W_OK)
    ):
        return LIVE_FILE
    FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    if LIVE_FILE.exists():
        shutil.copyfile(LIVE_FILE, FALLBACK_FILE)
    return FALLBACK_FILE


class FeedError(RuntimeError):
    """The live store could not be refreshed at all."""


@dataclass
class FeedStatus:
    days_in_store: int
    first_day: str | None
    last_full_day: str | None
    latest_interval: str | None
    intervals_today: int
    days_fetched: int
    fetched_at: str
    errors: list[str] = field(default_factory=list)
    revised_intervals: int = 0

    @property
    def ok(self) -> bool:
        return self.latest_interval is not None


def parse_day(html: str) -> pd.DataFrame:
    """One ERCOT real-time display page to long rows for the three modelled zones.

    Rows ERCOT has not published yet are simply absent, so today's page is partial.
    Daylight-saving hours that do not exist, or occur twice, are dropped here; the
    world model only ever uses days with exactly 96 intervals.
    """
    rows = live.parse_table(html)
    recs = []
    for r in rows:
        missing = [z for z in ZONES if z not in r]
        if missing:
            raise live.LiveDataError(f"zones {missing} not on the ERCOT page")
        hhmm = r["Interval Ending"]
        day = datetime.strptime(r["Oper Day"], "%m/%d/%Y")
        start = day + timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[2:])) - timedelta(minutes=15)
        for z in ZONES:
            recs.append((start, z, float(r[z])))
    frame = pd.DataFrame(recs, columns=COLUMNS)
    if frame.empty:
        return frame.astype({"SPP": float})
    ts = pd.to_datetime(frame["Interval Start"]).dt.tz_localize(
        "US/Central", ambiguous="NaT", nonexistent="NaT"
    )
    frame["Interval Start"] = ts
    frame = frame.dropna(subset=["Interval Start"])
    frame["Location"] = frame["Location"].astype("string")
    return frame.drop_duplicates(["Interval Start", "Location"]).reset_index(drop=True)


def load_store(path: Path | None = None) -> pd.DataFrame:
    path = path or store_path()
    if not path.exists():
        return pd.DataFrame(
            {
                "Interval Start": pd.Series([], dtype="datetime64[ns, US/Central]"),
                "Location": pd.Series([], dtype="string"),
                "SPP": pd.Series([], dtype=float),
            }
        )
    return pd.read_parquet(path)


def _full_days(store: pd.DataFrame) -> set[date]:
    if store.empty:
        return set()
    local = store["Interval Start"].dt.tz_convert("US/Central")
    counts = store.groupby([local.dt.date, store["Location"]]).size().unstack()
    full = counts.reindex(columns=list(ZONES)).ge(INTERVALS_PER_DAY - 4).all(axis=1)
    return set(full[full].index)


def refresh(
    path: Path | None = None,
    today: date | None = None,
    get: Callable[[str], str] = live._get,
    max_days: int = 400,
    first_day: date = FIRST_LIVE_DAY,
) -> FeedStatus:
    """Backfill missing days, re-read today, merge, and write the store.

    ``get`` is the HTTP fetch (swapped out in tests). A day that fails to load is
    reported in ``errors`` and retried on the next refresh; the store is still
    written with everything that did load.
    """
    today = today or datetime.now(live.CENTRAL).date()
    path = path or store_path()
    store = load_store(path)
    have = _full_days(store)
    wanted = [first_day + timedelta(days=i) for i in range((today - first_day).days + 1)]
    todo = [d for d in wanted if d not in have or d == today][-max_days:]
    frames, errors = [], []
    for d in todo:
        try:
            frames.append(parse_day(get(live.RT_URL.format(ymd=d.strftime("%Y%m%d")))))
        except live.LiveDataError as exc:
            errors.append(f"{d}: {exc}")
    revised = 0
    if frames:
        new = pd.concat(frames, ignore_index=True)
        if not store.empty:
            both = store.merge(new, on=["Interval Start", "Location"], suffixes=("", "_new"))
            revised = int((both["SPP"] - both["SPP_new"]).abs().gt(0.005).sum())
        store = (
            pd.concat([store, new], ignore_index=True)
            .drop_duplicates(["Interval Start", "Location"], keep="last")
            .sort_values(["Interval Start", "Location"])
            .reset_index(drop=True)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        store.to_parquet(path, index=False)
    status = describe(store, today)
    status.days_fetched = len(frames)
    status.errors = errors
    status.revised_intervals = revised
    if not status.ok and errors:
        raise FeedError("; ".join(errors[-3:]))
    return status


def last_full_day(path: Path | None = None) -> str | None:
    """The newest settled day in the store: the live world model's cache key."""
    return describe(load_store(path)).last_full_day


def describe(store: pd.DataFrame, today: date | None = None) -> FeedStatus:
    today = today or datetime.now(live.CENTRAL).date()
    fetched = datetime.now(live.CENTRAL).strftime("%Y-%m-%d %H:%M %Z")
    if store.empty:
        return FeedStatus(0, None, None, None, 0, 0, fetched)
    local = store["Interval Start"].dt.tz_convert("US/Central")
    full = sorted(_full_days(store) - {today})
    latest = local.max()
    return FeedStatus(
        days_in_store=int(local.dt.date.nunique()),
        first_day=str(local.min().date()),
        last_full_day=str(full[-1]) if full else None,
        latest_interval=(latest + pd.Timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M"),
        intervals_today=int((local.dt.date == today).sum() // len(ZONES)),
        days_fetched=0,
        fetched_at=fetched,
    )


def live_days(path: Path | None = None) -> PriceDays | None:
    """Full days in the live store (today's partial day is excluded by construction)."""
    store = load_store(path)
    if store.empty:
        return None
    try:
        return frame_to_days(store)
    except (ValueError, KeyError):
        return None


def combined_days(path: Path | None = None, bundled: Path = DATA_FILE) -> PriceDays:
    """Bundled 2021 to 2025 plus every full live day, deduplicated by date."""
    hist = load_days(bundled)
    extra = live_days(path)
    if extra is None or len(extra) == 0:
        return hist
    keep = ~extra.dates.isin(hist.dates)
    dates = hist.dates.append(extra.dates[keep])
    prices = np.concatenate([hist.prices, extra.prices[keep]])
    order = np.argsort(dates.values)
    return PriceDays(pd.DatetimeIndex(dates[order]), prices[order], hist.zones)


def today_prices(path: Path | None = None, today: date | None = None) -> np.ndarray | None:
    """Today's settled intervals as ``(3, k)``, or None before the first one settles."""
    today = today or datetime.now(live.CENTRAL).date()
    store = load_store(path)
    if store.empty:
        return None
    local = store["Interval Start"].dt.tz_convert("US/Central")
    day = store[local.dt.date == today].copy()
    if day.empty:
        return None
    day["slot"] = local[day.index].dt.hour * 4 + local[day.index].dt.minute // 15
    cube = day.pivot_table(index="Location", columns="slot", values="SPP").reindex(list(ZONES))
    cube = cube.dropna(axis=1)
    return cube.to_numpy() if cube.shape[1] else None


@dataclass(frozen=True)
class TodayCheck:
    intervals: int
    inside_share: float  # share of zone-intervals inside the model's 5 to 95% band
    band_low: np.ndarray  # (3, k)
    band_high: np.ndarray
    actual: np.ndarray

    @property
    def verdict(self) -> str:
        if self.inside_share >= 0.8:
            return "inside the model's range"
        if self.inside_share >= 0.5:
            return "partly outside the model's range"
        return "outside the model's range: the model is missing something today"


def check_today(
    model, actual: np.ndarray, month: int, sims: int = 200, seed: int = 11
) -> TodayCheck:
    """Is today's price path one the world model would have drawn for this month?"""
    rng = np.random.default_rng(seed)
    draws = model.sample(np.full(sims, month), rng, regime=max(model.regimes_), level_risk=False)
    k = actual.shape[1]
    lo = np.percentile(draws[:, :, :k], 5, axis=0)
    hi = np.percentile(draws[:, :, :k], 95, axis=0)
    inside = float(((actual >= lo) & (actual <= hi)).mean())
    return TodayCheck(k, inside, lo, hi, actual)


def watch(interval_s: int = REFRESH_S, rounds: int | None = None, **kw) -> None:
    n = 0
    while rounds is None or n < rounds:
        try:
            s = refresh(**kw)
            errs = f", {len(s.errors)} error(s)" if s.errors else ""
            print(
                f"[{s.fetched_at}] {s.days_in_store} days, latest interval {s.latest_interval}, "
                f"{s.intervals_today} today, fetched {s.days_fetched} page(s){errs}"
            )
        except FeedError as exc:
            print(f"refresh failed: {exc}")
        n += 1
        if rounds is None or n < rounds:
            time.sleep(interval_s)
